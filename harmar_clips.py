"""Transcribe a pilot folder's clip_NN.mp4 files with Harmar.

Usage: HARMAR_API_KEY=... python3 harmar_clips.py pilot-02
Each clip is cached in <pilot>/harmar/clip_NN/, so re-running never resubmits a finished or pending job.
New jobs ask for word timestamps (no surcharge per Harmar's docs) for the active-word caption style.
"""
import os
import sys
from pathlib import Path

import clipper as c

pilot = Path(sys.argv[1]).resolve()
clips = sorted(pilot.glob("clip_[0-9][0-9].mp4"))
total = sum(c.duration_of(v) for v in clips)
print(f"{len(clips)} clips, ~{total:.0f}s to transcribe")
pending = [v for v in clips if not (pilot / "harmar" / v.stem / "harmar_transcript.json").exists()]
need = sum(c.math.ceil(c.duration_of(v)) for v in pending)
if pending:
    left = int(c.api_json("/v1/balance", os.environ["HARMAR_API_KEY"]).get("seconds_remaining", 0))
    if need > left:
        sys.exit(f"New jobs need ~{need}s but only {left}s remain; nothing was submitted")
for video in clips:
    out = pilot / "harmar" / video.stem
    out.mkdir(parents=True, exist_ok=True)
    duration = c.duration_of(video)
    lines = c.harmar_transcribe(video, out, duration, 600, timestamps="word")
    print(f"{video.stem}: {duration:.1f}s -> {len(lines)} segments")
print("balance:", c.api_json("/v1/balance", os.environ["HARMAR_API_KEY"]))
