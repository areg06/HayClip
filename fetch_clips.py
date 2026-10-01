"""Download only the selected windows of a consented YouTube video.

Usage: .venv/bin/python fetch_clips.py pilot-04
For each selected clip in project.json (display order) this writes clips/<clip_id>/wide.mp4 (landscape
window with `pad` seconds on both sides, for rendering), audio.m4a (the exact bytes Harmar will be sent)
and window.json (their sha256 hashes). Already fetched clips are verified and skipped; recorded files
are never overwritten. See hayclips/fetch.py.
"""
import sys
from pathlib import Path

from hayclips.errors import PipelineError
from hayclips.fetch import fetch_project
from hayclips.project import ProjectRepo


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    repo = ProjectRepo(Path(argv[0]))
    try:
        results = fetch_project(repo)
        project = repo.load()
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    failed = 0
    for r in results:
        clip = project.clip(r.clip_id)
        if r.status == "error":
            failed += 1
            print(f"#{clip.order} {r.clip_id}: error\n  {r.error}", file=sys.stderr)
        else:
            print(f"#{clip.order} {r.clip_id}: {r.status} ({r.message})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
