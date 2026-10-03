"""SourceProvider interface. The pipeline depends on this, not on yt-dlp."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class SourceInfo:
    kind: str                  # "youtube", later "upload"
    ref: str                   # canonical reference (YouTube video id / upload id)
    url: str                   # canonical URL rebuilt from ref (never the raw user string)
    title: str = ""
    duration: float = 0.0
    size_estimate: int | None = None
    caption_languages: list[str] = field(default_factory=list)


@dataclass
class WindowFiles:
    wide: Path                 # landscape window, original aspect ratio, <= 1080p
    start: float               # source seconds actually covered
    end: float


class SourceProvider(Protocol):
    def inspect(self) -> SourceInfo: ...

    def fetch_captions(self, dest_dir: Path, lang: str = "hy-orig") -> Path:
        """Download free captions (SRT) and return the file path."""
        ...

    def fetch_window(self, start: float, end: float, dest: Path) -> WindowFiles:
        """Download only [start, end] of the source to dest (landscape)."""
        ...

    def fetch_preview(self, start: float, end: float, dest: Path) -> WindowFiles:
        """A cheap low-resolution copy of [start, end] for in-app previews (never used for paid work)."""
        ...
