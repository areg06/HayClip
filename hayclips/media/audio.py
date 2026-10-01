"""Audio-only artifact for paid transcription.

The audio is re-encoded once from the landscape window (wide.mp4) and never re-created: its sha256 is
what a paid transcript is bound to. The original sample rate and channel count are kept (no -ar/-ac)
so the audio timeline is the window's timeline; ffmpeg writes an MP4 edit list that hides the AAC
encoder priming delay, so t=0 in audio.m4a is t=0 in wide.mp4 (checked in tests to within 50 ms).
"""
from __future__ import annotations

from pathlib import Path

from .. import proc
from ..config import Settings, load_settings
from ..errors import SourceError
from ..hashing import sha256_file
from ..models import MediaFile
from .probe import probe

AUDIO_RECIPE = "aac128k-v1"   # bump when the command below changes; recorded in MediaFile.derived_from


def extract_audio(wide_path: Path, out_path: Path, settings: Settings | None = None, *,
                  wide_rel: str = "wide.mp4", rel: str | None = None) -> MediaFile:
    s = settings or load_settings()
    wide_path, out_path = Path(wide_path).resolve(), Path(out_path).resolve()
    if out_path.exists():
        raise SourceError(f"{out_path.name} already exists; refusing to overwrite an audio artifact")
    info = probe(wide_path, s)
    if not info.has_audio:
        raise SourceError("window has no audio; nothing to transcribe",
                          hint="check the source; a silent window cannot be captioned")
    proc.run([s.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-n", "-i", str(wide_path),
              "-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out_path)],
             timeout=s.ffmpeg_timeout(info.duration), outputs=[out_path], tool="ffmpeg")
    return MediaFile(path=rel or out_path.name, sha256=sha256_file(out_path), bytes=out_path.stat().st_size,
                     duration=probe(out_path, s).duration, kind="audio",
                     derived_from={"path": wide_rel, "sha256": sha256_file(wide_path), "recipe": AUDIO_RECIPE})
