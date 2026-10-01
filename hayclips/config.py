"""Settings, development cost limits and tool paths. Everything is overridable by environment variables.

The limits are Phase 1a development defaults (founder decision 2026-10-01), not product rules.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

REAL_HARMAR_BASE_URL = "https://api.harmar.ai"
# Real paid calls need this exact value; tests never set it (tests/conftest.py removes it).
PAID_OPT_IN_ENV = "HAYCLIPS_ALLOW_PAID_HARMAR"
PAID_OPT_IN_VALUE = "1"


@dataclass(frozen=True)
class Limits:
    max_paid_seconds_per_operation: int = 600
    max_paid_seconds_per_project: int = 600
    max_paid_seconds_per_day: int = 1200
    balance_margin: float = 0.10          # balance must be >= need * (1 + margin)
    max_source_bytes: int = 4 * 1024**3   # 4 GB
    max_source_seconds: int = 3 * 3600    # 3 h
    max_window_seconds: int = 180         # a single clip window sent to a paid provider


@dataclass(frozen=True)
class Timeouts:
    ffprobe: float = 60
    ffmpeg_min: float = 120               # floor for any ffmpeg call
    ffmpeg_per_media_second: float = 10   # render timeout = max(min, 10 x media length + 60)
    ytdlp_metadata: float = 120
    ytdlp_download: float = 900
    reframe: float = 600
    http: float = 30


@dataclass(frozen=True)
class Settings:
    home: Path
    limits: Limits = field(default_factory=Limits)
    timeouts: Timeouts = field(default_factory=Timeouts)
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    ytdlp: str = "yt-dlp"
    harmar_base_url: str = REAL_HARMAR_BASE_URL
    allow_paid_harmar: bool = False
    font_family: str = "Noto Sans Armenian"
    fonts_dir: Path | None = None

    @property
    def ledger_path(self) -> Path:
        """Cross-project record of every paid reservation/charge (used for the per-day budget)."""
        return self.home / "paid-ledger.jsonl"

    def ffmpeg_timeout(self, media_seconds: float) -> float:
        return max(self.timeouts.ffmpeg_min, self.timeouts.ffmpeg_per_media_second * media_seconds + 60)


def _num(env: Mapping[str, str], name: str, default, cast):
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a {cast.__name__}, got {raw!r}") from exc


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    d = Limits()
    limits = Limits(
        max_paid_seconds_per_operation=_num(env, "HAYCLIPS_MAX_PAID_SECONDS_PER_OPERATION", d.max_paid_seconds_per_operation, int),
        max_paid_seconds_per_project=_num(env, "HAYCLIPS_MAX_PAID_SECONDS_PER_PROJECT", d.max_paid_seconds_per_project, int),
        max_paid_seconds_per_day=_num(env, "HAYCLIPS_MAX_PAID_SECONDS_PER_DAY", d.max_paid_seconds_per_day, int),
        balance_margin=_num(env, "HAYCLIPS_BALANCE_MARGIN", d.balance_margin, float),
        max_source_bytes=_num(env, "HAYCLIPS_MAX_SOURCE_BYTES", d.max_source_bytes, int),
        max_source_seconds=_num(env, "HAYCLIPS_MAX_SOURCE_SECONDS", d.max_source_seconds, int),
        max_window_seconds=_num(env, "HAYCLIPS_MAX_WINDOW_SECONDS", d.max_window_seconds, int),
    )
    fonts_dir = env.get("HAYCLIPS_FONTS_DIR")
    return Settings(
        home=Path(env.get("HAYCLIPS_HOME") or Path.home() / ".hayclips"),
        limits=limits,
        ffmpeg=env.get("HAYCLIPS_FFMPEG", "ffmpeg"),
        ffprobe=env.get("HAYCLIPS_FFPROBE", "ffprobe"),
        ytdlp=env.get("HAYCLIPS_YTDLP", "yt-dlp"),
        harmar_base_url=env.get("HAYCLIPS_HARMAR_BASE_URL", REAL_HARMAR_BASE_URL).rstrip("/"),
        allow_paid_harmar=env.get(PAID_OPT_IN_ENV) == PAID_OPT_IN_VALUE,
        font_family=env.get("HAYCLIPS_FONT_FAMILY", "Noto Sans Armenian"),
        fonts_dir=Path(fonts_dir) if fonts_dir else None,
    )
