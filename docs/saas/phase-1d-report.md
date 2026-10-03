# Phase 1d report: a showable, creator-first local product

Date: 2026-10-03. Scope: Phase 1d only (product design, UX and the clip editor). The app is still local-first.

**No real Harmar request was made.** The real paid ledger does not exist (nothing was ever reserved or charged) and the 133 s balance is still reserved.

**Not built, as instructed:** billing, teams, hosting, publishing APIs, OAuth, LLM features, a mobile app or a timeline editor.

**How it was built:** inline in the lead session, because of agent usage limits. Major decisions are recorded below.

## Commits (after `db11d70`, the Phase 1c report)

| Commit | Purpose |
|---|---|
| `6ea2337` | Design system and product shell, part 1: tokens, top navigation, homepage, dashboard and projects list |
| (same) | Product shell, part 2: new-video page, Settings with a non-functional account shell, job chaining, `docs/product/principles.md` |
| `5218e93` | Uploads as a source, local discovery transcript, cheap candidate previews, Find more |
| `17033db` | Choose clips (preview, batch selection, pre-transcription trim) and the Add captions cost preview with a confirmed chain |
| `7b416b6` | Caption "looks" in the ASS engine; output without a look is unchanged |
| `8c5fd49` | Render applies each clip's look, hook settings and audited transcript corrections |
| `4330ac2` | Central safe-zone definitions; crop plans keep their automatic shots for reset |
| `f3298a8` | Editor, export view, planning calendar, Brand Kit, creator project page |
| `33c4f66` | Browser tests for the creator workflow; CSP-safe styling; Clean preset media |

## Pages (screenshots from the browser test, synthetic media)

Synthetic test media only, no creator content: `docs/saas/phase-1d/`.

| Page | What it shows | Primary action |
|---|---|---|
| Home `/` (`1d-0-home.jpg`) | Headline "Turn Armenian videos into ready-to-post shorts.", a long-video → HayClips → three vertical clips illustration, six features, four-step "How it works". No testimonials, customer counts or metrics | Start creating |
| Dashboard `/dashboard` | "Your videos": up to 8 recent projects as rows with a creator stage pill (Finding clips / Choose clips / Adding captions / Editing / Ready / Scheduled) and one line such as "6 moments · 2 chosen · 1 ready". Empty state when new | New video |
| Projects `/projects` | All projects, same rows | New video |
| New video `/new` | Two tabs, YouTube link and Upload a file, with the limits stated | Find clips / Upload and find clips |
| Project `/p/<id>` (`1d-2-project.jpg`) | Source line and stage pill; one next-action card that changes with the stage; clip cards with a video thumbnail, duration, state, Edit and Export. Jobs and advanced controls sit in a collapsed "Activity and advanced controls" | Changes with the stage |
| Choose clips (`1d-1-choose.jpg`) | One card per moment: checkbox, time range and length, short excerpt, "Why this moment" on demand, Preview, and an in-app player with start/end handles once previewed. A sticky dark bar shows "N selected · m:ss" | Add captions |
| Add captions | "3 clips selected · 2m 14s" and "2m 34s will be transcribed" (the 5 s margins are explained in a tooltip). Creator permission is inline if missing; the paid step is blocked visibly when off | Add captions (with the cost checkbox and your name) |
| Editor (`1d-3-editor.jpg`) | Phone-sized live preview on the left, controls on the right. Captions / Trim / Frame modes show one panel at a time | Render |
| Export (`1d-4-export.jpg`) | Rendered clip with a platform overlay switch; format, duration, caption state, hook and checks; Download MP4 and SRT; Add to calendar | Download MP4 |
| Calendar `/calendar` (`1d-5-calendar.jpg`) | Month grid with items, and a "Coming up" list with status updates (Draft / Editing / Ready / Scheduled) or removal. Labelled planning only | — |
| Brand Kit `/brand` | Default preset, font, colours, caption and hook position, and an optional logo (stored only) | Save Brand Kit |
| Settings `/settings` | Instagram, TikTok and YouTube shown as "Not connected". Connect only shows "Social publishing is not available in this version."; nothing is stored or called | — |

## Design tokens and principles

The principles and the token table are in `docs/product/principles.md`; the tokens themselves are in `hayclips/web/static/app.css` `:root`.

| Group | Values |
|---|---|
| Colour | bg `#F7F7F5`, surface `#FFFFFF`, ink `#151515` / `#707070`, line `#E8E8E5`, brand `#5B5FEF` / hover `#494DD8`, success `#1F9D69`, warning `#D98B27`, danger `#D84A4A` |
| Spacing | 4 px scale |
| Type | 12–44 px, with Noto Sans Armenian in the stack |
| Shape | radius 8 / 12 / 18 px and pill; two soft shadows; one visible focus ring |
| Buttons | primary (brand, one per view), outlined default, ghost, danger (text colour only), small / big |
| Status | pills always carry a word as well as a colour |

**CSS rules:**
- The app's CSP (`style-src 'self'`) blocks inline `style` attributes, so all layout lives in classes.
- 34 inline styles found during review were converted to utility classes.

## UX decisions

- **Spend late, by construction:**
  - "Find clips" is free: captions or local discovery, then candidates.
  - Preview, trim and choose are free.
  - Only "Add captions" on the cost screen starts paid work, and only for the chosen clips.
- **One primary action per page:**
  - the project page shows a single next-action card;
  - the editor's only primary button is Render;
  - the export page's is Download MP4.
- **Progressive controls:** the editor shows preset and size first. Colours, background and words per line sit under "More style options". Caption text editing and "Why this moment" are collapsed.
- **Creator language:**
  - Stages are Finding clips / Choose clips / Adding captions / Editing / Ready / Scheduled. Job states are kept only in the collapsed Activity panel.
  - Verbs are Find clips, Choose clips, Add captions, Edit, Render, Export, Add to calendar.
- **Human control:** moments are "suggestions"; the ⓘ tooltip says scores are not a prediction of performance. No "viral" wording anywhere (tested on the homepage).
- **Armenian:** preset samples and captions are never uppercased (`և` stays correct; tested).

## Upload architecture

```
browser --(raw body, X-CSRF-Token, same-origin)--> POST /new/upload
   streamed to projects/.uploads/<random>.part, stopped at HAYCLIPS_MAX_SOURCE_BYTES
   -> validate_upload: extension (.mp4/.mov/.m4v), ffprobe format (MP4/MOV family), a video track,
      5 s <= duration <= HAYCLIPS_MAX_SOURCE_SECONDS; failure deletes the file, creates nothing
   -> project folder: source/original.<ext> (fixed name; the user's file name is display-only)
   -> project.source = {kind: upload, file, sha256, size, duration, original_name}
```

- **Raw upload path:** the security middleware does not buffer bodies for the raw-upload paths (`/new/upload`, `/brand/logo`). It requires the same origin and the CSRF header instead of a form field.
- **One interface:** `UploadedFileSource` (`hayclips/sources/upload.py`) implements the same `SourceProvider` interface as YouTube:
  - `fetch_window` cuts the chosen window at ≤1080p;
  - `fetch_preview` cuts a 360p preview;
  - `fetch_captions` refuses, because there are no free captions.
- **Shared downstream:** fetch, transcription, rendering and the editor don't branch on the source type. `default_source_factory` picks the provider.

## Discovery transcript architecture (`hayclips/discovery.py`)

```
upload -> discover_transcript job (cpu pool, LOCAL): 16 kHz mono WAV -> discovery engine -> source/discovery.srt
       -> generate_candidates (same scoring as YouTube captions)
       -> Choose clips (preview/trim) -> Add captions (cost screen) -> only the chosen windows -> paid transcription
```

- **Engines:**
  - `auto` (default) uses faster-whisper if installed; otherwise it shows a clear "not installed, run `pip install faster-whisper`" error.
  - `whisper` forces faster-whisper (int8 on CPU).
  - `fake` is for tests.
- **Never the final captions:** the discovery SRT is recorded as `captions_kind: "discovery"`. Final captions always come from the paid transcription of chosen windows, the same audited path as YouTube.
- **Full upload never sent:** nothing sends the full upload anywhere.
  - The window cap is 180 s for paid transcription and 600 s per cut.
  - A test runs upload → discovery → candidates → preview → window with a fake Harmar and asserts **zero requests**.
- **Not yet run for real:** faster-whisper is not installed in this environment, so the real local engine path has not been exercised. Only the fake engine has.

## Cost-control behaviour

| Action | Server work |
|---|---|
| Find clips (YouTube) | free captions + scoring |
| Find clips (upload) | free local discovery + scoring |
| Preview | one 360p window: a YouTube section download or a local ffmpeg cut |
| Trim, choose, Find more | JSON only (Find more re-scores existing captions) |
| Add captions (confirm) | one chain for the chosen clips only: download windows → **paid** transcribe → local render |
| Editor drag / style / size / colour / safe-zone / crop slider | **browser only**, no request at all |
| Save style / hook / text / framing | a small JSON write, no job |
| Render (editor) | one local render job (cpu pool). The idempotency key includes the look, crop and text edits, so repeated clicks without changes are one job |

**Paid gating:**
- The paid step needs, all of: paid mode on in the server environment, a consent record, budgets, no unreconciled submission, a key, a ticked cost box and a typed name.
- The chain carries that confirmation and never adds clips.
- The Phase 1a guarantees (exact bytes, durable attempts, reconciliation) are unchanged.

**Trim before transcription:**
- If a window was already downloaded and no transcription attempt exists, the old window is moved to `clips/<id>/superseded/<time>/`, never deleted.
- Once a clip is transcribed, pre-transcription trim is refused; the editor's Trim mode is used instead.

## Editor features (`/p/<id>/clips/<clip>/editor`)

- **Live preview:** the landscape window is cropped in CSS exactly like the render crop, shot by shot. Captions and hook are HTML overlays at the same normalised positions the render uses; playback stays inside the cut.
- **Captions mode:**
  - presets Clean / Active word / Punch / Karaoke, mapping to ASS styles L / A / B / C (L is new);
  - size slider;
  - drag the caption on the video, or use the arrow keys when it has focus; the position is stored as x, y in 0–1;
  - more options: text colour, highlight, background box, words per line;
  - Save style.
- **Hook:** text (≤45 characters, never truncated), show/hide, duration, and position by dragging. The default is the top band for 3 s.
- **Caption text correction:** "Edit caption text" lists the transcript lines.
  - Saving writes `transcript_edits.json`, with history, original and new text.
  - The paid result file is never modified.
  - Word timing is kept: new words are spread over the original words' time span.
  - Renders record `transcript_edited`; the export page says "text corrected by you".
- **Safe zones:** platform switch TikTok / Reels / Shorts / Off.
  - Overlays come from `hayclips/platforms.py`: one documented table, approximate and conservative.
  - They are editor and export only, never burned in.
  - An overlap gives a subtle warning ("Captions overlap the TikTok caption and sound area") and a "Move to safe area" button.
- **Trim mode:** start and end handles over the downloaded window, with ±0.1 s nudges, live playback of the result, Save trim and Reset to automatic.
- **Frame mode:** one slider per camera shot moves the crop horizontally. The preview updates live. Save, or Reset to automatic (restores the plan's `auto_shots`). The Phase 1c metric `framing_adjusted` detects the edit.
- **Render:** a local job with the clip's look (`use_look`).
- **Brand Kit:** its defaults are copied onto clips chosen afterwards. Existing clips are untouched (tested).

## Browser-test coverage (`tests/test_browser_e2e.py`, Playwright + headless Chromium)

**`test_creator_workflow_youtube`:**
- homepage loads and Start creating → dashboard;
- Settings Connect shows the "not available" message;
- Brand Kit set to Karaoke;
- New video: an invalid link is refused visibly; a valid link runs Find clips through the captions → candidates chain;
- Choose clips: in-app preview of a candidate; trim by moving the start handle; choose 2 of 3 or more; the sticky bar says "2 selected";
- Add captions: the cost screen shows "2 clips selected" and "… will be transcribed";
- paid mode off: blocked in the UI, and an injected form POST is refused with 0 submits and 0 jobs;
- web restarted in fake-only paid mode: permission recorded, confirmed;
- only the 2 chosen clips are downloaded, transcribed (fake submits = 2) and rendered; unchosen moments are never downloaded;
- Brand Kit preset applied to the new clips; the trim persisted;
- editor:
  - Clean preset selected;
  - caption dragged, and the stored y changes;
  - TikTok zones drawn, with a warning and Move to safe area;
  - Reels overlay;
  - Save style;
  - hook text and duration saved;
  - caption text corrected;
  - crop slider moved and saved;
- Render with **no new submit**; `render.json` shows `transcript_edited` and the Clean look;
- export: 9:16 shown, Shorts overlay, MP4 downloaded (`ftyp` header) and SRT downloaded containing the corrected text;
- Add to calendar → the November 2026 grid shows the item at 19:00;
- total fake submits remain 2.

**`test_creator_workflow_upload`:** Upload tab → synthetic MP4 → project → local discovery → candidates → the stage pill reads "Choose clips" → zero requests to the fake Harmar.

Server-side tests added:
- `test_upload_source.py` (6): valid upload, bad type, non-video bytes, size cap, CSRF and origin, and the free local pipeline with zero paid requests.
- `test_web_editor.py` (5): editor data, look / hook / text / crop saves that queue nothing, a render job in the cpu pool with `use_look`, export and calendar statuses, Brand Kit defaults, logo type and size checks.
- `test_transcript_edits.py` (2).
- Caption-look unit tests (4) and a render-level look and edit test.
- Crop adjust/reset and safe-zone tests; product-page, Choose clips and Add captions web tests (9).

## Full test result

```
.venv/bin/python -m pytest -q   ->   302 passed in 204 s (2026-10-03, offline; includes the pilot-03 regression and both browser tests)
```

## Unresolved UX problems

- **Approximate caption preview:** the live overlay groups words with a simpler rule than the renderer's dynamic programming, so a screen can break differently from the final render. The render is authoritative.
- **No visual timeline:** trim is two sliders over the window, not a waveform.
- **Hook position is drag-only:** it has no numeric control and no snap guides.
- **Frame mode:** a slider per shot, not direct dragging of a crop box on the preview.
- **Candidate excerpts:** these come from YouTube or discovery captions and can be messy; with discovery transcripts they are approximate by design.
- **Unreconciled submissions:** the Add captions screen only says "needs checking"; reconciliation is still a CLI step.
- **Not yet reworked:** the Review and Summary pages kept their Phase 1c layout (restyled with the tokens).
- **Video-only gaps:** a project with an uploaded video that has no audio track gets no discovery job and no explanation yet.
- **Responsive gaps:** the editor is desktop-first. Below 860 px it stacks, but drag on touch screens is untested.

## Features deliberately deferred

- Burning the logo into videos. It is stored only.
- B-roll, avatars, music.
- Multi-track or timeline editing; tracking-based reframing.
- Social publishing, OAuth, scheduling automation. The calendar is planning only.
- Mood or category discovery; LLM hooks.
- Login, teams, billing, hosting.
- Moving paid records into Postgres.

## Recommendation for the first creator demo

1. Start with `scripts/dev-up.sh` and open http://127.0.0.1:8765/. Show the homepage for 10 seconds, then click Start creating.
2. Use one consented episode. Show Find clips → Choose clips: preview two moments, trim one, choose two or three. Point at the sticky "N selected · m:ss" bar and the cost screen. This is the "spend late" story.
3. Because the Harmar balance is reserved, either:
   - authorise one short paid run of 60–120 s of audio, one or two clips; or
   - demo the editor and export on `pilot-03` (Dashboard → Open an existing pilot folder), which already has transcripts.
4. In the editor, switch to Clean, drag the caption, turn on the TikTok overlay and press "Move to safe area". Fix one word in "Edit caption text", Render, and download on the Export page.
5. Ask the creator which preset they would post, how long fixing took, and what they would never accept. Record it in the Review page decision and the friction log (kept private).
