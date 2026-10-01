#!/usr/bin/env python3
"""Print a full-frame 9:16 speaker crop plan for a landscape clip (see hayclips/media/reframe.py).

Usage: .venv/bin/python reframe.py clips/<clip_id>/wide.mp4 > crop.json
"""
import json
import sys

from hayclips.errors import PipelineError
from hayclips.media.reframe import plan_crop

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    try:
        print(json.dumps(plan_crop(sys.argv[1]), indent=1))
    except PipelineError as exc:
        sys.exit(f"error: {exc}")
