"""Approximate on-screen UI areas of short-video apps, used ONLY for editor preview overlays and caption
placement warnings. They are never burned into exports.

Coordinates are fractions of a 9:16 frame (x0, y0, x1, y1; origin top-left). Platforms change their UI
often and do not publish exact numbers for organic posts, so these are deliberately conservative
approximations, collected in research/caption-style.md (sections 2 and 9):
- TikTok: third-party measurement of the in-feed UI (top 130 px, bottom 484 px, right 140 px of 1080x1920).
- Instagram Reels: organic UI, caption/username block at the bottom and the action rail on the right;
  Meta's ads rule (bottom 35%) is stricter and is not used here.
- YouTube Shorts: avoid the top ~10%, bottom ~25% and right ~10% (reported Google guidance).
"""
from __future__ import annotations

SAFE_ZONES: dict[str, dict] = {
    "tiktok": {"label": "TikTok", "zones": [
        {"name": "top bar", "box": (0.0, 0.0, 1.0, 0.068)},
        {"name": "caption and sound", "box": (0.0, 0.748, 1.0, 1.0)},
        {"name": "buttons", "box": (0.87, 0.40, 1.0, 0.748)},
    ]},
    "reels": {"label": "Reels", "zones": [
        {"name": "top bar", "box": (0.0, 0.0, 1.0, 0.09)},
        {"name": "caption and account", "box": (0.0, 0.80, 1.0, 1.0)},
        {"name": "buttons", "box": (0.86, 0.52, 1.0, 0.80)},
    ]},
    "shorts": {"label": "Shorts", "zones": [
        {"name": "top bar", "box": (0.0, 0.0, 1.0, 0.10)},
        {"name": "title and channel", "box": (0.0, 0.75, 1.0, 1.0)},
        {"name": "buttons", "box": (0.90, 0.45, 1.0, 0.75)},
    ]},
}
SAFE_MARGIN = 0.015


def overlaps(box: tuple[float, float, float, float], platform: str) -> list[str]:
    """Names of the platform's UI areas that a caption box (fractions) touches."""
    x0, y0, x1, y1 = box
    hits = []
    for z in SAFE_ZONES[platform]["zones"]:
        a0, b0, a1, b1 = z["box"]
        if x0 < a1 and x1 > a0 and y0 < b1 and y1 > b0:
            hits.append(z["name"])
    return hits


def safe_bottom(platform: str) -> float:
    """Lowest y (fraction) for the bottom edge of centred captions on this platform."""
    return round(min(z["box"][1] for z in SAFE_ZONES[platform]["zones"] if z["box"][3] >= 0.99) - SAFE_MARGIN, 4)
