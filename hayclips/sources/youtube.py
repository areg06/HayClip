"""YouTubeSource: the only code that talks to yt-dlp.

Security rules:
- The user's string is parsed into an 11-character video id and then discarded. yt-dlp only ever
  sees the canonical URL rebuilt from that id, placed after `--` so it cannot be read as an option.
- Every call disables user/system config files and plugins (--ignore-config, --no-plugin-dirs),
  removes any --exec (--no-exec), restricts extractors to YouTube (--use-extractors youtube) and
  runs through hayclips.proc with a timeout.
- Numbers placed in arguments (section times) are formatted from validated floats.
- Paths are ours; "%" is escaped so yt-dlp's output templating cannot expand anything.
"""
from __future__ import annotations

import json
import math
import re
import secrets
import shutil
import time
import urllib.parse
from pathlib import Path

from .. import proc
from ..config import Settings, load_settings
from ..errors import SourceError, ValidationError
from ..media.probe import duration_of
from .base import SourceInfo, WindowFiles

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
SHORT_HOST = "youtu.be"
CAPTION_LANG = re.compile(r"[a-z]{2,3}(-[A-Za-z0-9]{1,8})*")
VIDEO_FORMAT = "bv*[height<=1080]+ba/b[height<=1080]"
WINDOW_MAX_FILESIZE = "500M"
# Never download more than this in one window: a full-episode download is not a window.
MAX_DOWNLOAD_WINDOW_SECONDS = 600
DOWNLOAD_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5

PERMANENT_ERRORS = [
    (re.compile(r"video unavailable|video is unavailable|not available|has been removed|private video|does not exist|"
                r"account associated with this video has been terminated", re.I),
     "the video is unavailable or private", "check the link in a browser; the creator may have removed or restricted it"),
    (re.compile(r"sign in|members-only|join this channel|confirm your age|not a bot", re.I),
     "YouTube requires sign-in (age, members-only or bot check)", "ask the creator for the file or a public link"),
    (re.compile(r"unsupported url|no suitable extractor", re.I),
     "yt-dlp does not recognise this as a YouTube video", "check the link"),
]


def parse_youtube_ref(user_input: str) -> str:
    """Return the 11-character video id, or raise ValidationError. Never returns the raw string."""
    if not isinstance(user_input, str):
        raise ValidationError("source must be a YouTube link or video id")
    raw = user_input.strip()
    if not raw or any(ch.isspace() or ord(ch) < 32 or ord(ch) > 126 for ch in raw):
        raise ValidationError("source must be a YouTube link or video id (no spaces, control or non-ASCII characters)")
    if VIDEO_ID.fullmatch(raw) and not raw.startswith("-"):   # a bare "-…" id is ambiguous: paste the link
        return raw
    bad = ValidationError("only YouTube video links are accepted (youtube.com/watch?v=…, youtu.be/…, /shorts/…, /live/…)",
                          hint="playlists, other sites and local files are not supported")
    if not raw.lower().startswith(("https://", "http://")):
        raise bad
    parts = urllib.parse.urlsplit(raw)
    host = (parts.hostname or "").lower()
    if parts.netloc.lower() != host:      # userinfo ("x@host") or a port
        raise bad
    vid = None
    if host == SHORT_HOST:
        segs = parts.path.split("/")
        if len(segs) == 2:
            vid = segs[1]
    elif host in YOUTUBE_HOSTS:
        if parts.path == "/watch":
            vs = urllib.parse.parse_qs(parts.query).get("v", [])
            if len(vs) == 1:
                vid = vs[0]
        else:
            m = re.fullmatch(r"/(?:shorts|live)/([^/]+)", parts.path)
            vid = m.group(1) if m else None
    # real ids may start with "-"; that is safe here because only the rebuilt URL reaches yt-dlp
    if vid is None or not VIDEO_ID.fullmatch(vid):
        raise bad
    return vid


def canonical_url(video_id: str) -> str:
    if not VIDEO_ID.fullmatch(video_id):
        raise ValidationError("bad video id")
    return f"https://www.youtube.com/watch?v={video_id}"


def _caption_languages(meta: dict) -> list[str]:
    """Original-language auto captions (e.g. hy-orig and hy) plus manual subtitles.

    yt-dlp also lists every machine auto-translation (aa, ab, af, ...); those are not real captions."""
    auto = set(meta.get("automatic_captions") or {})
    orig = {k for k in auto if k.endswith("-orig")}
    keep = orig | {k[:-len("-orig")] for k in orig if k[:-len("-orig")] in auto}
    return sorted(keep | set(meta.get("subtitles") or {}))


def _template(path: Path) -> str:
    return str(path).replace("%", "%%")


def _error_from(exc: proc.ToolError) -> SourceError:
    lines = [ln.strip() for ln in (exc.stderr or "").splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.startswith("ERROR")] or lines[-1:]
    detail = errors[-1][:300] if errors else f"exit code {exc.returncode}"
    for pattern, message, hint in PERMANENT_ERRORS:
        if pattern.search(detail):
            err = SourceError(f"YouTube: {message} ({detail})", hint=hint)
            err.permanent = True
            return err
    err = SourceError(f"yt-dlp failed: {detail}", hint="often temporary; retry later or update yt-dlp")
    err.permanent = False
    return err


class YouTubeSource:
    kind = "youtube"

    def __init__(self, user_input: str, settings: Settings | None = None):
        self.video_id = parse_youtube_ref(user_input)
        self.url = canonical_url(self.video_id)
        self.settings = settings or load_settings()

    def _base(self) -> list[str]:
        return [self.settings.ytdlp, "--ignore-config", "--no-plugin-dirs", "--no-playlist", "--no-exec",
                "--use-extractors", "youtube", "--no-progress"]

    def _run(self, extra: list[str], timeout: float):
        try:
            return proc.run(self._base() + extra + ["--", self.url], timeout=timeout, tool="yt-dlp")
        except proc.ToolTimeout:
            raise
        except proc.ToolNotFound:
            raise
        except proc.ToolError as exc:
            raise _error_from(exc) from None

    def inspect(self) -> SourceInfo:
        out = self._run(["-J"], self.settings.timeouts.ytdlp_metadata).stdout
        try:
            meta = json.loads(out)
        except json.JSONDecodeError:
            raise SourceError("yt-dlp returned unreadable metadata") from None
        if meta.get("id") != self.video_id:
            raise SourceError("YouTube returned metadata for a different video")
        duration = meta.get("duration")
        if meta.get("is_live") or not duration:
            raise SourceError("live streams and videos without a known duration are not supported")
        limits = self.settings.limits
        if duration > limits.max_source_seconds:
            raise SourceError(f"source is {duration / 3600:.1f} h, longer than the "
                              f"{limits.max_source_seconds / 3600:.1f} h limit",
                              hint="raise HAYCLIPS_MAX_SOURCE_SECONDS if this is intended")
        size = meta.get("filesize_approx") or meta.get("filesize")
        if size and size > limits.max_source_bytes:
            raise SourceError(f"source is about {size / 1024**3:.1f} GB, over the "
                              f"{limits.max_source_bytes / 1024**3:.1f} GB limit",
                              hint="raise HAYCLIPS_MAX_SOURCE_BYTES if this is intended")
        langs = _caption_languages(meta)
        return SourceInfo(kind=self.kind, ref=self.video_id, url=self.url, title=meta.get("title", ""),
                          duration=float(duration), size_estimate=size, caption_languages=langs)

    def fetch_captions(self, dest_dir: Path, lang: str = "hy-orig") -> Path:
        if not isinstance(lang, str) or not CAPTION_LANG.fullmatch(lang):
            raise ValidationError(f"bad caption language {lang!r}")
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        stem = dest_dir / "src"
        self._run(["--skip-download", "--write-auto-subs", "--sub-langs", lang, "--sub-format", "srt",
                   "-o", _template(stem)], self.settings.timeouts.ytdlp_metadata)
        path = dest_dir / f"src.{lang}.srt"
        if not path.exists() or path.stat().st_size == 0:
            raise SourceError("no Armenian auto-captions for this video",
                              hint=f"YouTube has no '{lang}' captions; provide an SRT file instead")
        return path

    def fetch_window(self, start: float, end: float, dest: Path) -> WindowFiles:
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            raise ValidationError("window times must be numbers") from None
        if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end <= start:
            raise ValidationError(f"bad window {start}-{end}")
        if end - start > MAX_DOWNLOAD_WINDOW_SECONDS:
            raise ValidationError(f"window is {end - start:.0f}s; at most {MAX_DOWNLOAD_WINDOW_SECONDS}s "
                                  "is downloaded at once (full episodes are never downloaded)")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.parent / f".dl-{dest.name}-{secrets.token_hex(4)}"
        out = tmp / "window.mp4"
        extra = ["-f", VIDEO_FORMAT, "--download-sections", f"*{start:.2f}-{end:.2f}", "--force-keyframes-at-cuts",
                 "--merge-output-format", "mp4", "--max-filesize", WINDOW_MAX_FILESIZE, "-o", _template(out)]
        try:
            for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
                shutil.rmtree(tmp, ignore_errors=True)
                tmp.mkdir()
                try:
                    self._run(extra, self.settings.timeouts.ytdlp_download)
                    break
                except SourceError as err:
                    if getattr(err, "permanent", False) or attempt == DOWNLOAD_ATTEMPTS:
                        raise
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            if not out.exists():
                raise SourceError("yt-dlp finished but produced no file",
                                  hint="the section may be past the end of the video")
            got, want = duration_of(out, self.settings), end - start
            if abs(got - want) > 0.5:
                raise SourceError(f"downloaded window is {got:.2f}s, expected {want:.2f}s",
                                  hint="the section may extend past the video end; adjust the window")
            out.replace(dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return WindowFiles(wide=dest, start=start, end=end)
