"""Pilot-03 visual reference: re-rendering must reproduce the Phase 1a baseline without any paid call.

Runs only where pilot-03 and pilot-03/.phase1a-baseline exist (local, gitignored creator media).
Works on a temporary copy; pilot-03 itself is never written.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from hayclips.media.probe import probe
from hayclips.project import ProjectRepo, migrate_legacy
from hayclips.render import render_project

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "pilot-03"
BASE = PILOT / ".phase1a-baseline"

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not (BASE / "baseline.json").exists(), reason="pilot-03 baseline not present")]


def lufs(path: Path) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=300).stderr
    return float([l for l in err.splitlines() if l.strip().startswith("I:")][-1].split()[1])


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    work = tmp_path_factory.mktemp("p3") / "pilot-03"
    shutil.copytree(PILOT, work, ignore=shutil.ignore_patterns(".phase1a-baseline"))
    if not (work / "project.json").exists():
        migrate_legacy(work, probe=probe)
    repo = ProjectRepo(work)
    results = render_project(repo, styles=["A", "B", "C"], caption_bottom=930, hook_enabled=True)
    return repo, results


def test_all_clips_render_without_paid_calls(rendered):
    repo, results = rendered
    assert [r.status for r in results] == ["rendered"] * 3, [r.message for r in results]


def test_matches_baseline(rendered):
    repo, _ = rendered
    base = json.loads((BASE / "baseline.json").read_text())
    sug = json.loads((BASE / "suggestions.json").read_text())
    for i, clip in enumerate(repo.load().ordered()):
        n = clip.legacy_name or f"clip_{i + 1:02}"
        rdir = repo.render_dir(clip.id)
        info = json.loads((rdir / "render.json").read_text())
        assert (info["cut"]["start"], info["cut"]["end"]) == (sug[i]["caption_start"], sug[i]["caption_end"])
        assert info["alignment"]["method"] in ("xcorr", "provenance", "identical")
        assert abs(info["alignment"]["lag_s"]) <= 0.040
        old_crop = json.loads((BASE / f"{n}.crop.json").read_text())
        new_crop = json.loads((repo.clip_dir(clip.id) / "crop.json").read_text())
        assert new_crop["shots"] == old_crop["shots"] and new_crop["crop_w"] == old_crop["crop_w"]
        for st in "ABC":
            assert (rdir / f"{st}.ass").read_bytes() == (BASE / f"{n}_{st}.ass").read_bytes(), f"{n} {st}.ass differs"
            p = probe(rdir / f"{st}.mp4")
            old = base[f"{n}_{st}.mp4"]["probe"]
            assert (p.width, p.height) == (720, 1280)
            assert abs(p.duration - float(old["format"]["duration"])) <= 0.02
            assert (p.sample_rate, p.channels, p.audio_codec) == (48000, 2, "aac")
        assert abs(lufs(rdir / "A.mp4") - float(base[f"{n}_A.mp4"]["lufs_I"])) <= 0.3
