"""Worker loop: claim jobs from the Postgres queue, run handlers, keep the lease alive, reap dead workers.

- A heartbeat thread extends the lease every lease/3 seconds. If the lease is lost (another process
  reaped the job), the handler is told to stop and its outcome is NOT written.
- Cancellation: the heartbeat reports cancel_requested; handlers check ctx.cancelled() between steps.
  A paid job is never interrupted inside the transcription service (it only checks before starting).
- SIGTERM/SIGINT: stop claiming, let the current job finish, then exit.
- Every 30 s the worker also runs the reaper (expired leases; paid jobs are never re-run).
"""
from __future__ import annotations

import signal
import threading
import time
from pathlib import Path

import psycopg

from .. import db
from ..config import load_settings
from ..project import ProjectRepo
from . import handlers as H
from . import queue as q

REAP_EVERY = 30.0


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


class _Heartbeat(threading.Thread):
    def __init__(self, dsn: str, job_id: int, worker_id: str, lease_seconds: int):
        super().__init__(daemon=True)
        self.dsn, self.job_id, self.worker_id, self.lease = dsn, job_id, worker_id, lease_seconds
        self.stop_event = threading.Event()
        self.cancel = False
        self.lost = False
        self._progress: tuple[float | None, str | None] = (None, None)
        self._lock = threading.Lock()

    def set_progress(self, fraction: float, note: str = "") -> None:
        with self._lock:
            self._progress = (float(fraction), note[:200] if note else None)

    def run(self) -> None:
        interval = max(self.lease / 3.0, 0.2)
        with db.connect(self.dsn) as conn:
            while not self.stop_event.wait(interval):
                with self._lock:
                    frac, note = self._progress
                try:
                    row = q.heartbeat(conn, self.job_id, self.worker_id, self.lease, frac, note)
                except psycopg.Error:
                    continue                  # transient DB hiccup; the next beat retries
                if row is None:
                    self.lost = True
                    return
                self.cancel = bool(row["cancel_requested"])


class Worker:
    def __init__(self, pools: list[str], *, dsn: str | None = None, poll_interval: float = 1.0,
                 lease_seconds: int = 60):
        for p in pools:
            if p not in q.POOLS:
                raise ValueError(f"unknown pool {p!r}")
        self.pools, self.dsn = pools, dsn or db.dsn()
        self.poll_interval, self.lease = poll_interval, lease_seconds
        self.ids = {p: q.new_worker_id(p) for p in pools}
        self.stopping = False
        self._last_reap = 0.0

    def request_stop(self, *_):
        if not self.stopping:
            log("stop requested: finishing the current job, claiming no more")
        self.stopping = True

    def run_one(self, conn) -> bool:
        """Claim and run at most one job from any pool. Returns True if a job ran."""
        for pool in self.pools:
            if self.stopping:
                return False
            job = q.claim(conn, pool, self.ids[pool], self.lease)
            if job:
                self._execute(conn, job, self.ids[pool])
                return True
        return False

    def _execute(self, conn, job: dict, worker_id: str) -> None:
        jid, jtype = job["id"], job["type"]
        log(f"job {jid} {jtype} RUNNING (attempt {job['attempts']}/{job['max_attempts']})")
        hb = _Heartbeat(self.dsn, jid, worker_id, self.lease)
        hb.start()
        try:
            project = conn.execute("SELECT * FROM projects WHERE id = %s", (job["project_id"],)).fetchone()
            ctx = H.Context(repo=ProjectRepo(Path(project["dir"])), settings=load_settings(),
                            progress=hb.set_progress, cancelled=lambda: hb.cancel or hb.lost)
            result = H.HANDLERS[jtype](job, ctx)
        except BaseException as exc:  # noqa: BLE001 - every failure must be recorded
            hb.stop_event.set()
            hb.join()
            if hb.lost:
                log(f"job {jid} {jtype}: lease lost; outcome not recorded")
                return
            if isinstance(exc, KeyboardInterrupt):
                self.stopping = True
            retry = H.is_retryable(exc) and not isinstance(exc, H.JobCancelled)
            state = q.fail(conn, jid, worker_id, H.error_text(exc), retryable=retry,
                           result=getattr(exc, "job_result", None))
            log(f"job {jid} {jtype} {state}: {H.error_text(exc).splitlines()[0][:200]}")
            return
        hb.stop_event.set()
        hb.join()
        if hb.lost:
            log(f"job {jid} {jtype}: lease lost; outcome not recorded")
            return
        q.succeed(conn, jid, worker_id, result)
        log(f"job {jid} {jtype} SUCCEEDED")

    def maybe_reap(self, conn) -> None:
        if time.monotonic() - self._last_reap >= REAP_EVERY:
            self._last_reap = time.monotonic()
            for r in q.reap(conn):
                log(f"reaper: job {r['id']} {r['type']} -> {r['state']}")

    def run(self, once: bool = False) -> None:
        with db.connect(self.dsn) as conn:
            log(f"worker started, pools {','.join(self.pools)}")
            while not self.stopping:
                self.maybe_reap(conn)
                ran = self.run_one(conn)
                if once:
                    return
                if not ran:
                    time.sleep(self.poll_interval)
            log("worker stopped")


def run_worker(pools: list[str], *, dsn: str | None = None, once: bool = False, poll_interval: float = 1.0,
               lease_seconds: int = 60) -> Worker:
    w = Worker(pools, dsn=dsn, poll_interval=poll_interval, lease_seconds=lease_seconds)
    w.run(once=once)
    return w


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m hayclips.worker",
                                 description="Run HayClips background jobs from the Postgres queue.")
    ap.add_argument("--pools", default="io,cpu,paid", help="comma list of io, cpu, paid")
    ap.add_argument("--once", action="store_true", help="run at most one job, then exit")
    ap.add_argument("--lease", type=int, default=60)
    a = ap.parse_args(argv)
    pools = [p.strip() for p in a.pools.split(",") if p.strip()]
    try:
        with db.connect() as conn:
            db.migrate(conn)
    except psycopg.OperationalError as exc:
        print(f"error: cannot reach the database ({str(exc).splitlines()[0]})\n"
              "  hint: start it with scripts/dev-postgres.sh start", flush=True)
        return 2
    w = Worker(pools, lease_seconds=a.lease)
    signal.signal(signal.SIGTERM, w.request_stop)
    signal.signal(signal.SIGINT, w.request_stop)
    w.run(once=a.once)
    return 0
