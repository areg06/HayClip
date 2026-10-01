"""ffprobe wrappers (timeouts and errors via hayclips.proc)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .. import proc
from ..config import Settings, load_settings


@dataclass
class Probe:
    duration: float
    width: int | None
    height: int | None
    has_audio: bool
    sample_rate: int | None
    channels: int | None
    video_codec: str | None
    audio_codec: str | None


def probe(path: Path, settings: Settings | None = None) -> Probe:
    s = settings or load_settings()
    # "-i" keeps a path that starts with "-" from being parsed as an option
    out = proc.run([s.ffprobe, "-v", "error", "-show_entries",
                    "stream=codec_type,codec_name,width,height,sample_rate,channels:format=duration",
                    "-of", "json", "-i", str(path)],
                   timeout=s.timeouts.ffprobe, tool="ffprobe").stdout
    j = json.loads(out or "{}")
    v = next((x for x in j.get("streams", []) if x.get("codec_type") == "video"), {})
    a = next((x for x in j.get("streams", []) if x.get("codec_type") == "audio"), None)
    dur = j.get("format", {}).get("duration")
    return Probe(duration=float(dur) if dur not in (None, "N/A") else 0.0,
                 width=v.get("width"), height=v.get("height"), has_audio=a is not None,
                 sample_rate=int(a["sample_rate"]) if a and a.get("sample_rate") else None,
                 channels=a.get("channels") if a else None,
                 video_codec=v.get("codec_name"), audio_codec=a.get("codec_name") if a else None)


def duration_of(path: Path, settings: Settings | None = None) -> float:
    return probe(path, settings).duration
