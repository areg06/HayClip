# Caption styles for Armenian short-form clips: research and ASS presets

Author: research teammate. Date: 2026-09-27. Scope: research only. `burn_captions.py` and the pilot files were not changed.
Evidence labels: **official** means a platform or broadcaster document. **study** means a survey or experiment with a stated method. **folklore** means vendor blogs, creator lore or numbers with no visible method.

## TL;DR

1. Captions matter, but the solid numbers are older and come from ads research. Verizon Media/Publicis (2019, n=5,616 US adults): 69% watch with sound off in public and 80% are more likely to finish a video that has captions (**study**). Meta's internal tests found captioned video ads raise view time by 12% on average (**official**). The widely quoted "85% of Facebook video is watched without sound" figure comes from Digiday (2016) and was never confirmed by Meta (**folklore**).
2. There is **no public controlled study** showing that word-by-word or "Hormozi" captions beat static captions. Numbers like "+41% engagement" and "+12–25% watch time" come from vendor blogs (**folklore**). Word highlighting is the genre default, not a proven effect. The pilot should A/B it with the creator rather than assume it works.
3. **Placement bug in the current output.** Our captions end at y≈980 of 1280 (MarginV 300), which puts them inside Meta's official bottom-35% Reels/Stories zone (y > 832). They also sit on YouTube's bottom-25% line (y = 960). Moving the caption baseline to **MarginV ≈ 450** (bottom of text at y≈830) keeps it clear of every platform's UI and places it over the desk/mic area of our stacked layout, below the face. See `research/caption-samples/safezones_current.png` and `safezones_presetA.png`.
4. **Armenian specifics.** Do not use uppercase. Python's `str.upper()` turns `և` into `ԵՒ`, the Western/classical form, when Eastern orthography needs `ԵՎ` (verified locally). Armenian words run long: clip_02 averages about 6.4 characters per word and its longest word is 18 (`ուսումնասիրություն`). A 1-word-per-screen style therefore needs automatic font shrinking.
5. **Emoji do not work in libass.** A local test rendered `🔥💪` as empty boxes (tofu). Skip emoji, or overlay them as PNG images.
6. **Recommended default: Preset A, "active word".** It shows 2–4 words (≤22 characters) per screen in Noto Sans Armenian Black 58px, mixed case, with a thick outline. The spoken word turns yellow and shrinks from a 112% pop back to 100%. This needs Harmar `"timestamps": "word"`.

## 1. Evidence on captions and sound-off viewing

| Claim | Label | Source |
|---|---|---|
| 69% watch video with sound off in public and 25% in private. 80% are more likely to finish a video with captions. 50% say captions matter because they watch sound-off. Survey of 5,616 US adults aged 18–54, April 2019 | study (commissioned by an industry body) | https://www.3playmedia.com/blog/verizon-media-and-publicis-media-find-viewers-want-captions/ ; https://www.forbes.com/sites/tjmccue/2019/07/31/verizon-media-says-69-percent-of-consumers-watching-video-with-sound-off/ |
| 80% of caption users are not hearing-impaired | study (same survey as above) | https://www.streamingmedia.com/Articles/ReadArticle.aspx?ArticleID=131860 |
| "Captioned video ads increase video view time by an average of 12%" (Meta internal tests). A&W Canada case: +25% watch time. 80% react negatively when feed video ads play loudly without warning | official (Meta for Business) | https://www.facebook.com/business/news/updated-features-for-video-ads |
| 85% of Facebook video is watched without sound | folklore (Digiday 2016, publisher-reported, not confirmed by Meta) | https://adlibrary.com/posts/meta-ads-creative-best-practices (discusses the origin) |
| 70% of US Gen Z use subtitles "most of the time" on TV/movies | study (Preply survey, n=1,265; long-form TV, not Reels) | https://preply.com/en/blog/americas-subtitles-use/ |
| Animated captions give "+41% engagement" and "12–25% more watch time". 78.6% of 13.5M clips use animated captions | folklore (vendor blogs, no method shown) | https://kreateflo.com/blog/which-animated-caption-styles-actually-increase-video-watch-time ; https://reelwords.ai/blog/animated-captions |
| Subtitles placed near the speaker ("dynamic subtitles") produced gaze patterns closer to viewing without subtitles, and improved the experience for many but not all hearing-impaired users | study (BBC R&D / Univ. Manchester, ACM TVX 2015) | https://www.researchgate.net/publication/275212495_Dynamic_Subtitles_The_User_Experience |
| Learners given keyword-only captions did better on form recognition than those given full captions | study (L2 vocabulary learning context, not social video) | https://files.eric.ed.gov/fulltext/EJ1125240.pdf |

Takeaway: burned-in captions are clearly worth having for a sound-off feed. The specific style (word pop versus a plain line) is unproven, so treat it as a creator-taste decision and test it.

## 2. Platform safe zones, with pixels for our 720x1280 frame

| Platform | Guidance | Label | In 1080x1920 | In our 720x1280 |
|---|---|---|---|---|
| Instagram/Facebook Reels and Stories (Meta unified these in March 2026) | Keep text, logos and key elements out of the top **14%**, bottom **35%** and **6%** of each side | **official**: https://www.facebook.com/business/ads-guide/update/image/instagram-reels ; https://www.facebook.com/business/help/980593475366490/ | top 269, bottom 672, sides 65 | top 179, bottom 448 (text must end above y=832), sides 43 |
| TikTok | TikTok says the safe zone "is determined by the dimension, ad caption length and any interactive add-on". The official numbers are only in downloadable overlay files | **official** (no numbers in the text): https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads?lang=en | third-party reading: top 130, bottom 484, left 44, right 140 (**folklore**: https://cadenus.io/resources/blog/tiktok-safe-zone/) | top 87, bottom 323, left 29, right 93 |
| YouTube Shorts | Avoid the top 10%, bottom 25% and right 10% (the channel name, caption and buttons live there) | reported from Google Ads Help (the page I fetched did not show numbers, so treat as **unverified official**): https://support.google.com/google-ads/answer/9128498 | top 192, bottom 480, right 108 | top 128, bottom 320, right 72 |

**Union of the three zones (strictest value per edge) for our frame:** top ≥179, bottom ≥448, left ≥43, right ≥96. The usable caption band is x 48–624 (576px wide, centred at x=336 rather than 360) with the text bottom at y ≤ 832. Meta's 35% value is written for ads, which carry a CTA. Organic Reels UI is a little shorter, but using the ad rule costs us nothing.

Our stacked layout has blurred fill at y 0–436, a 16:9 video band at y 436–840 and blurred fill at y 840–1280. With MarginV=450, captions sit on the lower edge of the video band (desk and mic), away from the speaker's face and inside every platform's safe area.

## 3. What current tools produce by default

| Tool | Default look | Label / source |
|---|---|---|
| Submagic | About 21 templates, three of them "Hormozi". The Hormozi look is heavy condensed sans (Montserrat Black / Anton / Bebas), ALL CAPS, thick black stroke (8–12px at 1080p), 1–3 words per screen, yellow or green active or key word, pop-in | folklore/vendor: https://www.submagic.co/blog/how-to-make-alex-hormozi-captions ; https://ascynd.io/en/blog/hormozi-captions |
| Opus Clip | Animated templates, an "AI keyword highlight" that colours key terms, optional "auto emoji", "20+ languages" | vendor: https://www.opus.pro/captions ; https://www.opus.pro/blog/power-of-captions |
| Captions (captions.ai) | Word-by-word animated templates. "AI emphasis" colours or enlarges key words, the active word gets a rounded background, split-words control | vendor/official help: https://help.captions.ai/docs/captions/styles |
| CapCut auto-captions | Word-by-word highlight or one-at-a-time presets. Reportedly supports about 15 languages, **not Armenian**, so Armenian creators type captions by hand | folklore/third-party: https://capcutguide.com/capcut-word-by-word-captions/ ; https://caption-x.com/capcut-captions/armenian |

In short, the genre default is short chunks, a heavy font, a thick outline, a coloured active word and a pop. The ALL-CAPS part does not carry over well to Armenian (section 5).

## 4. Readability and accessibility guidance

- **Line length.** Netflix caps a line at 42 characters, with at most 2 lines (**official**, general requirements: https://partnerhelp.netflixstudios.com/hc/en-us/articles/215758617-Timed-Text-Style-Guide-General-Requirements). I found no Netflix Armenian-specific guide. BBC guidance puts online line length at about 68% of a 16:9 frame's width. On a 9:16 phone frame, 42 characters is far too wide at a readable size, and 20–30 characters per line fits our 576px band at 46–58px.
- **Reading speed.** BBC uses 160–180 wpm (**official**, reported via https://www.clevercast.com/bbc-subtitling-guidelines/, because bbc.co.uk could not be fetched from here). Word-synced captions follow the speaker automatically, so reading speed only becomes a problem when someone talks faster than viewers read. Keep a minimum of about 0.25s on screen for each word event or chunk.
- **Contrast.** White text with a black outline, or a 70–80% black box, is the standard pairing (reported BBC practice, same source). Our outline (4–6px) and a 1–3px shadow meet it.
- **Colour for emphasis.** Colour should never be the only signal, since some viewers have colour-vision deficiency. Preset A pairs the colour with a scale pop, so the active word stands out through motion as well. Yellow #FFD700 on the black outline has very high luminance contrast.
- **Motion.** Keep pops short (≤160ms) and small (≤112%). Large bouncing text is tiring and hard to read in a language with long words.

## 5. Armenian-specific findings

**Installed fonts** (`fc-list | grep -i armen` on this Mac):
- `Noto Sans Armenian` (`/System/Library/Fonts/NotoSansArmenian.ttc`) comes in Thin, ExtraLight, Light, Regular, Medium, SemiBold, Bold, ExtraBold and **Black**. It is licensed under the SIL OFL, so it is portable and can be bundled or shipped with `fontsdir`. libass resolves `Fontname = Noto Sans Armenian Black` correctly (checked with `fc-match` and in the renders). **Use this font.**
- `.SF Armenian` and `.SF Armenian Rounded` go from Ultralight to Black. These are Apple system UI fonts: the leading dot means private, and the licence does not allow use outside Apple platforms. They look good but are not portable, so avoid them in a pipeline that might run on Linux.
- Other Armenian families exist (Google Fonts has an Armenian subset filter, and the Armenian GHEA/Arian AMU families exist), but none are installed here, so I did not evaluate them.

**Weight.** Black (900) with a 5–6px outline stays legible at 58–76px on a 720-wide frame (see the samples). SemiBold is fine inside a box (Preset C).

**Uppercase (Մեծատառ).** No Armenian-specific legibility study turned up. Research on Latin all-caps shows it is less legible than mixed case (**study**, summarised at https://en.wikipedia.org/wiki/All_caps). Armenian capitals differ a lot from the lowercase forms (**official**-grade orthography notes: https://r12a.github.io/scripts/armn/hy.html), so all-caps loses the ascender/descender word shapes that Armenian lowercase relies on. There is also a concrete bug risk:
- `python3 -c "print('և'.upper())"` prints `ԵՒ` (U+0535 U+0552), the Western/classical capitalisation.
- Eastern Armenian, which our creators speak, needs `ԵՎ` (r12a: "Eastern orthography capitalises as Եվ").
- Any uppercase preset would need a custom `.replace('և', 'ԵՎ')` before `.upper()`.

**Recommendation:** use mixed case and get emphasis from weight, colour and scale.

**Word length.**
- Clip_02 averages 6.4 characters per word; the longest is 18 (`ուսումնասիրություն`).
- At Black 58px this word fills about 470px of the 576px band (Preset A sample), and at 76px it would overflow.
- Presets must therefore chunk by **characters, not word count**: ≤22 characters for A, ≤14 for B with auto-shrink, ≤30 for C.
- Use `WrapStyle: 2` (no automatic wrapping) so libass never breaks a line on its own.

**Punctuation.**
- The emphasis/question/exclamation marks `՛ ՜ ՞` sit on top of the word (`ինչո՞ւ`), so word splitting must never separate them from their word (r12a).
- Splitting on whitespace, as we do now, keeps them attached.
- Break chunks after `,` `։` `՝` (Armenian full stop U+0589 and comma-like U+055D).

**Emoji.** libass cannot render colour emoji. The local test at `research/caption-samples/emoji_and_uppercase_test.png` shows empty boxes. Opus/Captions-style emoji would need a separate PNG overlay, which is not worth it for the pilot.

## 6. Proposed presets (libass/ASS, 720x1280, word timestamps)

All three share this header. Note `WrapStyle: 2` and `ScaledBorderAndShadow: yes`:

```
[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 2
ScaledBorderAndShadow: yes
```

Colours are ASS `&HAABBGGRR`. Yellow #FFD700 is written `&H00D7FF&` in override tags. Margins L=48 and R=96 respect the side and right-button zones.

### Preset A: "active word" (recommended default)

2–4 words (≤22 characters) per screen, bottom-anchored at MarginV 450. There is one Dialogue event per word. Each event repeats the whole chunk and marks the active word yellow with a 112%→100% pop over 120ms. `{\r}` resets to the style after the word.

```
Style: A,Noto Sans Armenian Black,58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,0,0,0,0,100,100,0,0,1,5,2,2,48,96,450,1
Dialogue: 0,0:00:00.22,0:00:01.05,A,,0,0,0,,{\c&H00D7FF&\fscx112\fscy112\t(0,120,\fscx100\fscy100)}Ամերիկյան{\r} բանակի
Dialogue: 0,0:00:01.05,0:00:01.60,A,,0,0,0,,Ամերիկյան {\c&H00D7FF&\fscx112\fscy112\t(0,120,\fscx100\fscy100)}բանակի{\r}
```

- **Pros:** it reads like normal Armenian text, highlights what is being said, matches the look of the genre, and the long-word worst case fits.
- **Cons:** it needs word timestamps. The pop on a word mid-line briefly shifts its neighbours by a few px for 120ms. This is barely visible, but `\fscx112` can be dropped (colour only) if the editor dislikes it.
- **Implementation note:** each event ends at the next word's start, so there are no gaps or flicker inside a chunk.

### Preset B: "one-word punch"

1–2 words (≤14 characters) per screen, big and centred at `\pos(336,790)`, which is inside all safe zones and just below the face. Each event pops in (80%→106%→100% over 160ms). Font size shrinks for long words: `fs = min(76, 76*16/len)`, so an 18-character word gets 67.

```
Style: B,Noto Sans Armenian Black,76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,0,0,0,0,100,100,0,0,1,6,3,5,48,96,0,1
Dialogue: 0,0:00:01.05,0:00:01.60,B,,0,0,0,,{\pos(336,790)\fs76\fscx80\fscy80\t(0,80,\fscx106\fscy106)\t(80,160,\fscx100\fscy100)}բանակի
Dialogue: 0,0:00:01.60,0:00:03.25,B,,0,0,0,,{\pos(336,790)\fs67\fscx80\fscy80\t(0,80,\fscx106\fscy106)\t(80,160,\fscx100\fscy100)}ուսումնասիրություն
```

- **Pros:** the highest-energy option and the closest to Hormozi.
- **Cons:** with 6–18-character words the text changes every 0.3–0.8s and the size jumps, which is tiring for a reflective podcast. Any word-timestamp error is much more visible. It suits hype or punchline clips better than conversation.

### Preset C: "karaoke line in a box" (calm, lowest-risk)

A full short line (≤30 characters) sits in a translucent black box (`BorderStyle 3`; in libass the box uses OutlineColour, here 50% black). Native `\kf` sweeps each word from grey (SecondaryColour `&H00B4B4B4`) to white (PrimaryColour). There is one event per chunk, which makes it the simplest to generate. `\kf` values are word durations in centiseconds.

```
Style: C,Noto Sans Armenian SemiBold,46,&H00FFFFFF,&H00B4B4B4,&H80000000,&H00000000,0,0,0,0,100,100,0,0,3,10,0,2,48,96,450,1
Dialogue: 0,0:00:03.64,0:00:06.29,C,,0,0,0,,{\kf20}որ {\kf98}զինվորները {\kf88}սովորաբար {\kf59}տրավմա
```

- **Pros:** the box guarantees contrast on any background, it is the most readable for long Armenian phrases and it looks close to subtitles.
- **Cons:** it is the least "short-form native" and the sweep is subtle. It also works with **segment** timestamps alone: spread `\kf` by character share, as the samples do.

### Implementation sketch for `burn_captions.py` (for the clip-engine owner; not done here)

1. Request `"timestamps": "word"` from Harmar on the **next** consented job. Do not re-submit completed jobs: CLAUDE.md forbids a silent second charge. Until then, synthesise word times by character share, as `make_samples.py` does.
2. Chunk the words with `chunks(words, max_chars, max_words)`: break after `, ։ ՝ . ? !` or on a gap over 0.5s.
3. Emit events per preset as above. Keep the existing `{`→`(` escaping of transcript text.
4. Never call `.upper()` on Armenian text.

## 7. Sample renders (local, from pilot-02/clip_02.mp4, first 14s)

Word timings in these samples are **synthetic**: each cue's time is split across its words by character count. Sync will look slightly off and does not reflect Harmar's word timestamps.

- `research/caption-samples/A_active_word.mp4` with stills `A_active_word_t1.2.png`, `A_active_word_t12.0.png` and `A_active_word_longword_t2.0.png`
- `research/caption-samples/B_one_word_pop.mp4` with stills `B_one_word_pop_t1.2.png`, `B_one_word_pop_t12.0.png` and `B_one_word_pop_longword_t2.0.png`
- `research/caption-samples/C_karaoke_box.mp4` with stills `C_karaoke_box_t1.2.png` and `C_karaoke_box_t12.0.png`
- `research/caption-samples/safezones_current.png`: the current style with the Meta 14/35/6 zones (red), a right-side 96px band, and the YouTube bottom-25% line (yellow). The current captions sit inside the red zone.
- `research/caption-samples/safezones_presetA.png`: Preset A with the same overlay. It clears every zone.
- `research/caption-samples/emoji_and_uppercase_test.png`: emoji render as tofu, and `ԵՎ` vs `ԵՒ` shown side by side.
- The generator is `research/caption-samples/make_samples.py` (`python3 research/caption-samples/make_samples.py` from the repo root). All outputs are 720x1280 H.264 + AAC, 14.0s.

### 7a. Cross-check with research-visual's full-frame speaker crop

research-visual proposes replacing the blurred letterbox with a per-shot speaker crop, with the face at about y 150–450. I rendered Preset A, unchanged at MarginV 450, onto their `research/visual-samples/01_speaker_crop_static.mp4`:

- Result: `research/caption-samples/A_active_word_on_speaker_crop.mp4` (720x1280, 14.0s) and still `A_active_word_on_speaker_crop_t12.0.png`.
- The caption lands at about y 790–830, over the mic and chest. It stays clear of the face and inside every safe zone.
- So Preset A's placement works for both layouts and needs no position change if the team switches to the speaker crop.
- One limitation: yellow and white text on a grey T-shirt keeps enough contrast only because of the 5px black outline. If a creator wears white clothing, consider Preset C's box or a thicker outline.

## 8. What to test with the creator (no claims until then)

Show the editor A, B and C on the same clip. Ask which one they would post, what they would change (colour, size, position) and whether the synthetic sync is tolerable or real word timestamps are needed. Log the minutes of fixing each one needs. None of these styles has been shown to raise retention for Armenian audiences.

## 9. Caption height after the switch to a full-frame speaker crop (2026-09-30)

The MarginV 450 advice above assumed the blurred letterbox, where y≈830 was the desk. With the
face crop (`reframe.py`), the speaker's chin sits at y≈690 median, 724 p90 and 763 max
(YuNet, 1 sample/s over the 3 pilot-03 clips), so a caption ending at 830 (top ≈765) touched the beard.

- Organic Reels: the caption/username block covers roughly the bottom 270 px of 1920 on the left
  (y≈1100 in our frame) and the button rail about 400 px on the right (**folklore**/third-party:
  https://imagevideofit.com/guides/instagram-reels-safe-zone, https://www.hopperhq.com/blog/instagram-reel-size/).
  Meta's bottom-35% rule (y=832) is the ads spec.
- TikTok and Shorts: bottom ~25% (y=960), as in section 2. A cross-platform band of 50–78% of the
  height is commonly recommended (**folklore**: https://blitzcutai.com/blog/best-caption-placement-short-form-video).
- **New default: the text's bottom edge at y=930 (73%, MarginV 350).** The text top is ≈865, about 100 px
  below the p90 chin line, on the chest, and it clears the TikTok/Shorts line. `--caption-bottom 830`
  restores the ads-safe height. Comparison: `caption-samples/caption_height_830_880_930_980.png`
  (red = Meta ads 35%, yellow = TikTok/Shorts 25%, cyan = organic Reels caption block).
