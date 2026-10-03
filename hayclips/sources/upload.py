"""UploadedFileSource: a local video file the operator uploaded, behind the same SourceProvider interface.

The file lives inside the project folder (source/original.<ext>). Windows and previews are cut from it
with ffmpeg; nothing is downloaded. It has no free captions: moments are found with a cheap local
discovery transcript (hayclips.discovery). The full upload is never sent to a paid service.
"""
from __future__ import annotations

import math
from pathlib import Path

from .. import proc
from ..config import Settings, load_settings
from ..errors import SourceError, ValidationError
from ..hashing import sha256_file
from ..media.probe import duration_of, probe
from .base import SourceInfo, WindowFiles

ALLOWED_EXT = {".mp4", ".mov", ".m4v"}
ALLOWED_FORMATS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}    # ffprobe format_name parts for the MP4/MOV family
MAX_WINDOW_SECONDS = 600


def validate_upload(path: Path, ext: str, settings: Settings | None = None) -> dict:
    """Check an uploaded file before it becomes a project source. Returns probe facts or raises."""
    s = settings or load_settings()
    ext = ext.lower()
    if ext not in ALLOWED_EXT:
        raise ValidationError(f"{ext or 'this file type'} is not supported; upload an MP4, MOV or M4V file")
    size = path.stat().st_size
    if size == 0:
        raise ValidationError("the uploaded file is empty")
    if size > s.limits.max_source_bytes:
        raise ValidationError(f"the file is {size / 1024**3:.1f} GB; the limit is {s.limits.max_source_bytes / 1024**3:.1f} GB")
    import json
    try:
        out = proc.run([s.ffprobe, "-v", "error", "-show_entries", "format=format_name,duration:stream=codec_type",
                        "-of", "json", "-i", str(path)], timeout=s.timeouts.ffprobe, tool="ffprobe").stdout
        info = json.loads(out or "{}")
    except (proc.ToolError, ValueError):
        raise ValidationError("this file could not be read as a video") from None
    formats = set((info.get("format", {}).get("format_name") or "").split(","))
    kinds = {st.get("codec_type") for st in info.get("streams", [])}
    if not formats & ALLOWED_FORMATS:
        raise ValidationError("this is not an MP4/MOV video")
    if "video" not in kinds:
        raise ValidationError("this file has no video track")
    try:
        duration = float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        raise ValidationError("could not read the video duration") from None
    if not math.isfinite(duration) or duration < 5:
        raise ValidationError("the video is shorter than 5 seconds")
    if duration > s.limits.max_source_seconds:
        raise ValidationError(f"the video is {duration / 3600:.1f} h; the limit is {s.limits.max_source_seconds / 3600:.1f} h")
    return {"duration": round(duration, 3), "size": size, "has_audio": "audio" in kinds}


class UploadedFileSource:
    kind = "upload"

    def __init__(self, project_root: Path, source: dict, settings: Settings | None = None):
        self.settings = settings or load_settings()
        rel = source.get("file") or ""
        if not rel.startswith("source/original") or "/" in rel[len("source/"):] or Path(rel).suffix.lower() not in ALLOWED_EXT:
            raise SourceError("uploaded source path is not valid")
        self.path = (Path(project_root) / rel).resolve()
        if not self.path.is_relative_to(Path(project_root).resolve()):
            raise SourceError("uploaded source path is outside the project")
        self.source = source
        self.video_id = ""        # windows record an empty source_ref; the sha256 below identifies the upload

    def _file(self) -> Path:
        if not self.path.is_file():
            raise SourceError("the uploaded video is missing from the project folder",
                              hint="restore source/original.* from backup; it is never re-created")
        return self.path

    def inspect(self) -> SourceInfo:
        f = self._file()
        return SourceInfo(kind="upload", ref=self.source.get("sha256") or sha256_file(f), url="",
                          title=self.source.get("original_name", ""), duration=duration_of(f, self.settings),
                          size_estimate=f.stat().st_size)

    def fetch_captions(self, dest_dir: Path, lang: str = "hy") -> Path:
        raise SourceError("uploaded videos have no free captions",
                          hint="HayClips makes a local discovery transcript instead (Find clips)")

    def _cut(self, start: float, end: float, dest: Path, *, height: int, crf: int, preset: str) -> WindowFiles:
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            raise ValidationError("window times must be numbers") from None
        if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end <= start:
            raise ValidationError(f"bad window {start}-{end}")
        if end - start > MAX_WINDOW_SECONDS:
            raise ValidationError(f"window is {end - start:.0f}s; at most {MAX_WINDOW_SECONDS}s is cut at once")
        src = self._file()
        total = duration_of(src, self.settings)
        end = min(end, total)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        s = self.settings
        # accurate cut (re-encode); never taller than `height`, even dimensions
        proc.run([s.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src),
                  "-t", f"{end - start:.3f}", "-map", "0:v:0", "-map", "0:a:0?",
                  "-vf", f"scale=-2:'min({int(height)},ih)'", "-c:v", "libx264", "-preset", preset, "-crf", str(int(crf)),
                  "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest)],
                 timeout=s.ffmpeg_timeout(end - start), outputs=[dest], tool="ffmpeg")
        got = duration_of(dest, s)
        if abs(got - (end - start)) > 0.5:
            dest.unlink(missing_ok=True)
            raise SourceError(f"cut window is {got:.2f}s, expected {end - start:.2f}s")
        return WindowFiles(wide=dest, start=start, end=end)

    def fetch_window(self, start: float, end: float, dest: Path) -> WindowFiles:
        return self._cut(start, end, dest, height=1080, crf=18, preset="veryfast")

    def fetch_preview(self, start: float, end: float, dest: Path) -> WindowFiles:
        return self._cut(start, end, dest, height=360, crf=30, preset="ultrafast")

    def audio_for_discovery(self, dest: Path) -> Path:
        """16 kHz mono WAV of the whole upload, for the LOCAL discovery transcript only."""
        s = self.settings
        src = self._file()
        p = probe(src, s)
        if not p.has_audio:
            raise SourceError("the uploaded video has no audio track; there is nothing to transcribe")
        proc.run([s.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                  "-c:a", "pcm_s16le", str(dest)], timeout=s.ffmpeg_timeout(p.duration / 4), outputs=[dest], tool="ffmpeg")
        return dest
