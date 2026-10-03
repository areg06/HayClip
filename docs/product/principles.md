# HayClips product principles

Use these when making any UI decision. If a change breaks one, it needs a reason written next to it.

1. **Video first.** The video is usually the strongest visual element on the page.
2. **One primary action.** Each page has one obvious next action, styled as the only primary button.
3. **Space is intentional.** Prefer whitespace, fewer controls, short labels and clear hierarchy.
   Avoid long paragraphs, unnecessary cards, excessive badges, dense dashboards and fake statistics.
4. **Explain on demand.** Default copy is short. Details live behind tooltips, small help text or expandable sections.
5. **Spend late.** Paid processing happens only after the user has chosen which clips are worth finishing.
6. **Process only selected clips.** If 2 of 6 clips are chosen, expensive processing runs for those 2 only.
7. **Preview before spending.** Candidates can be previewed and trimmed before any final transcription or captions.
8. **Human stays in control.** Candidates and scores are suggestions. Never say "viral score",
   "guaranteed performance" or "perfect clip".
9. **Progressive controls.** The simplest controls show first; advanced ones appear on request.
10. **User-language actions.** Prefer Find clips, Choose clips, Add captions, Edit, Export, Schedule
    over vague verbs like Generate, Regenerate or Process.

## Cost rules that the UI must keep

- Previewing, trimming, choosing and moving captions never trigger transcription or a full render.
- Live editor feedback happens in the browser. Server work (render) needs an explicit "Render" click.
- Uploaded videos are never sent whole to a paid service. A cheap local "discovery transcript" finds
  moments; only chosen clips get the final (paid) transcription.

## Visual system

Tokens live in `hayclips/web/static/app.css` (`:root`). Roughly 90% neutral, 10% brand.

| Token | Value | Use |
|---|---|---|
| `--bg` | `#F7F7F5` | page background |
| `--surface` | `#FFFFFF` | cards, inputs |
| `--ink` | `#151515` | primary text |
| `--ink-2` | `#707070` | secondary text |
| `--line` | `#E8E8E5` | borders, dividers |
| `--brand` / `--brand-hover` | `#5B5FEF` / `#494DD8` | the one primary button per view, selection, focus |
| `--ok` / `--warn` / `--bad` | `#1F9D69` / `#D98B27` / `#D84A4A` | status only, always with words |

- **Spacing:** 4 px scale (`--s1` … `--s9`: 4, 8, 12, 16, 24, 32, 48, 72, 112).
- **Type:** 12 / 14 / 16 / 20 / 28 / 44 px, system UI with Noto Sans Armenian.
- **Radius:** 8 / 12 / 18 px and pill. **Shadows:** two levels, both soft.
- **Buttons:** `primary` (brand, one per view), default (outlined), `ghost`, `danger` (text colour only), sizes `small` / `big`.
- **Status pills:** a short creator word ("Choose clips", "Ready") plus a colour dot; colour is never the only signal.
