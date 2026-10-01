# HayClips SaaS: risk register, Phase 1 QA plan, product and creator-research risks

Phase 0 document. Authors: security-privacy, qa, product-ux and creator-research roles. Date: 2026-10-01.
Scope: risks of turning the local operator prototype into a SaaS. No code was changed for this document.

Tentative tech-lead stack assumed here:
- a modular monolith in Python: FastAPI plus a worker process;
- a Postgres-backed job queue;
- S3-compatible private storage with signed URLs;
- a server-rendered HTMX UI;
- Phase 1 is a single local operator bound to `127.0.0.1`; later phases add login and per-project ownership.

Ratings: L = likelihood, I = impact, each H/M/L.
Phase: when the mitigation must exist. P1 = local web app; P2 = first hosted multi-user version.
Evidence marked **[verified]** was reproduced in the scratchpad on 2026-10-01 (`qa/` fixtures, not in the repo).

## 1. Risk register

| ID | Risk | Area | L | I | Current state in prototype (evidence) | Mitigation | Owner | Phase |
|---|---|---|---|---|---|---|---|---|
| R01 | **Duplicate Harmar billing because the submit response is lost.** The job is created server-side, but the HTTP response times out, or the process dies before the job id is saved. A retry submits and pays again | cost / ai-transcription | M | H | `clipper.py:218-222`: `POST /v1/transcripts`, then `cache.write_text` of `job_id`. A crash or 30 s timeout (`clipper.py:146`) between the two leaves no record. The next run finds no cache and resubmits | Before calling Harmar, write a `Transcript` row with state `SUBMITTING` and a client request id, committed first. A worker that finds `SUBMITTING` with no `provider_job_id` must go to `NEEDS_OPERATOR_CHECK`, never auto-resubmit. Ask Harmar whether it supports an idempotency key or a "list my jobs" endpoint (open question). Reconcile against the Harmar dashboard before a manual retry | ai-transcription, worker-infra | P1 |
| R02 | **Duplicate billing on the normal retry path.** A user double-clicks, the UI is refreshed, two workers pick up the same job, or a re-render changes the media hash | cost | H | H | The cache is per folder and keyed by sha256 of `clip_NN.mp4` (`clipper.py:192-199`). A re-rendered preview gets a new hash. The prototype refuses that case ("another video"), which is safe, but the operator is tempted to delete the cache (R08). `fetch_clips.py:43-45` deliberately never re-renders `clip_NN.mp4` | Use a unique constraint on `(candidate_clip_id, media_sha256, provider)` and `SELECT … FOR UPDATE SKIP LOCKED` job claim. Create the paid job only through an explicit confirm action that carries a server-issued one-time token. The "Transcribe" button is disabled after the first click and shows the existing job | backend, worker-infra | P1 |
| R03 | **Worker crash or redeploy during a paid job.** Upload done, submit done, polling interrupted | cost / reliability | M | M | Resumable: the job id is cached before polling (`clipper.py:222`), and a 15-min poll timeout keeps the id (`clipper.py:233`) | Keep that ordering. The polling job is idempotent: resume by `provider_job_id`. A stale-job sweeper re-queues *polling*, never *submission* | worker-infra | P1 |
| R04 | **Large or long uploads** exhaust disk and memory and invite full-episode Harmar submission | cost / infra | H | H | No upload path yet. The Harmar 600 s cap is enforced per file (`clipper.py:209-211`), and `harmar_clips.py:17-23` checks the total against the balance | Cap upload size (e.g. 4 GB) and duration (e.g. 3 h) server-side, with direct-to-storage multipart upload. The Harmar step accepts **only selected clip windows**: a hard server-side max per clip (≤ 120 s) and per project (≤ 600 s default), plus a balance check. The full source can never be a Harmar input | backend, cost-engineering | P1 |
| R05 | **FFmpeg / OpenCV resource exhaustion**: a long, 4K or high-fps source, or many parallel renders | infra | M | H | No timeouts on any `subprocess.run` (`clipper.py:326,330`, `fetch_clips.py:35`, `burn_captions.py:209,235,261,314`). Rendering A/B/C × 3 clips took about 43 s of wall time at about 560% CPU on an M2 | A per-step `timeout=` derived from duration (e.g. 10× realtime). Worker concurrency of 1–2 render slots. ffprobe up front, rejecting > 1080p60 unless downscaled first. `nice`, and cgroup/container limits in P2 | worker-infra, devops | P1 |
| R06 | **yt-dlp failures**: missing JS runtime ("No supported JavaScript runtime", seen 2026-09-30), YouTube format or extractor changes, section downloads dropping mid-stream, rate limits or bot checks, age-gated or private videos | pipeline | H | M | The JS runtime warning was observed. Section downloads failed once with `ffmpeg exited with code 8`, and `fetch_clips.py:29-39` retries 3 times. yt-dlp is unpinned | Pin yt-dlp and ffmpeg versions in the image, plus a weekly upgrade job with a smoke test. Install deno (the default yt-dlp JS runtime). Classify errors (unavailable / private / geo / network / format) into user-facing messages. Downloads are free, so they may auto-retry with backoff | devops, video-pipeline | P1 |
| R07 | **Storage growth**: wide windows, previews and 3 styles × N clips × revisions | cost | H | M | pilot-03 is 110 MB for 3 clips; each wide window is about 10 MB/min | Retention classes (media-storage doc). Renders are reproducible, so they expire after 30 days. Wide windows are kept while the project is active. Transcripts are kept forever (paid) | media-storage | P2 |
| R08 | **Deleted or corrupted transcript cache leads to a repeat charge** | cost | M | H | `harmar/clip_NN/harmar_transcript.json` is the only record of a paid job, and README warns "never delete". Deleting `clip_NN.mp4` or re-rendering it orphans the cache by hash (`clipper.py:198`) | Transcripts live in Postgres (`raw_result` JSONB) and are not deletable by normal UI actions. The preview media hash is stored with them. Re-render never touches the Harmar preview artifact. Deleting a project requires typed confirmation and states that re-processing costs money | ai-transcription, media-storage | P1 |
| R09 | **Bad Armenian captions** shown as if accurate | quality / trust | H | M | Harmar mixes Cyrillic for Russian words (`вообще`, `гаишник`, pilot-03 clip_02). Words contain NBSP joins (`ծյոծ\xa0էր`). The text is "unreviewed" (`burn_captions.py` sets `transcript_source`). Python `'և'.upper()` gives `ԵՒ` **[verified]**, avoided because `.upper()` is never called | Every caption shows the status "machine transcript – unreviewed" until edited. Inline word edit. Flag Cyrillic and Latin tokens for review. Normalise NBSP only for display width, never for timing. Keep a lint that bans `.upper()` on Armenian text | ai-transcription, frontend | P1 |
| R10 | **Missing Armenian fonts in the production image** (libass renders tofu boxes) | devops | M | H | macOS has 9 `Noto Sans Armenian` faces **[verified]** (`fc-list`). A Linux image would not, and libass falls back silently | Bundle the OFL `NotoSansArmenian` TTFs in the image and pass `fontsdir=` to the `ass` filter. A render self-test burns a known string and compares it against a reference frame | devops, qa | P1 |
| R11 | **yt-dlp option injection via the source URL.** A "URL" starting with `-` (e.g. `--exec 'cmd'`) is parsed as an option | security | M (in a SaaS) | H (RCE) | `fetch_clips.py:37` puts `s["source"]` last with **no `--` separator**. **[verified]**: passing `--version` as the source makes yt-dlp print `2026.08.19` and exit 0. `--exec` would run a shell command. Local-only today (the operator edits the JSON) | Validate URLs server-side: parse them, allow only `https://www.youtube.com/watch?v=<11 chars>` / `youtu.be/<id>` and rebuild the canonical URL from the id. Always pass `--` before the URL. Also pass `--no-exec`-safe flags: `--ignore-config --no-plugin-dirs`. Never use `shell=True` (none today) | security, video-pipeline | P1 |
| R12 | **SSRF / arbitrary-file import through yt-dlp**: the generic extractor fetches any URL (internal IPs, cloud metadata), and `file://` or local paths are read | security | M | H | No restriction today | Same allowlist as R11. Pass `--use-extractors youtube`. Run downloads in a network namespace or with an egress allowlist in P2. Uploaded files go only through the upload endpoint | security, worker-infra | P1 |
| R13 | **FFmpeg filter-string injection** through values interpolated into `-vf` / `-af` | security | L→M | H | Filter strings are built with f-strings (`burn_captions.py:254` crop `x={expr}` from a user-editable `clip_NN.crop.json`, `:314` `ass={name}_{style}.ass`, `loudnorm` measured values). Today the names are internal and `x` is meant to be int, but nothing type-checks `crop.json` | The SaaS never builds filters from user strings. Crop `x` is validated as an int in `[0, width-crop_w]`. Styles come from an enum. File names are server-generated UUIDs. Times are floats formatted by code. The browser sends only typed parameters (style enum, trim floats, hook text, crop ints) | video-pipeline, security | P1 |
| R14 | **ASS override / line injection via transcript or hook text** | security / quality | M | L | `ass_text` turns `{` `}` `\` into `(` `)` `/` **[verified]**: `{\pos(0,0)}` becomes `(/pos(0,0))`. **Newlines are not stripped** **[verified]**: a hook of ≤ 22 chars containing `\n` (`burn_captions.py` `hook_event`, single-line branch) writes a raw newline into the `.ass` file, so the text after it becomes a new script line (it could add a `Dialogue:` or `Style:` line) | Strip control chars and `\r\n` in `ass_text`. Limit the hook to 45 chars on one line, validated server-side | video-pipeline | P1 |
| R15 | **HTML/JS injection in review pages** (titles, hooks, transcript) | security | L | M | `review.html` escapes all text with `html.escape` (`burn_captions.py:339-355`, `clipper.py:341`). File names are internal | Templates with autoescape on (Jinja2), a strict CSP, no inline user HTML. Media is served from a separate origin or signed URLs | frontend, security | P1 |
| R16 | **Unauthorized media access / cross-user access** (guessable paths, IDOR on `/projects/{id}`) | security / privacy | M (P2) | H | Local files only, no auth | Phase 1 binds to `127.0.0.1`, single user. P2: every query is scoped by `owner_id`, object keys use random UUIDs, the bucket is private, and short-lived signed GET URLs (≤ 15 min) are issued only after an ownership check. Add a test that user B gets 404 on user A's project, asset and job | backend, security | P1 (design) / P2 |
| R17 | **Signed URL leakage** (shared links, logs, `Referer`) | privacy | M | M | n/a | Short expiry. `Referrer-Policy: no-referrer`. Never log full signed URLs (the prototype already avoids logging the upload URL, `clipper.py:149`). "Share sample" in P2 is an explicit, revocable link | media-storage | P2 |
| R18 | **Harmar secret exposure** (browser, logs, error pages, repo, process args) | security | L | H | The key is read from env (`clipper.py:206`) or `~/.config/harmar/key` (chmod 600), never printed. HTTP errors drop headers (`clipper.py:148-150`). `harmar_clips.py:20` raises `KeyError: 'HARMAR_API_KEY'` if it is unset (it names the variable, not the value) | The key exists only in worker env / secret manager, never in the web tier response, DB or job payload. Scrub `Authorization` in logging. Run a pre-commit secret scan for `hk_live_` and `whsec_`. Rotate the key if it is ever pasted in chat (it happened once with a `whsec_`) | security, devops | P1 |
| R19 | **Creator consent for third-party transcription not captured**: media is sent to Harmar without creator permission | privacy / legal | M | H | Consent is enforced by process only (CLAUDE.md, README). It was recorded in conversation for pilot-01 and pilot-03. Harmar responses show `"media_retained": false` | A `ConsentRecord` (who consented, how: written/email/verbal + evidence note, scope: "send selected windows to Harmar", date) is **required** before the Transcribe action is enabled. Store `media_retained` from each response and show it | backend, product-ux | P1 |
| R20 | **Deletion requests / data retention** (a creator withdraws consent) | privacy | L | M | Manual `rm`. The full source is never stored (only windows) | A "Delete project media" action that deletes object storage and local temp. It keeps a minimal billing and audit record (job id, seconds, dates) without media or text when the creator asks for full erasure. Document what Harmar retains | media-storage, security | P2 |
| R21 | **Malicious or hostile media files** (FFmpeg demuxer CVEs, decompression bombs, 16k×16k frames, endless streams) | security | L (P1) / M (P2 uploads) | H | Inputs come only from YouTube today | ffprobe in a sandbox first. Reject unknown containers and codecs (allowlist mp4/mov/mkv/webm with h264/hevc/vp9/av1 + aac/opus). Cap resolution and duration. Process with a pinned, patched FFmpeg in an unprivileged container with no network | security, devops | P2 |
| R22 | **Stale or zombie jobs** (worker died, job stuck in `RENDERING` forever) | reliability | M | M | No job model | Heartbeat column plus a lease timeout. The sweeper marks expired leases `RETRYABLE` (free steps) or `NEEDS_OPERATOR_CHECK` (paid submit, R01). Cancellation flag checked between steps | worker-infra | P1 |
| R23 | **Rate limiting / abuse** (import spam, render spam, signed URL scraping) | security / cost | L (P1) / M (P2) | M | n/a | Per-user quotas: imports per day, render minutes per day, Harmar seconds per project (cost-engineering). Token bucket on the API | backend, cost-engineering | P2 |
| R24 | **YouTube ToS / copyright exposure**: importing videos the user does not own, plus downloading via yt-dlp | legal | M | H | Pilots use consented creators only; this is enforced by process | The import form requires the user to confirm rights or creator permission, stored with the project. P2: prefer creator **uploads** of their own masters, and treat YouTube import as an operator convenience to review legally before public launch | product-ux, security | P1 (checkbox) / P2 (legal review) |
| R25 | **Misleading claims**: "viral", "ready to post", scores read as predictions, hooks that misquote the speaker | trust / legal | M | M | Disclaimers in `review.html`. Hooks are labelled "draft, needs founder approval". `score` is heuristic (`clipper.py` docstring) | Never display a raw score as quality; show "why picked" reasons instead (opening, ending, laughs, fillers). A hook must be approved by a human before the final render. Ban terms (viral, guaranteed) in UI copy review | product-ux, clip-intelligence | P1 |
| R26 | **Automatic publishing or outreach creeping in** | trust / policy | L | H | Impossible today: no social or email integration; `outreach-hy.md` is a manual draft | Keep it architecturally impossible: no OAuth scopes for social posting and no SMTP/email provider in P1–P2. Downloads are the only export. Any change requires founder sign-off in the decision log | tech-lead | P1 |
| R27 | **Pipeline crashes on edge-case content** (not security, but they block jobs) | reliability | M | M | **[verified]** `snap()` raises `ValueError: min() iterable argument is empty` when no Harmar segment ends in terminal punctuation (`burn_captions.py:106`). **[verified]** `loudnorm()` raises `ValueError: substring not found` on a clip with no audio (`burn_captions.py:212`) | Fallbacks: no terminal end means keep the planned cut and add a snap note; no audio means skip loudnorm and flag it. Covered by QA tests T03/T07 | video-pipeline | P1 |
| R28 | **Wrong framing**: two-person wide shots, no face, the crop jumps on a false cut | quality | M | M | Static per-shot crop. Flags for `max_faces > 1`, drift > 0.35 and no face (`burn_captions.py` `speaker_crop`). On pilot-03 there was one false "2 faces" flag | Show the flags inline on the review timeline. Manual per-shot crop slider (int, validated, R13). Letterbox fallback per shot | video-pipeline, frontend | P1 |

Top 5 by expected cost: **R01/R02** (paid duplicates), **R11/R12** (command execution and SSRF via import), **R19** (consent), **R04** (full-episode cost), **R10** (font tofu in production).

## 2. QA test plan for Phase 1

Principle: real FFmpeg, libass, yt-dlp-free fixtures and a fake Harmar server. Only video *download* from YouTube is mocked or skipped in CI. A manual smoke test runs against one consented video.

### Fixtures (all generated locally, small, deterministic)

| Fixture | How to build |
|---|---|
| `F-land` 1920x1080, 20 s, tone | `ffmpeg -f lavfi -i testsrc2=size=1920x1080:rate=30 -f lavfi -i sine=f=440:sample_rate=48000 -t 20 -c:v libx264 -c:a aac land.mp4` |
| `F-port` 1080x1920, 10 s | same with `size=1080x1920` |
| `F-noaudio` | `testsrc` with no audio input (used for R27) |
| `F-face` / `F-two` | Short cuts from pilot-03 `clip_0N.wide.mp4` (consented): one single-face shot of about 5 s, plus a frame that has both people if one exists. Otherwise build a two-face frame by `hstack`ing two single-face crops. Keep them **local in `tests/fixtures/private/`, gitignored**, with a README stating their origin and consent |
| `F-noface` | `testsrc2` (no faces) |
| `F-srt-*` | Hand-written Armenian SRTs: valid with `։` endings; rolling overlap (verified case: cues 1–3 overlapping → ends clipped to the next start); malformed (bad timestamps, reversed times); empty; BOM + CRLF; tags `[ծիծաղ]` `[մաքրում է կոկորդը]` |
| `F-harmar-*` | JSON responses recorded from pilot-03 (words + segments), plus synthetic ones: no terminal punctuation, Cyrillic words, NBSP words, 18-char word `ուսումնասիրություն`, an empty `words` list |
| Fake Harmar server | A small FastAPI/`http.server` fixture implementing `/v1/balance`, `/v1/uploads` (signed PUT URL to itself), `/v1/transcripts` and its GET. Scripted modes: ok, 500 on submit, **submit succeeds but the response hangs/drops**, job `failed`, never completes, low balance. It counts submissions per media hash |

### Tests

| # | Case | Level | Fixture | Expected behaviour |
|---|---|---|---|---|
| T01 | Valid Armenian captions | unit | F-srt-valid | `load_srt` → cues. `sentence_units` breaks on `։`. Windows start and end on units. Armenian text round-trips unchanged |
| T02 | Malformed SRT | unit | F-srt-bad, empty | Clear `ValueError` ("No timed subtitle cues…") **[verified]**, surfaced as a project error and not a 500. Reversed cues are dropped |
| T03 | Rolling YouTube cues | unit | F-srt-rolling | Overlaps clipped: (1.0–2.5), (2.5–5.0), (5.0–8.0) **[verified]** |
| T04 | Missing captions | integration | yt-dlp mock returning no `hy`/`hy-orig` track | Project state `NO_CAPTIONS` with options (upload SRT, local whisper) and no crash |
| T05 | Bad URLs | unit | strings: `--exec x`, `--version`, `file:///etc/passwd`, `http://169.254.169.254/`, a vimeo URL, a playlist URL, a valid watch URL with tracking params | Only YouTube video ids are accepted. The canonical URL is rebuilt. `--`-prefixed input is rejected before any subprocess (R11/R12) |
| T06 | Unavailable / private / age-gated video | integration (mocked yt-dlp exit + stderr) | recorded stderr samples | Mapped to a user message. The job ends `FAILED_NOT_RETRYABLE`, with no retry loop |
| T07 | Landscape source | integration | F-land | 720x1280 output, duration within ±0.1 s of plan. Letterbox fallback when no face. Clip with no audio (F-noaudio) skips loudnorm, flagged (R27) |
| T08 | Portrait source | integration | F-port | No crop needed (scale only). The pipeline must not compute a negative crop x (currently untested; the reframe crop assumes landscape) |
| T09 | Two-person shot | integration | F-two | Crop plan flags `max_faces > 1`, and the review page shows the warning |
| T10 | No-face shot | integration | F-noface | Plan `face_hits = 0` → keeps the previous x or centre, flagged |
| T11 | Harmar failure modes | integration | fake server | 500 on upload: "nothing was charged", retry allowed. Job `failed`: terminal, no auto-resubmit. Submit response dropped: state `NEEDS_OPERATOR_CHECK`, **submission count stays 1** |
| T12 | Cached Harmar result | integration | fake server + existing transcript row | Re-running transcription makes **zero** POSTs to `/v1/transcripts`. A different media hash for the same clip is refused with a message |
| T13 | Worker restart | integration | kill the worker process mid-poll and mid-render (`SIGKILL`) | Poll resumes by job id. Render restarts from scratch with temp files cleaned. No paid resubmission |
| T14 | Failed FFmpeg | integration | corrupt MP4 (truncate a fixture to 1 KB) | Step fails with stderr tail stored. Job `FAILED` and retryable. Temp files removed |
| T15 | Render retry | integration | T14, then fixed input | Same `ClipEdit` revision re-renders deterministically, and older assets are replaced, not duplicated |
| T16 | Caption overflow | unit + render | F-harmar with 18-char word, a 60-char unpunctuated run, B style | No screen exceeds style width (A ≤ 22, B ≤ 14 or a single word with font shrink, C ≤ 30). Rendered frame: text bbox inside x 48–672 (check with a frame grab + simple pixel scan) |
| T17 | Duration checks | integration | any render | `probe()` gives 720x1280, \|d − planned\| ≤ 0.1 s, last cue ≤ d, first cue ≤ 0.3 s (existing `CHECK:` logic becomes test assertions) |
| T18 | Duplicate job submission | integration | two concurrent POSTs of "transcribe clip 1" + double-click | One `Transcript` row and one paid submission. The second request returns the existing job |
| T19 | ASS / hook injection | unit | hook `"ա\nDialogue: …"`, transcript `{\pos(0,0)}` | Newline stripped, braces neutralised. Today newlines are **not** stripped **[verified]**, so this test fails until R14 is fixed |
| T20 | Snap without punctuation | unit | F-harmar no terminal | Planned cut kept, note added. Today it raises **[verified]** (R27) |
| T21 | Armenian casing | unit/lint | — | No `.upper()` on caption text. `և` stays `և` |

CI cost: everything except T13/T16 rendering runs in seconds. Renders use 5–10 s fixtures. No test calls the real Harmar or YouTube. A manual pre-release smoke test does one consented video end-to-end and is logged.

## 3. Product / UX risks

- **Correction time is the product.** If an operator spends longer fixing a clip than making it by hand in CapCut, the tool fails regardless of code quality. Log the minutes spent per clip in review (time on page, edits made). The first milestone question is "which clip/style would you post, and how long did fixing it take?" (CLAUDE.md).
- **Uncertainty must be visible, not hidden.** Show these as plain badges:
  - captions: "machine transcript – unreviewed";
  - picks: the heuristic's reasons ("starts with a question", "ends on a full stop", "3 laughs") instead of a score;
  - framing: "auto crop – 1 shot flagged";
  - snap: "cut moved 1.2 s to sentence edge".
  Never show a single "quality %".
- **Accidental cost.** Transcribe is the only paid action. It needs:
  - its own confirmation step that shows seconds, balance after, and the consent record;
  - no "transcribe all";
  - no automatic transcription on import.
  Re-render is free and should feel free.
- **Too many choices.** Three styles × three clips × hooks becomes nine videos to judge. Default to style A, with B/C one click away.
- **Wrong mental model.** Users may expect a "done" Reel. Copy must say "draft clips for your review".
- **Armenian-specific editing.** Inline transcript fixes must handle mixed scripts (Armenian/Cyrillic/Latin) and must not break word timing when a word is edited.

## 4. Creator research log (template)

**No creator evidence has been collected yet.** Pilots 01–03 were run and reviewed by the founder only; no creator has seen or rated output. Do not fill these fields from assumptions.

One entry per creator session:

| Field | Value |
|---|---|
| Date / session id | |
| Creator / show (internal reference; no contact details here) | |
| Consent scope recorded (processing, Harmar, sharing samples) | |
| Their current workflow (tools, who edits, time per clip today, observed or self-reported?) | |
| Clips shown (pilot / clip ids / styles) | |
| Which clip and style they would actually post (verbatim) | |
| What they would change before posting | |
| Correction time, measured (minutes, by whom) | |
| Pain points observed | |
| Features requested (verbatim) | |
| Features rejected or disliked | |
| Caption accuracy issues found (Armenian, Cyrillic, names) | |
| Willingness to use again (stated / observed behaviour) | |
| Willingness to pay (**only if directly stated or acted on; quote it**) | |
| Follow-up agreed (no automatic outreach; founder sends manually) | |
