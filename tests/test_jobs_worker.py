"""Worker loop semantics: retries, permanent failures, cancellation, duplicates, crashes (local Postgres)."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from fixtures import synth
from hayclips import db, proc
from hayclips.errors import PipelineError
from hayclips.jobs import handlers as H
from hayclips.jobs import queue as q
from hayclips.jobs.worker import Worker, run_worker
from hayclips.models import SUBMITTED, UNKNOWN_SUBMISSION
from hayclips.project import ProjectRepo
from hayclips.transcription.fake_harmar import FakeHarmar
from hayclips.transcription.store import list_attempts
from test_transcription_service import KEY, make_project as make_paid_project

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


@pytest.fixture
def pid(pg, tmp_path):
    repo = ProjectRepo(tmp_path / "proj")
    repo.init("proj", {"kind": "youtube", "url": "https://youtu.be/abcDEF12345"})
    q.register_project(pg, project_id="prj_w", name="proj", dir=str(repo.root))
    return "prj_w"


def test_transient_error_retries_then_succeeds(pg, pg_dsn, pid, monkeypatch):
    calls = []

    def flaky(job, ctx):
        calls.append(job["attempts"])
        if len(calls) == 1:
            raise proc.ToolTimeout("ffmpeg", "timed out after 1s")
        return {"ok": True}

    monkeypatch.setitem(H.HANDLERS, "render", flaky)
    job = q.enqueue(pg, project_id=pid, type="render")
    run_worker(["cpu"], dsn=pg_dsn, once=True)
    assert q.get(pg, job["id"])["state"] == "RETRY_WAIT"
    pg.execute("UPDATE jobs SET run_after = now()")
    run_worker(["cpu"], dsn=pg_dsn, once=True)
    done = q.get(pg, job["id"])
    assert done["state"] == "SUCCEEDED" and done["result"] == {"ok": True} and calls == [1, 2]


def test_permanent_error_fails_with_message_and_partial_result(pg, pg_dsn, pid, monkeypatch):
    def broken(job, ctx):
        err = PipelineError("caption timeline does not match", hint="investigate")
        err.job_result = {"clips": [{"clip_id": "clp_0000000000", "status": "failed"}]}
        raise err

    monkeypatch.setitem(H.HANDLERS, "render", broken)
    job = q.enqueue(pg, project_id=pid, type="render")
    run_worker(["cpu"], dsn=pg_dsn, once=True)
    j = q.get(pg, job["id"])
    assert j["state"] == "FAILED" and "timeline" in j["error"] and "hint: investigate" in j["error"]
    assert j["attempts"] == 1 and j["result"]["clips"][0]["status"] == "failed"


def test_paid_failure_is_never_retried_even_if_transient(pg, pg_dsn, pid, monkeypatch):
    monkeypatch.setitem(H.HANDLERS, "transcribe", lambda job, ctx: (_ for _ in ()).throw(ConnectionError("reset")))
    job = q.enqueue(pg, project_id=pid, type="transcribe", payload={"clip_ids": ["clp_0000000000"], "confirmed_by": "t"})
    run_worker(["paid"], dsn=pg_dsn, once=True)
    assert q.get(pg, job["id"])["state"] == "FAILED"


def test_cancel_running_job_between_steps(pg, pg_dsn, pid, monkeypatch):
    started = threading.Event()

    def slow(job, ctx):
        started.set()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            H._check_cancel(ctx)
            time.sleep(0.05)
        return {"finished": True}

    monkeypatch.setitem(H.HANDLERS, "render", slow)
    job = q.enqueue(pg, project_id=pid, type="render")
    t = threading.Thread(target=run_worker, args=(["cpu"],), kwargs=dict(dsn=pg_dsn, once=True, lease_seconds=1))
    t.start()
    assert started.wait(10)
    with db.connect(pg_dsn) as other:
        assert q.request_cancel(other, job["id"]) == "RUNNING"
    t.join(15)
    assert not t.is_alive() and q.get(pg, job["id"])["state"] == "CANCELLED"


def test_duplicate_enqueue_while_running_executes_once(pg, pg_dsn, pid, monkeypatch):
    runs, started, release = [], threading.Event(), threading.Event()

    def once(job, ctx):
        runs.append(job["id"])
        started.set()
        release.wait(10)
        return {}

    monkeypatch.setitem(H.HANDLERS, "render", once)
    a = q.enqueue(pg, project_id=pid, type="render", idempotency_key="render:x")
    t = threading.Thread(target=run_worker, args=(["cpu"],), kwargs=dict(dsn=pg_dsn, once=True))
    t.start()
    assert started.wait(10)
    b = q.enqueue(pg, project_id=pid, type="render", idempotency_key="render:x")   # double click
    release.set()
    t.join(10)
    run_worker(["cpu"], dsn=pg_dsn, once=True)                                      # nothing left to run
    assert a["id"] == b["id"] and runs == [a["id"]]


def test_lost_lease_result_is_not_recorded(pg, pg_dsn, pid, monkeypatch):
    def slow(job, ctx):
        with db.connect(pg_dsn) as other:            # simulate the reaper taking the job away
            other.execute("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s", (job["id"],))
            q.reap(other)
        time.sleep(1.0)                              # let the heartbeat notice
        return {"late": True}

    monkeypatch.setitem(H.HANDLERS, "render", slow)
    job = q.enqueue(pg, project_id=pid, type="render")
    run_worker(["cpu"], dsn=pg_dsn, once=True, lease_seconds=1)
    j = q.get(pg, job["id"])
    assert j["state"] == "RETRY_WAIT" and j["result"] is None


def _spawn_worker(pg_dsn, pools, extra_env):
    env = dict(os.environ, HAYCLIPS_DATABASE_URL=pg_dsn, PYTHONPATH=str(ROOT), **extra_env)
    return subprocess.Popen([PY, "-m", "hayclips.worker", "--pools", pools, "--lease", "2"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _wait_state(conn, job_id, states, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = q.get(conn, job_id)
        if j["state"] in states:
            return j
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} never reached {states}: {q.get(conn, job_id)}")


def test_killed_worker_job_is_retried_by_reaper(pg, pg_dsn, pid):
    job = q.enqueue(pg, project_id=pid, type="render", payload={"styles": ["A"]})
    p = _spawn_worker(pg_dsn, "cpu", {H.TEST_SLEEP_ENV: "30"})
    try:
        _wait_state(pg, job["id"], {"RUNNING"})
        p.send_signal(signal.SIGKILL)
        p.wait(10)
    finally:
        p.kill()
    pg.execute("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s", (job["id"],))
    assert {r["id"]: r["state"] for r in q.reap(pg)} == {job["id"]: "RETRY_WAIT"}


@pytest.mark.parametrize("crash_at,expected", [("after_submit_response_before_persist", UNKNOWN_SUBMISSION),
                                               ("after_submit_response", SUBMITTED)])
def test_worker_crash_during_paid_job_never_charges_twice(pg, pg_dsn, tmp_path, monkeypatch, crash_at, expected):
    for k, v in {"HAYCLIPS_POLL_INTERVAL_MIN": "0.01", "HAYCLIPS_POLL_INTERVAL_MAX": "0.02",
                 "HAYCLIPS_POLL_TIMEOUT": "10"}.items():
        monkeypatch.setenv(k, v)
    with FakeHarmar(balance=600, api_key=KEY) as fake:
        monkeypatch.setenv("HAYCLIPS_HARMAR_BASE_URL", fake.base_url)
        monkeypatch.setenv("HARMAR_API_KEY", KEY)
        repo, (cid,) = make_paid_project(tmp_path)
        q.register_project(pg, project_id="prj_paid", name="paid", dir=str(repo.root))
        job = q.enqueue(pg, project_id="prj_paid", type="transcribe", payload={"clip_ids": [cid], "confirmed_by": "tester"})
        p = _spawn_worker(pg_dsn, "paid", {"HAYCLIPS_TEST_CRASH_AT": crash_at})
        try:
            assert p.wait(30) == 70                                  # the test crash hook fired
        finally:
            p.kill()
        assert fake.submit_count == 1
        assert q.get(pg, job["id"])["state"] == "RUNNING"            # dead worker still holds the lease
        pg.execute("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s", (job["id"],))
        assert {r["id"]: r["state"] for r in q.reap(pg)} == {job["id"]: "FAILED"}
        assert "never re-run automatically" in q.get(pg, job["id"])["error"]

        # The operator confirms again: the service must not submit a second time.
        again = q.enqueue(pg, project_id="prj_paid", type="transcribe", payload={"clip_ids": [cid], "confirmed_by": "tester"})
        run_worker(["paid"], dsn=pg_dsn, once=True)
        j = q.get(pg, again["id"])
        assert fake.submit_count == 1
        states = [a.state for a in list_attempts(repo, cid)]
        if expected == UNKNOWN_SUBMISSION:
            assert j["state"] == "FAILED" and "outcome unknown" in j["error"] and states == [UNKNOWN_SUBMISSION]
        else:
            assert j["state"] == "SUCCEEDED" and j["result"]["clips"][0]["action"] == "completed"   # polled only


def test_worker_refuses_to_start_without_database(tmp_path):
    env = dict(os.environ, HAYCLIPS_DATABASE_URL=f"host={tmp_path} port=1 user=x dbname=x", PYTHONPATH=str(ROOT))
    r = subprocess.run([PY, "-m", "hayclips.worker", "--once"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "dev-postgres.sh start" in r.stdout


def test_sigterm_finishes_current_job_then_exits(pg, pg_dsn, pid):
    job = q.enqueue(pg, project_id=pid, type="render", payload={"styles": ["A"]})   # empty project: sleeps, then succeeds
    p = _spawn_worker(pg_dsn, "cpu", {H.TEST_SLEEP_ENV: "2"})
    try:
        _wait_state(pg, job["id"], {"RUNNING"})
        time.sleep(0.3)
        p.send_signal(signal.SIGTERM)
        out, _ = p.communicate(timeout=30)
    finally:
        p.kill()
    assert p.returncode == 0 and "worker stopped" in out
    assert q.get(pg, job["id"])["state"] == "SUCCEEDED"       # the running job was finished, not abandoned
