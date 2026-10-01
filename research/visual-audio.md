# Visual and audio form for vertical podcast clips (HayClips research)

Author: research-visual teammate. Date: 2026-09-27. Scope: framing, motion, transitions, sound, and
what commercial clippers do. Everything here runs locally on an Apple M2 with 8 GB RAM. Nothing
touches Harmar or any paid API. No project code was edited (`clipper.py`, `burn_captions.py` and
`pilot-*` are unchanged; the only input used was a copy of `pilot-02/clip_02.mp4`).

Evidence labels:
- **[official]**: vendor or standards documentation.
- **[measured]**: I measured it on the pilot clip.
- **[study]**: a peer-reviewed paper.
- **[folklore]**: blogs, third-party guides and creator lore. Useful, but unverified.

---

## 0. What the pilot clip actually is (measured)

This changes the framing advice, so it comes first.

- **The source is already edited multicam, not one wide shot.** A contact sheet and shot detection
  of `clip_02` show single-person medium shots. The host sits in the left third and the guest is on
  the right. There are 9 camera cuts in 66 s: 26.12, 27.10, 52.54, 53.34, 54.52, 55.08, 62.08, 63.86
  and 65.62 s. FFmpeg `scdet` and my OpenCV histogram detector found the same times. [measured]
- **Camera shot does not equal active speaker.** In Harmar's `segments` for this clip, `speaker` is
  1 everywhere except 62.66–63.42 s (speaker 2 says "Վերաբերմունքը"). The cuts to the guest at 26.1,
  52.5 and 54.5 s are *reaction shots*, taken while the host is still talking. The show's editor
  already made the "who to show" decision. [measured]
- **In our current letterbox output the face is only ~60 px wide on a 720 px canvas.** It is ~200 px
  with a speaker crop. See `research/visual-samples/compare_t23_current_crop_punchin_reaction.jpg`.
  From left to right it shows the current output, the static speaker crop, the 1.15x punch-in and a
  reaction shot. [measured]
- The loudness of the current output is **−23.5 LUFS integrated, −4.6 dBFS peak**. That is about
  9.5 LU quieter than the common −14 target. On phones that apply no normalization upward, the clip
  sounds noticeably quieter than its neighbours in the feed. [measured]
- The 16:9 picture inside our rendered clip is only 720x405. Every crop from the *rendered* clip is
  therefore upscaled 3.16x and looks soft. **In production, crop from the original ≥1080p source.**
  A 9:16 crop of 1080p is 608x1080, which needs only a 1.18x upscale to 720x1280. The samples here
  are softer than the real pipeline would be. [measured]

## 1. Framing

### 1a. Layout options

| Layout | When it works | Who does it by default |
|---|---|---|
| **Fill / speaker crop** (full-frame 9:16 crop on one face) | Single-person shots or wide shots with ≤3 well-separated people | Opus Clip "Fill" ([help.opus.pro](https://help.opus.pro/docs/article/layout-and-reframing)) [official]; Vizard auto-reframe keeps the active speaker centred ([vizard.ai](https://vizard.ai/tools/ai-reframe)) [official marketing] |
| **Split** (two speakers stacked top and bottom) | Only when both speakers are in the *same* source frame | Opus Clip "Split": "will only work when both speakers appear together in the original video frame" [official]. Also "Three" and "Four" layouts |
| **Fit** (letterbox, which is what we do now) | Slides, screenshares, wide shots where cropping would cut people | Opus Clip "Fit" crops to 4:3 plus padding [official]. We fit full 16:9, which leaves the picture even smaller |
| Descript Automatic Multicam | Needs *separate camera tracks per speaker*; "Show only active speaker" or cutaways every 10 s ("Frequent") or 30 s ("Occasional") | [official](https://help.descript.com/hc/en-us/articles/28736507904525-Automatic-multicam) |

For HayClips' source type (an already-cut multicam podcast), **split screen is not applicable**. The
two people never share a frame. **Fill (a speaker crop) is the clear win.**

For a true single wide shot with 2–3 people, the choice is between two options:
- Fill with switching on the active speaker (needs active-speaker detection).
- Split when two faces are present.

Keep "Fit" as the fallback for any shot where no face is found, and for slides.

### 1b. Detecting where to crop, locally and for free

Options, cheapest first:

1. **Shot detection plus the largest face per shot (implemented, recommended now).**
   - OpenCV `FaceDetectorYN` (YuNet ONNX, 232 KB, from [opencv_zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)).
     An HSV histogram check on every frame finds cuts; faces are found at 5 samples/s. Each shot's
     crop x is the median face centre.
   - Measured cost: **4.4 s wall for a 66 s clip** on the M2 (`pip install opencv-python-headless`,
     ~40 MB). [measured]
   - It uses no ML speaker model and needs no extra download beyond YuNet.
   - The crop jumps only on the source's own camera cuts, so it never adds motion that the editor
     didn't make.
   - Script: `research/visual-samples/facetrack.py`.
2. **Harmar `speakers: true`.** Harmar already returns `speaker` per segment (see
   `pilot-02/harmar/clip_02/harmar_transcript.json`).
   - It costs nothing extra because the transcript is already paid for.
   - It says *who* is talking but not *where* they are. Map speaker ID to face by picking the face
     that is on screen, or the one with lip motion, during that speaker's longest segment.
   - Use it for wide shots with 2–3 people, where it decides which face gets the crop.
   - Segment granularity is 1–8 s, so switches will be coarse. [measured]
3. **Audio diarization with pyannote `speaker-diarization-community-1`.**
   - The weights are CC-BY-4.0 ([HF](https://huggingface.co/pyannote/speaker-diarization-community-1))
     [official], and you need a Hugging Face token to accept the terms.
   - CPU speed is reported as RTF 0.5–1, i.e. 30–60 min per hour of audio
     ([discussion](https://github.com/pyannote/pyannote-audio/discussions/778)). [folklore; that
     figure is for older pipelines]
   - It adds torch (~1 GB+), which is heavy for 8 GB of RAM.
   - It is redundant while Harmar returns speakers.
4. **Audio-visual active speaker detection (Light-ASD / LR-ASD).**
   - Light-ASD: 1.0 M parameters and 0.6 GFLOPs, with 94.1% mAP on AVA-ActiveSpeaker
     ([CVPR 2023](https://openaccess.thecvf.com/content/CVPR2023/html/Liao_A_Light_Weight_Model_for_Active_Speaker_Detection_CVPR_2023_paper.html)).
     [study]
   - LR-ASD: 0.84 M parameters ([repo](https://github.com/Junhua-Liao/LR-ASD)). [study]
   - Both are the right tool for single-camera wide shots where people interrupt each other. They
     are overkill for the first milestone.
5. **MediaPipe face detection or face mesh (lip-distance heuristic).**
   - Free, and fast on CPU.
   - Mouth-open variance per face is a crude active-speaker signal.
   - I did not install it: YuNet already covered detection, and MediaPipe wheels for Python 3.13
     were not checked.

### 1c. Motion between speakers

- **Hard switch, don't pan, when the source cuts.** A smooth pan across a camera cut looks broken.
  [measured on this clip; editorial convention]
- **For a single wide shot, switch speakers with a hard cut** after a minimum hold, so the crop
  doesn't ping-pong:
  - Only switch when the new speaker has talked for at least ~1 s.
  - Keep each crop for at least ~1.5–2 s.
  - These numbers are my proposal and still need a human eye test.

  Opus Clip's ReframeAnything is marketed as "tracks moving speakers" ([opus.pro](https://www.opus.pro/ai-reframe))
  [official marketing]. It gives no public numbers on smoothing.
- **Within a shot, lock the crop (static).** Only use eased tracking if the person leans out of frame:
  - Use an exponential moving average of face x.
  - Add a dead-zone of about ±8% of the crop width, so the frame moves only when the face leaves the
    middle band.
  - Static reads as "camera operator"; jittery tracking reads as "AI".

## 2. Motion: punch-ins and push-ins

What others do:
- Submagic offers "6 unique zoom styles" (fast zoom, crash, smooth, expo, linear…), "synced to your
  spoken audio". It is applied through one-click "Magic B-rolls". It publishes no zoom percentage or
  frequency ([submagic.co](https://www.submagic.co/features/auto-zooms)). [official, no numbers]
- A "visual change every 1.5–2 s" or "a cut every 2–4 s" appears across blogs, e.g.
  [shortzly](https://shortzly.com/blog/short-form-video-pacing-editing-guide) and
  [opus.pro blog](https://www.opus.pro/blog/ideal-youtube-shorts-length-format-retention).
  **[folklore]**: I found no peer-reviewed study or platform data behind these numbers.
- The 110–120% punch-in range is editor convention **[folklore]**. I found no official or study source.

What I recommend for a talking-head podcast:
- **One 1.12–1.18x punch-in per strong line** (a contrast, punchline or number), held until the next
  camera cut or sentence start.
- **A slow push-in of at most ~6% over a long unbroken shot**, so a 20 s static shot doesn't feel
  frozen.
- **Punch-ins at most every ~4–6 s.** This is my editorial proposal, and the samples should be
  judged by the human editor.
- Never punch across a camera cut; reset to 1.0 at each cut.

### FFmpeg implementation (tested)

Use `scale` with `eval=frame` plus a fixed crop, anchored near the eye line. `zoompan` is not used:

```
crop=228:405:'<per-shot x expr>':0,scale=720:1280:flags=lanczos,
scale=w='trunc(720*Z(t)/2)*2':h='trunc(1280*Z(t)/2)*2':eval=frame:flags=bicubic,
crop=720:1280:y='(in_h-out_h)*0.25'
Z(t) = if(between(t,21.764,26.12)+between(t,43.038,45.618), 1.15, if(lt(t,21.764), 1+0.06*t/21.764, 1))
```

- **`y=(in_h-out_h)*0.25` keeps headroom.** Zooming around the frame centre pushed the top of the
  headphones out of frame. Anchoring at 25% height zooms roughly around the eyes. [measured]
- **Hard punch-ins cannot jitter** because they are a single step.
- **Slow push-ins can stair-step,** because the output size and crop offset round to whole pixels.
  The FFmpeg zoompan jitter is documented; a 2020 [ffmpeg-devel patch](https://ffmpeg.org/pipermail/ffmpeg-devel/2020-February/256883.html)
  addresses it [official list]. The common fix is to upscale before zooming
  ([ffmpeg-micro](https://www.ffmpeg-micro.com/blog/ffmpeg-zoompan-filter-ken-burns-zoom-and-pan-without-the-jitter))
  [folklore].
- My push-in is only +6% over 21 s, which is 1 px steps about every 0.5 s at 720p. It needs a human
  eye check; I did not see shake in stills.
- If shake shows, run the zoom stage at 2x (1440x2560) and downscale at the end. That costs roughly
  2–3x more render CPU.
- Emphasis times came from Harmar segment starts. Automating this needs a "strong line" signal, which
  belongs to the clip-selection work.

## 3. Transitions and overlays

- **Don't fade in from black.** The first frame is the hook and often the thumbnail. Use only a
  30 ms audio fade-in to remove clicks. [editorial reasoning, not measured]
- **A short fade-out at the end (~250 ms)** hides an abrupt final cut. It is implemented in sample 04.
- **Smooth jump cuts inside a clip** (when silences or filler are removed) by alternating 1.0x and
  1.12x scale on each side of the cut. This is the standard "punch-in hides the jump cut" trick
  [folklore], and it reuses the scale expression above.
- Whoosh or flash transitions and emoji SFX: Submagic adds "whooshes, dings and impacts on cuts"
  ([submagic.co](https://www.submagic.co/features/transition-editor)) [official]. For a
  conversational Armenian podcast this is a brand risk. **Default off; offer it as an option only if
  a creator asks.**
- **Progress bar:** implemented as an 8 px bar at the bottom of the frame:
  `drawbox=x=0:y=ih-8:w='iw*t/DUR':h=8:color=white@0.85:t=fill`.
  - The retention claims are **[folklore]** (e.g. [subtitlebee](https://subtitlebee.com/blog/8-reasons-why-having-a-progress-bar-in-your-videos-increase-video-watch-retention/)).
  - Reels and Shorts draw their own UI at the bottom, so the bar may be hidden. Test it on a phone
    before shipping; A/B only if a creator cares.
- **Safe zones:** Meta's ads guide for Instagram Reels says to keep key elements out of the top 14%,
  the bottom 35% and 6% on the left and right
  ([Meta ads guide](https://www.facebook.com/business/ads-guide/update/image/instagram-reels))
  [official; verified by fetching the page, which rendered in Russian].
  - Our current ASS captions sit at MarginV 300, i.e. baseline y≈980 of 1280. That is inside the
    bottom 35% band (y ≥ 832).
  - With a speaker crop, the face sits around y 150–450, so captions fit at y ~700–830.
  - Cross-checked with `research-captions`. Their Preset A (MarginV 450, text at y≈790–830) is
    rendered on sample 01 in `research/caption-samples/A_active_word_on_speaker_crop.mp4`.
  - I viewed the t=12 s still: the caption lands over the mic arm and chest, clear of the face and
    the UI zone.
  - Remaining risk: light shirts. The outline or box style in `caption-style.md` §7a handles it.

## 4. Sound

### 4a. Loudness targets

- **None of YouTube, TikTok or Instagram publishes an official LUFS target for uploads.**
  - −14 LUFS is widely measured and repeated for YouTube; Spotify publishes −14 LUFS / −1 dBTP
    ([mrvocal](https://mrvocal.com/posts/loudness-for-shorts),
    [OpusClip blog](https://www.opus.pro/blog/best-loudness-normalizers)). [folklore / measured by third parties]
  - Some guides suggest −10 to −12 LUFS for TikTok and Reels
    ([thepostflow](https://thepostflow.com/post-production/post-production-workflows/export-settings-youtube-instagram-tiktok/)).
    [folklore]
- **Recommendation: −14 LUFS integrated, −1.5 dBTP, LRA 11, two-pass `loudnorm` with `linear=true`.**
  - My first render at TP −1.0 measured **−0.6 dBTP after AAC encoding** (an overshoot).
  - At TP −1.5 it measured **−14.4 LUFS / −1.2 dBTP**. [measured]
- `loudnorm` is EBU R128 based ([FFmpeg docs](https://ffmpeg.org/ffmpeg-filters.html#loudnorm)). [official]

```
# pass 1
ffmpeg -i in.mp4 -af loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json -f null -
# pass 2 (fill measured_* from pass 1 JSON)
-af loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=..:measured_TP=..:measured_LRA=..:measured_thresh=..:offset=..:linear=true
```

The pass-1 JSON must be sliced between the last `{` and `}`, because FFmpeg prints trailing lines
after it (a bug I hit). See `loudnorm_filter()` in `render_samples.py`.

### 4b. Background music and ducking

- **Default: no music.** Seated conversation carries itself. Music adds three problems:
  - Licensing risk.
  - Masking of Armenian consonants, which hurts caption and listener intelligibility.
  - Creator brand concerns.
- Offer it only on request.
- If a creator wants music, use a bed with a sidechain duck (tested):

```
[music]volume=-18dB[m];                      # bed about 18-20 dB under speech before ducking
[m][voice_key]sidechaincompress=threshold=0.01:ratio=20:attack=10:release=600:makeup=1[duck];
[voice][duck]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11
```

- Measured on the pilot voice:
  - My first try (**threshold 0.03, ratio 8**, close to common forum examples) **ducked only
    ~3–5 dB**.
  - **threshold 0.01, ratio 20** gave **−10 dB (speech at 38–41 s) to −17 dB (speech at 10–13 s)**,
    with ~−1 dB in the 36–37 s pause. [measured]
- Apply loudnorm **after** the mix. The first version, which used `alimiter`, peaked at +0.2 dBFS.
  [measured]
- Sample 05 uses a *synthetic sine pad*, because no licensed track is on disk. It demonstrates the
  mechanics only.
- Licence-safe sources:
  - **Pixabay Music:** free for commercial and non-commercial use with no attribution required
    ([license summary](https://pixabay.com/service/license-summary/)) [official]. Some tracks are
    registered in Content ID and can trigger claims [folklore/secondary].
  - **YouTube Audio Library:** standard-licence tracks are limited to YouTube; only CC-BY tracks can
    be used elsewhere, with attribution [secondary summary of YouTube's terms]. Not suitable for
    Reels or TikTok.
  - **Meta Sound Collection:** in-app for Reels, cleared for business accounts
    ([summary](https://tripepismith.com/insights/music-in-reels-business-accounts/)) [secondary]. The
    creator adds it in the Instagram app, so we ship a clean voice-only master.
  - Practical conclusion: **ship voice-only; let the creator add in-app music per platform.** This
    avoids all licensing risk for us.

### 4c. Sound effects

Whooshes on cuts are a Submagic default [official]. **Recommend off** for this genre. If a creator
asks, mix at −20 to −25 dB relative to speech [folklore/editor practice].

### 4d. Noise reduction

- **The pilot is studio audio.** Denoising risks artefacts with no benefit.
  - The pause at 36–37 s sits at ~−25 dB RMS after loudnorm, and that is room tone and breath, not
    hiss. [measured]
- Only add denoising on a per-creator flag for noisy sources:
  - `highpass=f=80,afftdn=nr=12:nf=-40:tn=1` is built in and needs no model. Above ~30 dB of
    reduction it produces "musical" artefacts ([ffmpeg-micro](https://www.ffmpeg-micro.com/blog/ffmpeg-can-remove-background-noise-arnndn-breaks-in-docker))
    [folklore].
  - `arnndn=m=/abs/path/model.rnnn:mix=0.7` needs a ~300 KB model from the community
    rnnoise-models repo and expects 48 kHz audio. It is better on voice [folklore], but untested here.
- Place denoising **before** loudnorm.

## 5. What the commercial tools do by default (summary)

| Tool | Framing | Motion / FX | Source |
|---|---|---|---|
| Opus Clip | Auto layout "as applicable": Fill / Fit / Split / Three / Four / Screenshare; AI reframe tracks the speaker | Captions, optional B-roll; zoom defaults not documented | [official](https://help.opus.pro/docs/article/layout-and-reframing) |
| Vizard | 9:16 with speaker auto-reframe by default; switches between speakers | Animated captions, optional emoji | [official marketing](https://vizard.ai/tools/ai-reframe) plus [review](https://creatify.ai/review/vizard-ai) |
| Submagic | (a caption and effects editor) | Auto zooms (6 styles, speech-synced), transitions, SFX (whoosh, ding, impact), AI B-roll | [official](https://www.submagic.co/features/auto-zooms) |
| Descript | Automatic Multicam from separate speaker tracks; active speaker plus cutaways every 10 or 30 s | Layout packs | [official](https://help.descript.com/hc/en-us/articles/28736507904525-Automatic-multicam) |

On what top podcast-clip channels do, all I have is **[folklore]**. I did no systematic channel audit.
The common pattern is:
- A tight single-speaker crop, or a stacked split for back-and-forth exchanges.
- Big word-level captions in the middle third.
- Occasional punch-ins on key lines.
- No music under serious talk.

A real audit of 10–20 Armenian and diaspora podcast Reels would be more useful than more web
reading. It could be a creator-research task.

---

## Prioritized recommendations

1. **Per-shot speaker crop (Fill) instead of the blurred letterbox.**
   - Biggest visual gain: the face goes from ~60 px to ~200 px wide.
   - Implementation: shot detection plus the YuNet face median per shot, then
     `crop=<w>:<h>:'if(lt(t,c1),x0,if(lt(t,c2),x1,...))':0` and `scale=720:1280`.
   - Fall back to the current Fit layout when a shot has no face, or more than 2 faces spread wider
     than the crop.
   - Crop from the original source, not a rendered clip.
   - Cost: ~5 s CPU per minute of video.
2. **Two-pass `loudnorm` to −14 LUFS / −1.5 dBTP on every export.**
   - It is one extra fast analysis pass. The current clip is −23.5 LUFS.
   - Combine it with a 30 ms audio fade-in and a 250 ms fade-out.
3. **Sparse punch-ins (1.15x, eye-line anchored) on emphasis lines, plus an optional ≤6% slow
   push-in on shots longer than ~10 s.**
   - Use the `scale eval=frame` + `crop y=(in_h-out_h)*0.25` chain above, reset at camera cuts.
   - Start with manual or Harmar-segment-based emphasis times and let the editor judge.
4. **Keep the source's own cuts; use Harmar `speaker` IDs only for single-wide-shot sources.**
   - Hard switches, a minimum 1.5–2 s hold, no smooth pans.
   - Add Light-ASD only if wide-shot creators appear.
5. **Voice-only master: no music, SFX, whooshes or progress bar by default.**
   - Offer the progress bar and a ducked music bed (`sidechaincompress threshold=0.01 ratio=20`,
     then loudnorm) as opt-in only after a creator asks.
   - Let creators add licensed music in the platform apps.
   - Keep denoise (`afftdn`/`arnndn`) as a per-source flag, off for studio audio.

## Samples

All samples are in `research/visual-samples/`: 720x1280, 66.30 s, H.264 CRF 20, AAC 160k at 48 kHz.
The render takes ~70 s for all five on the M2.

| File | What it is | Loudness (measured) |
|---|---|---|
| `01_speaker_crop_static.mp4` | Per-shot face crop, switched at the 9 camera cuts | −23.5 LUFS (unchanged) |
| `02_speaker_crop_punchin_115.mp4` | Speaker crop, +6% push-in for 0–21.8 s, 1.15x punch-ins at 21.76–26.12 s and 43.04–45.62 s | −23.5 LUFS |
| `03_letterbox_loudnorm_-14.mp4` | Current layout, audio two-pass loudnorm only | −14.4 LUFS, −1.2 dBTP |
| `04_combined_crop_zoom_bar_loudnorm.mp4` | Crop, zooms, progress bar, loudnorm and fades | −14.4 LUFS, −1.2 dBTP |
| `05_music_ducking_demo.mp4` | Letterbox, loudnorm voice and a ducked synthetic pad (mechanics demo) | −14.2 LUFS, −1.2 dBTP |
| `compare_t23_current_crop_punchin_reaction.jpg` | Stills: current / crop / punch-in / reaction shot | — |
| `facetrack.py`, `crop_x_expr.txt`, `render_samples.py` | Reproduce: `venv/bin/python facetrack.py clip.mp4 0 437 720 405 yunet.onnx > crop_x_expr.txt; python3 render_samples.py crop_x_expr.txt` | — |
| `clip_02_original_copy.mp4` | Untouched copy of the pilot input | −23.5 LUFS |

Still open and needing human review:
- Whether the push-in stair-steps on a phone screen.
- Whether 1.15x feels too aggressive for this creator.
- Whether the progress bar is hidden by Reels UI.
- Softness: the samples are upscaled 3.16x from the rendered clip's 720x405 band.

The YuNet model and the OpenCV venv live in the session scratchpad, not the repo. Re-download
`face_detection_yunet_2023mar.onnx` from opencv_zoo to reproduce.
