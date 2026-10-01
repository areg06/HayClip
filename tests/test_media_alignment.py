"""Caption-timeline vs render-timeline proof."""
import pytest

from fixtures import synth
from hayclips.errors import AlignmentError
from hayclips.media.alignment import verify_alignment
from hayclips.project import media_record


def test_identical_file_is_aligned(tmp_path):
    v = synth.video(tmp_path / "a.mp4", 4)
    m = media_record(v, "a.mp4", "preview_video", 4)
    assert verify_alignment(v, m, v, m).method == "identical"


def test_provenance_proves_alignment_without_decoding(tmp_path):
    v = synth.video(tmp_path / "w.mp4", 4)
    w = media_record(v, "w.mp4", "wide", 4.0)
    a = synth.audio_from(v, tmp_path / "a.m4a")
    am = media_record(a, "a.m4a", "audio", 4.0, derived_from={"path": "w.mp4", "sha256": w.sha256})
    assert verify_alignment(a, am, v, w).method == "provenance"


def test_independent_copy_is_verified_by_cross_correlation(tmp_path):
    v = synth.video(tmp_path / "w.mp4", 12)
    a = synth.audio_from(v, tmp_path / "a.m4a")
    r = verify_alignment(a, media_record(a, "a.m4a", "audio"), v, media_record(v, "w.mp4", "wide"))
    assert r.method == "xcorr" and abs(r.lag_s) <= 0.040 and r.confidence > 0.5


def test_shifted_audio_fails_and_reports_the_lag(tmp_path):
    v = synth.video(tmp_path / "w.mp4", 12)
    shifted = synth.video(tmp_path / "s.mp4", 12, delay=0.3)
    with pytest.raises(AlignmentError, match=r"lag [+-]\d+ ms"):
        verify_alignment(shifted, media_record(shifted, "s.mp4", "preview_video"), v, media_record(v, "w.mp4", "wide"))


def test_unrelated_audio_fails(tmp_path):
    v = synth.video(tmp_path / "w.mp4", 12)
    other = synth.video(tmp_path / "o.mp4", 12, audio="silent")
    with pytest.raises(AlignmentError):
        verify_alignment(other, media_record(other, "o.mp4", "audio"), v, media_record(v, "w.mp4", "wide"))


def test_silent_audio_cannot_be_verified_without_provenance(tmp_path):
    v = synth.video(tmp_path / "w.mp4", 12, audio="silent")
    a = synth.audio_from(v, tmp_path / "a.m4a")
    with pytest.raises(AlignmentError, match="silent"):
        verify_alignment(a, media_record(a, "a.m4a", "audio"), v, media_record(v, "w.mp4", "wide"))
