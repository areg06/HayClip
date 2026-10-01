# HayClips SaaS — migration plan (Phase 0, tech-lead)

Written 2026-10-01 by the tech-lead from the four Phase 0 documents:
- `current-system.md` (audit)
- `architecture-proposal.md` and `domain-model.md` (architecture/backend)
- `job-state-machine.md` (worker/transcription/cost)
- `risk-register.md` (security/QA)

Nothing in this plan has been started. Phase 1 begins only after the founder approves.

Rule for every step: **the existing CLI must still produce pilot-03-equivalent output after the step.**
The reference check is re-rendering pilot-03 from its cached Harmar results:
- the same 3 clips,
- 720x1280,
- durations within 0.1 s of today's,
- **no new Harmar charge**.

## Decision log (tech-lead resolutions of conflicts between the documents)

| # | Conflict | Resolution |
|---|---|---|
| D1 | State names differ: `domain-model.md` has clip states `selected/fetched/transcribed/...`, while `job-state-machine.md` has the fuller set with an approval gate and NEEDS_RECONCILIATION | `job-state-machine.md` is canonical for every state name. `domain-model.md` is aligned to it in Phase 1 step 1.3 |
| D2 | Transcript uniqueness key: `domain-model.md` uses `(provider, media_sha256, options_hash)`, while `job-state-machine.md` proposes a logical key `(source, window, extraction recipe, options)` | **Unique on the logical key; store sha256 as well and check it.** A re-encode of the preview then cannot orphan a paid transcript (a gap the audit found). The sha256 still detects a wrong file |
| D3 | Queue: hand-written SKIP LOCKED table or Procrastinate | Hand-written job table (about 200 lines), so the paid-job rules ("never auto-retry a submit", single-flight per account) stay visible in our code. Revisit if it grows beyond that |
| D4 | SQLite | Unit tests only. Phase 1 local app uses Postgres (Docker or Postgres.app) so we never migrate twice |
| D5 | Send Harmar the letterboxed preview MP4 or audio only | Audio-only (m4a/AAC of the padded window): smaller upload and no video re-encode in the cache key. **Needs founder approval** (it changes the media sent to a third party) |
| D6 | Render all three styles or only A | Default render is A only; B/C on request from the review page. This cuts CPU per clip by about 3×, and the pilot evidence on style preference has not been collected yet |
| D7 | Uncertain Harmar submit (crash, timeout, 5xx after the POST) | Goes to UNKNOWN_SUBMISSION / NEEDS_RECONCILIATION. It is **never** resubmitted automatically. An operator resolves it from the balance change or the Harmar dashboard. Harmar has no idempotency key or list endpoint (docs checked 2026-10-01) |

## Phase 0 (this phase): documentation only. Done when the founder accepts it.

## Phase 1a: make the prototype safe and library-shaped (no web app yet)

Each step is small, is reviewed on its own, and is followed by the pilot-03 reference check.

1. **Fix verified bugs that could cost money or run commands** (from `current-system.md` and `risk-register.md`):
   - Only YouTube video IDs are accepted. The URL is rebuilt from the ID, then `--` and `--use-extractors youtube` are passed to yt-dlp (R11, R12). Covers `fetch_clips.py:37`.
   - Harmar: write the pending marker before the transcript POST. A timeout, 5xx or crash during submit leaves an "unknown" marker and blocks automatic resubmit. Cache writes become atomic (tmp + rename).
   - `harmar_clips.py`: no `KeyError` when everything is cached. A job-ID-only cache is handled without crashing `burn_captions`.
   - `snap()`: falls back to the planned cut instead of raising `ValueError` when no segment has terminal punctuation; the same for silent clips in `loudnorm()`.
   - Hook/caption text: strip newlines; validate `crop.json` `x` as an int within the frame.
   - `subprocess` timeouts on every ffmpeg, ffprobe and yt-dlp call; delete partial outputs on failure.
2. **Package extraction (behaviour-preserving).** Create `hayclips/pipeline/` with pure functions:
   - `captions.load_srt`, `select.sentence_units`, `select.candidates`
   - `fetch.fetch_window`, `transcribe.harmar.*`, `reframe.plan_crop`
   - `render.snap`, `render.build_ass`, `render.render_clip`, `checks.probe`

   Functions take explicit paths and parameters: no `cwd`, no globals (`CAPTION_BOTTOM`), and no work at import time. The current scripts become thin CLIs over this package. `reframe` runs in the same environment; one `pyproject.toml` replaces the system-python/.venv split.
3. **Stable clip identity.** Clips get an `id` in `suggestions.json`, and directory names use the id rather than the list position. `harmar_clips` stops globbing files. A migration script maps today's `clip_NN` to ids, so pilot-01..03 keep their caches.
4. **Fixtures and tests** (from the QA plan in `risk-register.md`):
   - synthetic lavfi video + SRT fixtures
   - a fake Harmar HTTP server covering success, failure, timeout after POST, 429 and cached results
   - two short local face clips cut from pilot-03, gitignored (**founder approval**)
   - CI-less, run by `pytest` locally

Exit criteria:
- all tests pass;
- the pilot-03 reference check passes;
- the full test run makes zero paid calls (the fake server asserts that no real host is contacted).

## Phase 1b: local web app for one end-to-end workflow

This is the founder's Phase 1 target: one operator, localhost only, no login, billing or publishing.

1. **Database and job runner.** Postgres schema from `domain-model.md` (aligned per D1/D2), and the job table plus worker loop from `job-state-machine.md`. There are three pools: io (2), cpu (1), paid (1, single-flight).
2. **Importer.** Read pilot-01..03 folders into the DB, including existing `harmar_transcript.json` as completed Transcripts with charge rows. After this, the DB is the source of truth, but the folders are never deleted automatically.
3. **Storage interface.** A local filesystem backend under `data/` with the retention classes from `domain-model.md`. Media is served only through the app, with no direct folder exposure.
4. **Web UI** (FastAPI + Jinja + HTMX, polling). The screens follow the founder's workflow:
   - New project → paste a YouTube link.
   - Captions are fetched, and candidates are listed with an explanation of their score (opening, ending, fillers, laughter, silence).
   - Select 2–3 → windows are fetched.
   - A **consent + cost confirmation screen** shows the creator-consent checkbox with the stored text, the estimated seconds, the Harmar balance and the budget left. Transcribe is disabled until it is confirmed.
   - Transcribe → reframe → render A.
   - Review page:
     - styles A/B/C on demand
     - edit the hook and trim, then re-render (a free re-render, clearly labelled)
     - "would you post this / minutes to fix" form
     - download the MP4 and SRT

   Every machine output is labelled "unreviewed".
5. **Cost guards in the server** (`job-state-machine.md`): at most 600 s per run, 600 s per project and 1200 s per day of Harmar time, and a 1.1× balance margin. Requests for a full episode are refused. Upload and source caps apply (**founder approval of the numbers**).

Exit criteria:
- the founder runs a new consented episode end to end in the browser;
- the result matches the CLI output for the same windows;
- a forced worker kill during each state recovers without a second charge (tested against the fake Harmar server, then once for real only if the founder allows it).

## Phase 2 (only after a creator says the output is useful)

- Login (magic link or OAuth) with per-project ownership checks.
- R2/S3 storage backend with signed URLs.
- A Docker image with ffmpeg, yt-dlp, Noto Sans Armenian (bundled via `fontsdir`), OpenCV and YuNet.
- A single small VM.
- Harmar webhooks (HMAC-verified) replace polling.
- Uploads as an alternative to YouTube import.
- Per-user quotas.

Billing, teams, publishing and outreach automation stay out until there is evidence for them.

## Deferred on purpose (decision rule: does it reduce creator work for useful clips? not yet proven)

- React editor: switch only when a waveform trim or drag-to-reframe editor is needed.
- Microservices, Redis/Celery, LLM-based selection or hooks (needs founder approval for any API spend).
- Silence tightening, teasers, music.
- Active-speaker detection for single wide shots.
