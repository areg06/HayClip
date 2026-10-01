"""YouTubeSource: strict input validation and safe yt-dlp invocation (via tests/fixtures/fake_ytdlp.py)."""
import dataclasses
import json
from pathlib import Path

import pytest

from hayclips.config import Timeouts, load_settings
from hayclips.errors import SourceError, ValidationError
from hayclips.proc import ToolTimeout
from hayclips.sources.youtube import YouTubeSource, canonical_url, parse_youtube_ref

FAKE = Path(__file__).parent / "fixtures" / "fake_ytdlp.py"
VID = "abcDEF12345"


@pytest.fixture
def fake(monkeypatch, tmp_path):
    log = tmp_path / "ytdlp-argv.jsonl"
    monkeypatch.setenv("HAYCLIPS_YTDLP", str(FAKE))
    monkeypatch.setenv("FAKE_YTDLP_LOG", str(log))
    monkeypatch.setenv("FAKE_YTDLP_MODE", "ok")

    def calls():
        return [json.loads(x)["argv"] for x in log.read_text().splitlines()] if log.exists() else []
    return calls


def test_leading_dash_id_only_via_link():
    assert parse_youtube_ref("https://youtu.be/-abcdefghij") == "-abcdefghij"
    assert canonical_url("-abcdefghij") == "https://www.youtube.com/watch?v=-abcdefghij"


@pytest.mark.parametrize("raw", [
    VID,
    f"https://www.youtube.com/watch?v={VID}",
    f"https://youtube.com/watch?v={VID}&t=120s",
    f"http://m.youtube.com/watch?feature=share&v={VID}",
    f"https://youtu.be/{VID}",
    f"https://youtu.be/{VID}?si=abc",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/live/{VID}",
    f"  https://youtu.be/{VID}  ",
])
def test_accepts_youtube_forms(raw):
    assert parse_youtube_ref(raw) == VID
    assert canonical_url(VID) == f"https://www.youtube.com/watch?v={VID}"


@pytest.mark.parametrize("raw", [
    "", "   ", "file:///etc/passwd", "/Users/x/video.mp4", "./video.mp4", "http://127.0.0.1/x",
    "https://vimeo.com/12345678901", f"https://evil.com/watch?v={VID}", f"https://youtube.com.evil.com/watch?v={VID}",
    f"https://www.youtube.com@evil.com/watch?v={VID}", "javascript:alert(1)", f"ftp://youtube.com/watch?v={VID}",
    "https://www.youtube.com/playlist?list=PL1234567890", "https://www.youtube.com/watch?list=PL123",
    "short", "abcDEF12345x", f"https://youtu.be/{VID}x", f"https://www.youtube.com/watch?v={VID[:10]}",
    f"https://www.yоutube.com/watch?v={VID}",          # Cyrillic о lookalike
    f"{VID[:10]}А",                               # non-ASCII id char
    f"https://youtu.be/{VID}\n--exec=id", f"{VID}\x00", f"https://www.youtube.com/watch?v={VID} --exec=id",
    "--exec=touch /tmp/pwn", "-o/tmp/x", "--version", f"-{VID[1:]}",
])
def test_rejects_everything_else(raw, fake):
    with pytest.raises(ValidationError):
        parse_youtube_ref(raw)
    with pytest.raises(ValidationError):
        YouTubeSource(raw)
    assert fake() == []          # rejected before any process started


def test_inspect_argv_is_fixed_and_url_follows_double_dash(fake):
    info = YouTubeSource(f"https://youtu.be/{VID}?si=--exec").inspect()
    assert info.ref == VID and info.url == canonical_url(VID) and info.duration == 3600
    assert "hy-orig" in info.caption_languages
    (argv,) = fake()
    assert argv[-2:] == ["--", canonical_url(VID)]
    for flag in ("--ignore-config", "--no-plugin-dirs", "--no-playlist", "--no-exec"):
        assert flag in argv
    assert argv[argv.index("--use-extractors") + 1] == "youtube"
    assert not any("si=" in a or "exec" in a and a != "--no-exec" for a in argv)


def test_source_too_long_refused(fake, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_DURATION", str(4 * 3600))
    with pytest.raises(SourceError, match="longer than"):
        YouTubeSource(VID).inspect()


def test_size_estimate_over_limit_refused(fake, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_FILESIZE", str(5 * 1024**3))
    with pytest.raises(SourceError, match="GB"):
        YouTubeSource(VID).inspect()


def test_unavailable_video_maps_to_source_error(fake, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_MODE", "fail_unavailable")
    with pytest.raises(SourceError, match="unavailable") as e:
        YouTubeSource(VID).inspect()
    assert "Traceback" not in str(e.value)


def test_sign_in_required_maps_to_source_error(fake, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_MODE", "fail_private")
    with pytest.raises(SourceError, match="sign-in"):
        YouTubeSource(VID).inspect()


def test_fetch_captions(fake, tmp_path):
    path = YouTubeSource(VID).fetch_captions(tmp_path / "source")
    assert path.exists() and "Բարև" in path.read_text(encoding="utf-8")
    (argv,) = fake()
    assert "--skip-download" in argv and argv[argv.index("--sub-langs") + 1] == "hy-orig"
    assert argv[-2:] == ["--", canonical_url(VID)]


def test_missing_captions(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_YTDLP_MODE", "no_subs")
    with pytest.raises(SourceError, match="no Armenian auto-captions"):
        YouTubeSource(VID).fetch_captions(tmp_path / "source")


def test_bad_caption_language_rejected(fake, tmp_path):
    with pytest.raises(ValidationError):
        YouTubeSource(VID).fetch_captions(tmp_path, lang="--exec")


def test_fetch_window_downloads_exact_section(fake, tmp_path):
    dest = tmp_path / "clip" / "wide.mp4"
    wf = YouTubeSource(VID).fetch_window(100.0, 104.5, dest)
    assert dest.exists() and (wf.start, wf.end) == (100.0, 104.5)
    (argv,) = fake()
    assert argv[argv.index("--download-sections") + 1] == "*100.00-104.50"
    assert "--force-keyframes-at-cuts" in argv and "--max-filesize" in argv
    assert argv[-2:] == ["--", canonical_url(VID)]
    assert [p.name for p in dest.parent.iterdir()] == ["wide.mp4"]   # no temp leftovers


@pytest.mark.parametrize("a,b", [(-1, 10), (10, 10), (10, 5), (0, float("nan")), (0, float("inf")), (0, 1200)])
def test_fetch_window_rejects_bad_ranges(fake, tmp_path, a, b):
    with pytest.raises(ValidationError):
        YouTubeSource(VID).fetch_window(a, b, tmp_path / "w.mp4")
    assert fake() == []


def test_fetch_window_duration_mismatch(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_YTDLP_SHORTEN", "2")
    dest = tmp_path / "w" / "wide.mp4"
    with pytest.raises(SourceError, match="expected"):
        YouTubeSource(VID).fetch_window(0, 5, dest)
    assert not dest.exists()


def test_fetch_window_retries_transient_failure(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_YTDLP_MODE", "flaky")
    monkeypatch.setenv("FAKE_YTDLP_COUNTER", str(tmp_path / "counter"))
    monkeypatch.setenv("FAKE_YTDLP_FAILS", "2")
    dest = tmp_path / "w" / "wide.mp4"
    YouTubeSource(VID).fetch_window(0, 3, dest)
    assert dest.exists() and len(fake()) == 3
    assert [p.name for p in dest.parent.iterdir()] == ["wide.mp4"]


def test_fetch_window_does_not_retry_permanent_failure(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_YTDLP_MODE", "fail_unavailable")
    with pytest.raises(SourceError):
        YouTubeSource(VID).fetch_window(0, 3, tmp_path / "w.mp4")
    assert len(fake()) == 1


def test_ytdlp_timeout_kills_and_cleans_partials(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_YTDLP_MODE", "sleep")
    settings = dataclasses.replace(load_settings(), timeouts=Timeouts(ytdlp_metadata=1, ytdlp_download=1))
    dest = tmp_path / "w" / "wide.mp4"
    with pytest.raises(ToolTimeout):
        YouTubeSource(VID, settings=settings).fetch_window(0, 3, dest)
    assert not dest.exists()
    assert not dest.parent.exists() or list(dest.parent.iterdir()) == []
    with pytest.raises(ToolTimeout):
        YouTubeSource(VID, settings=settings).inspect()
