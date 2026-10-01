# HayClips — experimental Armenian podcast clipper

A local operator prototype that turns a long Armenian conversation (podcast, talk show, stand-up)
into a few vertical 9:16 shorts with burned-in Armenian captions, for a human to review.

It **does not** post anything, email creators, predict virality or guarantee accurate transcription.
Every clip, caption and hook needs human review before anyone publishes it (see `CLAUDE.md`).

## What it does

```
YouTube auto-captions (free)
  -> clipper.py        picks candidate windows on sentence edges (heuristic score)
  -> operator          keeps 2-3 windows, writes titles and optional hooks in suggestions.json
  -> fetch_clips.py    downloads only those windows (+5 s padding) at 1080p
  -> harmar_clips.py   paid Harmar transcription of the clips only (word timestamps, cached)
  -> burn_captions.py  re-snaps cuts to Harmar sentences, speaker crop (reframe.py),
                       loudness -14 LUFS, caption styles A/B/C, hook, review.html + checks
  -> human review      which clip/style would you post, and how long would fixing it take?
```

The full episode is never downloaded or sent to Harmar. Only the picked windows are (about 3 min
per pilot), which keeps the cost low and fits the Harmar trial.

## Files

| File | Role |
|---|---|
| `clipper.py` | Core library + CLI: SRT loading (fixes rolling YouTube cues), sentence units, window scoring, Harmar API client with caching, letterbox export. Also runs standalone on a local video (`--srt`, `--harmar`, `--transcribe`). |
| `fetch_clips.py` | Downloads the chosen windows with `yt-dlp`, keeps `clip_NN.wide.mp4` (landscape) and renders `clip_NN.mp4` (letterboxed preview; this is the file sent to Harmar). |
| `harmar_clips.py` | Sends each `clip_NN.mp4` to Harmar once, with a balance check up front; results go to `harmar/clip_NN/`. |
| `reframe.py` | Runs in `.venv` (OpenCV). Detects camera cuts and the speaker's face, then plans one static 9:16 crop per shot. |
| `burn_captions.py` | Final render: sentence re-snap, crop, captions, hook, loudnorm, `review.html`, `ffprobe` checks. |
| `models/face_detection_yunet_2023mar.onnx` | YuNet face detector (OpenCV Zoo), 232 KB. |
| `research/` | `caption-style.md` (caption styles, safe zones, height), `cutting-retention.md` (where to cut), `visual-audio.md` (framing, loudness), plus samples. Evidence labels separate platform docs from folklore. |
| `outreach-hy.md` | Draft creator outreach message. It is never sent automatically. |
| `START-IN-CLAUDE-CODE.md`, `.claude/agents/` | Four-teammate Claude Code setup (harmar-api, clip-engine, quality-review, creator-research). |

## Setup

- macOS with Python 3.10+, `brew install ffmpeg yt-dlp`.
- Captions use the **Noto Sans Armenian** font (preinstalled on macOS; on Linux install it, or libass shows boxes).
- Speaker crop: `python3 -m venv .venv && .venv/bin/pip install opencv-python-headless numpy`.
  Without it, clips fall back to a blurred letterbox.
- Optional local speech recognition: `.venv/bin/pip install faster-whisper` (`clipper.py --transcribe`).
- Harmar key: keep it in a file outside the repo, e.g. `~/.config/harmar/key` (chmod 600), and pass it
  per command as `HARMAR_API_KEY="$(cat ~/.config/harmar/key)"`. Never commit it or paste it into chat.
  Keys start with `hk_live_`. A `whsec_` value is a webhook secret, not an API key.

## Running a pilot (current flow)

Only do this with a video the creator has agreed to let us process and send to Harmar.

```bash
P=pilot-04; URL='https://www.youtube.com/watch?v=...'
# 1. free captions + candidate windows
yt-dlp --skip-download --write-auto-subs --sub-langs hy-orig --sub-format srt -o $P/source/src "$URL"
yt-dlp --skip-download --print duration "$URL"          # use as --duration
python3 clipper.py --srt $P/source/src.hy-orig.srt --no-render --duration 5400 \
  --skip-start 75 --skip-end 120 --count 6 --out $P
# 2. edit $P/suggestions.json by hand (see fields below): keep 2-3, add source/title/hook/pick_note
# 3. download only those windows
python3 fetch_clips.py $P
# 4. paid transcription of the clips (checks balance, never resubmits a cached job)
HARMAR_API_KEY="$(cat ~/.config/harmar/key)" python3 harmar_clips.py $P
# 5. render and review
python3 burn_captions.py $P --styles A,B,C
open $P/review.html
```

`--skip-start` / `--skip-end` skip a cold-open teaser and the outro. Re-running `burn_captions.py` costs
nothing and is the way to apply manual fixes.

### `suggestions.json` fields (one object per clip, in order clip_01, clip_02, ...)

| Field | Who sets it | Meaning |
|---|---|---|
| `start`, `end` | clipper / you | Planned window in source seconds |
| `text`, `score` | clipper | YouTube-caption text and heuristic score (not a virality prediction) |
| `source` | you | YouTube URL (required by `fetch_clips.py`) |
| `title` | you | Label on the review page |
| `hook` | you (founder approves) | ≤45 characters, shown at the top for the first 3 s. It must quote the speaker faithfully. `--no-hook` hides it |
| `pick_note` | you | Why this window was chosen or changed |
| `pad`, `pad_start` | fetch_clips | Padding downloaded around the window |
| `caption_start`, `caption_end`, `snap` | burn_captions / you | Final trim inside the padded clip. To fix a cut, edit these and set `"snap": "manual"` |
| `edits`, `transcript_source` | burn_captions | Audit trail: source times, trim, hook, audio, framing, snap notes |

### Output files per clip

| File | What it is |
|---|---|
| `clip_NN_A.mp4`, `_B.mp4`, `_C.mp4` | **Final vertical clips** in the three caption styles (720x1280) |
| `clip_NN.mp4` | Uncut padded preview, letterboxed. It is the file Harmar transcribed; do not re-render it, because the cache is keyed to its hash |
| `clip_NN.wide.mp4` | Uncut padded landscape window, used for the speaker crop |
| `clip_NN.crop.json` | Crop plan: one `x` per camera shot. Edit `x` to reframe a shot, then re-run |
| `clip_NN.srt` | Harmar captions for the final cut, for uploading as platform captions |
| `clip_NN.youtube.srt` | Free YouTube auto-captions for comparison |
| `clip_NN_<style>.ass` | Caption script burned into the video |
| `harmar/clip_NN/harmar_transcript.json` | Cached Harmar job and result. Never delete it: deleting means paying again |
| `review.html` | Review page: all styles side by side, uncut version, transcript, first-3-s text, framing notes |

`burn_captions.py` prints `CHECK:` lines for anything a human should look at: a wrong size or
duration, captions past the end, late first words, and shots with two faces or no face.

## Caption styles and placement

Defined in `research/caption-style.md`:

- **A · active word** (default): 2–4 words, the spoken word turns yellow with a small pop.
- **B · one-word punch**: 1–2 big words.
- **C · karaoke box**: a short line in a translucent box.

All styles use Noto Sans Armenian in mixed case. `.upper()` is never used, because Python turns `և` into `ԵՒ`.
Captions are centred horizontally. The text's bottom edge is at y=930 of 1280 (`--caption-bottom`), about 100 px
below the speaker's chin and above the TikTok/Shorts bottom UI. Use `--caption-bottom 830` for Meta ads.
There is no emoji, because libass cannot render colour emoji.

## Cost and safety rules

- Harmar is billed per second when a transcript is submitted. `harmar_clips.py` checks `/v1/balance` first and
  caches the job ID before polling, so a retry never charges twice.
- Before sending anything, confirm the creator's consent for Harmar.
- Nothing is published or emailed. The founder reviews every sample and message.
- Delete large downloads you no longer need. The pipeline keeps only per-clip windows.

## Status (2026-09-30)

| Pilot | Source | State |
|---|---|---|
| `pilot-01` | pilot-01 show (stand-up) | 3 clips, Harmar segment timestamps, old letterbox style |
| `pilot-02` | Podcast, pilot02vid0 | 3 clips, segment timestamps, old style |
| `pilot-03` | pilot-03 podcast | 3 clips, word timestamps, speaker crop, styles A/B/C. **Current reference** |

Harmar trial balance after pilot-03: **133 s**, which is not enough for another 3-clip pilot.
Pilots 01–02 can be re-rendered with the new captions, but they have no landscape windows (letterbox) and no padding (no re-snap).

## Known limitations

- Clip selection is a text heuristic. Its word lists (weak openers, fillers) were guessed and need checking by a native editor.
  Pre-Harmar snapping depends on YouTube's punctuation, which is patchy.
- Harmar writes Russian words in Cyrillic (вообще, гаишник). Decide with the creator whether to transliterate them.
- The speaker crop is static per camera shot, so it suits multicam studio shows. Single wide shots with 2–3
  people need active-speaker detection, which is not built.
- Not built: silence tightening, cold-open teasers, music, a hosted service, login or billing (out of scope until a
  creator confirms the output is useful).
- Captions and cuts are **unreviewed machine output** until a human signs them off.
