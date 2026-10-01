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

Free YouTube captions -> `clipper.py` (sentence-edge candidate windows) -> operator picks in
`suggestions.json` -> `fetch_clips.py` (only padded windows) -> `harmar_clips.py` (paid, word
timestamps, cached by clip hash) -> `burn_captions.py` (Harmar sentence re-snap, `reframe.py`
speaker crop via `.venv`, caption styles A/B/C, loudnorm, `review.html`, ffprobe checks).
Never re-render a `clip_NN.mp4` that has a Harmar cache. `README.md` has the file map, the
`suggestions.json` fields, pilot status and limitations. `research/` has the caption, cutting and
framing research. `outreach-hy.md` is a draft. The heuristic cannot understand content deeply
yet; human quality review is still the next product task.

## First milestone

One consented Armenian video under ten minutes; 2–3 candidate clips; a human
editor identifies which, if any, can become a publishable Reel and how long
fixing it takes. Then decide whether to improve selection, Armenian captions,
or framing. Do not build login, billing, or a hosted uploader yet.
