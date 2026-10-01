"""Cut two short local-only face fixtures from pilot-03 (founder-approved, 2026-10-01; never in git).

Usage: .venv/bin/python tests/fixtures/make_local_fixtures.py [pilot-03]
Writes tests/fixtures/local/one_face.mp4 (8 s, one speaker) and two_faces.mp4 (6 s, two speakers side
by side, built by stacking two halves). tests/fixtures/local/ is gitignored.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "local"


def wide_windows(pilot: Path) -> list[Path]:
    legacy = sorted(pilot.glob("clip_0[0-9].wide.mp4"))
    if legacy:
        return legacy
    import json
    project = json.loads((pilot / "project.json").read_text(encoding="utf-8"))
    clips = sorted(project["clips"], key=lambda c: c["order"])
    return [pilot / "clips" / c["id"] / "wide.mp4" for c in clips if (pilot / "clips" / c["id"] / "wide.mp4").exists()]


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True, timeout=300)


def main() -> None:
    pilot = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "pilot-03"
    wides = wide_windows(pilot)
    if len(wides) < 3:
        sys.exit(f"need the three pilot-03 wide windows in {pilot}")
    OUT.mkdir(parents=True, exist_ok=True)
    # one speaker: clip 2 at 8-16 s is a single host medium shot
    ff("-ss", "8", "-i", str(wides[1]), "-t", "8", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
       "-c:a", "aac", str(OUT / "one_face.mp4"))
    # two speakers: left half of clip 1 (guest) next to the right half of clip 2 (host)
    ff("-ss", "20", "-i", str(wides[0]), "-ss", "8", "-i", str(wides[1]), "-t", "6", "-filter_complex",
       "[0:v]crop=960:1080:300:0[l];[1:v]crop=960:1080:500:0[r];[l][r]hstack=2[v]",
       "-map", "[v]", "-map", "0:a", "-t", "6", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
       "-c:a", "aac", str(OUT / "two_faces.mp4"))
    print(f"wrote {OUT / 'one_face.mp4'} and {OUT / 'two_faces.mp4'}")


if __name__ == "__main__":
    main()
