# HayClips SaaS — architecture proposal (Phase 0)

Status: proposal for founder review. Nothing here is built yet.
Scope: how the existing local pipeline (`clipper.py`, `fetch_clips.py`, `harmar_clips.py`,
`burn_captions.py`, `reframe.py`) becomes a web application without changing what it produces.
Authors: architecture / backend / media-storage / devops / frontend roles, under tech-lead decisions.

## 1. Constraints taken from the current code

These facts drive the design. Each was checked in the repository on 2026-10-01.

| Fact in the prototype | Consequence for the SaaS |
|---|---|
| `fetch_clips.py` and `harmar_clips.py` run at import time and read `sys.argv[1]` | They must become functions before a worker can call them |
| `burn_captions.py` keeps `CAPTION_BOTTOM` as a mutable global and runs FFmpeg with `cwd=pilot` so that `ass=clip_NN_A.ass` resolves | Render functions take explicit paths and parameters, with no globals or cwd tricks. The ASS path needs FFmpeg filter escaping, or a per-job temp dir |
| `harmar_transcribe()` keys its cache on the sha256 of `clip_NN.mp4` and writes `job_id` before polling | Idempotency must be keyed on the media hash plus provider options in the DB, not on a JSON file next to the video |
| Re-rendering `clip_NN.mp4` changes its hash, which makes the cached transcript "belong to another video" | The Harmar preview is an immutable artifact. Renders use `wide.mp4`, never a rewritten preview |
| `reframe.py` needs OpenCV from `.venv` and is called as a subprocess | One image or venv per worker includes OpenCV. The worker may still run it out-of-process for crash isolation |
| `yt-dlp --download-sections` sometimes fails mid-stream (`ffmpeg exited with code 8` seen on pilot-03) | The download step retries per window, discards partial files and is safe to repeat (it is free) |
| Harmar is billed per second at submit; trial balance is 133 s after pilot-03 | Paid calls get their own queue with a concurrency of 1, a balance check, an explicit user confirmation and a ledger |
| Rendering three styles of three clips took ~43 s wall on an 8 GB M2 at ~550% CPU | CPU-heavy work goes on its own queue with one FFmpeg job per worker slot |

## 2. Recommended stack

| Concern | Choice | Rejected alternatives (why) |
|---|---|---|
| Language | Python 3.12+ everywhere | Node/TS backend: it would wrap a Python pipeline through subprocesses and add a second language |
| App structure | **Modular monolith**: one repo, one package `hayclips/`, two process types (web, worker) | Microservices: nothing yet needs independent scaling, and they would multiply ops work |
| Web framework | FastAPI + Uvicorn | Django: its admin and ORM are attractive but heavier, and async file streaming is clunkier. Flask: no typed request models |
| DB | **PostgreSQL 16** in dev and prod | SQLite: fine for unit tests of pure functions, but not for the app. `SKIP LOCKED`, concurrent workers and JSONB need Postgres, and dev and prod should match |
| ORM / migrations | SQLAlchemy 2.0 + Alembic | Raw SQL: harder to keep the domain model consistent |
| Queue | **Postgres `job` table** claimed with `SELECT … FOR UPDATE SKIP LOCKED`, plus a lease/heartbeat | Celery + Redis: extra infra, and its at-least-once retries make it easy to resubmit paid work by accident. Procrastinate: a reasonable fallback if the hand-rolled queue grows beyond ~300 lines |
| Object storage | `Storage` interface. `LocalStorage` (dev, under `var/media/`) and `S3Storage` (Cloudflare R2 preferred: no egress fees; AWS S3 is equivalent) | Local disk only in prod: it does not survive host replacement and disk is already 97% full on the dev machine |
| Frontend (Phase 1) | Server-rendered **Jinja2 + HTMX**, with polling for job progress. Plain `<video>` elements | React/Next: a build step and a second language before any interaction needs them (see the trigger in §8) |
| Auth (Phase 1) | None. Single operator, bound to `127.0.0.1` | Building login before a creator asks for it |
| Auth (later) | Email magic link or Google OAuth via a maintained library (e.g. Authlib), server-side sessions, per-project ownership checks | Rolling our own password auth |
| Media tools | FFmpeg 7.x, yt-dlp (pinned and updated deliberately), OpenCV headless 4.9+/5, YuNet ONNX, Noto Sans Armenian | Hosted transcoding APIs: paid infrastructure before validation |
| Packaging | `pyproject.toml` with one lockfile (uv). A Docker image for production later | Separate venvs per script (the current `.venv` split): one environment is simpler |

## 3. Application diagram

```
                 browser (operator / creator)
                         │  HTML + HTMX, <video src=signed URL>
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ web process (FastAPI)                                        │
│  api/  routes: projects, sources, candidates, consent,       │
│        transcribe (confirm), edits, renders, downloads       │
│  web/  Jinja templates                                       │
│  auth/ (later)  ownership checks on every project-scoped URL │
│  writes rows + enqueues jobs; never runs FFmpeg/Harmar       │
└──────────────┬──────────────────────────────┬────────────────┘
               │ SQL                          │ signed URLs (short TTL)
               ▼                              ▼
        ┌─────────────┐               ┌───────────────────┐
        │ PostgreSQL  │               │ object storage    │
        │ domain rows │               │ (local dir / R2)  │
        │ job table   │               │ private bucket    │
        │ charge      │               └─────────▲─────────┘
        │  ledger     │                         │ put/get
        └──────▲──────┘                         │
               │ claim (SKIP LOCKED), heartbeat, state
     ┌─────────┴─────────────────────────────────┴──────────┐
     │ worker processes (same codebase, `hayclips worker`)  │
     │  queue "io"     : captions fetch, window download    │
     │  queue "cpu"    : analyze, crop plan, render A/B/C   │
     │  queue "paid"   : Harmar submit/poll (concurrency 1) │
     │  each job: temp dir → pipeline.* → upload artifacts  │
     └──────┬──────────────────────┬────────────────────────┘
            │ subprocess (argv lists, no shell)
            ▼                      ▼
     ffmpeg / ffprobe / yt-dlp   Harmar API (HTTPS, key from env)
```

## 4. Package layout and shared library

```
hayclips/
  pipeline/            pure, filesystem-explicit functions (no DB, no env, no argv)
    captions.py        load_srt, rolling-cue fix, sentence_units      (from clipper.py)
    select.py          candidates, pick, scoring explanation           (from clipper.py)
    fetch.py           fetch_captions, fetch_window                    (from fetch_clips.py)
    snap.py            snap to provider sentence edges                 (from burn_captions.py)
    captions_ass.py    chunks, events A/B/C, hook, ASS writer          (from burn_captions.py)
    reframe.py         plan_crop (OpenCV)                              (from reframe.py)
    render.py          loudnorm, render_clip, probe checks             (from burn_captions.py)
    proc.py            run(argv, timeout, cwd=None) wrapper: no shell, logs, kill on timeout
  providers/
    base.py            TranscriptionProvider protocol
    harmar.py          upload/submit/poll; idempotent via DB, not files
  storage/             Storage protocol, LocalStorage, S3Storage
  jobs/                job table access, claim/heartbeat, handlers per job type
  domain/              SQLAlchemy models (see domain-model.md)
  api/, web/           FastAPI routes, templates
  cli/                 `hayclips select|fetch|transcribe|render` (replaces the scripts)
clipper.py, fetch_clips.py, harmar_clips.py, burn_captions.py, reframe.py
                       kept as thin wrappers over hayclips.cli during migration
```

Rule: the **CLI and the worker call the same `pipeline.*` functions**. The CLI writes artifacts to a
pilot folder with today's file names, so `pilot-03/` keeps working. The worker writes them to a temp
dir and uploads them through `Storage`. A golden test compares both paths against pilot-03 outputs
(see the migration plan).

## 5. Interface sketches (app ↔ pipeline)

All functions are synchronous, take explicit inputs and return dataclasses. None of them reads the
environment, cwd or the database. Workers handle persistence.

```python
@dataclass(frozen=True)
class Line: start: float; end: float; text: str

@dataclass(frozen=True)
class SelectParams:
    min_seconds: float = 25; max_seconds: float = 60; count: int = 6
    skip_start: float = 0; skip_end: float = 0

@dataclass(frozen=True)
class Candidate:
    source_start: float; source_end: float; text: str; score: float
    score_parts: dict[str, float]      # ending, opening, pace, laughs, filler_penalty, silence, length

def fetch_captions(url: str, workdir: Path, lang: str = "hy-orig") -> CaptionsResult  # free
def load_captions(srt_path: Path) -> list[Line]
def select_candidates(lines: list[Line], duration: float, p: SelectParams) -> list[Candidate]

@dataclass(frozen=True)
class WindowArtifacts:
    wide: Path            # landscape window, used for the crop and the renders
    preview: Path         # 720x1280 letterbox, the file sent to the provider (immutable)
    pad_start: float; duration: float; preview_sha256: str

def fetch_window(url: str, start: float, end: float, pad: float, workdir: Path,
                 *, retries: int = 3, timeout_s: int = 600) -> WindowArtifacts

class TranscriptionProvider(Protocol):
    name: str
    def estimate_seconds(self, media: Path) -> int
    def balance_seconds(self) -> int
    def submit(self, media: Path, options: dict) -> str            # returns provider job id; CHARGES
    def poll(self, job_id: str) -> ProviderStatus                  # never charges
@dataclass(frozen=True)
class Transcript:
    provider: str; job_id: str; media_sha256: str; options: dict
    words: list[Word]; segments: list[Segment]; raw: dict

def snap_to_sentences(t: Transcript, planned_start: float, planned_end: float) -> Trim
def plan_crop(wide: Path, model: Path) -> CropPlan                 # per-shot x, flags
@dataclass(frozen=True)
class RenderSpec:                                                  # built from a ClipEdit revision
    trim: Trim; style: Literal["A", "B", "C"]; caption_bottom: int = 930
    hook: str | None = None; crop: CropPlan | None = None
def render(spec: RenderSpec, wide: Path, preview: Path, t: Transcript, out_dir: Path,
           *, timeout_s: int = 900) -> RenderResult                # mp4, ass, srt, probe, checks[]
```

`transcribe()` is **not** a pipeline function. It is a worker handler that wraps
`provider.submit/poll` with the DB idempotency protocol in §7, because billing safety needs the DB.

## 6. Database

PostgreSQL holds every domain row, the job table and the provider charge ledger. Media bytes are
never stored in the DB, only `storage_key` + sha256. JSONB holds the raw provider responses and crop
plans, which are immutable once written. The full model is in `domain-model.md`.
Backups: nightly `pg_dump` to the object store in production. The transcript rows are the most valuable
part because they represent money already spent.

## 7. Queue and worker architecture

**Job table** (`job`): `type`, `queue` (`io` | `cpu` | `paid`), `state`, `payload` JSONB,
`idempotency_key` UNIQUE, `attempts`, `max_attempts`, `lease_until`, `heartbeat_at`, `progress`,
`error`, `cancel_requested`. Claim:

```sql
UPDATE job SET state='running', lease_until=now()+interval '5 min', attempts=attempts+1
WHERE id = (SELECT id FROM job WHERE queue=$1 AND state='queued' AND run_after<=now()
            ORDER BY priority, created_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING *;
```

Workers renew the heartbeat every 20 s. A reaper requeues `running` jobs whose lease expired, but
**only for non-paid job types**. Expired `paid` jobs go to `needs_attention` (see below).
Full states and transitions are in `job-state-machine.md`.

| Queue | Job types | Concurrency (1 small host) | Timeout | Retry |
|---|---|---|---|---|
| `io` | fetch_captions, fetch_window | 2 | 10 min | 3, exponential backoff, free |
| `cpu` | analyze (select), plan_crop, render_style | 1 per 2 vCPU; **one FFmpeg per slot** (`-threads` capped) | render 15 min, crop 5 min | 2, then failed |
| `paid` | transcribe_submit, transcribe_poll | **1 global** | submit 2 min, poll 15 min | submit: **never auto-retried after the HTTP request was sent**; poll: unlimited and free |

**Paid idempotency protocol** (preserves today's "cache the job id before polling" rule):

1. Look up `transcript` by unique `(provider, media_sha256, options_hash)`. If it is `completed`, reuse it with no call.
   If it is `submitted`, enqueue a poll only.
2. Check `consent_record` for (source_media, provider) and an explicit user confirmation for this
   batch with the estimated seconds. Check the balance and the project/day quota.
3. Insert `transcript(state='submitting')` and a `provider_charge(estimated)` row **in one DB transaction
   before the HTTP call**.
4. Upload (free; retryable), then POST submit. On a response, store `provider_job_id` and set state to `submitted`.
5. If the worker dies or times out between step 3 and step 4's response, the row stays `submitting`. The reaper
   marks it `needs_attention`, and a human checks the Harmar dashboard before choosing "resubmit" or
   "attach job id". No automatic resubmission.
6. Poll until completed. Store the raw result, normalized words and the `seconds_charged` actually reported.

Ask Harmar whether `POST /v1/transcripts` accepts an idempotency key. If it does, send
`transcript.id` as that key and step 5 can become automatic.

**Cancellation**: `cancel_requested` is checked between pipeline steps and by the `proc.run` wrapper,
which kills the FFmpeg/yt-dlp process group. A paid submit cannot be cancelled once it has been sent.
**Temp files**: each job runs in `var/tmp/job-<id>/`, which is deleted in `finally` after its artifacts are
uploaded. A startup sweep removes temp dirs of jobs that are no longer running.

## 8. Frontend

Phase 1 pages are server-rendered with HTMX partials: Dashboard → Project → Source (URL plus
consent checkboxes) → Candidates (table with score parts, text, play buttons from YouTube embed
start/end) → Selection → "Transcribe N clips, ~S seconds, balance B" confirm → Job progress
(HTMX poll every 2 s) → Review (A/B/C players side by side, hook text field, trim nudges ±0.25 s,
crop shot list with x override, re-render button) → Download (signed URL).

The UI marks everything machine-generated: "Unreviewed transcript", "Heuristic score, not a
prediction", and CHECK flags from the render probe.

**Trigger for switching to React** (or a React island inside the page): we need a waveform/timeline
trim editor, frame-accurate scrubbing with live caption preview, or drag-to-reframe. All of these
are deferred until a creator says trim and framing fixes are their main correction cost.

## 9. Object storage and media lifecycle

Key scheme: `projects/{project_id}/sources/{source_id}/windows/{clip_id}/{kind}/{sha256}.{ext}`.
Content-addressed names make uploads idempotent. The bucket is private, and the API returns signed GET URLs
(TTL 15 min) only after an ownership check. Uploads (when added) go to signed PUT URLs with size
and content-type limits, followed by an ffprobe validation job before use.

| Artifact | Retention class | Default |
|---|---|---|
| Transcript raw JSON, Harmar preview MP4 (hash anchor) | `paid_anchor` | Keep for the project's life. Delete only through an explicit "delete project" that warns that a re-run will be charged again |
| Wide window, crop plan, ASS, SRT | `regenerable` | Keep 30 days after the last edit; free to rebuild (download + CPU) |
| Rendered A/B/C MP4 | `derived` | Keep 14 days, or until downloaded + 7 days; re-render on demand |
| Full uploaded source (future upload path) | `source` | Delete after the windows are cut and verified (mirrors the current "delete source after clipping" rule) |
| Job temp dirs | `ephemeral` | Delete at job end |

## 10. Authentication and authorization

- Phase 1: no accounts. The app refuses to bind to anything but `127.0.0.1` unless `HAYCLIPS_AUTH=on`.
- Later: magic link or OAuth → `user` row → session cookie (HttpOnly, SameSite=Lax). Every
  route under `/projects/{id}` loads the project and checks `owner_id == current_user.id` through one
  dependency. Signed URLs are only minted after that check.
- Secrets (`HARMAR_API_KEY`, S3 keys, session secret) live in server-side env or a secret file.
  They are never rendered into templates, logs or job payloads.

## 11. Local development architecture

- `docker compose up postgres`, or a local Postgres from Homebrew. App and worker run as plain processes:
  `hayclips web` and `hayclips worker --queues io,cpu,paid`. FFmpeg, yt-dlp and fonts come from the host
  (Homebrew).
- `LocalStorage` under `var/media/`, with signed URLs emulated by an HMAC-token download route.
- `HAYCLIPS_PROVIDER=fake` provides a fake Harmar that returns pilot-03's cached JSON, so development never spends seconds.
- Fixtures: short synthetic MP4 + SRT plus pilot-03's cached transcripts (see the QA plan).

## 12. Production architecture (not deployed yet)

One small VM or container host (4 vCPU / 8 GB is enough for one operator) runs:
`web` (1), `worker-io` (1), `worker-cpu` (1–2), `worker-paid` (1), managed or local Postgres,
and R2 for media. One Docker image is used for all process types. It includes ffmpeg/ffprobe, yt-dlp, Noto Sans Armenian
(fc-list checked at build), OpenCV headless and the YuNet model (sha256 pinned). A health check
renders a 1-second Armenian ASS fixture at startup and fails the container if the font falls back to tofu boxes.
Scaling out later means adding `worker-cpu` hosts. Nothing else changes.

## 13. Disagreements with tech-lead

- **None blocking.** One refinement: Procrastinate (a Postgres task queue) instead of a hand-rolled job
  table is acceptable and less code, but the paid-job rules (no auto-retry after send,
  `needs_attention` instead of requeue) must be enforced in our handlers, not by the library's retry settings.
- SQLite: limit it to pure-function unit tests. Do not support it as an app backend.

## 14. Decisions needing founder approval

1. Postgres + R2 for production (R2 adds a Cloudflare account; free tier ~10 GB).
2. Phase 1 is localhost-only with no login.
3. The retention defaults in §9, especially keeping Harmar preview MP4s for the project's life.
4. Manual "needs attention" handling for an uncertain Harmar submit (until Harmar confirms idempotency keys).
5. YouTube import as the primary source in a hosted product. yt-dlp use on others' content needs the
   creator's permission, and YouTube's terms and reliability are product risks (see the risk register).
