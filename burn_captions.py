#!/usr/bin/env python3
"""Render captioned vertical clips for every selected clip of a project and rebuild review.html.

Usage: .venv/bin/python burn_captions.py pilot-03 [--styles A,B,C] [--caption-bottom 930] [--no-hook]

Styles (research/caption-style.md): A active word, B one-word punch, C karaoke box.
Per clip it verifies the transcribed media and render source by sha256, proves the caption timeline
matches the render source, snaps the cut to Harmar sentences (a manual trim in project.json wins),
plans the speaker crop, normalises loudness and writes clips/<id>/render/<style>.mp4 + render.json.
Exit code 1 when any clip failed or any CHECK needs a human look; 2 on a setup error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hayclips.captions.ass import CAPTION_BOTTOM
from hayclips.errors import PipelineError
from hayclips.project import ProjectRepo
from hayclips.render import render_project


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--styles", default="A", help="comma list of A, B, C")
    ap.add_argument("--caption-bottom", type=int, default=CAPTION_BOTTOM,
                    help=f"y of the caption text's bottom edge in the 720x1280 frame (default {CAPTION_BOTTOM})")
    ap.add_argument("--no-hook", action="store_true", help="skip the top hook text even if a clip has one")
    a = ap.parse_args(argv)
    styles = [s.strip().upper() for s in a.styles.split(",") if s.strip()]
    try:
        results = render_project(ProjectRepo(a.project), styles=styles, caption_bottom=a.caption_bottom,
                                 hook_enabled=not a.no_hook)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    bad = False
    for r in results:
        print(f"#{r.order} {r.clip_id}: {r.status} {r.message}")
        for st, o in r.outputs.items():
            print(f"    {st}: {o['width']}x{o['height']}, {o['duration']:.2f}s, {o['events']} events")
        for c in r.checks:
            print(f"CHECK: {r.clip_id}: {c}")
        bad |= r.status == "failed" or bool(r.checks)
    print(f"review page: {Path(a.project).resolve() / 'review.html'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
