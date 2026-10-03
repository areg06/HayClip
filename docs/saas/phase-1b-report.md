# Phase 1b report: local web app for one end-to-end workflow

Date: 2026-10-03.

Scope: the founder's Phase 1 target. This is one local operator on localhost. There is no billing, no teams, no publishing, no outreach automation and no virality claim.

The Harmar restriction stayed in force throughout: no real Harmar request was made, the paid ledger is still empty, and the balance is still reserved at 133 s.

## What exists now

```
browser ──HTTP (127.0.0.1 only)──> hayclips.web (FastAPI + Jinja)
                                       │  enqueue only (validated payloads)
                                       v
                              Postgres (socket-only dev cluster)
                              projects · jobs · review_decisions · events
                                       ^
                                       │  claim / heartbeat / result
                              hayclips.worker (pools io, cpu, paid)
                                       │  calls the Phase 1a library
                                       v
                project folder: project.json, candidates.json, clips/<id>/...
```

| Part | Files |
|---|---|
| Dev database | `scripts/dev-postgres.sh`: a private cluster under `~/.hayclips`, unix socket only, no TCP listener |
| Schema | `hayclips/migrations/001_init.sql`, applied by `hayclips/db.py` |
| Job queue | `hayclips/jobs/queue.py`, `hayclips/jobs/contracts.py` |
| Worker | `hayclips/jobs/handlers.py`, `hayclips/jobs/worker.py`, `python -m hayclips.worker` |
| Web app | `hayclips/web/` (`app.py`, `security.py`, templates, static), `python -m hayclips.web` |
| One-command start | `scripts/dev-up.sh` |
| Tests | `tests/test_jobs_queue.py`, `test_jobs_handlers.py`, `test_jobs_worker.py`, `test_web_app.py` |

**Where state lives:**
- Per-project artifacts stay in the project folder, exactly as in Phase 1a. The CLI and the app share them.
- Postgres holds the project index, the jobs, review decisions and an audit log.
- Paid attempt records stay in `clips/<id>/transcription/` as the single authoritative record of what was paid for. Moving them into Postgres is deferred until they can move with the same guarantees.

## Workflow in the browser

1. Create a project: name plus a YouTube link. The link is validated and rebuilt from the video id; anything else is refused and nothing is created.
2. Fetch the free captions.
3. Generate candidates, with optional skip-start and skip-end.
4. Review candidates. Each shows its time range, its score and the explained score parts, labelled "ranking heuristic, not a virality prediction".
5. Select clips. Each gets a stable id; it can be reordered or unselected.
6. Download only the selected windows (+5 s padding) and their audio.
7. Record consent. The transcription screen shows, for each clip, whether it will reuse, resume, need reconciliation or make a new paid submission, plus the budgets, consent status, paid mode and key presence (yes/no only).
   - Confirming needs the cost checkbox and your typed name.
   - The server re-checks everything.
   - The confirm form is not shown, and a direct POST is refused, unless the server runs with `HAYCLIPS_ALLOW_PAID_HARMAR=1`.
8. Render styles A/B/C, choosing caption height and whether the hook shows.
9. Review each clip:
   - players for every rendered style;
   - the cut, alignment, framing and audio notes, and CHECK lines;
   - the transcript, labelled unreviewed;
   - edit the title, hook (≤45 characters) or trim, then re-render;
   - a "would post / style / minutes to fix" form.
10. Download the MP4s and SRT.
11. Jobs panel: state, progress and errors, polled every 2 s, with a cancel button.

## Guarantees and controls

**Jobs:**
- A click or request never runs pipeline work itself; it only queues a job.
- An identical active job is never queued twice; a partial unique index on the idempotency key enforces this.
- Work survives a web or worker restart.
- If a worker dies, its job is reaped and retried with backoff. Paid jobs are the exception:
  - they get one attempt, are never re-run automatically, and only one runs at a time;
  - the Phase 1a transcription service still guarantees that a re-run never charges twice.

**Security:**
- The app binds 127.0.0.1 only; any other bind address is refused without an explicit flag.
- The Host allowlist blocks DNS rebinding.
- Every state change is a POST that needs same-origin and a per-session HMAC CSRF token.
- A strict CSP allows only same-origin content, with no inline script or style. Framing is denied.
- The session cookie is HttpOnly and SameSite=Strict.

**Data exposure:**
- Media is served only from a fixed allowlist of files inside `clips/<valid id>/`, with containment checks; symlinks and traversal attempts return 404. Range requests work.
- No secret, key or filesystem path outside the project appears in any page (tested with a dummy key).

**Payloads:**
- The browser never supplies tool arguments.
- Job payloads are whitelisted per type and validated in both the app and the worker.

## Test results

```
.venv/bin/python -m pytest -q   ->   239 passed (2026-10-03, offline; Postgres tests use throwaway databases)
```

New test modules:
- **Queue (8 tests):** idempotency, SKIP LOCKED, owner checks, backoff, paid single-flight, reaper, cancel.
- **Handlers (8):** import → candidates → select → fetch with the fake yt-dlp; render on synthetic media; transcribe against the fake Harmar; missing consent; real host without the opt-in.
- **Worker (11):** retries, cancel, duplicate enqueue, lost lease, SIGKILL, paid crash at two points (one submit total), SIGTERM, no database.
- **Web app (21):** the workflow, invalid links, stable ids, hook and trim validation, paid-disabled gating, consent never inferred, idempotent submits, DNS rebinding, cross-origin and missing CSRF, headers, media Range and traversal, review decisions, register allowlist, no secrets.

## Live end-to-end check (2026-10-03, real servers, real YouTube, no Harmar)

The web app and worker ran with `scripts`-equivalent commands and were driven over HTTP like a browser. The source was the consented pilot-03 video.

| Step | Result |
|---|---|
| Create project | 303 to the project page |
| Captions job | SUCCEEDED in 5–7 s |
| Candidates job | SUCCEEDED in 1 s; 6 candidates on the page |
| Select first candidate, download its window | SUCCEEDED in 37–38 s; `wide.mp4`, `audio.m4a`, `window.json` and `youtube.srt` written |
| Transcription page | shows "Paid transcription is disabled in this environment" with no confirm form. A direct POST was refused with the same message, and **0 transcribe jobs** were queued |
| Open `pilot-03` from the dashboard, render style A | SUCCEEDED in 26 s for 3 clips. The 3 `A.ass` files are **byte-identical** to the Phase 1a baseline |
| Review page | 3 players; a Range request returned 206; the review decision was saved |
| Attacks | traversal → 404, foreign Host → 421 |

Afterwards the scratch projects and their media (creator content) were deleted, and their database rows removed.

## Changes outside the new files

- `hayclips/db.py`: connections now save each change immediately (autocommit), so every `conn.transaction()` block is a real transaction. A read had been leaving one open, and later "transactions" were only savepoints inside it.
- `hayclips/selection.py`: `explain()` tolerates candidates written without score features.
- `hayclips/web/security.py` is pure ASGI middleware. The body is read once for the CSRF check and replayed, because Starlette's `BaseHTTPMiddleware` consumed the form before the endpoint could read it.
- `.gitignore`: `projects/` is ignored.

## Open risks and follow-ups

- **Two stores:** paid attempt records live in project folders while the job queue lives in Postgres. Migrating attempts into Postgres needs its own exactly-once design.
- **No browser-level test:** there is no click-through test in a real browser (Playwright etc.), and the UI has had no visual design review.
- **Private helpers:** `fetch_windows` uses private helpers in `fetch.py`; a public "fetch these clips" function would be cleaner.
- **Long database outages:** the heartbeat swallows transient database errors, so a long outage makes leases expire and the jobs get reaped.
- **Missing folders:** projects whose folder was deleted still appear on the dashboard and open as 404.
- **Paid path:** the real paid path in the app is untested, as intended, until the founder authorises a paid test.
- **Deprecation warning:** Starlette warns that the test client's `httpx` is deprecated in favour of `httpx2`. It is harmless today.

## Not done (deferred by the decision rule)

Login, teams, billing, uploads, cloud storage, webhooks, publishing, outreach, LLM features, and a React or waveform editor.
