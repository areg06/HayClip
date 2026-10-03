"""Caption and hook "looks": the editor/Brand Kit settings for a clip, validated in one place.

Positions are normalised (0..1 of the frame), so they scale to any render size:
  captions: (x, y) is the bottom-centre of the caption text;  hook: (x, y) is the top-centre.
A clip whose look is None renders exactly as before Phase 1d (legacy defaults, byte-identical ASS).
"""
from __future__ import annotations

import re

from .errors import ValidationError

PRESETS = {           # user-facing name -> underlying ASS style
    "clean": "L", "active": "A", "punch": "B", "karaoke": "C",
}
PRESET_LABELS = {"clean": "Clean", "active": "Active word", "punch": "Punch", "karaoke": "Karaoke"}
FONTS = ("Noto Sans Armenian",)            # families verified by the font preflight
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
DEFAULT_Y = 930 / 1280
DEFAULT_HOOK_Y = 200 / 1280

DEFAULT_LOOK = {"preset": "active", "size": 1.0, "x": 0.5, "y": round(DEFAULT_Y, 4), "color": "#FFFFFF",
                "highlight": "#FFD700", "background": False, "words_per_line": None, "font": FONTS[0]}
DEFAULT_HOOK = {"show": True, "x": 0.5, "y": round(DEFAULT_HOOK_Y, 4), "duration": 3.0}


def _num(v, lo, hi, name):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be a number") from None
    if not lo <= f <= hi:
        raise ValidationError(f"{name} must be between {lo} and {hi}")
    return round(f, 4)


def normalise_look(d: dict | None) -> dict:
    """Fill defaults and validate. Unknown keys are dropped."""
    d = {**DEFAULT_LOOK, **{k: v for k, v in (d or {}).items() if k in DEFAULT_LOOK}}
    if d["preset"] not in PRESETS:
        raise ValidationError("unknown caption preset")
    d["size"] = _num(d["size"], 0.6, 1.6, "size")
    d["x"] = _num(d["x"], 0.05, 0.95, "x")
    d["y"] = _num(d["y"], 0.1, 0.98, "y")
    for k in ("color", "highlight"):
        if not isinstance(d[k], str) or not HEX.match(d[k]):
            raise ValidationError(f"{k} must be a colour like #FFD700")
        d[k] = d[k].upper()
    d["background"] = bool(d["background"])
    if d["words_per_line"] not in (None, ""):
        d["words_per_line"] = int(_num(d["words_per_line"], 1, 8, "words per line"))
    else:
        d["words_per_line"] = None
    if d["font"] not in FONTS:
        raise ValidationError("that caption font is not available")
    return d


def normalise_hook(d: dict | None) -> dict:
    d = {**DEFAULT_HOOK, **{k: v for k, v in (d or {}).items() if k in DEFAULT_HOOK}}
    d["show"] = bool(d["show"])
    d["x"] = _num(d["x"], 0.05, 0.95, "hook x")
    d["y"] = _num(d["y"], 0.02, 0.9, "hook y")
    d["duration"] = _num(d["duration"], 1, 10, "hook duration")
    return d


def ass_colour(hex_rgb: str) -> str:
    """#RRGGBB -> ASS &H00BBGGRR."""
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H00{b}{g}{r}".upper()
