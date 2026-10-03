"""Fetch step: download each selected clip's padded window and create its audio-only paid artifact.

Owns clips/<clip_id>/window.json, wide.mp4, audio.m4a and youtube.srt.

Rules:
- A clip whose window.json exists and whose recorded files still verify is skipped (no network).
- If recorded bytes changed or went missing, that clip STOPS with ArtifactMismatch: a paid transcript
  may be bound to those bytes, so nothing is re-downloaded or re-created over them.
- A media file that exists but is not recorded is never overwritten (ArtifactMismatch: investigate).
- Writes are two-phase: files land as .<name>.pending, window.json records their hashes, then the
  files are renamed. An interrupted run is completed on the next run by renaming pending files whose
  hash matches the record; pending files with no record are leftovers and are deleted.
- New clips get no preview.mp4: Harmar receives audio.m4a; rendering uses wide.mp4.
"""
from __future__ import annotations

import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Settings, load_settings
from .errors import ArtifactMismatch, PipelineError
from .hashing import sha256_file
from .media.audio import extract_audio
from .media.probe import duration_of
from .models import Clip, MediaFile, Window
from .project import ProjectRepo, media_record
from .sources.base import SourceProvider

MEDIA_NAMES = ("wide.mp4", "audio.m4a", "preview.mp4")


@dataclass
class FetchResult:
    clip_id: str
    status: str                 # "fetched" | "skipped" | "error"
    message: str = ""
    error: PipelineError | None = None


def default_source_factory(project_source: dict, settings: Settings) -> SourceProvider:
    from .sources.youtube import YouTubeSource
    if project_source.get("kind") not in (None, "", "youtube"):
        raise PipelineError(f"unsupported source kind {project_source.get('kind')!r}")
    ref = project_source.get("video_id") or project_source.get("url", "")
    return YouTubeSource(ref, settings=settings)


def _pending(path: Path) -> Path:
    return path.with_name(f".{path.name}.pending")


def _recorded(window: Window) -> list[MediaFile]:
    return [m for m in (window.wide, window.audio, window.preview) if m is not None]


def _recover_pending(repo: ProjectRepo, clip_id: str, window: Window) -> None:
    for media in _recorded(window):
        final = repo.media_path(clip_id, media)
        pending = _pending(final)
        if not final.exists() and pending.exists() and sha256_file(pending) == media.sha256:
            pending.replace(final)


def _caption_writer():
    try:
        from .selection import load_srt, write_srt
        return load_srt, write_srt
    except ImportError:
        return None


def _fetch_clip(repo: ProjectRepo, clip: Clip, source: SourceProvider, settings: Settings,
                captions: list | None) -> FetchResult:
    cdir = repo.clip_dir(clip.id)
    window = repo.load_window(clip.id)
    if window is not None:
        _recover_pending(repo, clip.id, window)
        for media in _recorded(window):
            repo.verify_media(clip.id, media)       # raises ArtifactMismatch
        return FetchResult(clip.id, "skipped", "window already fetched and verified")

    cdir.mkdir(parents=True, exist_ok=True)
    for name in MEDIA_NAMES:
        if (cdir / name).exists():
            raise ArtifactMismatch(f"{clip.id}: {name} exists but is not recorded in window.json",
                                   hint="move it away after checking no paid transcript used it; it is never overwritten")
        _pending(cdir / name).unlink(missing_ok=True)   # leftovers from a run that crashed before recording

    start = max(0.0, clip.start - clip.pad)
    end = clip.end + clip.pad
    duration = float(repo.load().source.get("duration") or 0)
    if duration:
        end = min(end, duration)
    wide_final, audio_final = cdir / "wide.mp4", cdir / "audio.m4a"
    work = cdir / f".fetch-{secrets.token_hex(4)}"
    work.mkdir()
    try:
        files = source.fetch_window(start, end, work / "wide.mp4")
        wide_tmp = files.wide
        wide = media_record(wide_tmp, "wide.mp4", "wide", duration_of(wide_tmp, settings))
        audio = extract_audio(wide_tmp, work / "audio.m4a", settings, wide_rel="wide.mp4", rel="audio.m4a")
        wide_tmp.replace(_pending(wide_final))
        (work / "audio.m4a").replace(_pending(audio_final))
        window = Window(clip_id=clip.id, source_start=files.start, source_end=files.end,
                        pad_start=clip.start - files.start, wide=wide, audio=audio,
                        source_ref=getattr(source, "video_id", "") or "")
        repo.save_window(window)                     # hashes recorded before the files get final names
        _pending(wide_final).replace(wide_final)
        _pending(audio_final).replace(audio_final)
    except BaseException:
        if repo.load_window(clip.id) is None:
            for name in ("wide.mp4", "audio.m4a"):
                _pending(cdir / name).unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if captions is not None:
        load_srt, write_srt = _caption_writer()
        write_srt(captions, clip.start, clip.end, cdir / "youtube.srt")
    return FetchResult(clip.id, "fetched", f"{files.end - files.start:.1f}s window")


def fetch_selected_clips(repo: ProjectRepo, clip_ids: list[str] | None = None, *,
                         source_factory: Callable[[dict, Settings], SourceProvider] | None = None,
                         settings: Settings | None = None, raise_errors: bool = False,
                         on_progress: Callable[[int, int, str], None] | None = None,
                         should_stop: Callable[[], bool] | None = None) -> list[FetchResult]:
    """Public fetch API used by the CLI and the worker.

    Fetches the selected clips (all of them, or only `clip_ids`, which must all be selected) in display
    order. Verified artifacts are reused; one clip's failure does not stop the others unless
    raise_errors=True. `on_progress(k, n, clip_id)` is called before each clip and `should_stop()`
    between clips (the worker uses it for cancellation and lease loss)."""
    settings = settings or load_settings()
    project = repo.load()
    clips = project.ordered()
    if clip_ids is not None:
        selected = {c.id for c in clips}
        missing = [cid for cid in dict.fromkeys(clip_ids) if cid not in selected]
        if missing:
            raise PipelineError(f"not selected clips: {', '.join(missing)}",
                                hint="only selected clips can be fetched; select them first")
        wanted = set(clip_ids)
        clips = [c for c in clips if c.id in wanted]
    source = (source_factory or default_source_factory)(project.source, settings)
    captions = None
    writer = _caption_writer()
    srts = sorted((repo.root / "source").glob("*.srt"))
    if writer and srts:
        captions = writer[0](srts[0])
    results = []
    for k, clip in enumerate(clips):
        if should_stop is not None and should_stop():
            break
        if on_progress is not None:
            on_progress(k, len(clips), clip.id)
        try:
            results.append(_fetch_clip(repo, clip, source, settings, captions))
        except PipelineError as err:
            if raise_errors:
                raise
            results.append(FetchResult(clip.id, "error", str(err), err))
    return results


def fetch_project(repo: ProjectRepo, *, source_factory: Callable[[dict, Settings], SourceProvider] | None = None,
                  settings: Settings | None = None, raise_errors: bool = False) -> list[FetchResult]:
    """Fetch every selected clip (kept for existing callers; same as fetch_selected_clips(repo))."""
    return fetch_selected_clips(repo, None, source_factory=source_factory, settings=settings,
                                raise_errors=raise_errors)
