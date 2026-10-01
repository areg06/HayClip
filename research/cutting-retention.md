# Cutting podcast clips for retention: research and changes for HayClips

Researched 2026-09-27 for the HayClips local prototype. Scope: how short clips taken from
long conversations are *cut* (start, end, pacing, hooks) so that viewers keep watching,
and what we can change in `clipper.py` using only local Python and FFmpeg.

Nothing here predicts that a clip will perform well. Every recommendation below still
ends in human review (CLAUDE.md). Any retention effect has to be measured on the
creator's own analytics once the creator publishes something themselves.

## Evidence labels

| Label | Meaning |
|---|---|
| **[OFFICIAL]** | Stated by the platform in its own help, blog, newsroom or creator FAQ |
| **[STUDY]** | Peer-reviewed or published study with a stated method |
| **[VENDOR]** | Documentation or marketing from a clipping-tool company (describes their product, not proof that it works) |
| **[FOLKLORE]** | Creator or blog advice, or statistics I could not trace to a primary source. Treat as a hypothesis |
| **[LOCAL]** | Measured on our own `pilot-01/` and `pilot-02/` outputs |

---

## 1. What the platforms themselves say

### Retention, completion and loops

- **[OFFICIAL] TikTok:** in TikTok's words, "a strong indicator of interest, such as whether a user finishes
  watching a longer video from beginning to end, would receive greater weight than a weak
  indicator". Watching to the end is the one watch-time signal TikTok names.
  https://newsroom.tiktok.com/en-us/how-tiktok-recommends-videos-for-you
- **[OFFICIAL] Instagram:** Reels ranking predicts "how likely you are to reshare a reel, watch a
  reel all the way through, like it, and go to the audio page". Instagram also says it
  makes some reels less visible, including "low-resolution or watermarked reels, reels that are muted or contain borders,
  reels that are majority text".
  https://about.instagram.com/blog/announcements/instagram-ranking-explained
  - This matters for us: our 720x1280 layout (full 16:9 picture over a blurred copy of itself) is not a
    solid-colour border, but Instagram does not define "borders". **Open question:** does the
    blurred-letterbox look get treated as a border? We cannot answer this without platform data. Worth
    comparing later against a crop-to-speaker version.
- **[OFFICIAL] Instagram creator FAQ:** "Make sure the first 3 seconds of your reel are engaging, so that
  people don't move on." "We recommend videos to unconnected audiences that are 3 minutes or
  less." "Ditch the watermark!" "Upload the highest resolution possible."
  https://creators.instagram.com/faq?locale=en_US
- **[OFFICIAL] YouTube:** since the 31 March 2025 change, a Shorts view counts every time a Short starts or replays. The older
  "engaged views" metric is still used for revenue sharing. This is reported consistently by
  Sprout Social, TubeBuddy and others. I could not load YouTube's own page from here, so check it
  before quoting it.
  https://www.tubebuddy.com/blog/youtube-shorts-view-count-update-what-creators-need-to-know-about-the-new-metrics/
- **[OFFICIAL] YouTube Studio** shows Shorts creators a **"Viewed vs. swiped away"** metric, which is the share
  of feed impressions that were watched rather than skipped. This measures the opening directly.
  https://support.google.com/youtube/community-video/273390203/new-youtube-shorts-metric-viewed-vs-swiped-away?hl=en
- **[OFFICIAL] YouTube retention help:** "the absolute views for a segment can exceed your video's overall view
  count" because a viewer may rewatch parts within one view. Loops therefore show up as retention
  above 100%. https://support.google.com/youtube/answer/9314415

### Length limits (not targets)

- **[OFFICIAL]** YouTube Shorts can be up to **3 min** for uploads since 15 Oct 2024.
  https://blog.youtube/news-and-events/tall-updates-coming-to-shorts/
- **[OFFICIAL]** Instagram Reels can be up to **3 min** since Jan 2025 (Mosseri: "help you tell the stories that you really
  want to tell"). Before that, @instagramforbusiness said that only videos of 90 s or less are recommended to
  unconnected audiences and that "the sweet spot for engagement is with reels between 30-90 seconds".
  That guidance is superseded but still useful as a historical hint.
  https://www.medianama.com/2025/01/223-instagram-reels-3-minutes-us-tiktok-ban/
- **[FOLKLORE]** "TikTok Creator Center says 21–34 s gets the most engagement". Many blogs repeat this
  (e.g. https://www.teleprompter.com/blog/how-long-should-a-tiktok-video-be), but I could not
  find the primary TikTok source. Do not cite it as official.
- **[FOLKLORE]** Advice by genre: comedy 15–30 s, and stories or explainers 60 s or more. It sounds reasonable, but I found no data behind it.

**Takeaway:** the platforms allow up to 3 minutes and reward watching *to the end* and rewatching.
No official source gives an ideal length. A shorter clip that viewers finish usually beats a longer
one they abandon. Our 25–66 s range is reasonable. The real question is whether each second earns
its place.

### Swipe behaviour

- **[STUDY] Dashlet (NSDI 2024; arXiv 2204.12954):** a user study (258 MTurk users plus a college-campus
  group) on a TikTok-like player. "29% and 42% of swipes from MTurk users are within the first
  20% or last 20% of videos, respectively." Swipes in the middle are much rarer, and each video's
  swipe profile was stable across the two user groups. So the losses sit at the **opening**
  (the hook fails) and at the **ending** (the viewer is done, or the end drags). The middle matters less.
  https://arxiv.org/pdf/2204.12954
- **[FOLKLORE]** "Users decide in 1.7 s" and "65%+ 3-second retention gets 4–7x impressions".
  Blogs attribute these to Meta or internal data without links. I could not verify them, so don't use them.

---

## 2. Hooks in the first 1–3 seconds

| Technique | What it is | Evidence |
|---|---|---|
| Open on the claim | Start on the boldest sentence rather than the lead-up ("if something comes easy, it isn't good" instead of "yes, but usually people don't say easy…") | [OFFICIAL] only in general form ("first 3 seconds engaging"). The specific technique is [FOLKLORE] |
| Cold open / teaser | Copy 2–5 s of the payoff or strongest line to the front, then cut back to the setup | [FOLKLORE]. Standard in podcast trailers. DOAC editor Ant Smith describes layering "the unexpected" and using several hooks in one trailer: https://callummcdonnell.substack.com/p/meet-the-viral-editor-behind-steven |
| Open on the guest's face | DOAC testing found that "opening with the guest in the chair always came out on top" compared with b-roll openings | [FOLKLORE]: an internal A/B result reported second-hand, same source |
| Text hook overlay | A headline burned on screen for roughly the first 3 s | [VENDOR]: Vizard enables it by default ("a text overlay in the first 3 seconds to grab viewers' attention"), https://docs.vizard.ai/docs/advanced |
| Question opener | A host question as the first line, with the answer as the body | [FOLKLORE]. OpusClip scores "Hook: does the introduction grab attention and directly relate to the main topic", https://help.opus.pro/docs/article/virality-score |
| Don't open on a mid-thought connective | Avoid first lines like "and so…", "yes, but…", "that's why…" | [FOLKLORE]: consistent editor advice, e.g. "skip any 'so what we were just talking about' bridge" (https://overlap.ai/blogs/best-way-to-make-podcast-clips) |

A warning that creator writing also raises **[FOLKLORE]**: opening on a laugh or punchline
*without* its setup means strangers can't follow. For comedy (pilot-01), putting the punchline first
usually spoils it. Use a **text hook that states the setup or question** instead.

## 3. Dead air, fillers and jump cuts

- **[STUDY] Laske et al., 2024, *Journal of Applied Behavior Analysis*,** "Um, so, like, do speech
  disfluencies matter?". Raters judged speeches containing 0, 2, 5 or 12 fillers per minute. At 12/min,
  perceived effectiveness dropped significantly. **Around 5/min did not hurt.** This was English speech
  judged by college students, not short-form video.
  https://www.researchgate.net/publication/381043517 (summary: https://behavioristbookclub.com/aba-research/aba-fundamentals/um--so--like--do-speech-disfluencies-matter--a-parametric-evaluation-o/)
- **[STUDY]** Psycholinguistics shows that fillers are not pure noise: listeners use "uh"/"um" to predict
  difficult upcoming words (review: https://arxiv.org/pdf/2301.10761). Removing *every* filler can
  make speech sound unnatural.
- **[VENDOR]** Descript, Vizard and Submagic all offer silence removal and filler removal, and in each
  product it is **off or optional by default**. Vizard's docs warn that "removing too many pauses can make the final video
  feel choppy or glitchy". Descript added an "Avoid harsh cuts" option to filler removal.
  https://docs.vizard.ai/docs/advanced · https://help.descript.com/hc/en-us/sections/10119901035789-Script-editor ·
  https://www.submagic.co/features/ai-silence-remover
- **[FOLKLORE]** "4–8 jump cuts per minute improves retention 10–25%": a vendor blog claim with no
  method given (https://www.conbersa.ai/learn/podcast-clip-jump-cuts). Ignore the number, but keep the idea.
- **[FOLKLORE]** Editors hide cuts by cutting to the other person's reaction, by changing the on-screen
  text at the cut, or with a punch-in zoom or a whoosh sound
  (https://www.writepanda.ai/blog/how-diary-of-a-ceo-edits-podcast-clips/). We have **one static 16:9 shot**,
  so every internal cut shows up as a visible jump. A small alternating zoom (e.g. 100% → 108%) at each
  cut is the cheap FFmpeg equivalent.

## 4. Where to start and end

- **Start:** at the first word of a sentence that a stranger can understand, as close as possible to
  the strongest line. Start speech within ~0.2 s of frame 0. [FOLKLORE, but consistent across all sources]
- **End:** on the payoff (punchline, answer, conclusion, or the other person's reaction to it), then
  cut within about 0.3–1 s. The Dashlet data shows many exits in the last 20%, so a long tail after the
  payoff loses viewers and makes looping less likely. [STUDY for where exits happen; the fix is FOLKLORE]
- **Loop:** end on a line that flows back into the opening, or cut the tail so the replay starts
  immediately. Retention above 100% and rewatches are real signals [OFFICIAL]. Engineered loops are [FOLKLORE].
- **No outro or "subscribe" card.** Instagram penalises watermarks [OFFICIAL]. End cards add a tail after the payoff.

## 5. Structure patterns

1. **Setup → punchline** (comedy, pilot-01): include the full setup. Keep the laugh pause after the
   punchline, since the pause *is* the timing. End right after the laugh.
2. **Question → answer** (interview, pilot-02): host asks, guest answers. The clip is self-contained
   when it starts on the question. OpusClip's "Flow" criterion: "a satisfying conclusion".
3. **Story arc** ("I'll tell you a story…" → complication → quote or reveal): pilot-02 clip_03
   (Rick Rubin: "I already have my Armenian band") is a textbook arc with the reveal in the last 3 s.
   Tease the reveal with text ("Why did Rick Rubin turn down an Armenian band?") rather than
   spoiling the line.
4. **Claim → reason → example** (advice or opinion): open on the claim, then cut filler inside the reason.

## 6. What automatic clippers do (all [VENDOR])

| Tool | Selection | Cut polish |
|---|---|---|
| OpusClip | LLM "Virality Score" 0–99 built from Hook / Flow / Value / Trend. Its own docs present it as a prediction | Captions, reframing, B-roll |
| Vizard | Picks highlights; `headlineSwitch` (text hook in first 3 s) **on by default** | `removeSilenceSwitch` (silence plus fillers) **off by default** |
| Submagic | "Magic Clips" highlight picker, hook generation | Silence and filler removal, animated captions |
| Descript | Editing by transcript, human-driven | "Remove filler words" (with an "avoid harsh cuts" option), "Shorten word gaps" |

The common pattern: **automatic selection plus hook text on, with aggressive audio cleanup as an option.**
Independent reviews report discarding a large share of auto clips (e.g. the BIGVU test "the 40%
you'll discard", https://bigvu.tv/blog/opus-clip-tested-2026-where-ai-wins-40-percent-discard/,
[FOLKLORE]). This supports our human-review step. None of these tools claims good Armenian support.

## 7. Top podcast clip channels

- **Diary of a CEO:** narrative first. Trailers are written in text documents before editing, use
  several hooks, open on the guest, and add sound and text to create "the unexpected". Their job ad for
  a short-form editor exists, but the method is not public beyond interviews. [FOLKLORE]
- **JRE / Flagrant clip channels:** I found no primary source describing their method. From watching the
  genre, horizontal clip channels mostly use lightly trimmed 5–15 min segments, while the Shorts are
  30–60 s single bits with the punchline near the end. This is my **unverified observation**, so don't cite it.
- **Armenian podcasts:** searches in English and Armenian turned up no Armenian podcast clip channel
  with a documented method. Armenian Shorts results were dominated by joke and humour pages. There is
  **no public Armenian benchmark**, which is itself a finding: the pilot has to generate our own evidence.

---

## 8. What our pilot outputs show [LOCAL]

Measured from `pilot-02/suggestions.json`, `pilot-02/clip_0N.srt`,
`pilot-02/harmar/clip_0N/harmar_transcript.json` and `pilot-01/`:

| Clip | Length | Start | End | Internal gaps > 0.6 s (sum above 0.3 s) | Fillers in YT captions |
|---|---|---|---|---|---|
| p2 clip_01 "if it comes easy, it isn't good" | 42.2 s | Opens on the tail of the previous turn: «[creator quote removed]» | **Cuts mid-word:** last Harmar segment «[creator quote removed]» | 4 gaps, ~3.7 s reclaimable | 2 (2.8/min) + 4 `[ծիծաղ]`, 1 `[մաքրում է կոկորդը]` |
| p2 clip_02 US army study | 66.3 s | Opens mid-sentence «[creator quote removed]» | Clean «[creator quote removed]» | 7 gaps, ~3.5 s reclaimable | 4 `ըհը` (3.6/min) |
| p2 clip_03 Rick Rubin | 41.5 s | Good, starts on a question «[creator quote removed]» | **Payoff at 37.8–39.6 s**, then the other speaker's reaction «Դե։». Ideal ending | 1 gap, ~0.6 s | 0 |
| p1 clip_01 surgeon | 26.7 s | Strong: setup in the first 2 s | — | — | — |

Observations:

1. **Two of the three pilot-02 clips start mid-thought, and one ends mid-word.** Boundaries are the
   cheapest, highest-value fix.
2. Selection text comes from YouTube auto-captions (`source/src.hy.srt`), which **contain fillers
   (`ըըը`, `ըհը`) and tags**. Harmar's per-clip transcript **drops fillers** (clip_02 Harmar text has no `ըհը`).
   Harmar segments have `start/end/text/speaker` and **no word timestamps**. As a result:
   - filler *detection* has to use the YouTube captions or the audio, not the Harmar text;
   - filler *cutting* needs word-level timing, which we don't have.
3. Speech starts 0.2–0.7 s after frame 0 in all three clips, which is fine. It can be trimmed to about 0.1 s.
4. Every pilot-02 suggestion has `"score": 0.0`, so the ranking heuristic did not choose these clips
   (they look hand-picked). The scoring changes below need testing on a full transcript run.
5. Harmar gives a `speaker` field per segment. That makes question→answer and "other speaker's reaction"
   detection possible **after** transcription.

---

## 9. Recommended changes (prioritised)

All of these are local Python plus FFmpeg with no new paid services. The first two work on text we already
have. Every change must be visible in `review.html` (what was cut and where) so that the human editor
can audit it, and the uncut version should still be exported next to the edited one.

### P1. Snap boundaries to sentence edges and end on the payoff

**Why:** the pilot evidence (2 of 3 bad starts, 1 mid-word end), the Dashlet finding that exits cluster in
the first and last 20%, and "watch all the way through" as the official signal on TikTok and Instagram.

**How:**
- In `candidates()`, only allow a window to start on a line whose *previous* line ends in
  terminal punctuation `[.!?։՞…]`, or which follows a gap of 0.7 s or more (a turn change). Only allow an end on a line that
  ends in terminal punctuation. Hard-reject endings with `...` or a trailing hyphen.
- After Harmar re-transcribes a clip, **re-snap using Harmar punctuation**: drop a leading
  segment that begins with a lowercase continuation, and drop a trailing segment that ends in
  `...`/`…` without a sentence end. Re-cut from the local file (no new Harmar charge).
- Tail: end = payoff segment end + 0.4 s. If the next segment is a *different speaker* and shorter than 2 s
  (e.g. «Դե։», «[ծիծաղ]»), include it as the button and end 0.3 s after it.
- To make re-snapping possible without re-submitting, send Harmar the window padded by ~5 s on
  each side (about +10 s per clip, so 3 clips cost about 30 s more and stay well under 600 s), then snap inside it.
  Cache as today.

**Armenian risks:** YouTube auto-captions often have **no punctuation**, so pre-Harmar snapping is
weak. Use gap and turn heuristics there, and rely on the Harmar re-snap. Armenian `՞` sits on the
stressed word mid-sentence, not at the end, so don't treat it as an end marker. `։` (Armenian full stop) and
`:` (the ASCII colon Harmar sometimes emits, as in clip_01 «[creator quote removed]») must both count.

### P2. Tighten silences using transcript gaps confirmed by `silencedetect`

**Why:** about 3.5 s of reclaimable dead air in each of two pilot clips (roughly 8%) [LOCAL]. Vendors ship this
feature [VENDOR] but warn about choppiness, so be conservative.

**How:**
1. Candidate gaps: between consecutive segments where `next.start - prev.end > 0.6 s`.
2. Confirm on audio: `ffmpeg -i clip -af silencedetect=noise=-35dB:d=0.45 -f null -`. Only
   cut where transcript gaps and detected silence overlap. ASR segment edges are padded
   and imprecise, so never trust them alone.
3. Shrink each confirmed gap to 0.25 s (keep 0.12 s on each side). Build keep-intervals, render with
   `trim/atrim + setpts/asetpts + concat` in one `filter_complex`, add `afade` in/out of 15 ms at each
   join to avoid clicks, then apply the existing blur/pad graph.
4. Remap subtitle times through a piecewise function: `t_new = t - removed_before(t)`.
   ```python
   def remap(t, cuts):  # cuts: sorted [(start, end)] removed, in clip time
       return t - sum(min(e, t) - s for s, e in cuts if s < t)
   ```
5. Optional punch-in to mask the jump: alternate `scale=iw*1.06:-1,crop=iw/1.06:ih/1.06` on every
   other kept interval.

**Armenian and genre risks:** **never compress a gap that follows a `[ծիծաղ]` tag or a punchline**
(pilot-01 stand-up relies on laugh pauses). In dialogue, a pause before an answer can be dramatic.
Cap total removal at 10% of clip length. Room tone and music beds will jump, so flag clips that have
music.

### P3. Burn in a human-approved text hook for the first ~3 s

**Why:** Instagram officially stresses the first 3 seconds, Vizard's default on-screen hook [VENDOR], and it avoids
spoiling comedy punchlines. We already have a `title` field (e.g. «[creator quote removed]»).

**How:** add an ASS event, or a second `drawtext`, placed in the **top blurred area** (y ≈ 120–300 px,
which our layout leaves empty), shown for 0–3.0 s with a 0.2 s fade. Take the text from `suggestions.json` `title`
or a new `hook` field that **the founder edits in review before rendering**. Keep it under ~45 characters.
Pattern: a question or claim ("Why did Rick Rubin turn down an Armenian band?"), never "viral"/"must watch".

**Armenian risks:** the Armenian glyphs need a font with Armenian coverage (Noto Sans Armenian or
Arial Unicode) via `fontfile=`. Check that libass does not fall back to tofu. Long compound words
wrap badly at 720 px, so enforce the length limit. There is also a risk of misrepresentation: the hook must be
faithful to what the speaker said, because the creator is quoted.

### P4. Score the opening: trim weak lead-ins and reward strong first lines

**Why:** hook guidance [OFFICIAL, in general form] and the Dashlet exits in the first 20% [STUDY]. The current `opening`
bonus only checks for a `?`.

**How:** in `candidates()`:
- Penalise (−0.8) a first line that starts with a connective or back-channel. Starter list (Eastern
  Armenian, colloquial): `ու, բայց, դե, հա, ըհը, բան, այսինքն, որովհետև, ուրեմն, էսինքն, նույնն, էլի,
  էդ, տենց, դրա համար`. Better still: try starting 1–2 lines later and keep that version if the
  window is still at least 25 s.
- Bonus for the first 3 s containing a question (`՞`/`?`), a number, a proper noun / Latin-script name
  (clip_03's "System", "Apex Theory"), or a strong-claim word (`երբեք, բոլոր, ամենա-, ոչ ոք, իրականում,
  գաղտնիք, սխալ`).
- Report `time_to_first_word` and `first_3s_text` in `review.html`, so reviewers judge the hook first.

**Armenian risks:** the word lists are my guesses at colloquial Eastern Armenian. **A native editor
must check them** (Western Armenian speakers differ). `էդ/տենց` can open a perfectly good sentence
("Էդ ամենամեծ սխալն ա…"), so apply a penalty, not a hard rule.

### P5. Use filler density as a selection penalty (don't cut fillers yet)

**Why:** Laske et al. found no harm at about 5/min and harm at 12/min [STUDY]. Our clips run 0–3.6/min [LOCAL]. We lack
word timestamps, so cutting fillers is not yet safe.

**How:** count standalone fillers in the **YouTube caption text** for the window with
`(?<![\wԱ-֏])(ը{1,}|ըհը|էէ+|ըմ+|մմ+)(?![\wԱ-֏])`. Subtract `max(0, per_min - 5) * 0.3`.
Also penalise `[մաքրում է կոկորդը]`-type non-speech tags (but *not* `[ծիծաղ]`, which is a
positive signal in comedy).

**Armenian risks:** `ը` is also the **definite-article suffix** (-ը). Only match whitespace- or
punctuation-bounded tokens, never word endings. `ըհը` from the *other* speaker can be a meaningful
back-channel. Harmar strips fillers, so this signal only exists in the YouTube captions, which are machine-made
and unreviewed.

### P6. Detect question→answer and story windows from Harmar `speaker` labels

**How:** after transcription, prefer windows where speaker A's segment contains `՞`/`?` and the
following segments are speaker B through a terminal sentence. For stories, look for opening markers
(`մի հատ պատմություն, մի անգամ, հիշում եմ, պատկերացրու`) and quote-reveal markers near the end
(`ասել ա, ասաց, ու ասում ա`), as in clip_03 «[creator quote removed]».

**Risks:** speaker labels exist only after a paid Harmar pass. Use this for **re-ranking and re-snapping
the padded window**, not for the first selection. Diarization errors on overlapping Armenian
speech are unknown, so check them on the pilot.

### P7. Opt-in cold open (teaser) for claim and insight clips, never for comedy

**How:** in review, the founder marks one segment as `teaser`. Render `[teaser 2–5 s] + 0.15 s dip
to black + [clip from start]` with `concat`, and shift subtitles by the teaser length. Put a
subtle label ("↺" or nothing) in the review page stating that the order was changed.

**Risks:** reordering a creator's words can misrepresent them, so it needs **explicit creator
consent** to this edit style. The teaser repeats later in the clip, which some viewers find cheap. Test this only
after P1–P5.

### P8. Length target per clip type (a soft bonus, not a filter)

Keep the 25–66 s range. Add a soft bonus toward 25–40 s for high-laughter, stand-up-like windows
and 35–60 s for question→answer or story windows, then let the human pick. All platforms allow 3 min
[OFFICIAL], and no ideal length is officially published. Genre targets are [FOLKLORE].

### P9. Make the cut auditable

Write `edits` into `suggestions.json` for each clip, containing source start/end, removed gaps (source
timestamps), hook text, whether a teaser was used, and seconds removed. Show it in `review.html`
next to the uncut and cut players. This is required by the human-review constraint, and it is how the
editor answers the milestone question ("how long does fixing it take?").

### P10. Batch-end check (extends the team workflow)

After rendering, `ffprobe` each MP4 for 720x1280 and a duration equal to the planned `sum(keep_intervals)` ± 0.1 s.
Also confirm that the last subtitle cue ends no later than the video duration, and that the first cue
starts at 0.3 s or earlier.

---

## 10. What I would do first

1. **P1 + P9** in the next batch. This is pure text logic and fixes the clearest pilot defects.
2. **P2** behind a `--tighten` flag, off by default, with both versions exported. Ask the editor whether
   the tightened version is better.
3. **P3** once the founder has written hooks for the 3 pilot-02 clips by hand.
4. **P4/P5** as scoring tweaks, re-ranked on a full transcript with the word lists checked by a
   native speaker.
5. Don't build filler cutting, teasers or reframing until a creator says the clips are useful.

## Sources

- TikTok Newsroom, How TikTok recommends videos: https://newsroom.tiktok.com/en-us/how-tiktok-recommends-videos-for-you
- Instagram, Ranking explained: https://about.instagram.com/blog/announcements/instagram-ranking-explained
- Instagram for Creators FAQ: https://creators.instagram.com/faq?locale=en_US
- YouTube Blog, Tall updates coming to Shorts: https://blog.youtube/news-and-events/tall-updates-coming-to-shorts/
- YouTube Help, three-minute Shorts: https://support.google.com/youtube/answer/15424877
- YouTube Help, key moments for audience retention: https://support.google.com/youtube/answer/9314415
- YouTube Community, Viewed vs swiped away: https://support.google.com/youtube/community-video/273390203/new-youtube-shorts-metric-viewed-vs-swiped-away?hl=en
- TubeBuddy on the March 2025 Shorts view-count change: https://www.tubebuddy.com/blog/youtube-shorts-view-count-update-what-creators-need-to-know-about-the-new-metrics/
- MediaNama on 3-minute Reels and the earlier 90 s guidance: https://www.medianama.com/2025/01/223-instagram-reels-3-minutes-us-tiktok-ban/
- Dashlet, short-video swipe study: https://arxiv.org/pdf/2204.12954
- Laske et al. 2024, filler rates: https://www.researchgate.net/publication/381043517 ; summary https://behavioristbookclub.com/aba-research/aba-fundamentals/um--so--like--do-speech-disfluencies-matter--a-parametric-evaluation-o/
- Fillers in spoken language understanding (review): https://arxiv.org/pdf/2301.10761
- OpusClip Virality Score: https://help.opus.pro/docs/article/virality-score
- Vizard API advanced options: https://docs.vizard.ai/docs/advanced
- Submagic silence remover: https://www.submagic.co/features/ai-silence-remover
- Descript script editor help: https://help.descript.com/hc/en-us/sections/10119901035789-Script-editor
- DOAC editor interview (Ant Smith): https://callummcdonnell.substack.com/p/meet-the-viral-editor-behind-steven
- DOAC clip editing breakdown (third party): https://www.writepanda.ai/blog/how-diary-of-a-ceo-edits-podcast-clips/
- Podcast clip workflow advice: https://overlap.ai/blogs/best-way-to-make-podcast-clips
- Jump-cut cadence claim (vendor, unverified): https://www.conbersa.ai/learn/podcast-clip-jump-cuts
- BIGVU OpusClip test: https://bigvu.tv/blog/opus-clip-tested-2026-where-ai-wins-40-percent-discard/
- TikTok length blog (source of the unverified 21–34 s claim): https://www.teleprompter.com/blog/how-long-should-a-tiktok-video-be
