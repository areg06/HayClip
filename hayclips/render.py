"""Per-clip render orchestration. Reads project/window/transcription state; writes only clips/<id>/render/
(and crop.json via the reframe cache) plus review.html.

For each selected clip, in display order:
  window -> completed transcript (verified bytes) -> render source (verified bytes) -> alignment proof
  -> cut (manual trim wins, else snap to Harmar sentences) -> crop plan -> ASS + MP4 per style
  -> ffprobe checks -> render.json
A clip that fails is reported and skipped; the others still render.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import jsonio
from .captions import ass as A
from .captions.fonts import check_fonts
from .captions.snap import snap
from .config import Settings, load_settings
from .errors import PipelineError
from .media import ffmpeg as F
from .media.alignment import verify_alignment
from .media.probe import probe
from .media.reframe import frame_filter, load_or_plan
from .models import Clip, Line, now_iso
from .project import ProjectRepo
from .selection import write_srt
from .transcription.store import completed_transcript


@dataclass
class ClipResult:
    clip_id: str
    order: int
    status: str                          # "rendered", "skipped", "failed"
    message: str = ""
    checks: list[str] = field(default_factory=list)
    outputs: dict = field(default_factory=dict)


def _clip_words(raw: dict, cs: float, ce: float):
    words, source = A.words_of(raw)
    keep = [(max(a - cs, 0.0), min(b, ce) - cs, w, t) for a, b, w, t in words
            if a < ce and (a >= cs - 0.05 or b > cs + 0.05)]
    segs = [Line(max(float(x["start"]) - cs, 0.0), float(x["end"]) - cs, A.clean(x["text"]))
            for x in raw.get("segments", []) if str(x.get("text", "")).strip()
            and float(x["start"]) < ce and (float(x["start"]) >= cs - 0.05 or float(x["end"]) > cs + 0.05)]
    return keep, segs, source


def _cut(clip: Clip, window, raw: dict, full: float) -> tuple[float, float, str, str]:
    """(start, end, mode, note) inside the window file."""
    if clip.trim is not None:
        cs, ce = float(clip.trim.caption_start), float(clip.trim.caption_end)
        mode, note = "manual", ""
    elif clip.pad > 0:
        planned = window.pad_start
        cs, ce, note = snap(raw.get("segments", []), planned, planned + clip.end - clip.start)
        mode = "auto"
    else:
        cs, ce, mode, note = 0.0, full, "whole", ""
    ce = min(ce, full)
    if not 0 <= cs < ce:
        raise PipelineError(f"invalid cut {cs:.2f}-{ce:.2f}s inside a {full:.2f}s window",
                            hint="fix the trim with `python -m hayclips edit ... --trim START:END`")
    return round(cs, 3), round(ce, 3), mode, note


def render_clip(repo: ProjectRepo, clip: Clip, *, styles: list[str], caption_bottom: int, hook_enabled: bool,
                settings: Settings, use_look: bool = False) -> ClipResult:
    """use_look: render the clip's own look (editor / Brand Kit) instead of the given styles."""
    from .captions.edits import apply_edits, load_edits
    from .look import PRESETS, normalise_hook, normalise_look
    look = normalise_look(clip.look) if (use_look and clip.look) else None
    hook_look = normalise_hook(clip.hook_look) if (use_look and clip.hook_look) else None
    if look:
        styles = [PRESETS[look["preset"]]]
    res = ClipResult(clip.id, clip.order, "failed")
    window = repo.load_window(clip.id)
    if window is None:
        res.status, res.message = "skipped", "not fetched yet (no window.json)"
        return res
    tr = completed_transcript(repo, clip.id)       # raises NeedsReconciliation / ArtifactMismatch
    if tr is None:
        res.status, res.message = "skipped", "not transcribed yet"
        return res
    tpath = repo.media_path(clip.id, tr.media)
    src_media = window.wide or window.preview
    if src_media is None:
        raise PipelineError("window has neither wide.mp4 nor preview.mp4")
    src = repo.verify_media(clip.id, src_media)
    alignment = verify_alignment(tpath, tr.media, src, src_media, settings=settings)
    full = probe(src, settings).duration
    edits = load_edits(repo.clip_dir(clip.id))
    raw = apply_edits(tr.raw, edits)
    cs, ce, mode, note = _cut(clip, window, raw, full)
    length = round(ce - cs, 3)
    words, segs, word_source = _clip_words(raw, cs, ce)
    hook = A.validate_hook(clip.hook) if hook_enabled else ""
    audio = F.plan_audio(src, cs, ce, settings)
    flags: list[str] = []
    if window.wide is not None and src_media is window.wide:
        plan_path = repo.clip_dir(clip.id) / "crop.json"
        had_plan = plan_path.exists()
        plan, recomputed = load_or_plan(plan_path, src, src_media.sha256)
        vf, flags, framing = frame_filter(plan, cs)
        if recomputed and had_plan:          # a first plan for a new clip is not a warning
            flags.append("crop plan recomputed for the current wide.mp4")
    else:
        vf, framing = "", "preview (already 720x1280)"
    rdir = repo.render_dir(clip.id)
    rdir.mkdir(parents=True, exist_ok=True)
    write_srt(segs, 0.0, length, rdir / "captions.srt")
    checks = list(flags)
    if note:
        checks.append(note)
    if not words:
        checks.append("no transcript words inside the cut: rendered without captions")
    for style in styles:
        doc, ev = A.build_ass(style, words, length, caption_bottom=caption_bottom, hook=hook,
                              family=settings.font_family, look=look, hook_look=hook_look)
        (rdir / f"{style}.ass").write_text(doc, encoding="utf-8")
        out = rdir / f"{style}.mp4"
        F.render_clip(src=src, out=out, ass_name=f"{style}.ass", start=cs, length=length, frame_filter=vf,
                      audio=audio, cwd=rdir, settings=settings)
        p, problems = F.check_output(out, length, settings)
        cues = [e for e in ev if e[2] != "Hook"]
        if cues and max(e[1] for e in cues) > p.duration + 0.01:
            problems.append(f"{style}.mp4: last caption ends after the video")
        if cues and min(e[0] for e in cues) > 0.3:
            problems.append(f"{style}.mp4: first caption starts at {min(e[0] for e in cues):.2f}s (> 0.3 s)")
        checks += problems
        res.outputs[style] = {"path": f"render/{style}.mp4", "width": p.width, "height": p.height,
                              "duration": round(p.duration, 3), "events": len(ev)}
    jsonio.write_json(rdir / "render.json", {
        "rendered_at": now_iso(), "transcript_attempt": tr.attempt_id, "transcript_media": tr.media.to_dict(),
        "render_source": src_media.to_dict(), "alignment": alignment.to_dict(),
        "cut": {"start": cs, "end": ce, "mode": mode, "note": note or None,
                "source_start": round(window.source_start + cs, 3), "source_end": round(window.source_start + ce, 3)},
        "word_source": word_source, "hook": hook or None, "audio": audio.note, "framing": framing,
        "caption_bottom": caption_bottom, "styles": styles, "outputs": res.outputs, "checks": checks,
        "look": look, "hook_look": hook_look, "transcript_edited": bool(edits.get("segments")),
        "first_word_at": round(words[0][0], 3) if words else None,
        "first_3s_text": " ".join(w for a, _, w, _ in words if a < 3.0)})
    res.status, res.checks = "rendered", checks
    res.message = f"{length:.2f}s, {framing}, alignment {alignment.method}"
    return res


def render_project(repo: ProjectRepo, *, styles: list[str], caption_bottom: int = A.CAPTION_BOTTOM,
                   hook_enabled: bool = True, settings: Settings | None = None) -> list[ClipResult]:
    from .review import write_review
    s = settings or load_settings()
    project = repo.load()
    for st in styles:
        if st not in A.STYLES:
            raise PipelineError(f"unknown caption style {st!r}; use A, B or C")
    check_fonts(s)
    results = []
    for clip in project.ordered():
        try:
            results.append(render_clip(repo, clip, styles=styles, caption_bottom=caption_bottom,
                                       hook_enabled=hook_enabled, settings=s))
        except PipelineError as exc:
            results.append(ClipResult(clip.id, clip.order, "failed", str(exc)))
    write_review(repo, project, results)
    return results
