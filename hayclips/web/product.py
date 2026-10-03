"""Creator-facing language for project state. Engineering job states stay internal."""
from __future__ import annotations

from .. import jsonio
from ..models import COMPLETED
from ..project import ProjectRepo
from ..transcription.store import list_attempts

FINDING = {"import_captions", "discover_transcript", "generate_candidates"}
FINISHING = {"fetch_windows", "transcribe", "render"}
ACTIVE = ("QUEUED", "RUNNING", "RETRY_WAIT")


def fmt_duration(seconds: float) -> str:
    s = int(round(seconds))
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02}m"
    return f"{m}m {s:02}s" if m else f"{s}s"


def clip_facts(repo: ProjectRepo, clip) -> dict:
    att = list_attempts(repo, clip.id)
    info = jsonio.read_json(repo.render_dir(clip.id) / "render.json", default=None)
    return {"transcribed": any(a.state == COMPLETED for a in att), "rendered": bool(info and info.get("outputs")),
            "window": repo.load_window(clip.id) is not None, "info": info}


def project_stage(repo: ProjectRepo, jobs: list[dict], reviewed: set[str], scheduled: set[str]) -> dict:
    """{stage, tone, summary, n_candidates, n_chosen, n_ready} in creator words."""
    project = repo.load()
    n_cand = len(repo.load_candidates())
    chosen = project.ordered()
    facts = {c.id: clip_facts(repo, c) for c in chosen}
    ready = [c for c in chosen if facts[c.id]["rendered"]]
    active = {j["type"] for j in jobs if j["state"] in ACTIVE}
    if active & FINDING:
        stage, tone = "Finding clips", "brand"
    elif active & FINISHING:
        stage, tone = "Adding captions", "brand"
    elif not n_cand:
        stage, tone = "New", ""
    elif not chosen or not any(f["transcribed"] for f in facts.values()):
        stage, tone = "Choose clips", "warn"
    elif chosen and all(c.id in scheduled for c in chosen):
        stage, tone = "Scheduled", "ok"
    elif ready and len(ready) == len(chosen) and all(c.id in reviewed for c in chosen):
        stage, tone = "Ready", "ok"
    else:
        stage, tone = "Editing", "warn"
    parts = [f"{n_cand} moment{'s' if n_cand != 1 else ''}"] if n_cand else ["no moments yet"]
    if chosen:
        parts.append(f"{len(chosen)} chosen")
    if ready:
        parts.append(f"{len(ready)} ready")
    return {"stage": stage, "stage_tone": tone, "summary": " · ".join(parts), "n_candidates": n_cand,
            "n_chosen": len(chosen), "n_ready": len(ready), "facts": facts}
