# Phase 1a report: safe, testable, reusable pipeline

Date: 2026-10-01. Scope: Phase 1a only. No browser app was started.

Commits: 14 local commits on `main` after `c480db0` (the founder's pushed prototype commit). They are not pushed.
Diff since `c480db0`: 64 files changed, +5933 −981.

**Paid calls during Phase 1a: none.**
- No command was run with a Harmar key or with the paid opt-in set.
- The cross-project paid ledger (`~/.hayclips/paid-ledger.jsonl`) is empty.
- The Harmar balance is unchanged at 133 s.

## 1. Files changed

**New package `hayclips/`**

| Module | Purpose |
|---|---|
| `config.py` | settings, cost limits and tool paths from `HAYCLIPS_*` environment variables |
| `errors.py` | typed errors, each carrying an operator hint |
| `jsonio.py` | atomic JSON writes and fsynced JSONL appends |
| `hashing.py` | file hashing |
| `proc.py` | the only place that starts processes |
| `models.py` | domain records |
| `project.py` | repository and legacy migration |
| `cli.py`, `__main__.py` | the `python -m hayclips` operator commands |
| `sources/base.py` | the `SourceProvider` interface |
| `sources/youtube.py` | `YouTubeSource` |
| `fetch.py` | window fetching |
| `selection.py` | candidate selection |
| `captions/snap.py`, `ass.py`, `fonts.py` | snapping, caption styles, font check |
| `media/probe.py`, `ffmpeg.py`, `alignment.py`, `reframe.py`, `audio.py` | media operations |
| `transcription/harmar.py`, `budget.py`, `service.py`, `store.py`, `reconcile.py`, `fake_harmar.py` | paid transcription |
| `render.py`, `review.py` | rendering and the review page |

**Root scripts rewritten as thin CLIs:** `clipper.py`, `fetch_clips.py`, `harmar_clips.py`, `burn_captions.py`, `reframe.py`.

**Tests:**
- `tests/conftest.py`
- 15 `tests/test_*.py` modules
- `tests/fixtures/{synth.py, fake_ytdlp.py, make_local_fixtures.py}`
- `tests/fixtures/local/{one_face,two_faces}.mp4`: founder-approved pilot-03 cuts, gitignored and confirmed untracked

**Project files:** `pyproject.toml`, `requirements.txt`, `.gitignore`, `README.md`, `CLAUDE.md`, `START-IN-CLAUDE-CODE.md`.

**Data (outside git):**
- pilot-01, 02 and 03 were migrated to the stable-id layout; the originals are kept under `pilot-*/legacy/`.
- Backup of all 9 Harmar caches: `~/.hayclips/backups/2026-10-01-pre-phase1a/`.
- pilot-03 regression baseline: `pilot-03/.phase1a-baseline/`.

## 2. Architectural boundaries introduced

| Boundary | Rule |
|---|---|
| **Processes** | `hayclips/proc.py` only. Argument lists, never a shell. Every call has a timeout and the whole process group is killed on timeout. Declared outputs are deleted on failure. Errors carry the tail of stderr. |
| **Sources** | `SourceProvider` (`inspect` / `fetch_captions` / `fetch_window`). `YouTubeSource` is the only implementation; uploads are not built. yt-dlp is reached only through it. |
| **State ownership** | `project.json` is written only by operator commands. `candidates.json` by selection. `window.json` by fetch. `transcription/` by the paid service. `crop.json`, `render/` and `review.html` by the renderer. No script rewrites another script's file. |
| **Identity** | `Clip.id` (`clp_…`) is assigned at selection and never changes. `Clip.order` is display only. All artifacts live in `clips/<id>/`. Candidate ids are deterministic from the window. |
| **Paid transcription** | `transcription.service.transcribe` is the only path to a charge. `TranscriptionAttempt` records the exact media (sha256, bytes, duration, `derived_from`), provider job id, state history and reconciliation. Rendering reads transcripts only through `store.completed_transcript()`, which re-verifies the bytes. |
| **CLI vs library** | Root scripts parse arguments and print; all logic is in the package, so Phase 1b workers can call the same functions. A Postgres repository can replace `ProjectRepo` behind the same operations. |

## 3. Bugs fixed, with the behaviour now defined

| # | Bug | Defined behaviour now |
|---|---|---|
| 1 | Snapping crashed when no Harmar segment ended a sentence | Falls back to the nearest segment edges, with a note. An empty transcript keeps the planned cut, renders without captions and prints CHECK lines. A word starting just before the cut is kept. |
| 2 | Loudness analysis crashed on silent clips | A silent clip renders without loudness normalisation (fades kept) and the review page says so. A clip with no audio track renders video-only. |
| 3 | `harmar_clips.py` crashed with `KeyError` when the key was absent | With every clip cached it needs no key and no network. Without a cache it gives a clear error, makes zero requests and exits 2. Without `--confirm-paid` it only prints a plan. |
| 4 | A hook with a newline could inject an extra caption line | All caption and hook text has `\n\r\t` collapsed and `{ } \` neutralised. A hook over 45 characters is a `ValidationError` for that clip and is never silently truncated. |
| 5 | No subprocess timeouts | Every ffmpeg, ffprobe, yt-dlp and fc-match call has one. Partial outputs are removed on timeout or failure. |
| 6 | The video link reached yt-dlp unvalidated, so it could run commands | Only an 11-character YouTube video id is accepted, and the URL is rebuilt from it. Calls always use `--ignore-config --no-plugin-dirs --no-playlist --no-exec --use-extractors youtube … -- URL`. Option-looking input is rejected before any process starts. |
| 7 | Nothing checked that caption timing matched the rendered video | Before every render, alignment is checked by identical file, recorded provenance, or audio cross-correlation (±40 ms, peak ≥ 0.5). A failure stops that clip. A 0.3 s shift is detected as +300 ms. |

**Also fixed:**
- **Clip numbering by position:** "clip_NN" no longer depends on list order or file globbing (stable ids).
- **Lost hand edits:** re-running `clipper.py` wiped titles, hooks and cuts; it now writes only `candidates.json`.
- **Cache writes:**
  - they are atomic now, where a crash could leave a half-written cache before;
  - a job-id-only cache no longer crashes rendering;
  - a failed provider job no longer blocks re-runs forever.
- **Crop plans:**
  - a plan is now tied to the hash of the wide file and recomputed when it changes;
  - its values are validated before they reach ffmpeg;
  - an unreadable video gives a clear error instead of dividing by zero;
  - portrait sources get a fit layout instead of a negative crop;
  - a two-person shot no longer ping-pongs between faces; it becomes one shot, flagged for a human.
- **Download errors:** yt-dlp exit codes were ignored and its errors silenced; they are now mapped to useful messages.

## 4. Tests added

The suite collects 189 tests (several are parametrized) in 15 modules. Every test from the founder's required list is covered:

| Required test | Where |
|---|---|
| Reordered, deleted and added candidates | `test_project_identity.py` |
| No punctuation, empty transcript | `test_captions_snap.py`, `test_render_project.py` |
| Silent audio | `test_media_ffmpeg.py`, `test_render_project.py` |
| No faces, multiple faces | `test_media_reframe.py`. Uses synthetic media plus the local `one_face` / `two_faces` fixtures. |
| Invalid source URL, argument-looking input | `test_sources_youtube.py`: 27 invalid inputs, with proof that no process started, plus the exact argv yt-dlp received |
| yt-dlp timeout | `test_sources_youtube.py`, using the fake yt-dlp |
| ffmpeg timeout | `test_media_ffmpeg.py` |
| Missing Armenian font | `test_captions_fonts.py` |
| Newline hook, long hook | `test_captions_ass.py`, `test_render_project.py` |
| Key missing, with and without a cache | `test_transcription_service.py`, `test_transcription_cli.py` |
| Cached exact artifact; logical match with a hash mismatch | `test_transcription_service.py` |
| Ambiguous Harmar submission | Five variants: 500 that creates a job, 503, connection reset, hang past the timeout, malformed body |
| Crash simulation around persistence | Real subprocesses killed at three points, then rerun. Also two concurrent processes, which submit once. |
| Preview/render alignment mismatch | `test_media_alignment.py`: shifted copy, unrelated audio, silent audio without provenance |
| Additional | Insufficient balance; connection refused; 422/429; consent missing or revoked; no confirmation; all three budgets; window cap; the real host opening no socket; reconcile flows; render retry after failure; portrait source; caption overflow; duration checks; pilot-03 regression; guard tests |

## 5. Test results

```
.venv/bin/python -m pytest -q   ->   189 passed in 132.70s   (2026-10-01, M2, offline)
```

**Tests seen failing first** (each module was written before its code):
- The modules failed at collection until their code existed.
- The legacy CLI tests failed against the old scripts, which returned 0 on a legacy layout and printed tracebacks.
- The no-punctuation snap failed against the HEAD version with the old `ValueError`.

**Mutation checks** (code broken on purpose, then restored):
- Treating a 5xx as a rejection failed 3 tests.
- Skipping the byte re-check failed the changed-bytes test.
- Disabling stale-SUBMITTING detection failed 3 tests.

**pilot-03 regression**, re-rendered through the real CLI in the migrated `pilot-03/` with no key set:

| Check | Result |
|---|---|
| ASS caption files (9) | **byte-identical** to the baseline |
| Crop plans | identical |
| Trims | identical: 4.885–56.159, 5.157–64.043, 6.497–56.307 |
| Durations / size | 51.28 / 58.90 / 49.82 s, all 720x1280 |
| Audio | 48 kHz stereo AAC |
| Integrated loudness | −15.0 / −14.8 / −14.9 LUFS, the same as the baseline |
| Alignment | proven by audio cross-correlation for all three clips: lag 0 ms, confidence 1.0 |
| Harmar | `harmar_clips.py pilot-03` reported reuse ×3, 0 new seconds, exit 0 |

## 6. CLI compatibility notes

- **Run with `.venv/bin/python`.** System Python lacks numpy and OpenCV.
- **Pilots must be in the new layout.** The old layout gives a clear "run migrate" error. Pilots 01–03 are already migrated.
- **`clipper.py` produces candidates only:**
  - `--srt --duration --out [--source-url]`
  - the local-video modes (`--harmar` on a whole local video, `--transcribe`, local rendering) were removed, because `--harmar` bypassed every paid safeguard
  - the operator now runs `python -m hayclips select`
- **`fetch_clips.py`** writes `clips/<id>/{wide.mp4, audio.m4a, window.json, youtube.srt}`. New clips no longer get a letterboxed `preview.mp4`.
- **`harmar_clips.py`** is plan-only by default. A paid run needs `--confirm-paid --by NAME`, `HAYCLIPS_ALLOW_PAID_HARMAR=1` and a key. Exit codes: 0 ok, 2 blocked/error, 3 needs reconciliation.
- **`burn_captions.py`** keeps the same flags. Outputs moved to `clips/<id>/render/{A,B,C}.mp4`. It now exits 1 if any clip failed or any CHECK was printed: pilot-03 exits 1 because of the known two-face shot in clip 2, and pilot-01 because its first captions start later than 0.3 s.

## 7. Paid-operation guarantees

1. **Nothing is charged without all of these:** a consent record for harmar/selected_windows, an operator confirmation, the opt-in variable, a key, budgets (600 s per operation, 600 s per project, 1200 s per 24 h, 180 s per window, all configurable), and a balance of at least 110% of the need. Every check runs before any network call.
2. **Each step is saved before the network call that follows it.** SUBMITTING is saved before the charging POST, and the job id immediately after the response.
3. **An unclear outcome is never retried automatically.** A 5xx, timeout, reset, malformed reply or crash during SUBMITTING becomes UNKNOWN_SUBMISSION. It blocks new submissions account-wide until `python -m hayclips reconcile` records either the job id from the dashboard or verified non-creation.
4. **A transcript is valid only for the exact bytes it was made from.** If the bytes changed, or another attempt has the same logical key but a different hash, the run stops with ArtifactMismatch: nothing is reused and nothing is submitted.
5. **One submission at a time per account,** enforced by a file lock, so concurrent runs submit once.
6. **The real host needs the opt-in.** `HarmarClient` refuses `api.harmar.ai` without it and any other non-loopback host always. The test suite strips the key and opt-in, blocks non-local sockets, and refuses to start if the opt-in is set.
7. **Paid records are kept.** Nothing in Phase 1a deletes media, transcripts, job ids, attempt records or hashes.

## 8. Unresolved risks

| Risk | Note |
|---|---|
| **The new paid path has never run live** | Audio-only M4A is accepted per the Harmar docs but untested. It needs one founder-authorised paid test of about 60 s. |
| **429 assumption** | A 429 on submit is assumed to create no job. Unverified; ask Harmar support. |
| **Real yt-dlp untested** | The real yt-dlp download path (formats, error text, section accuracy) is tested only against the fake. A live fetch of one window is needed (free). |
| **Duplicate charge line in ledger** | Two processes polling the same job could both write a "charge" line. It is informational only; budgets use the attempt records. |
| **Provider-failure bookkeeping** | After a provider failure, `seconds_charged` is stored as charged − refunded, which may differ from Harmar's report. |
| **Provenance alignment fallback** | Provenance alignment needs durations equal within 40 ms. AAC padding can push it to audio cross-correlation, which still works when the clip has sound. |
| **Two-person shots** | Only flagged, not reframed. |
| **Caption overflow** | The shrink for single words over 22 or 30 characters is estimated, not visually reviewed. |
| **Creator stills in the public repo** | `c480db0` (already on GitHub) contains creator-derived stills and caption text in `research/caption-samples/`. It is now ignored going forward but still in the history. **Founder decision.** |
| **No override for unrecorded files** | Recovering an unrecorded media file needs manual investigation; there is no override command. |

## 9. Intentional behaviour changes

1. Harmar receives audio-only `audio.m4a` for new clips. Legacy pilots keep their preview-video transcripts, verified by hash.
2. The paid run is plan-only unless explicitly confirmed and opted in.
3. The unguarded whole-video `--harmar` mode of `clipper.py` was removed.
4. Clip artifacts moved to `clips/<id>/`, and display order is separate from identity.
5. `burn_captions.py` exits non-zero on failed clips or CHECK warnings.
6. Rendering refuses to start without the Armenian font, and refuses a clip whose timeline can't be proven aligned.
7. A hook over 45 characters fails that clip instead of rendering.
8. Loudness is measured on the render source (`wide.mp4`) instead of the preview. The LUFS figures are unchanged.

## 10. Recommended Phase 1b plan (not started)

1. **Do two live checks first**, with founder approval:
   - one real `fetch_clips.py` window (free);
   - one paid audio-only Harmar job of about 60 s on a consented clip, which proves the M4A path and the state machine end to end.
2. **Postgres `Repository`** implementing the `ProjectRepo` operations, plus an importer for `project.json` and the attempt records. Unique index on `(provider, media_sha256, options)` per clip.
3. **Job table and worker loop** (io / cpu / paid pools, leases, reaper) that call the same package functions. The paid pool stays single-flight.
4. **FastAPI + Jinja/HTMX local app, localhost only:**
   - new project from a URL → candidates with score explanations → select;
   - a consent screen and a cost confirmation screen (estimate, balance, budgets);
   - transcribe → render A;
   - a review page with A/B/C on demand, hook/trim edit and re-render, and a "would post / minutes to fix" form;
   - downloads.
5. **Keep the CLI working** against the same database through the repository interface. Re-run the pilot-03 regression after every step.
