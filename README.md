# HayClips — experimental Armenian podcast clipper

A local operator tool that turns a long Armenian conversation (podcast, talk show, stand-up)
into a few vertical 9:16 shorts with burned-in Armenian captions, for a human to review.

It **does not** post anything, email creators, predict virality or guarantee accurate transcription.
Every clip, caption and hook needs human review before anyone publishes it (see `CLAUDE.md`).

## What it does

```
YouTube auto-captions (free)
  -> clipper.py          candidate windows on sentence edges, with an explained heuristic score -> candidates.json
  -> python -m hayclips  operator selects 2-3 candidates, writes titles/hooks, records creator consent -> project.json
  -> fetch_clips.py      downloads only those windows (+5 s padding) at <=1080p, extracts audio.m4a
  -> harmar_clips.py     paid Harmar transcription of the audio only (word timestamps); plan-only unless confirmed
  -> burn_captions.py    re-snaps cuts to sentences, checks the caption/render timeline, speaker crop,
                         loudness -14 LUFS, caption styles A/B/C, hook, review.html + output checks
  -> human review        which clip/style would you post, and how long would fixing it take?
```

The full episode is never downloaded or sent to Harmar: only the selected windows.

## Setup

```bash
brew install ffmpeg yt-dlp fontconfig
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # numpy, OpenCV, pytest
.venv/bin/python -m pytest -q                                  # offline; cannot spend money
```

- Run everything with `.venv/bin/python` (one environment for all steps, including face detection).
- Captions need the **Noto Sans Armenian** font (Black and SemiBold). Rendering refuses to start if
  fontconfig cannot find it, instead of rendering empty boxes. `HAYCLIPS_FONTS_DIR` points at a font folder.
- Harmar key: keep it outside the repo, e.g. `~/.config/harmar/key` (chmod 600), and pass it per command.
  Keys start with `hk_live_`; a `whsec_` value is a webhook secret, not an API key.

## Running a pilot

Only with a video whose creator agreed to processing and to sending clip windows to Harmar.

```bash
P=pilot-04; URL='https://www.youtube.com/watch?v=...'
# 1. free captions + candidates
yt-dlp --ignore-config --skip-download --write-auto-subs --sub-langs hy-orig --sub-format srt -o $P/source/src -- "$URL"
.venv/bin/python clipper.py --srt $P/source/src.hy-orig.srt --duration 5400 --source-url "$URL" \
  --skip-start 75 --skip-end 120 --count 6 --out $P
# 2. choose clips (prints the new stable clip id) and record consent
.venv/bin/python -m hayclips select $P cand_xxxxxxxxxx --title "..." --hook "..." --pick-note "..."
.venv/bin/python -m hayclips consent $P --granted-by "creator name/channel" --statement "how and when they agreed"
# 3. download only those windows
.venv/bin/python fetch_clips.py $P
# 4. transcription: first a free plan, then the paid run
.venv/bin/python harmar_clips.py $P
HARMAR_API_KEY="$(cat ~/.config/harmar/key)" HAYCLIPS_ALLOW_PAID_HARMAR=1 \
  .venv/bin/python harmar_clips.py $P --confirm-paid --by "founder"
# 5. render and review (free to repeat)
.venv/bin/python burn_captions.py $P --styles A,B,C
open $P/review.html
```

Other operator commands (`.venv/bin/python -m hayclips ...`):
- `status P`: clips in display order, window, transcript state, renders, consent.
- `edit P CLIP --title/--hook/--pick-note/--order/--selected yes|no/--trim START:END/--auto-trim`
- `reconcile P CLIP --show | --attach-job-id JOB --by NAME | --not-created --by NAME --evidence TEXT`
- `migrate P`: convert an old `suggestions.json` pilot (already done for pilots 01–03).

## Project layout and who owns what

| Path | Owner | Contents |
|---|---|---|
| `project.json` | operator commands only | source, consent records, clips: stable id, display order, title, hook, pick note, planned window, manual trim |
| `candidates.json` | `clipper.py` | machine candidates with score features. Regenerating never touches `project.json` |
| `clips/<clip_id>/window.json` | `fetch_clips.py` | source range, padding, sha256 of `wide.mp4` / `audio.m4a` (or legacy `preview.mp4`) |
| `clips/<clip_id>/transcription/` | `harmar_clips.py` | one record per paid attempt (state, exact media sha256, job id, history) + raw results. **Never delete** |
| `clips/<clip_id>/crop.json` | renderer | per-shot crop plan, tied to the `wide.mp4` hash. Edit a shot's `x` to reframe |
| `clips/<clip_id>/render/` | `burn_captions.py` | `A/B/C.mp4` (final clips), `.ass`, `captions.srt`, `render.json` (cut, alignment, checks) |
| `review.html` | `burn_captions.py` | review page in display order |
| `legacy/` | migration | original `suggestions.json`, Harmar caches and review page of migrated pilots |

Clip ids (`clp_…`) never change. Reordering, deleting or adding clips cannot attach one clip's
transcript, crop, captions or render to another.

## Paid-operation rules (enforced in code)

- A new Harmar submission needs **all** of: a consent record, `--confirm-paid --by NAME`,
  `HAYCLIPS_ALLOW_PAID_HARMAR=1`, a key, a balance of ≥110% of the need, and the budgets:
  600 s per operation, 600 s per project, 1200 s per 24 h, 180 s per window. All are configurable with
  `HAYCLIPS_*` environment variables (`hayclips/config.py`).
- A transcript is reused only for the **exact bytes** it was made from (sha256). If the bytes changed, the run stops for investigation.
- Every step is saved before the next network call. If Harmar might have accepted a submission but we never got
  the job id (timeout, reset, 5xx, malformed reply, crash), the clip goes to **reconciliation**. Nothing is resubmitted
  automatically: check the Harmar dashboard and use `python -m hayclips reconcile`.
- The test suite blocks all non-local network traffic and refuses to run with the paid opt-in set.

## Caption styles and placement

Defined in `research/caption-style.md`:

- **A · active word** (default): 2–4 words, the spoken word turns yellow with a small pop.
- **B · one-word punch**: 1–2 big words.
- **C · karaoke box**: a short line in a translucent box.

All styles use Noto Sans Armenian in mixed case (no `.upper()`: Python turns `և` into `ԵՒ`). Captions are
centred; their bottom edge is at y=930 of 1280 (`--caption-bottom`), below the speaker's chin and above the
TikTok/Shorts UI. Use `--caption-bottom 830` for Meta ads. Hooks are limited to 45 characters. Transcript and hook
text cannot inject caption formatting. There is no emoji (libass cannot render colour emoji).

## Status (2026-10-01)

| Pilot | Source | State |
|---|---|---|
| `pilot-01` | stand-up show episode | migrated; consent recorded; segment timestamps; letterbox (no landscape window) |
| `pilot-02` | podcast episode | migrated; **no consent record** (cannot be sent to Harmar again); letterbox |
| `pilot-03` | podcast episode (90 min) | migrated; consent recorded; word timestamps; speaker crop; **regression reference** |

Harmar trial balance after pilot-03: 133 s. Phase 1a details: `docs/saas/phase-1a-report.md`.

## Known limitations

- Clip selection is a text heuristic. Its word lists (weak openers, fillers) were guessed and need checking by a native editor.
- Harmar writes Russian words in Cyrillic (вообще, гаишник). Decide with the creator whether to transliterate them.
- The speaker crop is static per camera shot (multicam studio shows). Two-person shots are only flagged.
- The audio-only Harmar path and the real yt-dlp download path are tested against fakes; neither has had a live run yet.
- Not built: browser app, uploads, login, billing, silence tightening, teasers, music.
- Captions and cuts are **unreviewed machine output** until a human signs them off.
