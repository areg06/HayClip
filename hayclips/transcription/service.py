"""Paid transcription service: durable, non-duplicating Harmar submissions.

Every step of an attempt is persisted (atomically) before the network call that follows it:

  PREPARED -> RESERVED (balance_before) -> UPLOAD_CREATED -> UPLOADED -> SUBMITTING -> [POST = charge]
           -> SUBMITTED (job id committed first) -> POLLING -> COMPLETED | PROVIDER_FAILED

Decision per clip, made before any network traffic:
  reconcile    an attempt is SUBMITTING (stale) or UNKNOWN_SUBMISSION: stop, a human must reconcile
  reuse        a COMPLETED attempt whose exact media bytes still match its recorded sha256
  resume       SUBMITTED/POLLING: poll the existing job (free); never resubmit
  new          send window.audio (audio-only); requires consent + operator confirmation + key + budgets
A logical-key match (same clip/window/recipe) with different bytes is an ArtifactMismatch: STOP.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import jsonio
from ..config import Settings, load_settings
from ..errors import (ArtifactMismatch, ConsentMissing, NeedsReconciliation, PaidOperationBlocked, PipelineError,
                      ProviderError, ValidationError)
from ..models import (COMPLETED, FAILED, IN_FLIGHT_STATES, POLLING, PRE_SUBMIT_STATES, PREPARED, PROVIDER_FAILED,
                      RESERVED, SUBMITTED, SUBMITTING, UNKNOWN_SUBMISSION, UPLOAD_CREATED, UPLOADED, Clip,
                      MediaFile, Project, TranscriptionAttempt, new_id, now_iso)
from ..project import ProjectRepo
from . import budget
from .harmar import HarmarClient, NotSent, SubmitRejected, SubmitUnknown
from .store import list_attempts, save_attempt

PROVIDER = "harmar"
OPTIONS = {"timestamps": "word", "punctuation": True}
SOURCE_LANG = "hy"
RECIPE_VERSION = "audio-v1"       # bump when the audio extraction recipe changes
CRASH_ENV = "HAYCLIPS_TEST_CRASH_AT"


@dataclass
class ClipPlan:
    clip_id: str
    order: int
    action: str                      # reuse | resume | reconcile | in_progress | new
    seconds: int = 0
    attempt: TranscriptionAttempt | None = None
    media: MediaFile | None = None
    logical_key: str = ""
    note: str = ""


@dataclass
class ClipOutcome:
    clip_id: str
    action: str                      # reused | completed | polling | failed | needs_reconciliation | in_progress | blocked
    attempt: TranscriptionAttempt | None = None
    note: str = ""


def default_client_factory(settings: Settings, api_key: str) -> HarmarClient:
    timeout = float(os.environ.get("HAYCLIPS_HTTP_TIMEOUT") or settings.timeouts.http)
    return HarmarClient(settings.harmar_base_url, api_key, timeout, settings)


def logical_key(window, media: MediaFile, options: dict) -> str:
    """Identity of the EXPECTED artifact. Not proof of payment: only media.sha256 is."""
    ident = {"source": window.source_ref, "start": round(window.source_start, 3), "end": round(window.source_end, 3),
             "pad_start": round(window.pad_start, 3), "kind": media.kind, "recipe": RECIPE_VERSION,
             "provider": PROVIDER, "options": options, "lang": SOURCE_LANG}
    return hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()


class AccountLock:
    """Single-flight paid submissions per account: exclusive flock under HAYCLIPS_HOME."""

    def __init__(self, settings: Settings):
        self.path = settings.home / "harmar-submit.lock"
        self.fd = None

    def acquire(self, blocking: bool = True) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            return True
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            return False

    def release(self) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def _lock_is_free(settings: Settings) -> bool:
    lock = AccountLock(settings)
    if lock.acquire(blocking=False):
        lock.release()
        return True
    return False


def _fail(repo: ProjectRepo, a: TranscriptionAttempt, state: str, error: str) -> None:
    a.error = error[:500]
    a.transition(state, error[:200])
    save_attempt(repo, a)


def _plan_clip(repo: ProjectRepo, clip: Clip, settings: Settings, *, lock_held: bool = False) -> ClipPlan:
    attempts = list_attempts(repo, clip.id)
    plan = ClipPlan(clip_id=clip.id, order=clip.order, action="new")
    # (a) danger-window leftovers: a SUBMITTING record with nobody holding the lock is a crash
    for a in attempts:
        if a.state == SUBMITTING:
            if lock_held or _lock_is_free(settings):
                a.error = "process stopped between persisting SUBMITTING and recording the job id"
                a.transition(UNKNOWN_SUBMISSION, "stale SUBMITTING found; a job may exist")
                save_attempt(repo, a)
                budget.record(settings, "unknown", attempt_id=a.id, clip_id=clip.id, project=repo.root.name,
                              seconds=a.seconds_estimate)
            else:
                plan.action, plan.attempt, plan.note = "in_progress", a, "another process is submitting this clip"
                return plan
    unknown = [a for a in attempts if a.state == UNKNOWN_SUBMISSION]
    if unknown:
        plan.action, plan.attempt = "reconcile", unknown[-1]
        plan.note = "a previous submission may have been accepted by Harmar"
        return plan
    # (b) reuse only the exact bytes that were paid for
    done = [a for a in attempts if a.state == COMPLETED and a.result_file]
    if done:
        repo.verify_media(clip.id, done[-1].media)   # raises ArtifactMismatch: never reuse, never resubmit
        plan.action, plan.attempt, plan.media = "reuse", done[-1], done[-1].media
        return plan
    # (c) a job exists: poll it, never resubmit
    flight = [a for a in attempts if a.state in IN_FLIGHT_STATES]
    if flight:
        plan.action, plan.attempt, plan.media = "resume", flight[-1], flight[-1].media
        return plan
    # interrupted before the charge point: nothing was charged
    for a in attempts:
        if a.state in PRE_SUBMIT_STATES and (lock_held or _lock_is_free(settings)):
            _fail(repo, a, FAILED, "interrupted before the charge point; nothing was charged")
    # (d) a new submission of the audio-only artifact
    window = repo.load_window(clip.id)
    if window is None or window.audio is None:
        raise ValidationError(f"{clip.id}: no audio artifact to transcribe",
                              hint="run fetch_clips.py for this project first (it extracts audio.m4a)")
    media = window.audio
    repo.verify_media(clip.id, media)
    key = logical_key(window, media, OPTIONS)
    for a in attempts:
        if a.logical_key == key and a.media.sha256 != media.sha256:
            raise ArtifactMismatch(
                f"{clip.id}: logical clip matches attempt {a.id} but the audio bytes differ "
                f"({a.media.sha256[:12]}… vs {media.sha256[:12]}…)",
                hint="the window was re-extracted; investigate before transcribing again (nothing was submitted)")
    duration = media.duration
    if duration is None:
        from ..media.probe import duration_of
        duration = duration_of(repo.media_path(clip.id, media), settings)
    plan.seconds = budget.billable_seconds(duration)
    budget.check_window(settings, plan.seconds, clip.id)
    plan.media, plan.logical_key = media, key
    return plan


def _select(project: Project, clip_ids) -> list[Clip]:
    clips = project.ordered()
    if clip_ids:
        known = {c.id for c in project.clips}
        missing = [c for c in clip_ids if c not in known]
        if missing:
            raise ValidationError(f"unknown clip id(s): {', '.join(missing)}")
        clips = [c for c in clips if c.id in set(clip_ids)]
    return clips


def plan(repo: ProjectRepo, clip_ids=None, *, settings: Settings | None = None) -> list[ClipPlan]:
    """Read-only decision for each clip (only stale-record bookkeeping is written). No network."""
    settings = settings or load_settings()
    return [_plan_clip(repo, c, settings) for c in _select(repo.load(), clip_ids)]


def _poll_defaults():
    lo = float(os.environ.get("HAYCLIPS_POLL_INTERVAL_MIN") or 3)
    hi = float(os.environ.get("HAYCLIPS_POLL_INTERVAL_MAX") or 30)
    return (lo, hi), float(os.environ.get("HAYCLIPS_POLL_TIMEOUT") or 900)


def transcribe(repo: ProjectRepo, clip_ids=None, *, settings: Settings | None = None, api_key: str | None = None,
               confirmed_by: str | None = None, client_factory: Callable | None = None,
               hooks: dict[str, Callable] | None = None, poll_interval=None, poll_timeout=None) -> list[ClipOutcome]:
    settings = settings or load_settings()
    factory = client_factory or default_client_factory
    d_interval, d_timeout = _poll_defaults()
    poll_interval = poll_interval or d_interval
    poll_timeout = poll_timeout if poll_timeout is not None else d_timeout
    project = repo.load()
    clips = {c.id: c for c in _select(project, clip_ids)}
    plans = [_plan_clip(repo, c, settings) for c in clips.values()]   # ArtifactMismatch stops everything here

    rec = [p for p in plans if p.action == "reconcile"]
    if rec:
        raise NeedsReconciliation(
            "; ".join(f"{p.clip_id}: attempt {p.attempt.id} outcome unknown" for p in rec),
            hint="check the Harmar dashboard, then `python -m hayclips reconcile <project> <clip_id> --show`")
    new = [p for p in plans if p.action == "new"]
    resume = [p for p in plans if p.action == "resume"]
    total = sum(p.seconds for p in new)
    if new:
        open_unknown = budget.unreconciled_unknowns(settings)
        if open_unknown:
            raise NeedsReconciliation(
                f"the Harmar account has {len(open_unknown)} unreconciled submission(s) with unknown outcome",
                hint="reconcile them first so balance changes can be attributed (single-flight rule)")
        consent = project.active_consent(PROVIDER)
        if consent is None:
            raise ConsentMissing(f"{project.name}: no active creator consent for sending clip audio to Harmar",
                                 hint="record it: python -m hayclips consent <project> --granted-by ... --statement ...")
        all_attempts = [a for c in project.clips for a in list_attempts(repo, c.id)]
        budget.check_budgets(settings, new_seconds=total, project_attempts=all_attempts)
        if not (confirmed_by or "").strip():
            raise PaidOperationBlocked(
                f"{len(new)} new paid submission(s), {total} s of Harmar time; not confirmed",
                hint="re-run with --confirm-paid --by <your name> after reviewing the plan")
    if (new or resume) and not api_key:
        raise PaidOperationBlocked("HARMAR_API_KEY is not set; needed to "
                                   + ("submit new clips" if new else "resume polling an existing job (no charge)"))
    client = factory(settings, api_key) if (new or resume) else None

    outcomes: list[ClipOutcome] = []
    stop_new = ""
    for p in plans:
        if p.action == "reuse":
            outcomes.append(ClipOutcome(p.clip_id, "reused", p.attempt, f"{p.media.kind} {p.media.sha256[:12]}…"))
        elif p.action == "in_progress":
            outcomes.append(ClipOutcome(p.clip_id, "in_progress", p.attempt, p.note))
        elif p.action == "resume":
            outcomes.append(_poll(repo, p.attempt, client, settings, poll_interval, poll_timeout))
        elif stop_new:
            outcomes.append(ClipOutcome(p.clip_id, "blocked", None, stop_new))
        else:
            out = _submit_new(repo, clips[p.clip_id], client, settings, confirmed_by, consent.id, hooks or {},
                              poll_interval, poll_timeout)
            outcomes.append(out)
            if out.action == "needs_reconciliation":
                stop_new = "not submitted: an earlier submission in this run has an unknown outcome"
    return outcomes


def _hook(name: str, hooks: dict, client) -> None:
    if name in hooks:
        hooks[name]()
    if os.environ.get(CRASH_ENV) == name and not getattr(client, "is_real", True):
        os._exit(70)   # test-only crash simulation; never honoured against the real host


def _submit_new(repo, clip, client, settings, confirmed_by, consent_id, hooks, poll_interval, poll_timeout) -> ClipOutcome:
    with AccountLock(settings):
        fresh = _plan_clip(repo, clip, settings, lock_held=True)   # another process may have done it meanwhile
        if fresh.action == "reuse":
            return ClipOutcome(clip.id, "reused", fresh.attempt, "completed by a concurrent run")
        if fresh.action == "reconcile":
            return ClipOutcome(clip.id, "needs_reconciliation", fresh.attempt, fresh.note)
        if fresh.action == "resume":
            a = fresh.attempt
        else:
            a = _submit_locked(repo, clip, fresh, client, settings, confirmed_by, consent_id, hooks)
            if a.state != SUBMITTED:
                return ClipOutcome(clip.id, "needs_reconciliation" if a.state == UNKNOWN_SUBMISSION else "failed", a,
                                   a.error or "")
    return _poll(repo, a, client, settings, poll_interval, poll_timeout)


def _submit_locked(repo, clip, p: ClipPlan, client, settings, confirmed_by, consent_id, hooks) -> TranscriptionAttempt:
    a = TranscriptionAttempt(id=new_id("att"), clip_id=clip.id, provider=PROVIDER, state=PREPARED, media=p.media,
                             options=dict(OPTIONS, source_lang=SOURCE_LANG), logical_key=p.logical_key,
                             seconds_estimate=p.seconds, confirmed_by=confirmed_by, consent_id=consent_id)
    a.history.append({"at": now_iso(), "from": None, "to": PREPARED, "note": "operator confirmed paid run"})
    save_attempt(repo, a)
    project_name = repo.root.name
    try:
        bal = int(client.balance().get("seconds_remaining", 0))
        a.balance_before = bal
        budget.check_balance(settings, bal, p.seconds)
        a.transition(RESERVED, f"balance {bal}s")
        save_attempt(repo, a)
        up = client.create_upload(Path(p.media.path).name, p.media.bytes)
        a.provider_media_id = up["media_id"]
        a.transition(UPLOAD_CREATED)
        save_attempt(repo, a)
        repo.verify_media(clip.id, p.media)          # the bytes we upload are the bytes on record
        client.put_upload(up["upload_url"], up.get("content_type", "application/octet-stream"),
                          repo.media_path(clip.id, p.media))
        a.transition(UPLOADED)
        save_attempt(repo, a)
        _hook("before_submit", hooks, client)        # last chance to stop (e.g. worker lost its lease); free
    except PipelineError as exc:
        _fail(repo, a, FAILED, f"before the charge point: {exc.message}")
        raise
    # ---- charge point ----
    a.transition(SUBMITTING, "persisted before POST /v1/transcripts")
    save_attempt(repo, a)
    budget.record(settings, "reserve", attempt_id=a.id, clip_id=clip.id, project=project_name, seconds=p.seconds)
    _hook("after_submitting_persisted", hooks, client)
    try:
        job = client.submit(a.provider_media_id, OPTIONS, SOURCE_LANG)
    except NotSent as exc:
        _fail(repo, a, FAILED, exc.message)
        budget.record(settings, "release", attempt_id=a.id, seconds=p.seconds, reason="not sent")
        return a
    except SubmitRejected as exc:
        _fail(repo, a, FAILED, exc.message + (f" (Retry-After {exc.retry_after}s)" if exc.retry_after else ""))
        budget.record(settings, "release", attempt_id=a.id, seconds=p.seconds, reason=f"HTTP {exc.status}")
        return a
    except SubmitUnknown as exc:
        a.error = exc.message
        a.transition(UNKNOWN_SUBMISSION, exc.message)
        save_attempt(repo, a)
        budget.record(settings, "unknown", attempt_id=a.id, clip_id=clip.id, project=project_name, seconds=p.seconds)
        return a
    _hook("after_submit_response_before_persist", hooks, client)
    a.provider_job_id = str(job["id"])           # first thing after the response
    a.submitted_at = now_iso()
    a.transition(SUBMITTED)
    save_attempt(repo, a)
    _hook("after_submit_response", hooks, client)
    return a


def _poll(repo, a: TranscriptionAttempt, client, settings, interval, timeout) -> ClipOutcome:
    if a.state == SUBMITTED:
        a.transition(POLLING)
        save_attempt(repo, a)
    delay, deadline = interval[0], time.monotonic() + timeout
    while True:
        try:
            r = client.get(a.provider_job_id)
        except ProviderError:
            r = None
        status = (r or {}).get("status")
        if status == "completed":
            a.result_file = f"{a.id}.result.json"
            jsonio.write_json(repo.transcription_dir(a.clip_id) / a.result_file, r)
            a.seconds_charged = r.get("seconds_charged")
            a.completed_at = r.get("completed_at") or now_iso()
            a.transition(COMPLETED)
            save_attempt(repo, a)
            budget.record(settings, "charge", attempt_id=a.id, seconds=a.seconds_charged or a.seconds_estimate)
            return ClipOutcome(a.clip_id, "completed", a)
        if status == "failed":
            refunded = int(r.get("seconds_refunded") or 0)
            a.seconds_charged = int(r.get("seconds_charged") or 0) - refunded
            a.error = f"Harmar reported failure ({r.get('error', 'no reason')}); refunded {refunded}s"
            a.transition(PROVIDER_FAILED, a.error)
            save_attempt(repo, a)
            budget.record(settings, "release", attempt_id=a.id, seconds=refunded or a.seconds_estimate,
                          reason="provider failed (refunded)")
            return ClipOutcome(a.clip_id, "failed", a, a.error)
        if time.monotonic() >= deadline:
            return ClipOutcome(a.clip_id, "polling", a, "still processing; re-run to keep polling (no new charge)")
        time.sleep(delay)
        delay = min(delay * 1.5, interval[1])
