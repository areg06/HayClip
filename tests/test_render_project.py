"""End-to-end render of synthetic one-clip projects (real ffmpeg, no network, no paid calls)."""
import pytest

from fixtures import synth
from hayclips import jsonio
from hayclips.config import Settings, load_settings
from hayclips.errors import ValidationError
from hayclips.media.probe import probe
from hayclips.models import UNKNOWN_SUBMISSION, Trim
from hayclips.render import render_project
from hayclips.transcription.store import list_attempts, save_attempt

pytestmark = pytest.mark.slow


def render(repo, **kw):
    return render_project(repo, styles=kw.pop("styles", ["A"]), **kw)


def test_landscape_render_has_reel_dimensions_and_duration(tmp_path):
    repo, clip = synth.make_project(tmp_path / "p", seconds=6, hook="Փորձնական հուք")
    [r] = render(repo, styles=["A", "C"])
    assert r.status == "rendered", r.message
    for st in ("A", "C"):
        p = probe(repo.render_dir(clip.id) / f"{st}.mp4")
        assert (p.width, p.height) == (720, 1280) and abs(p.duration - 6.0) <= 0.1 and p.sample_rate == 48000
    info = jsonio.read_json(repo.render_dir(clip.id) / "render.json")
    assert info["alignment"]["method"] == "provenance" and info["cut"]["mode"] == "whole"
    assert any("no face" in c for c in info["checks"])                       # testsrc has no faces
    assert (repo.root / "review.html").exists() and clip.id in (repo.root / "review.html").read_text()


def test_portrait_source_renders_fitted(tmp_path):
    repo, clip = synth.make_project(tmp_path / "p", seconds=4, size="720x1280")
    [r] = render(repo)
    assert r.status == "rendered" and "fit" in r.message
    p = probe(repo.render_dir(clip.id) / "A.mp4")
    assert (p.width, p.height) == (720, 1280)


def test_padded_clip_is_snapped_and_manual_trim_wins(tmp_path):
    repo, clip = synth.make_project(tmp_path / "p", seconds=10, pad=2.0)
    [r] = render(repo)
    info = jsonio.read_json(repo.render_dir(clip.id) / "render.json")
    assert r.status == "rendered" and info["cut"]["mode"] == "auto"
    repo.update_clip(clip.id, trim=Trim(caption_start=1.0, caption_end=7.5))
    [r] = render(repo)
    info = jsonio.read_json(repo.render_dir(clip.id) / "render.json")
    assert info["cut"] == {**info["cut"], "start": 1.0, "end": 7.5, "mode": "manual"}
    assert abs(probe(repo.render_dir(clip.id) / "A.mp4").duration - 6.5) <= 0.1


def test_alignment_mismatch_stops_the_clip(tmp_path):
    repo, clip = synth.make_project(tmp_path / "p", seconds=12, transcript_media="shifted")
    [r] = render(repo)
    assert r.status == "failed" and "caption timeline does not match" in r.message
    assert not (repo.render_dir(clip.id) / "A.mp4").exists()


def test_independent_audio_passes_by_cross_correlation(tmp_path):
    repo, clip = synth.make_project(tmp_path / "p", seconds=12, transcript_media="independent")
    [r] = render(repo)
    assert r.status == "rendered" and "xcorr" in r.message


def test_silent_clip_renders_with_provenance_and_says_so(tmp_path):
    repo, clip = synth.make_project(tmp_path / "p", seconds=4, audio="silent")
    [r] = render(repo)
    info = jsonio.read_json(repo.render_dir(clip.id) / "render.json")
    assert r.status == "rendered" and info["audio"].startswith("silent audio")


def test_empty_transcript_renders_without_captions_and_flags_it(tmp_path):
    repo, clip = synth.make_project(tmp_path / "p", seconds=4, pad=1.0,
                                    response={"status": "completed", "segments": [], "words": []})
    [r] = render(repo)
    assert r.status == "rendered"
    assert any("empty transcript" in c for c in r.checks) and any("without captions" in c for c in r.checks)


def test_long_hook_fails_only_that_clip(tmp_path):
    repo, clip = synth.make_project(tmp_path / "p", seconds=4, hook="ա" * 46)
    [r] = render(repo)
    assert r.status == "failed" and "limit is 45" in r.message
    [r] = render(repo, hook_enabled=False)
    assert r.status == "rendered"


def test_unknown_paid_submission_blocks_render(tmp_path):
    repo, clip = synth.make_project(tmp_path / "p", seconds=4)
    a = list_attempts(repo, clip.id)[0]
    a.id, a.state, a.result_file = "att_unknown01", UNKNOWN_SUBMISSION, None
    save_attempt(repo, a)
    [r] = render(repo)
    assert r.status == "failed" and "reconcile" in r.message


def test_changed_render_source_bytes_block_render(tmp_path):
    repo, clip = synth.make_project(tmp_path / "p", seconds=4)
    synth.video(repo.clip_dir(clip.id) / "wide.mp4", 4, audio="silent")     # different bytes, same name
    [r] = render(repo)
    assert r.status == "failed" and "bytes changed" in r.message


def test_not_fetched_or_not_transcribed_is_skipped(tmp_path):
    repo, clip = synth.make_project(tmp_path / "p", seconds=4)
    for f in repo.transcription_dir(clip.id).glob("*"):
        f.unlink()
    [r] = render(repo)
    assert r.status == "skipped" and "not transcribed" in r.message


def test_failed_render_leaves_no_partial_output_and_retry_succeeds(tmp_path):
    repo, clip = synth.make_project(tmp_path / "p", seconds=6)
    flaky = tmp_path / "flaky-ffmpeg"
    # fails the final encode after writing half a file, like a crash mid-render
    flaky.write_text('#!/bin/sh\nfor last; do :; done\n'
                     'if [ "$last" = "A.mp4" ]; then echo partial > A.mp4; echo "encoder crashed" >&2; exit 1; fi\n'
                     'exec ffmpeg "$@"\n')
    flaky.chmod(0o755)
    base = load_settings()
    broken = Settings(home=base.home, ffmpeg=str(flaky))
    [r] = render(repo, settings=broken)
    assert r.status == "failed" and "encoder crashed" in r.message
    assert not (repo.render_dir(clip.id) / "A.mp4").exists()
    [r] = render(repo)
    assert r.status == "rendered"


def test_missing_font_stops_before_any_clip(tmp_path, monkeypatch):
    repo, clip = synth.make_project(tmp_path / "p", seconds=4)
    monkeypatch.setenv("HAYCLIPS_FONT_FAMILY", "Nonexistent Font")
    from hayclips.errors import FontMissing
    with pytest.raises(FontMissing):
        render(repo)
    assert not (repo.render_dir(clip.id) / "A.mp4").exists()


def test_unknown_style_is_refused(tmp_path):
    repo, _ = synth.make_project(tmp_path / "p", seconds=4)
    with pytest.raises(Exception, match="unknown caption style"):
        render(repo, styles=["Z"])
