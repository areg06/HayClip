# HayClips: current system (Phase 0 audit)

Audited 2026-10-01 against the code on disk. Labels:
- **V**: verified in code, with a `file:line` reference.
- **V-data**: verified by inspecting pilot output.
- **I**: inferred and not executed.

No code was changed for this audit.

## 1. Summary

The system is a set of local Python 3 scripts that run against one **pilot folder** (`pilot-NN/`) as a shared workspace.
The steps have no shared state other than files in that folder. Order and naming carry the meaning:
- `clip_NN` is the 1-based index into `suggestions.json`.
- The Harmar cache is keyed to the SHA-256 of `clip_NN.mp4`.

There is no database, no job runner and no lock. A human runs each step in order.
`.gitignore` excludes `pilot-*/`, `*.mp4` and `.venv/` (`.gitignore`), and the folder is not a git repo.
As a result, paid transcripts (`harmar/`) exist only on local disk, with no backup (V, I).

| Step | Command | Free/paid | Runtime |
|---|---|---|---|
| 0 | `yt-dlp --skip-download --write-auto-subs --sub-langs hy-orig ...` (manual, README) | free | system |
| 1 | `clipper.py --srt ... --no-render --duration N --out pilot` | free | system python3 |
| 2 | Human edits `suggestions.json` (keep 2–3, add `source`, `title`, `hook`, `pick_note`) | — | — |
| 3 | `fetch_clips.py pilot` | free (network) | system python3 + yt-dlp + ffmpeg |
| 4 | `harmar_clips.py pilot` | **paid** | system python3 + `HARMAR_API_KEY` |
| 5 | `burn_captions.py pilot --styles A,B,C [--caption-bottom N] [--no-hook]` | free | system python3, which spawns `.venv/bin/python reframe.py` |
| 6 | Human opens `review.html` | — | browser |

## 2. Scripts: inputs, outputs, behaviour

### 2.1 `clipper.py` (library + CLI)

It works as both an importable library (`import clipper as c` in `fetch_clips.py:15`, `harmar_clips.py:11` and `burn_captions.py:24`) and a CLI (`clipper.py:351-399`).

**Library functions used by other scripts:**
- `load_srt` (`:58`), `stamp` (`:50`)
- `Line` and `Clip` dataclasses (`:23-35`)
- `clip_subtitles` (`:306`), `export` (`:315`), `duration_of` (`:329`)
- `api_json` (`:140`), `harmar_transcribe` (`:190`)
- `math`, reached through the module as `c.math.ceil` (`harmar_clips.py:18`), which is an accidental re-export

**CLI arguments** (`:352-369`):
- Optional positional `video`.
- One of `--srt`, `--transcribe` or `--harmar` (mutually exclusive, required).
- `--model`, `--max-api-seconds` (600), `--out` (`output`), `--count` (3), `--min-seconds` (25), `--max-seconds` (60).
- `--skip-start`, `--skip-end`, `--duration`, `--no-render`.
- Without a video it needs `--srt --no-render --duration` together (`:371`).

**Pipeline:**
1. Source lines:
   - `load_srt` (V `:58-74`) strips tags, merges whitespace, sorts, and clamps rolling YouTube cues with `a.end = b.start` when they overlap.
   - The Harmar path calls `harmar_transcribe` on the **whole video** (`:384`), with its cache at `<out>/harmar_transcript.json` (`:194`).
   - The local path calls faster-whisper (`:123-134`).
2. `sentence_units` (`:83-111`): splits each cue at `[.!?։…:]`, times the pieces by character share, and merges them until terminal punctuation or a gap of 0.7 s or more. A unit is closed at 400 characters.
3. `candidates` (`:249-292`): O(n²) windows bounded by `maximum`.
   - Rejected: fewer than 16 tokens, or an unfinished ending (`...`/`…`/`-`).
   - Score components:
     - ending +2 if the last unit ends on terminal punctuation
     - opening +0.6 for a question mark, +0.3 for a digit, a Latin word or a strong word, −0.8 for a weak starter
     - fillers: −0.3 for each filler per minute above 5
     - laughs: +0.25 for each `[ծիծաղ]`, up to 4
     - pace: up to +1
     - silence: −(gaps, capped at 8 s)/4
     - length: up to +0.4
   - The word lists are guesses and are labelled as such (`:114-115`).
4. `pick` (`:295-303`): greedy selection; rejects a window that overlaps an already-picked one by ≥35% of the shorter clip.

**Writes into `--out`:**
- `clip_NN.srt` (`:393`): **YouTube/source text**, later overwritten by `burn_captions`.
- `clip_NN.mp4` (`:395`): letterboxed render, only without `--no-render`.
- `suggestions.json` (`:396`): **overwritten unconditionally**.
- `review.html` (`:398`): overwritten.
- `suggestions.json` items hold only `start`, `end`, `score` and `text` (`asdict(Clip)`).

**Hazard (V):** running `clipper.py` again with `--out` set to an existing pilot destroys hand-edited `suggestions.json` fields (`title`, `hook`, `pad`, `caption_*`, `snap`). In pilot-03 the operator worked around this by running the top-6 selection into the scratchpad and saving it as `suggestions.clipper-top6.json` (V-data).

`export` (`:315-326`) builds a fixed 720x1280 graph: blurred fill plus a fitted foreground. It seeks with input `-ss` and uses `-map 0:a:0?`. The CRF is a parameter (default 23; `fetch_clips` uses 18).

### 2.2 `fetch_clips.py`

Usage: `python3 fetch_clips.py <pilot>`. It is a module-level script with no `main()` and no argparse (`:17-48`).

**Reads:**
- `suggestions.json`
- the first `source/*.srt` in sorted order (`:19-20`)

**For each suggestion i** (`:21-47`):
1. `pad = s.setdefault("pad", 5.0)`; window `[max(0, start-pad), end+pad]`; `pad_start` = the actual leading pad (`:24-26`).
2. Writes `clip_NN.youtube.srt`: YouTube text for the **planned** window, with times relative to the planned start, not the padded one (`:27-28`).
3. Downloads `clip_NN.wide.mp4` with `yt-dlp -f "bv*[height<=1080]+ba/b[height<=1080]" --download-sections "*a-b" --force-keyframes-at-cuts`, up to 3 attempts.
   - Before each attempt it deletes leftovers matching `clip_NN.wide*` (`:30-40`).
   - The yt-dlp **exit code is ignored** (no `check=True`, `:35-37`); success means only that the file exists.
4. Duration guard: |wide − (b−a)| ≤ 0.5 s, otherwise `SystemExit` (`:41-42`).
5. If `clip_NN.mp4` already exists, it is never re-rendered, because the Harmar cache is keyed to its hash (`:43-45`). Otherwise it calls `c.export(wide, ..., crf=18)` (`:46`).
6. Writes `suggestions.json` back **once, at the end** (`:48`). A crash mid-loop loses `pad`/`pad_start` for earlier clips, but they are recomputed on the next run (I).

`yt-dlp` runs with `-q --no-warnings` (`:35`). This hides yt-dlp's "No supported JavaScript runtime" warning, which was seen during this pilot and says that some formats may be missing. A silent quality downgrade below 1080p is therefore possible and is not detected (I).

### 2.3 `harmar_clips.py`

Usage: `HARMAR_API_KEY=... python3 harmar_clips.py <pilot>`. It is a module-level script (`:13-29`).
1. `clips = sorted(glob("clip_[0-9][0-9].mp4"))` (`:14`). This excludes `_A/_B/_C`, `.wide` and `_captions` files. The set comes from the **files present**, not from `suggestions.json`.
2. "Pending" means there is no `harmar/clip_NN/harmar_transcript.json` (`:17`). `need = Σ ceil(duration)` (`:18`).
3. Calls `GET /v1/balance` before anything else. If `need > seconds_remaining`, it exits with nothing submitted (`:19-22`).
4. For each clip, calls `c.harmar_transcribe(video, harmar/clip_NN, duration, 600, timestamps="word")` (`:27`), so each clip has a hard 600 s cap.
5. Calls `GET /v1/balance` again at the end (`:29`).
   - The key is read with `os.environ["HARMAR_API_KEY"]` at `:20` and `:29`. Without the key, the script fails with a `KeyError` traceback, **even when everything is cached** (`:29` always runs).

### 2.4 `harmar_transcribe` (`clipper.py:190-241`): the only paid code path

1. Hashes the video with SHA-256 (`:193`). The cache file is `<out>/harmar_transcript.json` (`:194`).
2. If the cache exists:
   - If the hash differs, it raises "transcript for another video" (`:198-199`). This is a safety stop, not a re-charge.
   - Otherwise it reads `response` and `job_id` (`:200-201`).
3. With no completed response and no `job_id`, it submits:
   1. `ceil(duration) > max_seconds` raises an error (`:209-211`).
   2. `GET /v1/balance` (`:212-214`).
   3. `POST /v1/uploads {filename, file_size}`, which returns `upload_url`, `content_type` and `media_id` (`:215-216`).
   4. `PUT` to the signed HTTPS URL (`upload_to_signed_url`, `:153-179`). It retries 3 times on connection errors or 5xx, and rejects non-HTTPS URLs (`:156`). This step is free, per the code comment (`:154`).
   5. **`POST /v1/transcripts {media_id, source_lang:"hy", options:{timestamps, punctuation:true}}`: the charge happens here** (`:218-220`).
   6. Writes `{video_sha256, job_id}` to the cache **after** the POST returns (`:221-223`).
4. Polls `GET /v1/transcripts/{job_id}` every 3 s for up to 15 minutes (`:224-233`).
   - `failed` raises an error (`:229-230`).
   - A timeout raises "check job id before retrying" (`:233`).
5. Writes `{video_sha256, job_id, response}` (`:234-236`) and returns segments as `Line`s (`:237-241`).

`api_json` (`:140-150`):
- Bearer header, 30 s timeout.
- Wraps `HTTPError` as "Harmar API returned HTTP {code}" and deliberately hides the body.
- `URLError` and socket timeouts are **not** wrapped and propagate as raw exceptions (V).

**Charges observed (V-data, pilot-03):** `seconds_charged` was 62, 70 and 63 for clips of 61.1, 69.1 and 62.1 s. That is ceil(duration), matching the guard at `harmar_clips.py:18`.

### 2.5 `reframe.py` (runs only inside `.venv`)

Usage: `.venv/bin/python reframe.py <video>`. It prints crop-plan JSON to stdout (`:76-77`).
- Model: `<repo>/models/face_detection_yunet_2023mar.onnx` (`:15`).
- Detection runs on a 960 px wide copy of the frame (`:16`, `:23-24`).
- Decodes **every frame**, computes an HSV histogram, and stores the Bhattacharyya distance (`:27-33`).
- Runs YuNet every `round(fps/5)` frames and keeps the largest face's centre x (`:34-40`).
- **Cuts:**
  - a histogram distance > 0.35 (`:44`), or
  - the face jumping more than 8% of the frame width between two samples. The cut is then placed at the frame with the highest distance (`:46-50`).
- **Shots:** per shot, `x = clamp(median(face cx) − crop_w/2, 0, w−crop_w)`, rounded down to an even number. A shot with no face reuses the previous shot's x (`:62-72`).
- Output: `{"width","height","crop_w","shots":[{"start","x","face_hits","samples","max_faces","drift"}]}`. `start` is relative to the wide clip (V-data).

### 2.6 `burn_captions.py`

Usage: `python3 burn_captions.py <pilot> [--styles A,B,C] [--caption-bottom 930] [--no-hook]` (`:270-276`).

**For each suggestion i** (`:282-353`):
1. Loads `harmar/clip_NN/harmar_transcript.json["response"]` (`:284`).
2. `full = duration_of(clip_NN.mp4)` (`:285`).
3. **Re-snap.** Runs only if the suggestion has a `pad` key and `snap != "manual"` (`:287-290`).
   - `snap()` (`:99-119`) picks the sentence start nearest to `pad_start` and the terminal-punctuation end nearest to `pad_start + (end − start)`.
   - It can extend the end by one short (<2 s) reaction from the other speaker.
   - It writes `caption_start`, `caption_end` and `snap = "auto"`.
4. Trim: `cs = caption_start or 0`, `ce = caption_end or full` (`:291`). Old pilots therefore use `caption_end` alone (V-data, pilot-01/02).
5. **Words** (`words_of`, `:76-96`):
   - Uses Harmar `words` when present.
   - Otherwise synthesises words from segment times by character share. pilot-01/02 have no `words` (V-data).
   - Then filters to `[cs−0.05, ce)` and shifts the times (`:294`).
6. **Outputs:**
   - `clip_NN.srt`: Harmar segments, shifted (`:295-297`).
   - Loudness: a first pass of `loudnorm` measured on **`clip_NN.mp4`** over `[cs, ce]` (`:298`, `:207-216`).
7. **Crop** (`speaker_crop`, `:222-257`):
   - Needs `clip_NN.wide.mp4`. If `clip_NN.crop.json` is missing, it runs reframe through `.venv/bin/python`.
   - Without the wide file, or without the venv when no cached plan exists, it falls back to the letterboxed `clip_NN.mp4`.
   - Builds a nested `if(lt(t,...))` crop-x expression with times shifted by −cs.
   - Flags shots that have no face, more than one face, or drift > 0.35.
8. **For each style** (`:308-327`):
   - Writes `clip_NN_<S>.ass`.
   - Runs `ffmpeg -ss cs -i <video_in> -t len -vf "<crop>,ass=clip_NN_<S>.ass" -af <loudnorm second pass>` with `cwd=pilot` (`:314-318`).
   - Calls `ffprobe` and records a problem if: the size isn't 720x1280, |duration − planned| > 0.1, the last cue is past the end, or the first cue starts after 0.3 s.
9. Mutates the suggestion: adds `transcript_source` and `edits` (`source_start` and `source_end` from `start − pad_start + cs/ce`, plus trim, hook, audio, snap_note and framing) (`:328-333`).

**After the loop:**
- Rewrites `suggestions.json` (`:354`) and `review.html` (`:356-366`).
- Prints `CHECK:` lines (`:367-368`). **Problems never cause a non-zero exit.**

**Caption styles** (`:47-51`, `:172-195`):

| Style | Screen grouping | Animation | Position |
|---|---|---|---|
| A | ≤22 characters, ≤4 words; one event per word | active word yellow, 112%→100% pop | — |
| B | ≤14 characters, ≤2 words | font shrinks for long words, pop-in | `\pos(360, CAPTION_BOTTOM−45)` |
| C | ≤30 characters, ≤6 words | `\kf` karaoke sweep, BorderStyle 3 box | — |

- Grouping uses dynamic programming (`chunks`, `:122-161`). Hard breaks are a sentence end, a gap over 0.5 s, or a speaker turn.
- Hook (`:198-204`): 0–3 s, boxed, `\an8` with MarginV 200. Longer than 45 characters only produces a warning (`:300-301`).

## 3. Filesystem contract (pilot folder)

| Path | Producer | Consumers | Notes |
|---|---|---|---|
| `source/*.srt`, `*.srv1` | manual yt-dlp | `fetch_clips` (first sorted `*.srt`), `clipper --srt` | `fetch_clips` takes whichever sorts first |
| `suggestions.json` | `clipper` (overwrite), human, `fetch_clips` (adds `pad`, `pad_start`), `burn_captions` (adds `caption_*`, `snap`, `edits`, `transcript_source`) | all | **Array order defines `clip_NN`.** Reordering or deleting an entry silently re-pairs clips with other transcripts (I) |
| `clip_NN.wide.mp4` | `fetch_clips` | `burn_captions` (video + audio), `reframe` | 1080p landscape, padded window |
| `clip_NN.mp4` | `fetch_clips` (`export` from the wide file) or `clipper` | `harmar_clips` (hash + upload), `burn_captions` (duration, loudnorm measurement, letterbox fallback) | **Immutable after transcription**: its hash keys the paid cache |
| `harmar/clip_NN/harmar_transcript.json` | `harmar_transcribe` | `burn_captions` | `{video_sha256, job_id, response}`; `response` keys: `id`, `status`, `duration_seconds`, `seconds_charged`, `quality`, `text`, `words?`, `segments[{text,start,end,speaker}]`, `srt_url`, `vtt_url`, `media_retained` (V-data) |
| `clip_NN.crop.json` | `burn_captions` → `reframe` | `burn_captions` | Cached forever; hand-editable `x` |
| `clip_NN_<S>.ass`, `clip_NN_<S>.mp4` | `burn_captions` | `review.html` | Overwritten on every run |
| `clip_NN.srt` | `clipper` (YouTube text), then `burn_captions` (Harmar text) | human | Two producers, two meanings |
| `clip_NN.youtube.srt` | `fetch_clips` | `burn_captions` (text only) | Times relative to the planned start, not the padded clip |
| `review.html` | `clipper` or `burn_captions` | human | Relative `src` links; it must sit next to the media |

**Pilot generations (V-data):**
- pilot-01/02: no `pad`, `pad_start`, `wide` or `words`. Their renders are the older `clip_NN_captions.mp4`.
  - Re-running `burn_captions` works and gives a letterbox, no re-snap, and synthetic word timing. The CHECK for first cue > 0.3 s fires, which was seen in this session.
  - **Hazard:** running `fetch_clips.py` on pilot-01/02 would `setdefault("pad", 5.0)` and download wide files (`:24`, `:29-37`). The next `burn_captions` would then re-snap as if `clip_NN.mp4` had 5 s of padding, which it does not, so the cuts would be wrong (I, from `:287-289`).
- pilot-03: the full current contract, plus `suggestions.clipper-top6.json`, which no script reads.

## 4. Hidden coupling

1. **Index naming.** `clip_NN` means position in `suggestions.json` in `fetch_clips` (`:21-22`) and `burn_captions` (`:282-283`), but means files on disk in `harmar_clips` (`:14`). There is no stable clip ID.
2. **Two video sources for one output.** The render takes picture and audio from `clip_NN.wide.mp4` (`:314-315`), while loudnorm is measured on `clip_NN.mp4` (`:298`). Harmar timestamps also come from `clip_NN.mp4`.
   - The code assumes both files are sample-aligned. Nothing checks it.
   - In pilot-03 `clip_NN.mp4` was rendered from an earlier, deleted download and the wide file was downloaded again later. Alignment was measured at 0 ms by cross-correlation during the session (I/manual).
   - If YouTube serves the section with a different keyframe or offset, the captions drift silently.
3. **Mutable module globals.** `CAPTION_BOTTOM` is reassigned through `global` in `main()` (`:269`, `:277`) and read inside `events()` (`:187`) and the style formatting (`:310`). This is not safe for a library or for concurrent use.
4. **Process and cwd assumptions:**
   - ffmpeg renders run with `cwd=pilot` and relative `video_in` and `ass=` paths (`:314-318`). The ASS filter path is unescaped, which is safe only while names stay `clip_NN_X.ass`.
   - `reframe` runs as a separate interpreter at the hardcoded `<repo>/.venv/bin/python` (`:219`, `:235`). The other scripts run on system `python3`. Two environments are involved.
5. **Scripts are not importable.** `fetch_clips` and `harmar_clips` run all their work at import time. `burn_captions` has a `main()`, but its state lives in local variables.
6. **Contract fields are spread across producers.** `pad`, `pad_start`, `caption_*`, `snap` and `edits` are defined implicitly in three files. `snap: "manual"` is the only lock on hand edits.
7. **The cache is on the wrong artifact.** The hash is of the re-encoded letterbox preview, not the source window. Re-encoding the same window, for example with a different ffmpeg version or CRF, gives a different hash, and `harmar_transcribe` then refuses the cache (`:198`). That fails safe, but the transcript is stranded.
8. **The face-crop cache is stale-prone.** `crop.json` is never invalidated if the wide file is downloaded again (`:232`).
9. **Fonts.** The ASS styles name `Noto Sans Armenian Black` and `SemiBold` (`:36`, `:48-50`). They are resolved by libass and fontconfig on the macOS system font `/System/Library/Fonts/NotoSansArmenian.ttc` (research/caption-style.md §5). No `fontsdir` is passed. On a missing font, libass falls back silently, and Armenian may render as empty boxes (I).

## 5. External dependencies

| Dependency | Used by | Notes |
|---|---|---|
| ffmpeg / ffprobe (Homebrew) | `export`, `duration_of`, `loudnorm`, render, `probe` | Needs libass (`ass=` filter) and libx264 |
| yt-dlp (system, Python 3.13 framework) | manual captions, `fetch_clips` | No JS runtime installed (warning seen this session); section download relies on ffmpeg; a transient "ffmpeg exited with code 8" happened once and the retry recovered |
| libass + fontconfig + Noto Sans Armenian | render | No emoji; never `.upper()` (`:68`) |
| OpenCV 5.0.0 headless + numpy in `.venv` | `reframe` | It prints a "Targets are not supported by the new graph engine" warning (harmless; seen this session) |
| YuNet ONNX, 232 KB, in `models/` | `reframe` | Provenance: copied from an earlier session's scratchpad. SHA-256 `8f2383e4…52fa4` was not checked against OpenCV Zoo |
| faster-whisper (optional) | `clipper --transcribe` | Downloads a model on first use; unused in pilots |
| Harmar API `https://api.harmar.ai` | `api_json` | Endpoints used: `GET /v1/balance`, `POST /v1/uploads`, signed `PUT`, `POST /v1/transcripts`, `GET /v1/transcripts/{id}`. `srt_url`/`vtt_url` are returned but unused. The response also has `media_retained:false` |

## 6. Paid operations and cache behaviour

- **The only charge point is `POST /v1/transcripts`** (`clipper.py:218`). Uploads and polling are free (per the comment at `:154` and the observed balances).
- **Guards:**
  - 600 s cap per clip
  - balance check per job (`:212`)
  - batch balance check (`harmar_clips.py:19-22`)
  - hash-bound cache
  - `clip_NN.mp4` is never re-rendered (`fetch_clips.py:43`)
- **Cache states:**
  - none → submit
  - `{sha, job_id}` → poll only, no charge (resume works)
  - `{sha, job_id, response}` → no network for the transcript itself (V)
- **Gaps:**
  1. **Crash or timeout between the POST and the cache write** (`:218-223`). If the process dies, or `urlopen` times out after the server accepted the job, no `job_id` is stored. Re-running submits and charges **again**. No idempotency key or client reference is sent (V).
  2. **A deleted or lost cache file means paying again.** Nothing else (no ledger, no backup, no lookup by `media_id`) can recover the job. `pilot-*` is git-ignored (V).
  3. **A failed job is sticky.** `{sha, job_id}` stays in the cache, so every re-run polls the same failed job and raises (`:229-230`). The only way out is deleting the cache by hand. That re-submits, which is correct for a failure, but there is no tooling for it (V).
  4. **A 15-minute poll timeout** leaves `{sha, job_id}`. Re-running resumes polling, which is correct (V).
  5. **Cache writes are not atomic** (`write_text`). A crash mid-write leaves invalid JSON. `json.loads` then raises on the next run. That fails safe, but the job_id may be lost (I).
  6. The pre-check uses `ceil(duration)` while the server charges what it decides. They matched in pilot-03 (V-data), but there is no contract guarantee.
  7. `clipper.py --harmar` sends the **whole input video** (`:384`), with only the 600 s cap and the cache at `<out>/harmar_transcript.json`. It is the legacy path and does not follow the "selected windows only" rule.
  8. Concurrent runs on the same pilot are unguarded: two processes can both see "no cache" and both submit (I).

## 7. Failure cases visible in code

| # | Where | Trigger → result |
|---|---|---|
| F1 | `clipper.py:67-68` | No cues → `ValueError` (unhandled traceback in the CLI) |
| F2 | `clipper.py:390-391` | No windows → `parser.error` |
| F3 | `clipper.py:396` | Re-run on an existing pilot → hand edits overwritten |
| F4 | `fetch_clips.py:18`, `:37` | Missing `suggestions.json` or `source` key → `FileNotFoundError` / `KeyError` |
| F5 | `fetch_clips.py:35-40` | yt-dlp fails (private or removed video, geo block, bad URL) → 3 silent attempts, then `SystemExit("section download failed 3 times")`, with no error text from yt-dlp (output suppressed by `-q`) |
| F6 | `fetch_clips.py:41-42` | Section length off by more than 0.5 s → `SystemExit`; the bad wide file is kept, so a re-run hits the same check (I) |
| F7 | `harmar_clips.py:20,29` | No env key → `KeyError`, even with everything cached |
| F8 | `clipper.py:146` | Network error → raw `URLError`/`TimeoutError`; during submit this can cause gap 1 |
| F9 | `clipper.py:229-233` | Harmar failed or timed out → `RuntimeError` with the job id |
| F10 | `burn_captions.py:284` | No cache file → `FileNotFoundError`; cache with only `job_id` → `KeyError: 'response'` |
| F11 | `burn_captions.py:106` | No terminal-punctuation segment at or after the chosen start → `min()` on an empty sequence → **`ValueError`** |
| F12 | `burn_captions.py:116-118` | Gaps between consecutive Harmar segments under 0.05 s keep the end from overlapping; fine in practice. Not verified with zero-length segments |
| F13 | `burn_captions.py:173`, `:310` | Unknown style letter (e.g. `--styles D`) → `KeyError` |
| F14 | `burn_captions.py:212` | No audio stream, or loudnorm prints no JSON → `ValueError` from `rindex` |
| F15 | `burn_captions.py:236`, `reframe.py:22-24` | Unreadable video → width 0 → `ZeroDivisionError` in `reframe`, surfaced as `CalledProcessError` |
| F16 | `reframe.py:62-66` + `burn_captions.py:251` | Source narrower than 9:16 (portrait/square) → `crop_w > w`, so x clamps negative → ffmpeg crop error (I). No frames at all → `shots=[]` → `IndexError` at `:251` (I) |
| F17 | `burn_captions.py:314-318` | ffmpeg failure → `CalledProcessError`; the partial `clip_NN_<S>.mp4` from the `-y` write is left behind; `review.html` and `suggestions.json` are not updated for the run |
| F18 | `burn_captions.py:322-324` | Quality checks only print `CHECK:`; exit status is 0 |
| F19 | `burn_captions.py:294` | Words that start before `cs` are dropped even if they end after it; the first word can be lost when the snap lands mid-word (I) |
| F20 | `burn_captions.py:287-289` | `pad` present but `clip_NN.mp4` has no real padding (old pilot after `fetch_clips`) → wrong trims (I) |

## 8. Current security and privacy constraints

- **Key handling.** The key is read only from the `HARMAR_API_KEY` environment variable (`clipper.py:205`, `harmar_clips.py:20`). It is stored outside the repo in `~/.config/harmar/key` (mode 600; V via `ls`). It is passed per command as `HARMAR_API_KEY="$(cat …)"`.
  - It is never written to cache files: the cache keys are `sha`, `job_id` and `response` (V-data).
  - HTTP errors hide the response body (`:148-150`).
- **Subprocesses.** Every call uses an argument list, and there is no `shell=True` anywhere (V: all `subprocess.run` calls take lists).
  - **Argument injection:** `fetch_clips.py:37` passes `s["source"]` from editable JSON straight to yt-dlp as a positional argument, with no `--` separator and no URL validation. A value starting with `-` is parsed as a yt-dlp option, for example `--exec`. Today this is only a local-operator risk, but it is critical for the SaaS version (V).
  - The ffmpeg filter strings embed only numbers computed by the code and fixed file names (V). Hook and caption text go into ASS files, not into filter arguments.
- **Output escaping.**
  - ASS: `{` → `(`, `}` → `)`, `\` → `/` (`burn_captions.py:67-69`). Line breaks inside a single word are not handled; Harmar words have none (I).
  - HTML: title, pick_note, hook, snap note, framing, transcript, YouTube text and source are passed through `html.escape` (`:342-355`). `pilot.name` in `<title>`/`<h1>` is not escaped (`:358`, `:364`), which is a local folder name.
  - `clipper.report` escapes the text (`:341`).
- **Consent** is only a human rule in `CLAUDE.md`. No artifact records it.
- **Data residency.** Harmar reports `media_retained: false` (V-data). Creator media stays on local disk with no encryption or retention policy. The full episode is never downloaded: only padded windows, about 60–70 s each (`fetch_clips`).
- No network-facing surface exists. `review.html` is opened from `file://`.

## 9. What a SaaS wrapper must preserve

1. Only the selected windows are downloaded and transcribed; the full episode never is.
2. A paid job is submitted at most once for each media fingerprint. The job ID is persisted *before* any later step. Gap 1 needs a durable "submitting" state written *before* the POST, plus reconciliation.
3. The transcribed media object (today `clip_NN.mp4`) is immutable and kept as long as its transcript.
4. Hand edits to cuts (`snap: manual`) and crops (`crop.json` x) survive re-renders.
5. Picture and audio stay aligned with the timeline that was transcribed. Making this explicit means rendering from the transcribed file, or verifying offsets.
6. Quality checks (720x1280, duration, cue bounds, faces) are surfaced to the human, not swallowed.
