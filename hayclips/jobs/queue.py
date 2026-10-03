"""Durable job queue on Postgres (SELECT ... FOR UPDATE SKIP LOCKED with leases).

Rules:
- Job state lives only in the jobs table; web requests just enqueue and read.
- An active job (QUEUED/RUNNING/RETRY_WAIT) with the same idempotency key is never enqueued twice.
- A worker owns a job only while its lease is valid; every update checks the owner.
- Expired leases are reaped: normal jobs retry with backoff up to max_attempts; PAID jobs are never
  re-run automatically (max_attempts = 1, reaped to FAILED), because only a human may confirm a new
  paid run. The transcription service itself still guarantees no duplicate charge on any re-run.
- At most one paid job is RUNNING at a time (partial unique index).
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

import psycopg

from ..errors import PipelineError

ACTIVE = ("QUEUED", "RUNNING", "RETRY_WAIT")
POOLS = ("io", "cpu", "paid")
TYPES = {"import_captions": "io", "discover_transcript": "cpu", "generate_candidates": "cpu",
         "preview_candidate": "io", "fetch_windows": "io", "transcribe": "paid", "render": "cpu"}
BACKOFF_SECONDS = (10, 60, 300)


def enqueue(conn: psycopg.Connection, *, project_id: str, type: str, payload: dict | None = None,
            clip_id: str | None = None, idempotency_key: str | None = None, requested_by: str | None = None,
            max_attempts: int | None = None) -> dict:
    """Insert a job, or return the already-active job with the same idempotency key."""
    if type not in TYPES:
        raise ValueError(f"unknown job type {type!r}")
    pool = TYPES[type]
    row = project(conn, project_id)
    if row is None or row.get("removed_at") is not None:
        raise PipelineError(f"project {project_id} is not active", hint="it was removed or never registered")
    if storage_state(row) == MISSING_STORAGE:
        raise PipelineError("local project files are missing (MISSING_STORAGE); nothing can be processed",
                            hint="restore the project folder, or remove the stale entry from the dashboard")
    if pool == "paid":
        max_attempts = 1
    with conn.transaction():
        if idempotency_key:
            row = conn.execute("SELECT * FROM jobs WHERE idempotency_key = %s AND state = ANY(%s)",
                               (idempotency_key, list(ACTIVE))).fetchone()
            if row:
                return row
        try:
            with conn.transaction():
                return conn.execute(
                    """INSERT INTO jobs (project_id, clip_id, type, pool, payload, idempotency_key, requested_by, max_attempts)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                    (project_id, clip_id, type, pool, json.dumps(payload or {}), idempotency_key, requested_by,
                     max_attempts or 3)).fetchone()
        except psycopg.errors.UniqueViolation:   # raced with an identical enqueue
            return conn.execute("SELECT * FROM jobs WHERE idempotency_key = %s AND state = ANY(%s)",
                                (idempotency_key, list(ACTIVE))).fetchone()


def new_worker_id(pool: str) -> str:
    return f"{pool}-{secrets.token_hex(4)}"


def claim(conn: psycopg.Connection, pool: str, worker_id: str, lease_seconds: int = 60) -> dict | None:
    if pool not in POOLS:
        raise ValueError(pool)
    try:
        with conn.transaction():
            return conn.execute(
                """UPDATE jobs SET state = 'RUNNING', lease_owner = %s,
                          lease_expires_at = now() + make_interval(secs => %s),
                          attempts = attempts + 1, started_at = coalesce(started_at, now()), error = NULL
                   WHERE id = (SELECT id FROM jobs
                               WHERE pool = %s AND state IN ('QUEUED', 'RETRY_WAIT') AND run_after <= now()
                                 AND NOT cancel_requested
                               ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
                   RETURNING *""", (worker_id, lease_seconds, pool)).fetchone()
    except psycopg.errors.UniqueViolation:     # another paid job is already running
        return None


def heartbeat(conn: psycopg.Connection, job_id: int, worker_id: str, lease_seconds: int = 60,
              progress: float | None = None, note: str | None = None) -> dict | None:
    """Extend the lease; returns {'cancel_requested': bool} or None if this worker lost the job."""
    with conn.transaction():
        return conn.execute(
            """UPDATE jobs SET lease_expires_at = now() + make_interval(secs => %s),
                      progress = coalesce(%s, progress), progress_note = coalesce(%s, progress_note)
               WHERE id = %s AND lease_owner = %s AND state = 'RUNNING'
               RETURNING cancel_requested""", (lease_seconds, progress, note, job_id, worker_id)).fetchone()


def succeed(conn: psycopg.Connection, job_id: int, worker_id: str, result: Any = None) -> bool:
    with conn.transaction():
        row = conn.execute(
            """UPDATE jobs SET state = 'SUCCEEDED', result = %s, progress = 1, finished_at = now(),
                      lease_owner = NULL, lease_expires_at = NULL
               WHERE id = %s AND lease_owner = %s AND state = 'RUNNING' RETURNING id""",
            (json.dumps(result), job_id, worker_id)).fetchone()
    return row is not None


def fail(conn: psycopg.Connection, job_id: int, worker_id: str, error: str, *, retryable: bool,
         result: Any = None) -> str | None:
    """Record a failure; returns the new state (RETRY_WAIT or FAILED) or None if not the owner."""
    with conn.transaction():
        job = conn.execute("SELECT * FROM jobs WHERE id = %s AND lease_owner = %s AND state = 'RUNNING' FOR UPDATE",
                           (job_id, worker_id)).fetchone()
        if not job:
            return None
        retry = retryable and job["pool"] != "paid" and job["attempts"] < job["max_attempts"] \
            and not job["cancel_requested"]
        delay = BACKOFF_SECONDS[min(job["attempts"] - 1, len(BACKOFF_SECONDS) - 1)]
        state = "RETRY_WAIT" if retry else ("CANCELLED" if job["cancel_requested"] else "FAILED")
        conn.execute(
            """UPDATE jobs SET state = %s, error = %s, result = coalesce(%s, result),
                      run_after = CASE WHEN %s THEN now() + make_interval(secs => %s) ELSE run_after END,
                      finished_at = CASE WHEN %s THEN NULL ELSE now() END,
                      lease_owner = NULL, lease_expires_at = NULL
               WHERE id = %s""",
            (state, error[:4000], json.dumps(result) if result is not None else None, retry, delay, retry, job_id))
    return state


def request_cancel(conn: psycopg.Connection, job_id: int) -> str | None:
    """Queued jobs are cancelled at once; running ones are flagged for the worker to stop between steps."""
    with conn.transaction():
        row = conn.execute(
            """UPDATE jobs SET state = CASE WHEN state IN ('QUEUED', 'RETRY_WAIT') THEN 'CANCELLED' ELSE state END,
                      cancel_requested = true,
                      finished_at = CASE WHEN state IN ('QUEUED', 'RETRY_WAIT') THEN now() ELSE finished_at END
               WHERE id = %s AND state = ANY(%s) RETURNING state""", (job_id, list(ACTIVE))).fetchone()
    return row["state"] if row else None


def reap(conn: psycopg.Connection) -> list[dict]:
    """Handle RUNNING jobs whose lease expired (the worker died or hung)."""
    out = []
    with conn.transaction():
        rows = conn.execute("""SELECT * FROM jobs WHERE state = 'RUNNING' AND lease_expires_at < now()
                               FOR UPDATE SKIP LOCKED""").fetchall()
        for job in rows:
            if job["pool"] == "paid":
                state, err = "FAILED", ("worker stopped during a paid transcription job; it is never re-run "
                                        "automatically. Check the clip status (reconcile if needed), then confirm again")
            elif job["cancel_requested"]:
                state, err = "CANCELLED", "cancelled (worker stopped)"
            elif job["attempts"] < job["max_attempts"]:
                state, err = "RETRY_WAIT", "worker lease expired; retrying"
            else:
                state, err = "FAILED", "worker lease expired too many times"
            conn.execute("""UPDATE jobs SET state = %s, error = %s, lease_owner = NULL, lease_expires_at = NULL,
                                   finished_at = CASE WHEN %s = 'RETRY_WAIT' THEN NULL ELSE now() END,
                                   run_after = now() + interval '10 seconds'
                            WHERE id = %s""", (state, err, state, job["id"]))
            out.append({"id": job["id"], "type": job["type"], "state": state})
    return out


def get(conn: psycopg.Connection, job_id: int) -> dict | None:
    return conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()


def for_project(conn: psycopg.Connection, project_id: str, limit: int = 50) -> list[dict]:
    return conn.execute("SELECT * FROM jobs WHERE project_id = %s ORDER BY id DESC LIMIT %s",
                        (project_id, limit)).fetchall()


# ----- project index and audit log -----------------------------------------------------------------

MISSING_STORAGE = "MISSING_STORAGE"


def project(conn: psycopg.Connection, project_id: str) -> dict | None:
    return conn.execute("SELECT * FROM projects WHERE id = %s", (project_id,)).fetchone()


def storage_state(row: dict) -> str:
    """OK, or MISSING_STORAGE when the project folder or its project.json is gone (never auto-repaired)."""
    return "OK" if (Path(row["dir"]) / "project.json").is_file() else MISSING_STORAGE


def active_projects(conn: psycopg.Connection) -> list[dict]:
    rows = conn.execute("""SELECT * FROM projects WHERE NOT archived AND removed_at IS NULL
                           ORDER BY created_at DESC""").fetchall()
    for r in rows:
        r["storage_state"] = storage_state(r)
    return rows


def remove_stale_project(conn: psycopg.Connection, project_id: str, *, confirm_name: str, actor: str) -> dict:
    """Soft-remove a project whose files are missing. Requires the exact project name; audited.
    Queued jobs are cancelled; rows, jobs and events are kept."""
    row = project(conn, project_id)
    if row is None or row["removed_at"] is not None:
        raise PipelineError("no such active project")
    if storage_state(row) != MISSING_STORAGE:
        raise PipelineError("only projects whose files are missing can be removed here",
                            hint="this project's folder still exists; nothing was changed")
    if (confirm_name or "").strip() != row["name"]:
        raise PipelineError("type the project name exactly to confirm removal")
    if not (actor or "").strip():
        raise PipelineError("say who is removing the entry")
    with conn.transaction():
        conn.execute("UPDATE projects SET removed_at = now(), removed_by = %s WHERE id = %s", (actor.strip(), project_id))
        cancelled = conn.execute("""UPDATE jobs SET state = 'CANCELLED', cancel_requested = true, finished_at = now(),
                                           error = 'project entry removed (files were missing)'
                                    WHERE project_id = %s AND state IN ('QUEUED', 'RETRY_WAIT') RETURNING id""",
                                 (project_id,)).fetchall()
        conn.execute("INSERT INTO events (project_id, kind, detail, actor) VALUES (%s, %s, %s, %s)",
                     (project_id, "stale_project_removed",
                      json.dumps({"dir_name": Path(row["dir"]).name, "name": row["name"],
                                  "cancelled_jobs": [c["id"] for c in cancelled]}), actor.strip()))
    return project(conn, project_id)


def register_project(conn: psycopg.Connection, *, project_id: str, name: str, dir: str,
                     source_url: str | None = None) -> dict:
    with conn.transaction():
        row = conn.execute("SELECT * FROM projects WHERE dir = %s", (dir,)).fetchone()
        if row:
            return row
        return conn.execute("INSERT INTO projects (id, name, dir, source_url) VALUES (%s, %s, %s, %s) RETURNING *",
                            (project_id, name, dir, source_url)).fetchone()


def log_event(conn: psycopg.Connection, project_id: str | None, kind: str, detail: dict | None = None,
              clip_id: str | None = None, actor: str | None = None) -> None:
    with conn.transaction():
        conn.execute("INSERT INTO events (project_id, clip_id, kind, detail, actor) VALUES (%s, %s, %s, %s, %s)",
                     (project_id, clip_id, kind, json.dumps(detail or {}), actor))
