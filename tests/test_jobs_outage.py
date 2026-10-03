"""Database outage and lease-loss policy for the worker (Phase 1c).

Policy under test:
- a heartbeat that fails is retried on a fresh connection; a short outage changes nothing;
- if ownership cannot be re-proved within 2/3 of the lease (before the lease can expire), the job is
  told to stop and its outcome is not written: "uncertain" is treated exactly like "lost";
- the claim loop survives an unreachable database (no job claimed, retry later);
- a result write is retried; if it still fails, the job is left to the reaper (safe jobs retry,
  paid jobs are never re-run);
- a paid job that cannot prove ownership stops BEFORE the charging submit.
"""
import psycopg
import pytest

from hayclips.jobs import handlers as H
from hayclips.jobs import queue as q
from hayclips.jobs import worker as W
from hayclips.jobs.worker import run_worker
from hayclips.project import ProjectRepo


@pytest.fixture
def pid(pg, tmp_path):
    repo = ProjectRepo(tmp_path / "proj")
    repo.init("proj", {"kind": "youtube", "url": "https://youtu.be/abcDEF12345"})
    q.register_project(pg, project_id="prj_o", name="proj", dir=str(repo.root))
    return "prj_o"


def flaky(real, failures):
    """Wrap a queue function so its first `failures` calls raise like a dropped database."""
    state = {"n": 0}

    def wrapper(*a, **k):
        state["n"] += 1
        if failures is None or state["n"] <= failures:
            raise psycopg.OperationalError("simulated database outage")
        return real(*a, **k)
    wrapper.calls = state
    return wrapper


def slow_render(pg, pid, monkeypatch, seconds):
    monkeypatch.setenv(H.TEST_SLEEP_ENV, str(seconds))
    return q.enqueue(pg, project_id=pid, type="render", payload={"styles": ["A"]})


def test_heartbeat_fails_once_then_recovers(pg, pg_dsn, pid, monkeypatch):
    job = slow_render(pg, pid, monkeypatch, 2.5)
    monkeypatch.setattr(q, "heartbeat", flaky(q.heartbeat, 1))
    run_worker(["cpu"], dsn=pg_dsn, once=True, lease_seconds=3)
    done = q.get(pg, job["id"])
    assert done["state"] == "FAILED" and "no selected clips" in done["error"]   # ran to completion, recorded


def test_heartbeat_failing_repeatedly_stops_the_job_and_writes_nothing(pg, pg_dsn, pid, monkeypatch, capsys):
    job = slow_render(pg, pid, monkeypatch, 8)
    monkeypatch.setattr(q, "heartbeat", flaky(q.heartbeat, None))
    run_worker(["cpu"], dsn=pg_dsn, once=True, lease_seconds=3)
    done = q.get(pg, job["id"])
    assert done["state"] == "RUNNING" and done["result"] is None              # outcome not written
    assert "lease uncertain" in capsys.readouterr().out
    pg.execute("UPDATE jobs SET lease_expires_at = now() - interval '1 second'")
    assert q.reap(pg)[0]["state"] == "RETRY_WAIT"                             # safe job: reaper retries


def test_database_unavailable_during_claim(pg, pg_dsn, pid, monkeypatch):
    job = q.enqueue(pg, project_id=pid, type="render", payload={"styles": ["A"]})
    monkeypatch.setattr(q, "claim", flaky(q.claim, 1))
    run_worker(["cpu"], dsn=pg_dsn, once=True)                               # must not raise
    assert q.get(pg, job["id"])["state"] == "QUEUED"
    run_worker(["cpu"], dsn=pg_dsn, once=True)
    assert q.get(pg, job["id"])["state"] == "FAILED"                          # ran on the next attempt


def test_result_write_retried_then_recorded(pg, pg_dsn, pid, monkeypatch):
    job = q.enqueue(pg, project_id=pid, type="render", payload={"styles": ["A"]})
    monkeypatch.setattr(W, "RESULT_RETRY_DELAYS", (0.01, 0.01, 0.01))
    monkeypatch.setattr(q, "fail", flaky(q.fail, 2))
    run_worker(["cpu"], dsn=pg_dsn, once=True)
    assert q.get(pg, job["id"])["state"] == "FAILED"


def test_result_write_never_succeeds_leaves_job_to_reaper(pg, pg_dsn, pid, monkeypatch, capsys):
    job = q.enqueue(pg, project_id=pid, type="render", payload={"styles": ["A"]})
    monkeypatch.setattr(W, "RESULT_RETRY_DELAYS", (0.01, 0.01))
    monkeypatch.setattr(q, "fail", flaky(q.fail, None))
    run_worker(["cpu"], dsn=pg_dsn, once=True)                               # must not raise
    assert q.get(pg, job["id"])["state"] == "RUNNING"
    assert "result not recorded" in capsys.readouterr().out


def test_lost_lease_during_safe_job_stops_it_early(pg, pg_dsn, pid, monkeypatch):
    import time
    job = slow_render(pg, pid, monkeypatch, 20)
    monkeypatch.setattr(q, "heartbeat", lambda *a, **k: None)                # someone else owns it now
    t = time.monotonic()
    run_worker(["cpu"], dsn=pg_dsn, once=True, lease_seconds=3)
    assert time.monotonic() - t < 6                                          # stopped, did not run 20 s
    assert q.get(pg, job["id"])["result"] is None


def test_paid_job_without_provable_lease_stops_before_submit(pg, pg_dsn, tmp_path, monkeypatch):
    from hayclips.transcription.fake_harmar import FakeHarmar
    from test_transcription_service import KEY, make_project as make_paid_project
    from hayclips.transcription.store import list_attempts
    with FakeHarmar(balance=600, api_key=KEY) as fake:
        repo, (cid,) = make_paid_project(tmp_path, consent=True)
        q.register_project(pg, project_id="prj_paid1", name="paid", dir=str(repo.root))
        monkeypatch.setenv("HAYCLIPS_HARMAR_BASE_URL", fake.base_url)
        monkeypatch.setenv("HARMAR_API_KEY", KEY)
        job = q.enqueue(pg, project_id="prj_paid1", type="transcribe",
                        payload={"clip_ids": [cid], "confirmed_by": "op"})
        monkeypatch.setattr(q, "heartbeat", lambda *a, **k: None)            # ownership lost immediately
        monkeypatch.setenv(H.TEST_SLEEP_ENV, "1.5")                           # give the heartbeat time to notice
        run_worker(["paid"], dsn=pg_dsn, once=True, lease_seconds=3)
        assert fake.submit_count == 0                                         # never reached the charge point
        att = list_attempts(repo, cid)
        assert all(a.state not in ("SUBMITTING", "SUBMITTED", "UNKNOWN_SUBMISSION") for a in att)
        assert q.get(pg, job["id"])["result"] is None
