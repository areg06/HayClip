"""Job handlers: one function per job type in contracts.py. They call the same package functions as the CLIs.

handler(job, ctx) -> dict. The dict is stored as the job result and shown in the UI. Handlers raise on
failure; the worker classifies the exception with `is_retryable`. Paid transcription is never retryable.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import proc
from ..config import Settings, load_settings
from ..errors import PipelineError, SourceError
from ..project import ProjectRepo
from . import contracts

TEST_SLEEP_ENV = "HAYCLIPS_TEST_SLEEP_IN_HANDLER"   # tests only; honoured only with a loopback Harmar URL


@dataclass
class Context:
    repo: ProjectRepo
    settings: Settings = field(default_factory=load_settings)
    progress: Callable[[float, str], None] = lambda fraction, note="": None
    cancelled: Callable[[], bool] = lambda: False


class JobCancelled(PipelineError):
    code = "cancelled"


def is_retryable(exc: BaseException) -> bool:
    """Transient: a tool timeout, a transient yt-dlp/source failure, or a dropped connection."""
    if isinstance(exc, proc.ToolTimeout):
        return True
    if isinstance(exc, SourceError):
        return getattr(exc, "permanent", True) is False
    return isinstance(exc, ConnectionError)


def error_text(exc: BaseException) -> str:
    if isinstance(exc, PipelineError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _check_cancel(ctx: Context) -> None:
    if ctx.cancelled():
        raise JobCancelled("cancelled by the operator")


def _test_sleep(ctx: Context) -> None:
    """Test hook so crash tests can kill a worker mid-job. Ignored unless Harmar points at loopback."""
    secs = os.environ.get(TEST_SLEEP_ENV)
    base = ctx.settings.harmar_base_url
    if secs and (base.startswith("http://127.0.0.1") or base.startswith("http://localhost")):
        deadline = time.monotonic() + float(secs)
        while time.monotonic() < deadline:
            time.sleep(0.05)
            _check_cancel(ctx)


# ----- handlers --------------------------------------------------------------------------------------

def import_captions(job: dict, ctx: Context) -> dict:
    contracts.validate("import_captions", job.get("payload") or {})
    from ..sources.youtube import YouTubeSource
    project = ctx.repo.load()
    src = YouTubeSource(project.source.get("video_id") or project.source.get("url", ""), settings=ctx.settings)
    ctx.progress(0.1, "reading video metadata")
    info = src.inspect()
    ctx.repo.update_source(kind=info.kind, video_id=info.ref, url=info.url, title=info.title, duration=info.duration)
    _check_cancel(ctx)
    ctx.progress(0.5, "downloading free Armenian captions")
    path = src.fetch_captions(ctx.repo.root / "source")
    ctx.repo.update_source(captions=str(path.relative_to(ctx.repo.root)))
    return {"title": info.title, "duration": info.duration, "captions": str(path.relative_to(ctx.repo.root)),
            "caption_languages": info.caption_languages}


def generate_candidates(job: dict, ctx: Context) -> dict:
    p = contracts.validate("generate_candidates", job.get("payload") or {})
    from ..selection import candidates, load_srt, pick, sentence_units
    project = ctx.repo.load()
    rel = project.source.get("captions")
    srt = ctx.repo.root / rel if rel else next(iter(sorted((ctx.repo.root / "source").glob("*.srt"))), None)
    if srt is None or not Path(srt).exists():
        raise PipelineError("no captions for this project yet", hint="run the caption import first")
    duration = float(project.source.get("duration") or 0)
    lines = load_srt(Path(srt))
    if not duration:
        duration = max(l.end for l in lines)
    ctx.progress(0.3, "scoring sentence windows")
    ranked = candidates(sentence_units(lines), duration, p["min_seconds"], p["max_seconds"], p["skip_start"], p["skip_end"])
    chosen = pick(ranked, p["count"])
    ctx.repo.save_candidates(chosen, {**p, "duration": duration})
    return {"count": len(chosen), "candidate_ids": [c.id for c in chosen]}


def fetch_windows(job: dict, ctx: Context) -> dict:
    p = contracts.validate("fetch_windows", job.get("payload") or {})
    from ..fetch import FetchResult, _caption_writer, _fetch_clip, default_source_factory
    project = ctx.repo.load()
    clips = project.ordered()
    if p["clip_ids"] is not None:
        wanted = set(p["clip_ids"])
        missing = wanted - {c.id for c in clips}
        if missing:
            raise PipelineError(f"not selected clips: {', '.join(sorted(missing))}")
        clips = [c for c in clips if c.id in wanted]
    source = default_source_factory(project.source, ctx.settings)
    writer = _caption_writer()
    srts = sorted((ctx.repo.root / "source").glob("*.srt"))
    captions = writer[0](srts[0]) if writer and srts else None
    results, first_error = [], None
    for k, clip in enumerate(clips):
        _check_cancel(ctx)
        ctx.progress(k / max(len(clips), 1), f"window {k + 1}/{len(clips)}")
        try:
            r = _fetch_clip(ctx.repo, clip, source, ctx.settings, captions)
        except PipelineError as err:
            r = FetchResult(clip.id, "error", str(err), err)
            first_error = first_error or err
        results.append({"clip_id": r.clip_id, "status": r.status, "message": r.message})
    if first_error is not None:
        first_error.job_result = {"clips": results}
        raise first_error
    return {"clips": results}


def transcribe(job: dict, ctx: Context) -> dict:
    """PAID. Every guard (consent, opt-in, confirmation, budgets, balance, exact bytes, reconciliation)
    lives in the transcription service. The key is read from this worker's environment only."""
    p = contracts.validate("transcribe", job.get("payload") or {})
    from ..models import retry_safety
    from ..transcription import service
    _test_sleep(ctx)
    ctx.progress(0.05, "checking consent, budgets and existing transcripts")
    outcomes = service.transcribe(ctx.repo, p["clip_ids"], settings=ctx.settings,
                                  api_key=os.environ.get("HARMAR_API_KEY") or None, confirmed_by=p["confirmed_by"])
    out = [{"clip_id": o.clip_id, "action": o.action, "note": o.note,
            "state": o.attempt.state if o.attempt else None,
            "retry_safety": retry_safety(o.attempt.state) if o.attempt else None} for o in outcomes]
    bad = [o for o in out if o["action"] in ("failed", "needs_reconciliation", "blocked")]
    if bad:
        err = PipelineError("; ".join(f"{o['clip_id']}: {o['action']} {o['note']}".strip() for o in bad),
                            hint="see the clip status; a submission with an unknown outcome needs reconciliation")
        err.job_result = {"clips": out}
        raise err
    return {"clips": out}


def render(job: dict, ctx: Context) -> dict:
    p = contracts.validate("render", job.get("payload") or {})
    from ..captions.fonts import check_fonts
    from ..render import ClipResult, render_clip
    from ..review import write_review
    _test_sleep(ctx)
    project = ctx.repo.load()
    clips = project.ordered()
    if p["clip_ids"] is not None:
        wanted = set(p["clip_ids"])
        missing = wanted - {c.id for c in clips}
        if missing:
            raise PipelineError(f"not selected clips: {', '.join(sorted(missing))}")
        clips = [c for c in clips if c.id in wanted]
    check_fonts(ctx.settings)
    results = []
    for k, clip in enumerate(clips):
        _check_cancel(ctx)
        ctx.progress(k / max(len(clips), 1), f"rendering clip {k + 1}/{len(clips)}")
        try:
            results.append(render_clip(ctx.repo, clip, styles=p["styles"], caption_bottom=p["caption_bottom"],
                                       hook_enabled=p["hook_enabled"], settings=ctx.settings))
        except PipelineError as exc:
            results.append(ClipResult(clip.id, clip.order, "failed", str(exc)))
    write_review(ctx.repo, project, results)
    out = [{"clip_id": r.clip_id, "status": r.status, "message": r.message, "checks": r.checks,
            "outputs": r.outputs} for r in results]
    failed = [r for r in results if r.status == "failed"]
    if failed:
        err = PipelineError("; ".join(f"{r.clip_id}: {r.message}" for r in failed))
        err.job_result = {"clips": out}
        raise err
    rendered = [r for r in results if r.status == "rendered"]
    if not results:
        raise PipelineError("nothing was rendered: no selected clips", hint="select clips first")
    if not rendered:
        err = PipelineError("nothing was rendered: " + "; ".join(f"{r.clip_id}: {r.message}" for r in results),
                            hint="clips need a downloaded window and a transcript (steps 4 and 5) before rendering")
        err.job_result = {"clips": out}
        raise err
    skipped = len(results) - len(rendered)
    ctx.progress(1.0, f"rendered {len(rendered)} clip(s)" + (f", skipped {skipped} (see result)" if skipped else ""))
    return {"clips": out}


HANDLERS: dict[str, Callable[[dict, Context], dict]] = {
    "import_captions": import_captions,
    "generate_candidates": generate_candidates,
    "fetch_windows": fetch_windows,
    "transcribe": transcribe,
    "render": render,
}
