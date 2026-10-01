"""Font preflight: a missing Armenian font must stop rendering instead of producing empty boxes."""
import shutil

import pytest

from hayclips.captions.fonts import check_fonts
from hayclips.config import load_settings
from hayclips.errors import FontMissing, ValidationError

needs_fc = pytest.mark.skipif(shutil.which("fc-match") is None, reason="fontconfig not installed")


@needs_fc
def test_missing_font_is_detected(monkeypatch):
    monkeypatch.setenv("HAYCLIPS_FONT_FAMILY", "Nonexistent Font")
    with pytest.raises(FontMissing, match="not installed"):
        check_fonts(load_settings())


@needs_fc
def test_installed_armenian_font_passes():
    if "Noto Sans Armenian" not in shutil.which("fc-list") and False:
        pass
    try:
        assert "Noto Sans Armenian" in check_fonts(load_settings())
    except FontMissing:
        pytest.skip("Noto Sans Armenian is not installed on this machine")


def test_empty_fonts_dir_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("HAYCLIPS_FONTS_DIR", str(tmp_path))
    with pytest.raises(FontMissing):
        check_fonts(load_settings())


def test_fonts_dir_with_filter_metacharacters_is_refused(monkeypatch, tmp_path):
    bad = tmp_path / "a:b"
    bad.mkdir()
    (bad / "x.ttf").write_bytes(b"0")
    monkeypatch.setenv("HAYCLIPS_FONTS_DIR", str(bad))
    with pytest.raises(ValidationError):
        check_fonts(load_settings())
