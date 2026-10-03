# HayClips — team brief

Goal: test whether Armenian podcast creators would publish short clips selected
from long Armenian-language conversations. The current code is a local operator
prototype, not a public product. First run one creator-approved video and review
the output before expanding scope.

## Team workflow

When asked to run the team, act as lead. Spawn named teammates `harmar-api`,
`clip-engine`, `quality-review`, and `creator-research` from `.claude/agents/`.
Assign separate files/tasks and use the shared task list. Ask teammates to
message each other directly about API response shapes, segment boundaries,
quality findings, and creator feedback. Integrate their work in the lead session.
Do not have two teammates edit `clipper.py` at once; separate investigation and
implementation tasks or work in branches/worktrees. Teammates must report
evidence, risks, and exact files changed. End each batch by running a real local
fixture through FFmpeg and reviewing the MP4 dimensions and timestamps.

## Hard product constraints

- API key goes only in `HARMAR_API_KEY` environment variable; never commit it.
- Use Harmar's published async API contract. Check `/v1/balance` and keep the
  default pilot to <=600 seconds. Cache submitted job IDs and completed results;
  retrying a job must not silently submit another charge.
- Do not send creator media to Harmar without the creator's permission.
- Do not email creators or publish videos automatically. The founder reviews
  each recipient, message, and sample before outreach.
- Never claim a clip is viral, ready to post, or accurate without human review.
- Keep the pilot cheap; avoid paid infrastructure until a creator confirms
  the output is useful.

## Current architecture

Package `hayclips/` (Phase 1a, see `docs/saas/phase-1a-report.md`); root scripts are thin CLIs:
`clipper.py` (candidates.json) -> `python -m hayclips select/consent` (project.json) ->
`fetch_clips.py` (clips/<id>/wide.mp4 + audio.m4a + window.json) -> `harmar_clips.py` (durable
paid attempts, plan-only unless `--confirm-paid` and `HAYCLIPS_ALLOW_PAID_HARMAR=1`) ->
`burn_captions.py` (alignment check, crop, captions A/B/C, review.html). Clips have stable ids;
never rename/delete `clips/<id>/transcription/` or re-create recorded media. All external processes go
through `hayclips/proc.py`. Run with `.venv/bin/python`; tests (`pytest`) are offline and cannot spend
money. `docs/saas/` holds the SaaS plan. Phase 1b local web app: `scripts/dev-up.sh` (Postgres job queue + worker + FastAPI on 127.0.0.1); see `docs/saas/phase-1b-report.md`.
`outreach-hy.md` is a draft. The heuristic cannot understand content deeply yet; human quality
review is still the next product task.

## First milestone

One consented Armenian video under ten minutes; 2–3 candidate clips; a human
editor identifies which, if any, can become a publishable Reel and how long
fixing it takes. Then decide whether to improve selection, Armenian captions,
or framing. Do not build login, billing, or a hosted uploader yet.
