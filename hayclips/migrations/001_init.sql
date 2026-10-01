-- Phase 1b schema. Per-project artifacts (project.json, clips/<id>/...) stay in the project directory;
-- the database holds the project index, the durable job queue, review decisions and an audit log.

CREATE TABLE projects (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    dir         text NOT NULL UNIQUE,          -- absolute project directory (artifact store)
    source_url  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    archived    boolean NOT NULL DEFAULT false
);

CREATE TABLE jobs (
    id               bigserial PRIMARY KEY,
    project_id       text NOT NULL REFERENCES projects(id),
    clip_id          text,
    type             text NOT NULL,            -- import_captions | generate_candidates | fetch_windows | transcribe | render
    pool             text NOT NULL CHECK (pool IN ('io', 'cpu', 'paid')),
    state            text NOT NULL DEFAULT 'QUEUED'
                     CHECK (state IN ('QUEUED', 'RUNNING', 'RETRY_WAIT', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    payload          jsonb NOT NULL DEFAULT '{}',
    idempotency_key  text,
    attempts         integer NOT NULL DEFAULT 0,
    max_attempts     integer NOT NULL DEFAULT 3,   -- paid jobs: always 1 (never re-run automatically)
    run_after        timestamptz NOT NULL DEFAULT now(),
    lease_owner      text,
    lease_expires_at timestamptz,
    progress         real,
    progress_note    text,
    result           jsonb,
    error            text,
    cancel_requested boolean NOT NULL DEFAULT false,
    requested_by     text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    finished_at      timestamptz
);
-- A double click or a retried HTTP request cannot enqueue the same work twice while it is active.
CREATE UNIQUE INDEX jobs_active_idempotency ON jobs (idempotency_key)
    WHERE idempotency_key IS NOT NULL AND state IN ('QUEUED', 'RUNNING', 'RETRY_WAIT');
CREATE INDEX jobs_claim ON jobs (pool, run_after) WHERE state IN ('QUEUED', 'RETRY_WAIT');
CREATE INDEX jobs_project ON jobs (project_id, created_at DESC);
-- At most one paid job runs at a time (single-flight per account, also enforced by a file lock).
CREATE UNIQUE INDEX jobs_one_paid_running ON jobs (pool) WHERE pool = 'paid' AND state = 'RUNNING';

CREATE TABLE review_decisions (
    id              bigserial PRIMARY KEY,
    project_id      text NOT NULL REFERENCES projects(id),
    clip_id         text NOT NULL,
    style           text,
    would_post      text NOT NULL CHECK (would_post IN ('yes', 'no', 'maybe')),
    minutes_to_fix  integer CHECK (minutes_to_fix >= 0),
    notes           text,
    reviewer        text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE events (
    id          bigserial PRIMARY KEY,
    project_id  text REFERENCES projects(id),
    clip_id     text,
    kind        text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}',
    actor       text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
