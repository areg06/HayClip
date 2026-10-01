"""Operator reconciliation of a paid submission whose outcome is unknown. Never submits anything.

  python -m hayclips reconcile <project> <clip_id> --show
  python -m hayclips reconcile <project> <clip_id> --attach-job-id JOB --by NAME [--evidence TEXT]
      the job was found in the Harmar dashboard: the attempt becomes SUBMITTED and the next
      harmar_clips.py run polls it (free)
  python -m hayclips reconcile <project> <clip_id> --not-created --by NAME --evidence TEXT
      the operator verified that no job exists and the balance did not drop by the expected amount:
      the attempt becomes RECONCILED_NOT_CREATED, which allows ONE new confirmed submission
"""
from __future__ import annotations

from pathlib import Path

from ..config import load_settings
from ..errors import PipelineError, ValidationError
from ..models import (NEEDS_RECONCILIATION_STATES, RECONCILED_NOT_CREATED, SUBMITTED, SUBMITTING,
                      UNKNOWN_SUBMISSION, now_iso)
from ..project import ProjectRepo
from . import budget
from .service import _lock_is_free
from .store import list_attempts, save_attempt


def pending(repo: ProjectRepo, clip_id: str):
    repo.load().clip(clip_id)
    att = [a for a in list_attempts(repo, clip_id) if a.state in NEEDS_RECONCILIATION_STATES]
    return att[-1] if att else None


def show(repo: ProjectRepo, clip_id: str) -> str:
    a = pending(repo, clip_id)
    if a is None:
        return f"{clip_id}: nothing to reconcile"
    return (f"{clip_id}: attempt {a.id} is {a.state}\n"
            f"  media {a.media.path} sha256 {a.media.sha256[:16]}… ({a.media.bytes} bytes)\n"
            f"  balance before submit: {a.balance_before}s; expected charge if a job exists: {a.seconds_estimate}s\n"
            f"  provider upload id: {a.provider_media_id}\n  error: {a.error}\n"
            "  check the Harmar dashboard for a job created around "
            f"{a.updated_at}; then use --attach-job-id or --not-created")


def resolve(repo: ProjectRepo, clip_id: str, *, by: str, job_id: str | None = None, not_created: bool = False,
            evidence: str = "", settings=None):
    settings = settings or load_settings()
    if bool(job_id) == bool(not_created):
        raise ValidationError("give exactly one of --attach-job-id or --not-created")
    if not by.strip():
        raise ValidationError("--by is required")
    if not_created and not evidence.strip():
        raise ValidationError("--not-created needs --evidence (e.g. balance before/after, dashboard check)")
    a = pending(repo, clip_id)
    if a is None:
        raise ValidationError(f"{clip_id}: no attempt needs reconciliation")
    if a.state == SUBMITTING and not _lock_is_free(settings):
        raise PipelineError(f"{clip_id}: a submission is in progress right now; wait for it to finish")
    a.reconciliation = {"by": by, "at": now_iso(), "evidence": evidence, "from_state": a.state,
                        "result": "job_attached" if job_id else "not_created"}
    if job_id:
        a.provider_job_id = job_id.strip()
        a.transition(SUBMITTED, f"reconciled by {by}: job found in dashboard")
    else:
        a.transition(RECONCILED_NOT_CREATED, f"reconciled by {by}: no job was created")
    save_attempt(repo, a)
    budget.record(settings, "reconciled", attempt_id=a.id, result=a.reconciliation["result"])
    if not_created:
        budget.record(settings, "release", attempt_id=a.id, seconds=a.seconds_estimate, reason="reconciled not created")
    return a


def _cmd(args) -> None:
    repo = ProjectRepo(Path(args.project))
    if args.show or not (args.attach_job_id or args.not_created):
        print(show(repo, args.clip_id))
        return
    a = resolve(repo, args.clip_id, by=args.by or "", job_id=args.attach_job_id, not_created=args.not_created,
                evidence=args.evidence or "")
    print(f"{args.clip_id}: attempt {a.id} -> {a.state}")
    if a.state == SUBMITTED:
        print("  next: harmar_clips.py will poll this job (no new charge)")
    else:
        print("  next: a new submission is allowed after you confirm it with --confirm-paid --by NAME")


def add_parser(sub) -> None:
    r = sub.add_parser("reconcile", help="resolve a paid submission with unknown outcome (never submits)")
    r.add_argument("project")
    r.add_argument("clip_id")
    r.add_argument("--show", action="store_true")
    r.add_argument("--attach-job-id")
    r.add_argument("--not-created", action="store_true")
    r.add_argument("--by")
    r.add_argument("--evidence")
    r.set_defaults(fn=_cmd)


__all__ = ["add_parser", "resolve", "show", "pending", "UNKNOWN_SUBMISSION"]
