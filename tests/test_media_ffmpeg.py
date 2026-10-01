"""ffmpeg helpers: timeouts, silent and missing audio."""
import pytest

from fixtures import synth
from hayclips import proc
from hayclips.config import load_settings
from hayclips.media import ffmpeg as F


def test_ffmpeg_timeout_kills_and_leaves_no_output(tmp_path):
    out = tmp_path / "long.mp4"
    with pytest.raises(proc.ToolTimeout):
        proc.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60:duration=600",
                  "-c:v", "libx264", "-preset", "veryslow", str(out)], timeout=1, outputs=[out])
    assert not out.exists()


def test_failed_tool_reports_stderr_and_removes_output(tmp_path):
    out = tmp_path / "x.mp4"
    out.write_bytes(b"partial")
    with pytest.raises(proc.ToolError) as e:
        proc.run(["ffmpeg", "-hide_banner", "-i", str(tmp_path / "missing.mp4"), str(out)], timeout=30, outputs=[out])
    assert "missing.mp4" in e.value.stderr and not out.exists()


def test_shell_strings_are_refused():
    with pytest.raises(TypeError):
        proc.run("ffmpeg -version", timeout=5)


def test_silent_audio_is_not_normalised(tmp_path):
    v = synth.video(tmp_path / "silent.mp4", 4, audio="silent")
    plan = F.plan_audio(v, 0, 4, load_settings())
    assert plan.filter and "loudnorm" not in plan.filter and "afade" in plan.filter
    assert "silent audio" in plan.note


def test_no_audio_stream_renders_video_only(tmp_path):
    v = synth.video(tmp_path / "mute.mp4", 3, audio="none")
    plan = F.plan_audio(v, 0, 3, load_settings())
    assert plan.filter is None and "no audio" in plan.note


def test_normal_audio_gets_two_pass_loudnorm(tmp_path):
    v = synth.video(tmp_path / "noise.mp4", 4)
    plan = F.plan_audio(v, 0.5, 3.5, load_settings())
    assert "loudnorm=I=-14" in plan.filter and "measured_I=" in plan.filter and plan.measured_lufs < 0
