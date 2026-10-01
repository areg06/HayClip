"""Durable job queue semantics (needs the local Postgres dev cluster)."""
import psycopg
import pytest

from hayclips import db
from hayclips.jobs import queue as q


@pytest.fixture
def project(pg):
    return q.register_project(pg, project_id="prj_test", name="t", dir="/tmp/hayclips-test-proj")


def test_migrations_are_idempotent(pg):
    assert db.migrate(pg) == []


def test_enqueue_is_idempotent_while_active(pg, project):
    a = q.enqueue(pg, project_id="prj_test", type="render", idempotency_key="render:c1:A")
    b = q.enqueue(pg, project_id="prj_test", type="render", idempotency_key="render:c1:A")
    assert a["id"] == b["id"]
    w = q.new_worker_id("cpu")
    job = q.claim(pg, "cpu", w)
    assert q.succeed(pg, job["id"], w)
    c = q.enqueue(pg, project_id="prj_test", type="render", idempotency_key="render:c1:A")
    assert c["id"] != a["id"]          # finished jobs do not block new work


def test_claim_skips_locked_and_respects_pool(pg_dsn, project):
    with db.connect(pg_dsn) as c1, db.connect(pg_dsn) as c2:
        q.enqueue(c1, project_id="prj_test", type="render")
        q.enqueue(c1, project_id="prj_test", type="render")
        q.enqueue(c1, project_id="prj_test", type="fetch_windows")
        j1, j2 = q.claim(c1, "cpu", "w1"), q.claim(c2, "cpu", "w2")
        assert j1["id"] != j2["id"] and q.claim(c1, "cpu", "w3") is None
        assert q.claim(c2, "io", "w4")["type"] == "fetch_windows"


def test_only_owner_can_update_and_retry_backoff(pg, project):
    q.enqueue(pg, project_id="prj_test", type="render")
    job = q.claim(pg, "cpu", "w1")
    assert q.succeed(pg, job["id"], "intruder") is False
    assert q.fail(pg, job["id"], "w1", "boom", retryable=True) == "RETRY_WAIT"
    assert q.claim(pg, "cpu", "w1") is None              # backoff: not runnable yet
    pg.execute("UPDATE jobs SET run_after = now()")
    job = q.claim(pg, "cpu", "w1")
    assert job["attempts"] == 2


def test_paid_jobs_have_one_attempt_and_single_flight(pg, project):
    a = q.enqueue(pg, project_id="prj_test", type="transcribe", payload={"clip_ids": ["c1"]})
    q.enqueue(pg, project_id="prj_test", type="transcribe", payload={"clip_ids": ["c2"]})
    assert a["max_attempts"] == 1 and a["pool"] == "paid"
    j = q.claim(pg, "paid", "p1")
    assert q.claim(pg, "paid", "p2") is None             # one paid job at a time
    assert q.fail(pg, j["id"], "p1", "provider down", retryable=True) == "FAILED"   # never auto-retried


def test_reaper_never_reruns_paid_jobs(pg, project):
    q.enqueue(pg, project_id="prj_test", type="transcribe")
    q.enqueue(pg, project_id="prj_test", type="render")
    p, r = q.claim(pg, "paid", "p1", lease_seconds=1), q.claim(pg, "cpu", "c1", lease_seconds=1)
    pg.execute("UPDATE jobs SET lease_expires_at = now() - interval '1 second'")
    states = {x["type"]: x["state"] for x in q.reap(pg)}
    assert states == {"transcribe": "FAILED", "render": "RETRY_WAIT"}
    assert "never re-run automatically" in q.get(pg, p["id"])["error"]
    assert q.heartbeat(pg, r["id"], "c1") is None         # the dead worker lost its lease


def test_cancel_queued_and_running(pg, project):
    a = q.enqueue(pg, project_id="prj_test", type="render")
    assert q.request_cancel(pg, a["id"]) == "CANCELLED"
    q.enqueue(pg, project_id="prj_test", type="render")
    j = q.claim(pg, "cpu", "w")
    assert q.request_cancel(pg, j["id"]) == "RUNNING"
    assert q.heartbeat(pg, j["id"], "w")["cancel_requested"] is True
    assert q.fail(pg, j["id"], "w", "stopped", retryable=True) == "CANCELLED"


def test_unknown_type_rejected(pg, project):
    with pytest.raises(ValueError):
        q.enqueue(pg, project_id="prj_test", type="rm -rf")
