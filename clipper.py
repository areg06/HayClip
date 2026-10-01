#!/usr/bin/env python3
"""Find candidate clip windows in free captions (machine output only; never edits selected clips).

Usage:
  .venv/bin/python clipper.py --srt pilot-04/source/src.hy-orig.srt --duration 5400 --out pilot-04 \
      [--source-url URL] [--skip-start 75] [--skip-end 120] [--count 6] [--min-seconds 25] [--max-seconds 60]

Writes <out>/candidates.json (creating <out>/project.json if missing) and prints each candidate id
with its score explanation. Select windows with `.venv/bin/python -m hayclips select <out> <candidate_id>`.
Scores are ranking heuristics for a human, not virality predictions.

The old `--harmar` mode (sending a whole local video to Harmar) was removed in Phase 1a: paid
transcription now runs only on selected windows through harmar_clips.py and its safeguards.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from hayclips.errors import PipelineError
from hayclips.media import ffmpeg as _ff
from hayclips.media.probe import duration_of as _duration_of
from hayclips.models import Line  # noqa: F401  (compatibility re-export)
from hayclips.project import ProjectRepo
from hayclips.selection import (candidates, explain, load_srt, pick, sentence_units,  # noqa: F401
                                stamp, write_srt)


@dataclass
class Clip:
    """Compatibility shim for older scripts: a plain time window."""
    start: float
    end: float
    score: float = 0.0
    text: str = ""


def clip_subtitles(lines, clip, path) -> None:
    write_srt(lines, clip.start, clip.end, Path(path))


def export(video, clip, output, crf: int = 23) -> None:
    _ff.export_letterbox(Path(video), Path(output), clip.start, clip.end - clip.start, crf=crf)


def duration_of(video) -> float:
    return _duration_of(Path(video))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Find candidate clip windows in Armenian captions (free, local).")
    ap.add_argument("--srt", type=Path, required=True, help="timecoded Armenian captions of the source")
    ap.add_argument("--duration", type=float, required=True, help="source length in seconds")
    ap.add_argument("--out", type=Path, required=True, help="project directory")
    ap.add_argument("--source-url", default="", help="source URL recorded in a new project.json")
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--min-seconds", type=float, default=25)
    ap.add_argument("--max-seconds", type=float, default=60)
    ap.add_argument("--skip-start", type=float, default=0, help="ignore the first N seconds (cold open, intro)")
    ap.add_argument("--skip-end", type=float, default=0, help="ignore the last N seconds (outro)")
    a = ap.parse_args(argv)
    if a.min_seconds <= 0 or a.max_seconds < a.min_seconds or a.count < 1 or a.duration <= 0:
        ap.error("invalid length, count or duration")
    try:
        repo = ProjectRepo(a.out)
        if not repo.exists():
            repo.init(a.out.resolve().name, {"kind": "youtube" if "youtu" in a.source_url else "unknown",
                                             "url": a.source_url, "duration": a.duration})
        chosen = pick(candidates(sentence_units(load_srt(a.srt)), a.duration, a.min_seconds, a.max_seconds,
                                 a.skip_start, a.skip_end), a.count)
        if not chosen:
            raise PipelineError("no candidate windows found", hint="try --min-seconds 10 or a source with more speech")
        repo.save_candidates(chosen, {"srt": str(a.srt), "duration": a.duration, "count": a.count,
                                      "min_seconds": a.min_seconds, "max_seconds": a.max_seconds,
                                      "skip_start": a.skip_start, "skip_end": a.skip_end})
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for c in chosen:
        print(f"{c.id}  {stamp(c.start)}–{stamp(c.end)}  ({c.end - c.start:.1f}s)  score {c.score:.2f}")
        print(f"    {explain(c)}")
        print(f"    {c.text[:160]}")
    print(f"{len(chosen)} candidates in {repo.candidates_path}; select with: "
          f".venv/bin/python -m hayclips select {a.out} <candidate_id> --title ... --hook ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
