# HayClips SaaS — domain model (Phase 0)

Status: proposal. This validates and revises the founder's starting model against how the prototype
actually stores state (`suggestions.json`, `harmar/clip_NN/harmar_transcript.json`, `clip_NN.crop.json`,
file names). Types are PostgreSQL. All ids are `uuid` (v7, time-ordered). `created_at timestamptz
default now()` is on every table and is omitted below.

## 1. Changes from the starting model, and why

| Starting entity | Change | Reason (grounded in the prototype) |
|---|---|---|
| User | Kept; adds `status` | Needed later for auth. Phase 1 has one seeded operator row so ownership columns exist from day 1 |
| Project | Kept; `status` becomes derived/summary only | Real progress lives in jobs. A stored project status drifts |
| SourceMedia | Kept; adds `consent` link, `captions_lang`, `content_sha256` (uploads) | Captions come from `hy-orig` auto-subs. Consent belongs to the media, not the project |
| — | **New `CaptionTrack`** | The free YouTube SRT is an input artifact with its own provenance ("machine, unreviewed"). Pilots compare it with Harmar |
| CandidateClip | Split into **Candidate** (machine output, immutable) and **Clip** (a human-selected window with title/hook/pick_note) | Today `suggestions.clipper-top6.json` is machine output and `suggestions.json` is the human edit. On pilot-03 the operator moved clip_03's start 7.2 s earlier. Both must be kept for auditing |
| — | **New `MediaArtifact`** | The prototype has ~10 file kinds per clip, each with different retention and cost-to-rebuild. One table with `kind` + `storage_key` + `sha256` |
| Transcript | Keyed by unique `(provider, media_sha256, options_hash)`; states include `submitting`/`needs_attention` | Today the cache is keyed by the sha256 of `clip_NN.mp4`. Re-rendering that file "orphans" a paid transcript. A DB uniqueness rule makes double billing a constraint violation instead of a convention |
| — | **New `ProviderCharge`** (ledger) | Harmar reports `seconds_charged` (62/70/63 s on pilot-03). Cost guards and quotas need actual and estimated seconds per call |
| ClipEdit | Kept as **immutable revisions** (`revision` int, unique per clip); crop plan moved to an artifact plus an `crop_overrides` JSON | Today `caption_start/caption_end/snap` are overwritten in place in suggestions.json, so the history is lost. "How long did fixing take" needs revisions |
| RenderJob | Replaced by generic **Job** + **Render** | Downloads, crop plans, Harmar submit/poll and renders all need the same lease/retry/progress machinery. `Render` records what was rendered from which edit revision |
| RenderedAsset | Becomes a `MediaArtifact(kind='render')` linked from `Render` | Same storage and retention logic as every other file |
| ConsentRecord | Per **(source_media, provider, scope)**, with who/when/how and the text shown | The CLAUDE.md rule "no creator media to Harmar without permission" must be checkable before each paid submit |
| — | **New `ReviewDecision`** | The first milestone asks "which clip/style would you post, and how many minutes to fix?". That answer is product evidence and must be stored, not just chatted |
| — | **New `Quota`** (simple) | Cost guard: max provider seconds per project/day, before any billing exists |

## 2. Entities

### user
| field | type | notes |
|---|---|---|
| id | uuid pk | |
| email | citext unique null | null for the Phase 1 local operator |
| display_name | text | |
| role | text check in (`operator`,`creator`,`admin`) | |
| status | text check in (`active`,`disabled`) | |

### project
| field | type | notes |
|---|---|---|
| id | uuid pk | |
| owner_id | uuid fk user not null | every query filters on this |
| title | text not null | |
| archived_at | timestamptz null | soft delete; the hard delete flow is in §4 |

Project "status" is computed: draft → has candidates → has selected clips → transcribed → reviewed.

### source_media
| field | type | notes |
|---|---|---|
| id | uuid pk | |
| project_id | uuid fk | |
| source_type | text check in (`youtube`,`upload`) | |
| youtube_url | text null | normalized canonical `https://www.youtube.com/watch?v=<id>` |
| youtube_id | text null | unique per project |
| upload_artifact_id | uuid fk media_artifact null | |
| duration_s | numeric(10,3) | from yt-dlp metadata / ffprobe |
| metadata | jsonb | title, channel, height; no stream URLs (they expire and leak tokens) |
| permission_basis | text not null | operator statement, e.g. "creator agreed by email 2026-09-30" |

### caption_track
| field | type | notes |
|---|---|---|
| id | uuid pk | |
| source_media_id | uuid fk | |
| origin | text check in (`youtube_auto`,`youtube_manual`,`upload_srt`,`local_whisper`) | |
| lang | text | `hy-orig` today |
| artifact_id | uuid fk media_artifact | raw SRT |
| cue_count | int | 2202 on pilot-03 |
| quality_notes | jsonb | overlap fixed count, terminal punctuation ratio |

### candidate (machine output, immutable)
| field | type | notes |
|---|---|---|
| id | uuid pk | |
| caption_track_id | uuid fk | |
| analysis_run_id | uuid | groups one run with its params |
| params | jsonb | SelectParams used (min/max/skip/count) |
| rank | int | |
| source_start, source_end | numeric(10,3) | |
| text | text | YouTube caption text |
| score | numeric(6,3) | **heuristic ranking, not a prediction** |
| score_parts | jsonb | `{ending, opening, pace, laughs, filler_penalty, silence, length}` for explainability |
unique (analysis_run_id, rank)

### clip (human-selected window)
| field | type | notes |
|---|---|---|
| id | uuid pk | |
| project_id | uuid fk | |
| candidate_id | uuid fk null | null if the operator typed times by hand |
| ordinal | int | clip_01, clip_02… (stable display order) |
| planned_start, planned_end | numeric(10,3) | may differ from the candidate (pilot-03 clip_03: −7.2 s) |
| pad_s | numeric(5,2) default 5 | |
| pad_start_s | numeric(5,2) | actual padding (clamped at 0 s) |
| title | text | |
| pick_note | text | why chosen or changed |
| state | text | `selected`, `fetched`, `transcribed`, `in_review`, `approved`, `rejected` |
unique (project_id, ordinal)

### media_artifact
| field | type | notes |
|---|---|---|
| id | uuid pk | |
| project_id | uuid fk | for ownership and deletion |
| clip_id | uuid fk null | |
| kind | text check in (`upload_source`,`captions_srt`,`window_wide`,`harmar_preview`,`transcript_raw`,`crop_plan`,`ass`,`srt`,`render`,`thumbnail`) | |
| storage_key | text unique | content-addressed path |
| sha256 | char(64) not null | |
| bytes | bigint | |
| mime | text | |
| probe | jsonb null | width, height, duration, codecs (ffprobe) |
| retention_class | text check in (`paid_anchor`,`regenerable`,`derived`,`source`,`ephemeral`) | |
| expires_at | timestamptz null | set by the retention policy; never set for `paid_anchor` |
| deleted_at | timestamptz null | tombstone; the row is kept for audit |
| produced_by_job_id | uuid fk job null | |
index (clip_id, kind)

### consent_record
| field | type | notes |
|---|---|---|
| id | uuid pk | |
| source_media_id | uuid fk | |
| provider | text | `harmar` |
| scope | text check in (`selected_windows_only`,`full_media`) | today: selected windows only |
| granted_by | text | creator name/contact as recorded by the operator |
| evidence | text | e.g. "email 2026-09-30", free text; no file upload in Phase 1 |
| recorded_by | uuid fk user | |
| shown_text | text | the exact consent sentence the operator confirmed |
| accepted_at | timestamptz | |
| revoked_at | timestamptz null | revocation blocks new submits; existing transcripts are kept unless deletion is requested |
unique (source_media_id, provider, scope) where revoked_at is null

### transcript (paid; the idempotency anchor)
| field | type | notes |
|---|---|---|
| id | uuid pk | also sent to the provider as an idempotency key if supported |
| clip_id | uuid fk | |
| provider | text | `harmar` |
| media_artifact_id | uuid fk | must be `harmar_preview` |
| media_sha256 | char(64) | copied for the unique key |
| options | jsonb | `{"timestamps":"word","punctuation":true,"source_lang":"hy"}` |
| options_hash | char(64) | sha256 of canonical JSON |
| state | text check in (`submitting`,`submitted`,`completed`,`failed`,`needs_attention`) | |
| provider_job_id | text null unique | |
| confirmed_by | uuid fk user | the explicit "spend S seconds" confirmation |
| raw_artifact_id | uuid fk media_artifact null | full provider JSON |
| words | jsonb null | normalized `[{start,end,text,speaker,turn_start,script}]`; `script` ∈ hy/cyrl/latn to flag Cyrillic words |
| segments | jsonb null | |
| quality | text null | provider's `quality` field |
| error | text null | |
**unique (provider, media_sha256, options_hash)**. A second submit of the same media and options becomes a
DB error before any HTTP call.

### provider_charge (ledger)
| field | type | notes |
|---|---|---|
| id | uuid pk | |
| transcript_id | uuid fk unique | |
| estimated_s | int | ceil(duration) at confirmation |
| charged_s | int null | provider's `seconds_charged` |
| balance_before_s, balance_after_s | int null | from `/v1/balance` |
| status | text check in (`reserved`,`charged`,`released`,`disputed`) | |

### clip_edit (immutable revisions)
| field | type | notes |
|---|---|---|
| id | uuid pk | |
| clip_id | uuid fk | |
| revision | int | 1 = automatic snap |
| parent_revision | int null | |
| trim_start, trim_end | numeric(8,3) | relative to the padded window (today `caption_start/end`) |
| snap_mode | text check in (`auto`,`manual`) | |
| snap_note | text null | e.g. "kept the reaction «Մերսի» as the ending" |
| hook | text null check (char_length(hook) <= 45) | |
| hook_approved_by | uuid fk user null | the hook is rendered as "draft" until approved |
| caption_bottom | int default 930 check (caption_bottom between 600 and 1100) | |
| crop_plan_artifact_id | uuid fk media_artifact null | |
| crop_overrides | jsonb | `[{shot_start, x}]`; validated range 0..(width-crop_w) |
| transcript_id | uuid fk | |
| author_id | uuid fk user | |
unique (clip_id, revision)

### render
| field | type | notes |
|---|---|---|
| id | uuid pk | |
| clip_edit_id | uuid fk | |
| style | text check in (`A`,`B`,`C`) | |
| job_id | uuid fk job | |
| artifact_id | uuid fk media_artifact null | the MP4 |
| ass_artifact_id, srt_artifact_id | uuid fk null | |
| checks | jsonb | `CHECK:` findings: size, duration vs planned, cue bounds, faces/drift |
| loudness | jsonb | measured I/TP |
unique (clip_edit_id, style). Re-rendering the same revision reuses the row; a new revision gives a new row.

### job
| field | type | notes |
|---|---|---|
| id | uuid pk | |
| project_id | uuid fk | |
| type | text | `fetch_captions`, `analyze`, `fetch_window`, `plan_crop`, `transcribe_submit`, `transcribe_poll`, `render_style`, `cleanup` |
| queue | text check in (`io`,`cpu`,`paid`) | |
| state | text | see job-state-machine.md |
| payload | jsonb | ids only; never secrets, never raw argv from the browser |
| idempotency_key | text unique | e.g. `render:{clip_edit_id}:{style}`, `fetch_window:{clip_id}:{start}:{end}:{pad}` |
| priority | int default 100 | |
| attempts, max_attempts | int | paid submit: max_attempts = 1 |
| run_after | timestamptz | backoff |
| lease_until, heartbeat_at | timestamptz null | |
| progress | numeric(4,3) | 0..1 |
| progress_note | text | |
| error | text null | |
| cancel_requested | bool default false | |
| started_at, finished_at | timestamptz null | |

### review_decision
| field | type | notes |
|---|---|---|
| id | uuid pk | |
| clip_id | uuid fk | |
| reviewer | text | "founder", "creator: <role>" (no personal data needed) |
| would_post | text check in (`yes`,`with_fixes`,`no`) | |
| preferred_style | text null check in (`A`,`B`,`C`) | |
| minutes_to_fix | numeric(5,1) null | the milestone metric |
| fix_kinds | text[] | `cut`,`caption_text`,`caption_timing`,`framing`,`hook`,`audio` |
| notes | text | |

### quota
| field | type | notes |
|---|---|---|
| scope | text | `global`, `project:<id>`, `user:<id>` |
| provider | text | |
| max_seconds_per_day | int | default 600 (today's per-run cap) |
| max_seconds_per_project | int | |
primary key (scope, provider)

## 3. ER sketch

```
user 1─* project 1─* source_media 1─* caption_track 1─* candidate
                 │           │ 1─* consent_record
                 │           └─────────────┐
                 ├─* clip *─0..1 candidate │
                 │    │ 1─* media_artifact (window_wide, harmar_preview, crop_plan, ass, srt, render)
                 │    │ 1─* transcript 1─1 provider_charge
                 │    │ 1─* clip_edit (revisions) 1─* render ─1 job
                 │    └ 1─* review_decision
                 └─* job
quota (by scope)
```

## 4. Invariants (enforced in code and, where possible, in the DB)

1. A `transcript_submit` job may run only if: an active consent_record exists for (source, provider, the
   window scope), `confirmed_by` is set, the quota is not exceeded and no transcript row exists for the unique key.
2. A `harmar_preview` artifact referenced by any transcript is `paid_anchor`. Expiry never touches it.
   Deleting it requires the project-delete flow, which shows the seconds that a re-run would cost.
3. `clip_edit` rows are never updated. Edits create a new revision.
4. A render's MP4 must pass the probe checks (720x1280, |duration − (trim_end − trim_start)| ≤ 0.1 s) or the render is
   marked `checks_failed`. The review UI shows it with a warning rather than hiding it.
5. All ownership checks go through `project.owner_id`. Child rows carry `project_id` directly
   (media_artifact, job) so that signed-URL minting needs one indexed lookup.

## 5. Mapping from today's files (for the importer in the migration plan)

| Prototype file | Model |
|---|---|
| `pilot-NN/source/*.srt` | caption_track + media_artifact(captions_srt) |
| `suggestions.clipper-top6.json` | candidate rows (one analysis_run) |
| `suggestions.json` entries | clip + clip_edit revision 1 (trim, snap, hook) |
| `clip_NN.mp4` | media_artifact(harmar_preview, paid_anchor) |
| `clip_NN.wide.mp4` | media_artifact(window_wide) |
| `harmar/clip_NN/harmar_transcript.json` | transcript(completed) + provider_charge(charged_s = seconds_charged) |
| `clip_NN.crop.json` | media_artifact(crop_plan) |
| `clip_NN_X.ass/.mp4`, `clip_NN.srt` | render + media_artifacts |

## 6. Open questions

- Does Harmar accept an idempotency key or a client reference on submit? (ai-transcription to confirm with the docs or support.)
- Should creators get their own `user` rows with view-only access to review pages (Phase 2), or are
  signed share links enough? Product-ux and the founder decide.
