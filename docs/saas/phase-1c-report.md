# Phase 1c report: a reliable, comfortable local workflow for operator testing

Date: 2026-10-03. Scope: the five Phase 1c areas only, with no new infrastructure.

**Restrictions held throughout:**
- **No real Harmar request.** The real paid ledger (`~/.hayclips/paid-ledger.jsonl`) is still empty and the 133 s balance is still reserved.
- **Paid behaviour unchanged.** The paid-attempt records stay in the project folders as the authoritative record, and the exactly-once guarantees are unchanged.
- **Built inline.** The work was done in the lead session rather than by spawned agents, because earlier agents had stopped when the session and weekly usage limits ran out.

## Commits (after `b6b8479`, the last Phase 1b push)

The first two commits come from the founder's own first operator session on 2026-10-03 (see "Found by using it").

| Commit | Purpose |
|---|---|
| `c51e8e2` | A render job that rendered nothing now fails with a reason. Before, it reported SUCCEEDED with every clip skipped |
| `5c0397e` | Web: candidates are blocked until captions exist and render until a transcript exists; human-readable times; "watch on YouTube" links; the review page explains unrendered clips |
| `17c0bc9` | Public `fetch_selected_clips` API used by the CLI and the worker |
| `0c1f5ef` | `MISSING_STORAGE` state; no jobs for a missing project folder; confirmed, audited soft removal |
| `7debb5e` | Web UI for missing project folders and stale-entry removal |
| `f1a1b9a` | Transcription service: a `before_submit` hook, the last free point to stop a paid run |
| `bd8de24` | Worker policy for database outages and lease loss |
| `8b753f9` | Crop plans record their automatic baseline, so hand edits to framing are detectable. Migration 003 is also in this commit |
| `d3eeccc` | Operator-effort metrics on review decisions, change-only edit logging, a local summary page |
| `7af4c1c` | Focused UX pass |
| `5f11488` | `HAYCLIPS_FORBID_REAL_HARMAR` hard block for all test processes; fake yt-dlp can serve custom captions |
| `d6c6612` | Playwright click-through test of the whole operator workflow |
| `2a245e0` | Order of guard messages in the Harmar client |
| `3b9d898` | Clearer crop warnings |
| `56eb7a1` | Review players at phone size; optional screenshots from the browser test |

36 files changed, +1361 −111.

## 1. Real browser end-to-end test

`tests/test_browser_e2e.py` uses Playwright and headless Chromium. It starts the real web app and worker as subprocesses, on a throwaway Postgres database, with the fake yt-dlp and an in-process fake Harmar on 127.0.0.1. The browser only clicks and types.

The single scenario covers all 23 required steps:
1. **Dashboard, project creation, link check:** open the dashboard; an invalid link (`--exec=…`) is refused visibly and nothing is created; a valid project is created.
2. **Captions and candidates:** fetch captions; "Generate candidates" stays disabled until captions exist; generate candidates.
3. **Selection:** select two candidates with titles, then reorder them.
4. **Persistence:** reload the page; the state persists.
5. **Window download:** download the windows through the fake source.
6. **Consent:** record consent through the form.
7. **Paid mode off:** the paid page shows "disabled" and has no confirm form.
8. **Bypass attempt:** a devtools-style injected form POST is refused by the server; no transcribe job is queued and the fake receives 0 submits.
9. **Fake transcription:** the web app is restarted in paid mode (fake only) and the operator confirms in the UI. The fake receives exactly 2 submits, one per clip.
10. **Render and review:** render style A, then open review.
11. **Edits:**
    - edit the title and a valid hook;
    - a hook over 45 characters is capped by the input, and when that cap is removed the server refuses it visibly without truncating or saving it;
    - edit the trim.
12. **Decision and summary:** save a review decision; the summary shows "Would post: 1 of 1" and "Needed a trim edit: 1".
13. **Downloads:** the MP4 (checked: `ftyp` header, over 10 KB) and the SRT through the visible buttons.
14. **Cancel:** with the worker restarted in slow mode, a running re-render is cancelled from the Cancel button and reaches CANCELLED.
15. **Worker restart:** the worker is restarted and the page reloaded; the job state, clip order, edited title and review state are intact, and the fake still shows only 2 submits.

It runs in about 25 s and passed 5 runs out of 5.

**Why it cannot spend money:**
- Every subprocess runs with `HAYCLIPS_FORBID_REAL_HARMAR=1`. The client refuses the real host when this is set, even in paid mode; `test_guards.py` tests this.
- The fixture refuses to start unless the Harmar base URL is loopback.
- The pytest process itself refuses to start if the paid opt-in is set.
- Setting `HAYCLIPS_E2E_SCREENSHOTS=<dir>` saves full-page screenshots for a human UX review.

## 2. Missing project folders

- A project whose folder or `project.json` is gone has state **`MISSING_STORAGE`**. It is computed from the filesystem and never repaired automatically.
- **Dashboard:** the project is still listed, with a red "files missing" tag.
- **Opening the project:** a page explains that the local project files are missing, says to restore the folder from backup because the paid transcripts live inside it, and offers stale-entry removal.
- **Processing is blocked in three places:**
  - `queue.enqueue` refuses any job for a missing or removed project, for both the CLI and the web;
  - the worker fails an already-queued job with a `MISSING_STORAGE` message on the first attempt, with no retry and no files created;
  - web POSTs redirect with "nothing was queued or changed".
- **Removal:**
  - only possible while files are missing;
  - requires typing the exact project name and your own name;
  - it is a soft delete: `removed_at` and `removed_by` are set, queued jobs are cancelled, and the rows, jobs and events are kept;
  - it writes a `stale_project_removed` audit event.

## 3. Public fetch API

`hayclips.fetch.fetch_selected_clips(repo, clip_ids=None, *, source_factory, settings, raise_errors, on_progress, should_stop)`:
- It is the one fetch path for the worker handler and `fetch_clips.py`. `fetch_project` remains as a thin alias.
- It requires listed ids to be selected.
- `on_progress` and `should_stop` callbacks let the worker report progress and stop on cancellation or lease loss between clips.
- Regression tests cover: stable ids preserved, only listed selected clips fetched, unselected or unknown ids refused, verified artifacts reused with `window.json` byte-identical, partial files cleaned after a failed download, alignment metadata (`derived_from` hash) preserved, callbacks, and a static check that the worker and CLI use no private helpers.

## 4. Database outage and lease-loss policy (`hayclips/jobs/worker.py`)

| Situation | Behaviour |
|---|---|
| Heartbeat fails once or briefly | Retried on a fresh connection at the next beat (every lease/3). Nothing else changes |
| Ownership cannot be re-proved within **2/3 of the lease** | The lease becomes **UNCERTAIN** before it could expire and another worker could take the job. The handler is told to stop at its next check, the outcome is **not written**, and the job stays uncertain even if the database comes back |
| Lease lost (another owner) | Same as uncertain: stop and write nothing |
| Database unreachable while claiming | Logged and retried with backoff up to 30 s; nothing is claimed; the worker does not crash |
| Result write fails | Retried on fresh connections (0.5 / 1 / 2 / 4 s). If it still fails, the job is left RUNNING for the reaper and the worker says so ("result not recorded") |
| After the reaper | Safe jobs retry later; they are idempotent (verified artifacts are reused). Paid jobs are never re-run |
| Paid job | The handler passes a `before_submit` hook to the transcription service. If the job was cancelled or ownership can't be proved, the run stops **before the charging POST**: the attempt is FAILED "before the charge point" and safe to retry with confirmation. After a submit, polling is free and continues |

Tests in `tests/test_jobs_outage.py` simulate:
- a heartbeat failing once (the job completes and is recorded);
- a heartbeat failing repeatedly (the job stops, nothing is written, the reaper later retries it);
- a claim during an outage;
- a result write failing twice and then recorded;
- a result write never succeeding (left to the reaper);
- a lost lease during a safe job (it stops in under 6 s instead of running 20 s);
- a lost lease during a paid job (0 submits to the fake).

A service-level test checks that the `before_submit` hook stops a run with 0 submits.

## 5. UX pass

Found by looking at the real pages and the browser screenshots:
- **Workflow steps:** shown as pills; the current one is highlighted, completed ones get ✓, and a "Next:" sentence says what to do. On a paid-disabled server it says that new clips stop at the transcription step. An "N job(s) in progress" tag appears while jobs run.
- **Jobs:** plain names ("Download windows"), coloured status tags (waiting / running / will retry / done / failed / cancelled), the time queued, and errors in a readable box.
- **Selection:** clips are tagged "selected" or "unselected" on both the project and candidates pages.
- **Score explanation:** a "Why this score" list in plain words, largest effect first (for example "+2.00 ends on a complete sentence", "−0.80 starts with a weak connective «բայց»"). The banner still says it is a ranking heuristic, not a virality prediction.
- **Review page:**
  - each clip is badged "machine output — not reviewed" until a decision exists, then "reviewed: would post / maybe / would not post";
  - CHECK warnings sit in one boxed "N thing(s) to check before posting" list, or "No automatic warnings";
  - warnings are clearer: no negative times, the first shot says it used the centre, a brand-new crop plan is no longer flagged;
  - "Download MP4 (A)" and "Download SRT" are green buttons;
  - the cut field shows the current automatic or manual cut and is pre-filled when manual;
  - decision choices are large toggles, with the style pre-selected when only one was rendered;
  - players are phone-sized (9:16, up to 360 px wide) instead of filling the width;
  - unrendered clips say why (download or transcript missing) and play the downloaded landscape window.
- **Guards:** candidates can't be queued before captions, and rendering can't be queued before a transcript; the UI explains both.

## Operator metrics captured (migration 003)

Each `review_decisions` row now stores, from real operator actions only:

| Field | Source |
|---|---|
| `would_post`, `style`, `minutes_to_fix`, `notes`, `reviewer`, `created_at` | the decision form |
| `final_title`, `final_hook`, `final_trim` | the clip at decision time; `final_trim` null = automatic snap |
| `warning_count` | CHECK lines on the latest render |
| `title_edited`, `hook_edited`, `trim_edited` | `clip_edited` audit events, which now log only fields that actually changed |
| `framing_adjusted` | `crop.json` differs from its recorded automatic baseline; null when no baseline exists |
| `transcript_edited` | always null: transcript editing is not supported, so it is never guessed |

`/p/<id>/summary` shows, from the latest decision per clip:
- clips reviewed, and would post X of N (plus maybe);
- median minutes to fix;
- style choices;
- clips that needed a trim, hook or framing edit;
- a per-clip table.

It is labelled as local operator data: not creator feedback and not a performance prediction.

## Full test result

```
.venv/bin/python -m pytest -q   ->   272 passed in 186 s (2026-10-03, offline, including the pilot-03 regression and the browser test)
```

All Phase 1a and Phase 1b tests still pass. pilot-03 `.ass` files are still byte-identical to the Phase 1a baseline.

## Found by using it (the founder's first operator session, 2026-10-03)

1. `dev-up.sh` aborted when Postgres was already running. Fixed in `b6b8479`.
2. "Generate candidates" clicked before the captions finished gave a failed job. Now the button is disabled and the server refuses.
3. "Render" on clips without a transcript reported SUCCEEDED but rendered nothing. Now the button is replaced by an explanation and the job fails with the reason.
4. Times showed as SRT stamps (`00:12:10,733`). They now show as `12:10`.
5. There was no way to watch a candidate before selecting it. Each candidate now has a "watch this moment on YouTube" link.

## Unresolved risks

- **Paid path untested live:** the real paid path is still exercised only against the fake. The first real run needs founder authorisation.
- **Synthetic media:** the browser test uses synthetic captions and media (testsrc picture, short fake transcript), so it proves the workflow, not caption or cut quality.
- **No framing editor:** framing edits are detectable but there is no UI for them; operators edit `crop.json` by hand.
- **Unedited YouTube captions:** transcript text editing is not supported, so `transcript_edited` stays null.
- **Two stores:** paid-attempt records live in project folders while the job queue lives in Postgres (deliberately deferred).
- **Heartbeat granularity:** an uncertain lease stops a safe job only at its next check. FFmpeg renders check between clips, so one long render can run up to its timeout after the database disappears. Its result is never written.
- **Unsaved migration:** the founder's currently running app was started before migrations 002 and 003. Restart `scripts/dev-up.sh` to apply them.

## Recommendations for the first creator/operator validation session

1. Restart the app (`scripts/dev-up.sh`) so the new migrations and UI load.
2. Pick one consented episode the operator hasn't seen. Time each step with the friction-log template (copy it to a private place first).
3. Choose candidates using the "Why this score" list and the YouTube moment links, and note how many had to be skipped as incoherent.
4. Because new transcription is blocked while the balance is reserved, either:
   - authorise one short paid test (about 60–120 s, audio-only, one or two clips); or
   - do the review part of the session on `pilot-03`, which already has transcripts.
5. For every rendered clip, save a decision with honest minutes-to-fix. Edit hook, title and trim in the app rather than outside it, so the metrics reflect the real effort.
6. Afterwards, read `/p/<id>/summary` together. Decide whether selection, captions or framing is the biggest correction cost before planning more features.
