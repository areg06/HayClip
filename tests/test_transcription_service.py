"""Paid transcription state machine against the fake Harmar server (never the real one)."""
from __future__ import annotations

import json
import socket

import pytest

from hayclips import jsonio
from hayclips.config import load_settings
from hayclips.errors import (ArtifactMismatch, BudgetExceeded, ConsentMissing, NeedsReconciliation,
                             PaidOperationBlocked, ValidationError)
from hayclips.models import (COMPLETED, FAILED, PROVIDER_FAILED, SUBMITTED, UNKNOWN_SUBMISSION, Clip,
                             ConsentRecord, TranscriptionAttempt, Window, new_id)
from hayclips.project import ProjectRepo, media_record
from hayclips.transcription import service
from hayclips.transcription.fake_harmar import FakeHarmar
from hayclips.transcription.harmar import HarmarClient
from hayclips.transcription.store import completed_transcript, list_attempts, save_attempt

KEY = "hk_test_fake"
FAST = dict(poll_interval=(0.01, 0.02), poll_timeout=5)


def make_project(tmp_path, *, duration=5.0, consent=True, n=1, name="proj"):
    repo = ProjectRepo(tmp_path / name)
    p = repo.init(name, {"kind": "youtube", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "video_id": "aaaaaaaaaaa"})
    ids = []
    for i in range(n):
        cid = new_id("clp")
        p.clips.append(Clip(id=cid, order=i + 1, start=100.0 + 100 * i, end=100.0 + 100 * i + duration - 2 * 0.5, pad=0.5))
        cdir = repo.clip_dir(cid)
        cdir.mkdir(parents=True)
        (cdir / "wide.mp4").write_bytes(b"wide" + cid.encode())
        (cdir / "audio.m4a").write_bytes(b"audio-bytes-" + cid.encode())
        wide = media_record(cdir / "wide.mp4", "wide.mp4", "wide", duration)
        audio = media_record(cdir / "audio.m4a", "audio.m4a", "audio", duration,
                             derived_from={"path": "wide.mp4", "sha256": wide.sha256})
        repo.save_window(Window(clip_id=cid, source_start=99.5 + 100 * i, source_end=99.5 + 100 * i + duration,
                                pad_start=0.5, wide=wide, audio=audio, source_ref="aaaaaaaaaaa"))
        ids.append(cid)
    if consent:
        p.consent.append(ConsentRecord(id=new_id("cns"), provider="harmar", scope="selected_windows",
                                       granted_by="Test Creator", recorded_by="tester", statement="ok to transcribe clips"))
    repo.save(p)
    return repo, ids


@pytest.fixture
def fake(monkeypatch):
    with FakeHarmar(balance=600, api_key=KEY) as f:
        monkeypatch.setenv("HAYCLIPS_HARMAR_BASE_URL", f.base_url)
        yield f


def run(repo, **kw):
    kw.setdefault("settings", load_settings())
    kw.setdefault("api_key", KEY)
    kw.setdefault("confirmed_by", "tester")
    return service.transcribe(repo, **{**FAST, **kw})


def test_successful_submit_and_poll(tmp_path, fake):
    fake.scenario["polls_until_complete"] = 3
    repo, (cid,) = make_project(tmp_path)
    out = run(repo)
    assert [o.action for o in out] == ["completed"]
    a = list_attempts(repo, cid)[-1]
    assert a.state == COMPLETED and a.provider_job_id and a.seconds_charged == 5
    assert a.media.kind == "audio" and a.balance_before == 600 and a.consent_id
    ref = completed_transcript(repo, cid)
    assert ref.raw["words"] and ref.media.sha256 == a.media.sha256
    assert fake.submit_count == 1
    states = [h["to"] for h in a.history]
    assert states == ["PREPARED", "RESERVED", "UPLOAD_CREATED", "UPLOADED", "SUBMITTING", "SUBMITTED", "POLLING", "COMPLETED"]


def test_cached_exact_artifact_reused_without_key_or_network(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)
    run(repo)
    before = len(fake.requests)
    out = run(repo, api_key=None, confirmed_by=None)
    assert [o.action for o in out] == ["reused"]
    assert len(fake.requests) == before and fake.submit_count == 1


def test_completed_attempt_with_changed_bytes_stops(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)
    run(repo)
    (repo.clip_dir(cid) / "audio.m4a").write_bytes(b"re-encoded")
    with pytest.raises(ArtifactMismatch):
        run(repo)
    assert fake.submit_count == 1


def test_logical_match_with_different_hash_stops(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)
    run(repo)
    done = list_attempts(repo, cid)[-1]
    # a failed attempt for the same logical clip but different bytes, then new audio on disk
    w = repo.load_window(cid)
    (repo.clip_dir(cid) / "audio2.m4a").write_bytes(b"different audio")
    w.audio = media_record(repo.clip_dir(cid) / "audio2.m4a", "audio2.m4a", "audio", 5.0, w.audio.derived_from)
    repo.save_window(w)
    done.state = FAILED            # pretend the completed one failed, so only the logical key links them
    save_attempt(repo, done)
    with pytest.raises(ArtifactMismatch, match="logical"):
        run(repo)
    assert fake.submit_count == 1


def test_insufficient_balance_no_post(tmp_path, fake):
    fake.balance = 5            # need 5 * 1.1
    repo, (cid,) = make_project(tmp_path)
    with pytest.raises(BudgetExceeded, match="balance"):
        run(repo)
    assert fake.submit_count == 0
    assert list_attempts(repo, cid)[-1].state == FAILED


def test_connection_refused_is_definite_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("HAYCLIPS_HARMAR_BASE_URL", "http://127.0.0.1:9")
    repo, (cid,) = make_project(tmp_path)
    with pytest.raises(Exception):
        run(repo)
    a = list_attempts(repo, cid)
    assert all(x.state == FAILED for x in a)


def test_4xx_submit_is_failed_safe_to_retry(tmp_path, fake):
    fake.scenario["fail_submit_status"] = 422
    repo, (cid,) = make_project(tmp_path)
    out = run(repo)
    assert out[0].action == "failed"
    a = list_attempts(repo, cid)[-1]
    assert a.state == FAILED and a.to_dict()["retry_safety"] == "FAILED_SAFE_TO_RETRY"
    fake.scenario["fail_submit_status"] = None
    out = run(repo)                         # retry is allowed (with confirmation) and creates a new attempt
    assert out[0].action == "completed" and len(list_attempts(repo, cid)) == 2


def test_429_is_definite_rejection(tmp_path, fake):
    fake.scenario["fail_submit_status"] = 429
    repo, (cid,) = make_project(tmp_path)
    run(repo)
    assert list_attempts(repo, cid)[-1].state == FAILED and fake.submit_count == 1


@pytest.mark.parametrize("scenario", [
    {"fail_submit_status": 500, "create_job_on_failure": True},
    {"fail_submit_status": 503},
    {"reset_after_submit": True},
    {"submit_hangs": 1.5},
    {"malformed_submit_body": True},
])
def test_ambiguous_submit_goes_to_reconciliation_and_is_never_resubmitted(tmp_path, fake, monkeypatch, scenario):
    fake.scenario.update(scenario)
    monkeypatch.setenv("HAYCLIPS_HTTP_TIMEOUT", "0.5")
    repo, (cid,) = make_project(tmp_path)
    out = run(repo)
    assert out[0].action == "needs_reconciliation"
    assert list_attempts(repo, cid)[-1].state == UNKNOWN_SUBMISSION
    assert fake.submit_count == 1
    fake.scenario.update({"fail_submit_status": None, "reset_after_submit": False, "submit_hangs": 0,
                          "malformed_submit_body": False})
    with pytest.raises(NeedsReconciliation):
        run(repo)
    assert fake.submit_count == 1


def test_unknown_outcome_blocks_new_submissions_account_wide(tmp_path, fake):
    fake.scenario["fail_submit_status"] = 502
    repo1, _ = make_project(tmp_path, name="p1")
    run(repo1)
    fake.scenario["fail_submit_status"] = None
    repo2, _ = make_project(tmp_path, name="p2")
    with pytest.raises(NeedsReconciliation, match="account"):
        run(repo2)
    assert fake.submit_count == 1


def test_stale_submitting_record_becomes_unknown(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)

    def crash():
        raise SystemExit("simulated crash in the danger window")
    with pytest.raises(SystemExit):
        run(repo, hooks={"after_submit_response_before_persist": crash})
    assert list_attempts(repo, cid)[-1].state == "SUBMITTING"
    with pytest.raises(NeedsReconciliation):
        run(repo)
    assert list_attempts(repo, cid)[-1].state == UNKNOWN_SUBMISSION
    assert fake.submit_count == 1


def test_crash_after_job_id_persisted_resumes_polling(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)

    def crash():
        raise SystemExit("crash after commit")
    with pytest.raises(SystemExit):
        run(repo, hooks={"after_submit_response": crash})
    assert list_attempts(repo, cid)[-1].state == SUBMITTED
    out = run(repo, confirmed_by=None)       # resuming a poll is free; no confirmation needed
    assert out[0].action == "completed" and fake.submit_count == 1


def test_resume_polling_without_key_is_clear_error(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)
    with pytest.raises(SystemExit):
        run(repo, hooks={"after_submit_response": lambda: (_ for _ in ()).throw(SystemExit())})
    before = len(fake.requests)
    with pytest.raises(PaidOperationBlocked, match="HARMAR_API_KEY"):
        run(repo, api_key=None)
    assert len(fake.requests) == before


def test_provider_failure_is_refunded_and_safe(tmp_path, fake):
    fake.scenario["job_fails"] = True
    repo, (cid,) = make_project(tmp_path)
    out = run(repo)
    assert out[0].action == "failed" and list_attempts(repo, cid)[-1].state == PROVIDER_FAILED
    assert fake.balance == 600


def test_poll_timeout_leaves_polling_and_never_resubmits(tmp_path, fake):
    fake.scenario["polls_until_complete"] = 10_000
    repo, (cid,) = make_project(tmp_path)
    out = run(repo, poll_timeout=0.2)
    assert out[0].action == "polling"
    out = run(repo, poll_timeout=0.2)
    assert out[0].action == "polling" and fake.submit_count == 1


def test_calling_twice_submits_once(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)
    run(repo)
    run(repo)
    assert fake.submit_count == 1 and len(list_attempts(repo, cid)) == 1


def test_consent_missing_blocks_before_network(tmp_path, fake):
    repo, _ = make_project(tmp_path, consent=False)
    with pytest.raises(ConsentMissing):
        run(repo)
    assert fake.requests == []


def test_revoked_consent_blocks(tmp_path, fake):
    repo, _ = make_project(tmp_path)
    p = repo.load()
    p.consent[0].revoked_at = "2026-10-01T00:00:00+00:00"
    repo.save(p)
    with pytest.raises(ConsentMissing):
        run(repo)


def test_no_confirmation_means_no_paid_call(tmp_path, fake):
    repo, _ = make_project(tmp_path)
    with pytest.raises(PaidOperationBlocked, match="5 s"):
        run(repo, confirmed_by=None)
    assert fake.submit_count == 0


def test_key_missing_without_cache_is_clear_error(tmp_path, fake):
    repo, _ = make_project(tmp_path)
    with pytest.raises(PaidOperationBlocked, match="HARMAR_API_KEY"):
        run(repo, api_key=None)
    assert fake.requests == []


def test_budget_per_operation(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("HAYCLIPS_MAX_PAID_SECONDS_PER_OPERATION", "8")
    repo, _ = make_project(tmp_path, n=2)
    with pytest.raises(BudgetExceeded, match="operation"):
        run(repo)
    assert fake.requests == []


def test_budget_per_project_counts_previous_charges(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("HAYCLIPS_MAX_PAID_SECONDS_PER_PROJECT", "8")
    repo, (a, b) = make_project(tmp_path, n=2)
    run(repo, clip_ids=[a])
    with pytest.raises(BudgetExceeded, match="project"):
        run(repo, clip_ids=[b])
    assert fake.submit_count == 1


def test_budget_per_day_is_account_wide(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("HAYCLIPS_MAX_PAID_SECONDS_PER_DAY", "8")
    r1, _ = make_project(tmp_path, name="p1")
    r2, _ = make_project(tmp_path, name="p2")
    run(r1)
    with pytest.raises(BudgetExceeded, match="day"):
        run(r2)
    assert fake.submit_count == 1


def test_window_longer_than_cap_refused(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("HAYCLIPS_MAX_WINDOW_SECONDS", "4")
    repo, _ = make_project(tmp_path, duration=5.0)
    with pytest.raises(BudgetExceeded, match="window"):
        run(repo)
    assert fake.requests == []


def test_missing_audio_artifact_tells_operator_to_fetch(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)
    w = repo.load_window(cid)
    w.audio = None
    repo.save_window(w)
    with pytest.raises(ValidationError, match="fetch"):
        run(repo)
    assert fake.requests == []


def test_legacy_preview_transcript_is_reused(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)
    cdir = repo.clip_dir(cid)
    (cdir / "preview.mp4").write_bytes(b"legacy preview")
    w = repo.load_window(cid)
    w.preview = media_record(cdir / "preview.mp4", "preview.mp4", "preview_video", 5.0)
    repo.save_window(w)
    att = TranscriptionAttempt(id=new_id("att"), clip_id=cid, provider="harmar", state=COMPLETED, media=w.preview,
                               options={"timestamps": "word"}, provider_job_id="legacy-job", seconds_charged=5,
                               origin="legacy_import", result_file=None)
    att.result_file = f"{att.id}.result.json"
    jsonio.write_json(repo.transcription_dir(cid) / att.result_file, {"segments": [], "words": []})
    save_attempt(repo, att)
    out = run(repo, api_key=None, confirmed_by=None)
    assert out[0].action == "reused" and fake.requests == []


def test_real_host_without_opt_in_opens_no_socket(monkeypatch):
    opened = []
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(OSError()))
    s = load_settings({"HAYCLIPS_HOME": "/tmp/x"})          # real base URL, no opt-in
    with pytest.raises(PaidOperationBlocked):
        HarmarClient(s.harmar_base_url, "hk_live_whatever", 5, s).balance()
    assert opened == []


def test_unknown_non_loopback_host_refused():
    s = load_settings({"HAYCLIPS_HOME": "/tmp/x", "HAYCLIPS_HARMAR_BASE_URL": "https://evil.example.com"})
    with pytest.raises(PaidOperationBlocked):
        HarmarClient(s.harmar_base_url, "k", 5, s)


def test_plan_makes_no_requests(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)
    plans = service.plan(repo, settings=load_settings())
    assert [p.action for p in plans] == ["new"] and plans[0].seconds == 5
    assert fake.requests == []


def test_errors_never_contain_key_or_signed_url(tmp_path, fake, capsys):
    fake.scenario["upload_fail_times"] = 10
    repo, (cid,) = make_project(tmp_path)
    with pytest.raises(Exception) as exc:
        run(repo)
    text = str(exc.value) + json.dumps([a.to_dict() for a in list_attempts(repo, cid)])
    assert KEY not in text and "X-Signature" not in text
    assert list_attempts(repo, cid)[-1].state == FAILED and fake.submit_count == 0
