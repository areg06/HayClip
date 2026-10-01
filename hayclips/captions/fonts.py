"""Font preflight: libass silently substitutes a missing font (Armenian then renders as empty boxes),
so rendering refuses to start unless the caption font resolves to the requested family."""
from __future__ import annotations

import re
from pathlib import Path

from .. import proc
from ..config import Settings
from ..errors import FontMissing, ValidationError

WEIGHTS = ("Black", "SemiBold")
SAFE_DIR = re.compile(r"^[A-Za-z0-9_./ -]+$")


def check_fonts(settings: Settings) -> str:
    """Return a short description of the fonts used, or raise FontMissing."""
    family = settings.font_family
    if settings.fonts_dir is not None:
        d = Path(settings.fonts_dir)
        if not SAFE_DIR.match(str(d)) or ":" in str(d) or "'" in str(d):
            raise ValidationError(f"HAYCLIPS_FONTS_DIR has characters that cannot be passed to ffmpeg: {d}")
        files = [p for p in d.glob("*") if p.suffix.lower() in (".ttf", ".otf", ".ttc")] if d.is_dir() else []
        if not files:
            raise FontMissing(f"no font files in HAYCLIPS_FONTS_DIR={d}", hint=f"put {family} .ttf/.otf files there")
        return f"{family} from {d}"
    for weight in WEIGHTS:
        try:
            out = proc.run(["fc-match", "-f", "%{family}", f"{family}:style={weight}"], timeout=30, tool="fc-match").stdout
        except proc.ToolNotFound as exc:
            raise FontMissing("fc-match (fontconfig) is not installed, so the caption font cannot be verified",
                              hint="brew install fontconfig, or set HAYCLIPS_FONTS_DIR") from exc
        families = [f.strip() for f in out.split(",")]
        if family not in families:
            raise FontMissing(f"caption font «{family} {weight}» is not installed (fontconfig substitutes {families[0]!r})",
                              hint=f"install {family} (Google Fonts, SIL OFL) or set HAYCLIPS_FONTS_DIR; "
                                   "without it Armenian captions render as empty boxes")
    return f"{family} ({', '.join(WEIGHTS)}) via fontconfig"
