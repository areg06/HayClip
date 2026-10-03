"""fetch_project: per-clip windows keyed by stable id, audio-only paid artifact, never overwrite recorded bytes."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hayclips import jsonio
from hayclips.errors import ArtifactMismatch, SourceError
from hayclips.fetch import fetch_project
from hayclips.hashing import sha256_file
from hayclips.media.audio import AUDIO_RECIPE, extract_audio
from hayclips.media.probe import probe
from hayclips.models import Candidate, candidate_id
from hayclips.project import ProjectRepo

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).parent / "fixtures" / "fake_ytdlp.py"
VID = "abcDEF12345"


@pytest.fixture
def fake(monkeypatch, tmp_path):
    log = tmp_path / "ytdlp-argv.jsonl"
    monkeypatch.setenv("HAYCLIPS_YTDLP", str(FAKE))
    monkeypatch.setenv("FAKE_YTDLP_LOG", str(log))
    monkeypatch.setenv("FAKE_YTDLP_MODE", "ok")
    return lambda: [json.loads(x)["argv"] for x in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def repo(tmp_path):
    r = ProjectRepo(tmp_path / "proj")
    r.init("proj", {"kind": "youtube", "url": f"https://youtu.be/{VID}", "video_id": VID})
    r.save_candidates([Candidate(id=candidate_id(a, b), start=a, end=b, score=1, text="")
                       for a, b in ((10, 13), (20, 22))], {})
    (r.root / "source").mkdir()
    (r.root / "source" / "src.hy-orig.srt").write_text(
        "1\n00:00:10,500 --> 00:00:12,000\nԲարև ձեզ։\n\n2\n00:00:20,000 --> 00:00:21,500\nԻնչպե՞ս եք։\n", encoding="utf-8")
    r.select(candidate_id(10, 13), pad=1.0)
    r.select(candidate_id(20, 22), pad=1.0)
    return r


def downloads(calls):
    return [a for a in calls() if "--download-sections" in a]


def test_fetch_records_wide_and_audio_by_stable_id(repo, fake):
    results = fetch_project(repo)
    assert [r.status for r in results] == ["fetched", "fetched"]
    clip = repo.load().ordered()[0]
    w = repo.load_window(clip.id)
    cdir = repo.clip_dir(clip.id)
    assert (w.source_start, w.source_end, w.pad_start) == (9.0, 14.0, 1.0)
    assert w.wide.sha256 == sha256_file(cdir / "wide.mp4") and w.preview is None
    assert w.audio.path == "audio.m4a" and w.audio.kind == "audio"
    assert w.audio.sha256 == sha256_file(cdir / "audio.m4a")
    assert w.audio.derived_from == {"path": "wide.mp4", "sha256": w.wide.sha256, "recipe": AUDIO_RECIPE}
    assert abs(w.wide.duration - 5.0) < 0.2 and abs(w.audio.duration - 5.0) < 0.2
    assert sorted(p.name for p in cdir.iterdir()) == ["audio.m4a", "wide.mp4", "window.json", "youtube.srt"]
    assert "Բարև" in (cdir / "youtube.srt").read_text(encoding="utf-8")
    assert downloads(fake)[0][-2:] == ["--", f"https://www.youtube.com/watch?v={VID}"]


def test_second_run_downloads_nothing(repo, fake):
    fetch_project(repo)
    before = len(fake())
    assert [r.status for r in fetch_project(repo)] == ["skipped", "skipped"]
    assert len(fake()) == before


def test_changed_wide_bytes_stop_that_clip_without_redownload(repo, fake):
    fetch_project(repo)
    first, second = repo.load().ordered()
    wide = repo.clip_dir(first.id) / "wide.mp4"
    wide.write_bytes(wide.read_bytes() + b"x")
    before = len(fake())
    results = fetch_project(repo)
    assert results[0].status == "error" and isinstance(results[0].error, ArtifactMismatch)
    assert results[1].status == "skipped"
    assert len(fake()) == before
    with pytest.raises(ArtifactMismatch):
        fetch_project(repo, raise_errors=True)


def test_missing_recorded_audio_is_not_recreated(repo, fake):
    fetch_project(repo)
    clip = repo.load().ordered()[0]
    (repo.clip_dir(clip.id) / "audio.m4a").unlink()
    r = fetch_project(repo)[0]
    assert r.status == "error" and isinstance(r.error, ArtifactMismatch)
    assert not (repo.clip_dir(clip.id) / "audio.m4a").exists()


def test_unrecorded_audio_file_is_never_overwritten(repo, fake):
    clip = repo.load().ordered()[0]
    cdir = repo.clip_dir(clip.id)
    cdir.mkdir(parents=True)
    (cdir / "audio.m4a").write_bytes(b"someone else's bytes")
    r = fetch_project(repo)[0]
    assert r.status == "error" and isinstance(r.error, ArtifactMismatch)
    assert (cdir / "audio.m4a").read_bytes() == b"someone else's bytes"
    assert len(downloads(fake)) == 1        # only the second clip was downloaded


def test_interrupted_fetch_completes_pending_rename(repo, fake):
    fetch_project(repo)
    clip = repo.load().ordered()[0]
    cdir = repo.clip_dir(clip.id)
    (cdir / "audio.m4a").rename(cdir / ".audio.m4a.pending")   # crash after window.json, before rename
    before = len(fake())
    assert fetch_project(repo)[0].status == "skipped"
    assert (cdir / "audio.m4a").exists() and len(fake()) == before


def test_leftover_pending_without_window_is_discarded(repo, fake):
    clip = repo.load().ordered()[0]
    cdir = repo.clip_dir(clip.id)
    cdir.mkdir(parents=True)
    (cdir / ".wide.mp4.pending").write_bytes(b"half")
    assert fetch_project(repo)[0].status == "fetched"
    assert not (cdir / ".wide.mp4.pending").exists()


def test_window_without_audio_is_a_clear_error(repo, fake, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_NOAUDIO", "1")
    r = fetch_project(repo)[0]
    assert r.status == "error" and isinstance(r.error, SourceError) and "no audio" in str(r.error)
    assert repo.load_window(repo.load().ordered()[0].id) is None


def test_extract_audio_keeps_timeline(tmp_path):
    wide = tmp_path / "wide.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=3",
                    "-f", "lavfi", "-i", "sine=sample_rate=44100:duration=3", "-ac", "1", "-c:v", "libx264",
                    "-c:a", "aac", "-shortest", str(wide)], check=True, timeout=60)
    media = extract_audio(wide, tmp_path / "audio.m4a")
    p = probe(tmp_path / "audio.m4a")
    assert p.width is None and p.has_audio and p.sample_rate == 44100 and p.channels == 1
    assert abs(p.duration - probe(wide).duration) < 0.05
    assert media.derived_from == {"path": "wide.mp4", "sha256": sha256_file(wide), "recipe": AUDIO_RECIPE}
    assert media.sha256 == sha256_file(tmp_path / "audio.m4a")


def test_cli_on_legacy_layout_says_migrate(tmp_path):
    d = tmp_path / "old"
    d.mkdir()
    (d / "suggestions.json").write_text("[]")
    r = subprocess.run([sys.executable, str(ROOT / "fetch_clips.py"), str(d)], capture_output=True, text=True,
                       timeout=60, env=dict(os.environ))
    assert r.returncode != 0 and "migrate" in r.stderr


def test_cli_reports_errors_with_nonzero_exit(repo, fake, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_MODE", "fail_unavailable")
    r = subprocess.run([sys.executable, str(ROOT / "fetch_clips.py"), str(repo.root)], capture_output=True,
                       text=True, timeout=120, env=dict(os.environ))
    assert r.returncode != 0 and "unavailable" in r.stderr and "Traceback" not in r.stderr


# ----- public API: fetch_selected_clips (Phase 1c) -------------------------------------------------

def test_public_api_fetches_only_the_listed_selected_clips(repo, fake):
    from hayclips.fetch import fetch_selected_clips
    first, second = repo.load().ordered()
    ids_before = [c.id for c in repo.load().clips]
    results = fetch_selected_clips(repo, [second.id])
    assert [(r.clip_id, r.status) for r in results] == [(second.id, "fetched")]
    assert repo.load_window(first.id) is None and repo.load_window(second.id).clip_id == second.id
    assert len(downloads(fake)) == 1
    assert [c.id for c in repo.load().clips] == ids_before          # stable ids untouched


def test_public_api_rejects_unselected_or_unknown_ids(repo, fake):
    from hayclips.errors import PipelineError
    from hayclips.fetch import fetch_selected_clips
    first, _ = repo.load().ordered()
    repo.update_clip(first.id, selected=False)
    for ids in ([first.id], ["clp_0000000000"]):
        with pytest.raises(PipelineError, match="not selected"):
            fetch_selected_clips(repo, ids)
    assert downloads(fake) == []


def test_public_api_reuses_verified_artifacts_and_keeps_alignment_metadata(repo, fake):
    from hayclips.fetch import fetch_selected_clips
    fetch_selected_clips(repo)
    clip = repo.load().ordered()[0]
    before = (repo.clip_dir(clip.id) / "window.json").read_bytes()
    n = len(downloads(fake))
    assert [r.status for r in fetch_selected_clips(repo)] == ["skipped", "skipped"]
    assert len(downloads(fake)) == n
    assert (repo.clip_dir(clip.id) / "window.json").read_bytes() == before
    w = repo.load_window(clip.id)
    assert w.audio.derived_from["sha256"] == w.wide.sha256 == sha256_file(repo.clip_dir(clip.id) / "wide.mp4")


def test_public_api_cleans_partial_files_after_a_failed_download(repo, fake, monkeypatch):
    from hayclips.fetch import fetch_selected_clips
    clip = repo.load().ordered()[0]
    cdir = repo.clip_dir(clip.id)
    cdir.mkdir(parents=True)
    (cdir / ".wide.mp4.pending").write_bytes(b"leftover from a crash")
    monkeypatch.setenv("FAKE_YTDLP_MODE", "fail_unavailable")
    results = fetch_selected_clips(repo, [clip.id])
    assert results[0].status == "error"
    assert sorted(p.name for p in cdir.iterdir()) == []                # no pending files, no work dirs


def test_public_api_reports_progress_and_can_stop_between_clips(repo, fake):
    from hayclips.fetch import fetch_selected_clips
    seen = []
    results = fetch_selected_clips(repo, on_progress=lambda k, n, cid: seen.append((k, n)),
                                   should_stop=lambda: len(seen) >= 1)
    assert seen == [(0, 2)] and len(results) == 1


def test_worker_and_cli_use_only_the_public_fetch_api():
    for f in ("hayclips/jobs/handlers.py", "fetch_clips.py"):
        text = (ROOT / f).read_text(encoding="utf-8")
        assert "fetch_selected_clips" in text and "_fetch_clip" not in text and "_caption_writer" not in text
