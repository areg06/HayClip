# Start the Claude Code agent team

From this folder, run `claude` in an **interactive** terminal. Project settings
enable Claude Code's experimental agent teams. Paste this first message:

> Read CLAUDE.md, README.md and the existing code. Create an agent team with
> named teammates harmar-api, clip-engine, quality-review, and creator-research.
> Use the shared task list and have teammates exchange findings directly.
> First, audit the Harmar integration against the official API docs, review the
> current clipper with synthetic fixtures, and prepare a 10-minute consented
> pilot and personalized outreach. Assign non-overlapping file ownership. Do
> not call the paid API, contact creators, or publish anything yet. Report what
> is ready and what requires my API key or a consented video.

For a real pilot, follow "Running a pilot" in `README.md`: pick moments from free captions,
then send only the chosen clips to Harmar with the key read from `~/.config/harmar/key`:

```bash
HARMAR_API_KEY="$(cat ~/.config/harmar/key)" python3 harmar_clips.py pilot-04
```

Open `pilot-04/review.html`. Do not paste the
key into Claude chat or commit it. Harmar bills seconds when the transcript is
submitted; the script checks balance and length before submitting, and caches
the job ID and completed result in the output folder. If there is an uncertain
submission failure before the job ID is returned, inspect your Harmar dashboard
before retrying.

Agent teams require an interactive Claude Code session. They use more Claude
tokens than a single session; start the four teammates for the first audit and
close idle teammates after it. The team files define roles; they do not run
until Claude Code launches them.
