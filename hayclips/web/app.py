"""HayClips local operator web app (Phase 1b): one workflow, one operator, localhost only.

Pages render server-side (Jinja2, autoescape). Long work (downloads, analysis, paid transcription,
rendering) is never done inside a request: the app only enqueues jobs (hayclips.jobs.queue) with
payloads checked by hayclips.jobs.contracts, and the worker process executes them.
Per-project artifacts stay in the project directory (ProjectRepo); Postgres holds the project index,
jobs, review decisions and the audit log.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import db, jsonio
from ..config import Settings, load_settings
from ..errors import PipelineError, ValidationError
from ..jobs import contracts, queue
from ..models import ConsentRecord, Trim, new_id, retry_safety
from ..project import ProjectRepo
from ..selection import explain, explain_parts, stamp
from ..sources.youtube import canonical_url, parse_youtube_ref
from ..transcription import budget
from ..transcription.store import list_attempts
from .security import CSRF_FIELD, Csrf, SecurityMiddleware, allowed_hosts_for, session_id

HERE = Path(__file__).parent
REPO_ROOT = HERE.parents[1]
CLIP_ID = re.compile(r"^clp_[0-9a-f]{10}$")
PROJECT_ID = re.compile(r"^prj_[0-9a-f]{10}$")
MEDIA = {  # public name -> path inside clips/<id>/
    "A.mp4": "render/A.mp4", "B.mp4": "render/B.mp4", "C.mp4": "render/C.mp4",
    "captions.srt": "render/captions.srt", "wide.mp4": "wide.mp4", "preview.mp4": "preview.mp4",
}
PILOT_DIR = re.compile(r"^pilot-\d{2}$")


class NotFound(Exception):
    pass


class MissingStorage(Exception):
    def __init__(self, row):
        super().__init__(row["id"])
        self.row = row


def hms(value) -> str:
    """Seconds as m:ss or h:mm:ss for people (SRT-style stamps stay in files)."""
    try:
        total = int(round(float(value)))
    except (TypeError, ValueError):
        return ""
    h, rest = divmod(total, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02}:{sec:02}" if h else f"{m}:{sec:02}"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40]


def create_app(*, dsn: str | None = None, projects_root: Path | None = None, repo_root: Path | None = None,
               port: int = 8765, allowed_hosts: set[str] | None = None) -> FastAPI:
    projects_root = Path(projects_root or os.environ.get("HAYCLIPS_PROJECTS_ROOT") or REPO_ROOT / "projects").resolve()
    repo_root = Path(repo_root or REPO_ROOT).resolve()
    conninfo = dsn or db.dsn()
    csrf = Csrf()
    app = FastAPI(title="HayClips", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityMiddleware, allowed_hosts=allowed_hosts or allowed_hosts_for(port), csrf=csrf)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]))
    env.filters["stamp"] = stamp
    env.filters["hms"] = hms
    env.filters["safety"] = retry_safety

    with db.connect(conninfo) as conn:
        db.migrate(conn)

    # ----- helpers -------------------------------------------------------------------------------
    def conn():
        return db.connect(conninfo)

    def page(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        settings = load_settings()
        ctx.update(csrf=csrf.token(session_id(request)), csrf_field=CSRF_FIELD,
                   paid_enabled=settings.allow_paid_harmar)
        return HTMLResponse(env.get_template(name).render(**ctx), status_code=status)

    def back(url: str, msg: str | None = None) -> RedirectResponse:
        return RedirectResponse(url + (f"?msg={quote(msg)}" if msg else ""), status_code=303)

    def project_row(c, pid: str) -> dict:
        if not PROJECT_ID.match(pid):
            raise NotFound()
        row = c.execute("SELECT * FROM projects WHERE id = %s", (pid,)).fetchone()
        if not row or row["removed_at"] is not None:
            raise NotFound()
        if queue.storage_state(row) == queue.MISSING_STORAGE:
            raise MissingStorage(row)   # explained to the operator; never repaired automatically
        return row

    def repo_for(row) -> ProjectRepo:
        return ProjectRepo(Path(row["dir"]))

    def clip_of(project, cid: str):
        if not CLIP_ID.match(cid):
            raise NotFound()
        try:
            return project.clip(cid)
        except KeyError:
            raise NotFound() from None

    def enqueue(c, row, jtype: str, payload: dict, key: str, actor: str = "operator", clip_id=None):
        payload = contracts.validate(jtype, payload)
        job = queue.enqueue(c, project_id=row["id"], type=jtype, payload=payload, clip_id=clip_id,
                            idempotency_key=key, requested_by=actor)
        queue.log_event(c, row["id"], f"enqueue:{jtype}", {"job_id": job["id"], "payload": payload}, actor=actor)
        return job

    def edit_revision(project) -> str:
        data = json.dumps([c.to_dict() for c in project.clips], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(data.encode()).hexdigest()[:12]

    def selected_ids(project) -> list[str]:
        return [c.id for c in project.ordered()]

    def transcribed(repo: ProjectRepo, clip_ids) -> set[str]:
        from ..models import COMPLETED
        return {cid for cid in clip_ids if any(a.state == COMPLETED for a in list_attempts(repo, cid))}

    def has_captions(repo: ProjectRepo) -> bool:
        src = repo.root / "source"
        return src.is_dir() and any(src.glob("*.srt"))

    @app.exception_handler(NotFound)
    async def _nf(request, exc):
        return HTMLResponse("not found", status_code=404)

    @app.exception_handler(MissingStorage)
    async def _missing(request, exc):
        if request.method != "GET" or request.url.path != f"/p/{exc.row['id']}":
            return back(f"/p/{exc.row['id']}", "local project files are missing; nothing was queued or changed")
        return page(request, "missing.html", row=None, project_row=exc.row,
                    dir_name=Path(exc.row["dir"]).name, msg=request.query_params.get("msg"))

    @app.post("/p/{pid}/remove")
    async def remove_stale(request: Request, pid: str):
        form = await request.form()
        if not PROJECT_ID.match(pid):
            raise NotFound()
        with conn() as c:
            try:
                queue.remove_stale_project(c, pid, confirm_name=str(form.get("confirm_name", "")),
                                           actor=str(form.get("removed_by", ""))[:100])
            except PipelineError as exc:
                return back(f"/p/{pid}", exc.message)
        return back("/dashboard", "stale project entry removed (its jobs and audit history are kept)")

    # ----- product pages ----------------------------------------------------------------------------
    def with_stage(c, rows):
        from .product import project_stage
        for r in rows:
            r.setdefault("stage", "Files missing")
            r.setdefault("stage_tone", "bad")
            r.setdefault("summary", "")
            if r.get("storage_state") == queue.MISSING_STORAGE:
                continue
            jobs = queue.for_project(c, r["id"], 10)
            reviewed = {x["clip_id"] for x in c.execute(
                "SELECT DISTINCT clip_id FROM review_decisions WHERE project_id = %s", (r["id"],)).fetchall()}
            scheduled = {x["clip_id"] for x in c.execute(
                "SELECT DISTINCT clip_id FROM calendar_items WHERE project_id = %s AND status = 'Scheduled'",
                (r["id"],)).fetchall()}
            try:
                r.update(project_stage(ProjectRepo(Path(r["dir"])), jobs, reviewed, scheduled))
            except PipelineError:
                pass
        return rows

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, msg: str | None = None):
        return page(request, "home.html", nav_active="home", msg=msg)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request, msg: str | None = None):
        with conn() as c:
            rows = with_stage(c, queue.active_projects(c))
        pilots = sorted(p.name for p in repo_root.glob("pilot-*") if PILOT_DIR.match(p.name) and (p / "project.json").exists())
        known = {Path(r["dir"]).name for r in rows}
        return page(request, "dashboard.html", nav_active="dashboard", projects=rows, msg=msg,
                    registrable=[p for p in pilots if p not in known])

    @app.get("/projects", response_class=HTMLResponse)
    def projects_page(request: Request, msg: str | None = None):
        with conn() as c:
            rows = with_stage(c, queue.active_projects(c))
        return page(request, "projects.html", nav_active="projects", projects=rows, msg=msg)

    def new_page(request: Request, status: int = 200, **ctx):
        lim = load_settings().limits
        ctx.setdefault("tab", "youtube")
        return page(request, "new.html", status=status, nav_active="dashboard",
                    max_gb=round(lim.max_source_bytes / 1024**3, 1), max_hours=round(lim.max_source_seconds / 3600, 1), **ctx)

    @app.get("/new", response_class=HTMLResponse)
    def new_video(request: Request, tab: str = "youtube", msg: str | None = None):
        return new_page(request, tab="upload" if tab == "upload" else "youtube", msg=msg)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, msg: str | None = None):
        return page(request, "settings.html", nav_active="settings", msg=msg)

    @app.post("/projects")
    async def create_project(request: Request):
        form = await request.form()
        name = str(form.get("name", "")).strip()[:120]
        url = str(form.get("url", ""))
        try:
            vid = parse_youtube_ref(url)
            if not name:
                raise ValidationError("give the project a name")
        except PipelineError as exc:
            return new_page(request, status=400, error=exc.message, hint=exc.hint, form_name=name)
        pid = new_id("prj")
        slug = _slug(name)
        projects_root.mkdir(parents=True, exist_ok=True)
        target = projects_root / (f"{slug}-{pid[4:9]}" if slug else pid)
        repo = ProjectRepo(target)
        repo.init(name, {"kind": "youtube", "video_id": vid, "url": canonical_url(vid)})
        with conn() as c:
            row = queue.register_project(c, project_id=pid, name=name, dir=str(target), source_url=canonical_url(vid))
            queue.log_event(c, row["id"], "project_created", {"source": canonical_url(vid)}, actor="operator")
            if form.get("find") == "1":       # "Find clips": captions, then candidates, no paid step
                enqueue(c, row, "import_captions", {"then": ["generate_candidates"]}, f"{pid}:import_captions")
        return back(f"/p/{row['id']}")

    @app.post("/projects/register")
    async def register_existing(request: Request):
        form = await request.form()
        name = str(form.get("dir", ""))
        candidates = {}
        for p in repo_root.glob("pilot-*"):
            if PILOT_DIR.match(p.name):
                candidates[p.name] = p
        if projects_root.exists():
            for p in projects_root.iterdir():
                candidates.setdefault(p.name, p)
        target = candidates.get(name)
        if target is None or "/" in name or not (target / "project.json").is_file() or target.is_symlink():
            return back("/dashboard", "That folder is not an allowed project folder (pilot-NN or a folder in the projects root)")
        target = target.resolve()
        repo = ProjectRepo(target)
        try:
            project = repo.load()
        except PipelineError as exc:
            return back("/dashboard", exc.message)
        with conn() as c:
            row = queue.register_project(c, project_id=new_id("prj"), name=project.name, dir=str(target),
                                         source_url=project.source.get("url"))
            queue.log_event(c, row["id"], "project_registered", {"dir": target.name}, actor="operator")
        return back(f"/p/{row['id']}")

    # ----- project overview -------------------------------------------------------------------------
    @app.get("/p/{pid}", response_class=HTMLResponse)
    def overview(request: Request, pid: str, msg: str | None = None):
        with conn() as c:
            row = project_row(c, pid)
            jobs = queue.for_project(c, pid, 15)
        repo = repo_for(row)
        project = repo.load()
        cands = repo.load_candidates()
        clips = []
        for cl in project.ordered(selected_only=False):
            att = list_attempts(repo, cl.id)
            w = repo.load_window(cl.id)
            info = jsonio.read_json(repo.render_dir(cl.id) / "render.json", default=None)
            clips.append({"clip": cl, "window": w, "attempt": att[-1] if att else None,
                          "rendered": sorted(info["outputs"]) if info else []})
        captions = sorted(p.name for p in (repo.root / "source").glob("*.srt")) if (repo.root / "source").exists() else []
        sel = [x for x in clips if x["clip"].selected]
        n_transcribed = len(transcribed(repo, [x["clip"].id for x in sel]))
        with conn() as c:
            n_reviewed = c.execute("SELECT count(DISTINCT clip_id) AS n FROM review_decisions WHERE project_id = %s",
                                   (pid,)).fetchone()["n"]
        done = [bool(captions), bool(cands), bool(sel), bool(sel) and all(x["window"] for x in sel),
                bool(sel) and n_transcribed == len(sel), bool(sel) and all(x["rendered"] for x in sel),
                bool(sel) and n_reviewed >= len(sel)]
        current = next((k for k, d in enumerate(done) if not d), None)
        active = [j for j in jobs if j["state"] in ("QUEUED", "RUNNING", "RETRY_WAIT")]
        return page(request, "project.html", row=row, project=project, clips=clips, jobs=jobs, msg=msg,
                    n_candidates=len(cands), captions=captions, n_transcribed=n_transcribed,
                    steps_done=done, current_step=current, active_jobs=active)

    @app.get("/p/{pid}/jobs", response_class=HTMLResponse)
    def jobs_partial(request: Request, pid: str):
        with conn() as c:
            project_row(c, pid)
            jobs = queue.for_project(c, pid, 15)
        return page(request, "_jobs.html", jobs=jobs, pid=pid)

    @app.post("/p/{pid}/jobs/{jid}/cancel")
    def cancel_job(pid: str, jid: int):
        with conn() as c:
            project_row(c, pid)
            job = queue.get(c, jid)
            if not job or job["project_id"] != pid:
                raise NotFound()
            state = queue.request_cancel(c, jid)
            queue.log_event(c, pid, "cancel_requested", {"job_id": jid}, actor="operator")
        msg = "cancelled" if state == "CANCELLED" else ("cancel requested; the worker stops at the next step"
                                                       if state else "job already finished")
        if job["type"] == "transcribe" and state == "RUNNING":
            msg += ". A paid submission that already reached Harmar cannot be cancelled; its transcript is kept"
        return back(f"/p/{pid}", msg)

    @app.post("/p/{pid}/jobs/import_captions")
    def job_import(pid: str):
        with conn() as c:
            row = project_row(c, pid)
            enqueue(c, row, "import_captions", {}, f"{pid}:import_captions")
        return back(f"/p/{pid}", "caption import queued")

    @app.post("/p/{pid}/jobs/generate_candidates")
    async def job_candidates(request: Request, pid: str):
        form = await request.form()
        try:
            payload = {k: float(form.get(k)) for k in ("min_seconds", "max_seconds", "skip_start", "skip_end")
                       if form.get(k) not in (None, "")}
            if form.get("count") not in (None, ""):
                payload["count"] = int(form.get("count"))
            key = f"{pid}:candidates:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
            with conn() as c:
                row = project_row(c, pid)
                if not has_captions(repo_for(row)):
                    return back(f"/p/{pid}", "fetch the free captions first (step 1); candidates are built from them")
                enqueue(c, row, "generate_candidates", payload, key)
        except (ValueError, PipelineError) as exc:
            return back(f"/p/{pid}", getattr(exc, "message", None) or "numbers only, please")
        return back(f"/p/{pid}", "candidate generation queued")

    @app.post("/p/{pid}/jobs/fetch_windows")
    def job_fetch(pid: str):
        with conn() as c:
            row = project_row(c, pid)
            project = repo_for(row).load()
            ids = selected_ids(project)
            if not ids:
                return back(f"/p/{pid}", "select clips first")
            enqueue(c, row, "fetch_windows", {"clip_ids": ids}, f"{pid}:fetch:{','.join(sorted(ids))}")
        return back(f"/p/{pid}", "window download queued")

    @app.post("/p/{pid}/jobs/render")
    async def job_render(request: Request, pid: str):
        form = await request.form()
        styles = [s for s in ("A", "B", "C") if form.get(f"style_{s}")] or ["A"]
        try:
            cb = int(form.get("caption_bottom") or 930)
            with conn() as c:
                row = project_row(c, pid)
                project = repo_for(row).load()
                only = form.get("clip_id")
                ids = [clip_of(project, only).id] if only else selected_ids(project)
                if not ids:
                    return back(f"/p/{pid}", "select clips first")
                if not transcribed(repo_for(row), ids):
                    return back(f"/p/{pid}/review" if form.get("from") == "review" else f"/p/{pid}",
                                "rendering needs a transcript: transcribe the clips first (step 5)")
                payload = {"clip_ids": ids, "styles": styles, "caption_bottom": cb,
                           "hook_enabled": form.get("hook_enabled") == "on"}
                key = f"{pid}:render:" + hashlib.sha256(
                    json.dumps([payload, edit_revision(project)], sort_keys=True).encode()).hexdigest()[:16]
                enqueue(c, row, "render", payload, key)
        except (ValueError, PipelineError) as exc:
            return back(f"/p/{pid}", getattr(exc, "message", None) or "caption height must be a number")
        dest = f"/p/{pid}/review" if form.get("from") == "review" else f"/p/{pid}"
        return back(dest, "render queued")

    # ----- candidates and clip selection ---------------------------------------------------------
    @app.get("/p/{pid}/candidates", response_class=HTMLResponse)
    def candidates_page(request: Request, pid: str, msg: str | None = None):
        with conn() as c:
            row = project_row(c, pid)
        repo = repo_for(row)
        project = repo.load()
        taken = {c.candidate_id: c for c in project.clips}
        vid = project.source.get("video_id") or ""
        try:
            vid = parse_youtube_ref(vid or project.source.get("url", ""))
        except PipelineError:
            vid = ""
        cands = [{"c": x, "explain": explain(x), "parts": explain_parts(x), "clip": taken.get(x.id),
                  "watch": f"https://www.youtube.com/watch?v={vid}&t={int(x.start)}s" if vid else None}
                 for x in repo.load_candidates()]
        return page(request, "candidates.html", row=row, project=project, cands=cands, msg=msg)

    @app.post("/p/{pid}/select")
    async def select(request: Request, pid: str):
        form = await request.form()
        with conn() as c:
            row = project_row(c, pid)
        repo = repo_for(row)
        cand = str(form.get("candidate_id", ""))
        if not re.fullmatch(r"cand_[0-9a-f]{10}", cand):
            raise NotFound()
        try:
            clip = repo.select(cand, title=str(form.get("title", ""))[:200], hook=str(form.get("hook", ""))[:200],
                               pick_note=str(form.get("pick_note", ""))[:500])
            if not clip.selected:
                repo.update_clip(clip.id, selected=True)
        except PipelineError as exc:
            return back(f"/p/{pid}/candidates", exc.message)
        with conn() as c:
            queue.log_event(c, pid, "clip_selected", {"candidate": cand}, clip_id=clip.id, actor="operator")
        return back(f"/p/{pid}/candidates", f"selected as {clip.id}")

    @app.post("/p/{pid}/clips/{cid}/move")
    async def move(request: Request, pid: str, cid: str):
        form = await request.form()
        with conn() as c:
            row = project_row(c, pid)
        repo = repo_for(row)
        project = repo.load()
        clip_of(project, cid)
        order = [c.id for c in project.ordered(selected_only=False)]
        i = order.index(cid)
        j = i - 1 if form.get("dir") == "up" else i + 1
        if 0 <= j < len(order):
            order[i], order[j] = order[j], order[i]
            for n, x in enumerate(order, 1):
                repo.update_clip(x, order=n)
        return back(f"/p/{pid}")

    @app.post("/p/{pid}/clips/{cid}/selected")
    async def set_selected(request: Request, pid: str, cid: str):
        form = await request.form()
        with conn() as c:
            row = project_row(c, pid)
        repo = repo_for(row)
        clip_of(repo.load(), cid)
        repo.update_clip(cid, selected=form.get("value") == "yes")
        return back(f"/p/{pid}")

    @app.post("/p/{pid}/clips/{cid}/edit")
    async def edit_clip(request: Request, pid: str, cid: str):
        form = await request.form()
        with conn() as c:
            row = project_row(c, pid)
        repo = repo_for(row)
        clip_of(repo.load(), cid)
        changes = {k: str(form.get(k))[:500] for k in ("title", "hook", "pick_note") if form.get(k) is not None}
        trim = str(form.get("trim", "")).strip()
        dest = f"/p/{pid}/review" if form.get("from") == "review" else f"/p/{pid}"
        if trim:
            if trim == "auto":
                changes["trim"] = None
            else:
                m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", trim)
                if not m or float(m.group(1)) >= float(m.group(2)):
                    return back(dest, "trim must be START:END seconds inside the padded window, or auto")
                changes["trim"] = Trim(caption_start=float(m.group(1)), caption_end=float(m.group(2)))
        if len(changes.get("hook", "")) > 45:
            return back(dest, "hook must be 45 characters or fewer (it is not shortened automatically)")
        current = repo.load().clip(cid)
        changed = {k: v for k, v in changes.items() if getattr(current, k) != v}
        if not changed:
            return back(dest, "nothing changed")
        repo.update_clip(cid, **changed)
        with conn() as c:
            queue.log_event(c, pid, "clip_edited", {"fields": sorted(changed)}, clip_id=cid, actor="operator")
        return back(dest, "saved; re-render to see the change")

    # ----- consent and paid transcription -------------------------------------------------------
    @app.get("/p/{pid}/consent", response_class=HTMLResponse)
    def consent_page(request: Request, pid: str, msg: str | None = None):
        with conn() as c:
            row = project_row(c, pid)
        return page(request, "consent.html", row=row, project=repo_for(row).load(), msg=msg)

    @app.post("/p/{pid}/consent")
    async def consent_post(request: Request, pid: str):
        form = await request.form()
        granted_by = str(form.get("granted_by", "")).strip()[:200]
        statement = str(form.get("statement", "")).strip()[:2000]
        recorded_by = str(form.get("recorded_by", "")).strip()[:100]
        if not granted_by or not statement or not recorded_by or form.get("confirm") != "yes":
            return back(f"/p/{pid}/consent", "fill in who agreed, how/when, your name, and tick the box")
        with conn() as c:
            row = project_row(c, pid)
            repo = repo_for(row)
            rec = ConsentRecord(id=new_id("cns"), provider="harmar", scope="selected_windows", granted_by=granted_by,
                                recorded_by=recorded_by, statement=statement,
                                source_ref=repo.load().source.get("video_id", ""))
            repo.add_consent(rec)
            queue.log_event(c, pid, "consent_recorded", {"consent_id": rec.id, "provider": "harmar"}, actor=recorded_by)
        return back(f"/p/{pid}/transcribe", "consent recorded")

    def transcription_view(repo: ProjectRepo, settings: Settings):
        from ..transcription import service
        project = repo.load()
        ids = selected_ids(project)
        plans, error = [], None
        try:
            plans = service.plan(repo, ids, settings=settings) if ids else []
        except PipelineError as exc:
            error = exc
        new_seconds = sum(p.seconds for p in plans if p.action == "new")
        attempts = [a for cl in project.clips for a in list_attempts(repo, cl.id)]
        lim = settings.limits
        budget_error = None
        if new_seconds:
            try:
                budget.check_budgets(settings, new_seconds=new_seconds, project_attempts=attempts)
            except PipelineError as exc:
                budget_error = exc.message
        return {
            "project": project, "plans": plans, "error": error, "new_seconds": new_seconds,
            "project_used": budget.project_used(attempts), "day_used": budget.day_used(settings), "limits": lim,
            "consent": project.active_consent("harmar"), "key_present": bool(os.environ.get("HARMAR_API_KEY")),
            "needs_reconcile": [p for p in plans if p.action in ("reconcile", "in_progress")],
            "work": [p for p in plans if p.action in ("new", "resume")], "budget_error": budget_error,
            "unknowns": budget.unreconciled_unknowns(settings),
        }

    @app.get("/p/{pid}/transcribe", response_class=HTMLResponse)
    def transcribe_page(request: Request, pid: str, msg: str | None = None):
        with conn() as c:
            row = project_row(c, pid)
        settings = load_settings()
        view = transcription_view(repo_for(row), settings)
        d = Path(row["dir"])
        cli_ref = str(d.relative_to(repo_root)) if d.is_relative_to(repo_root) else f"$HAYCLIPS_PROJECTS_ROOT/{d.name}"
        return page(request, "transcribe.html", row=row, msg=msg, cli_ref=cli_ref, **view)

    @app.post("/p/{pid}/transcribe")
    async def transcribe_confirm(request: Request, pid: str):
        form = await request.form()
        with conn() as c:
            row = project_row(c, pid)
        repo = repo_for(row)
        settings = load_settings()
        view = transcription_view(repo, settings)
        who = str(form.get("confirmed_by", "")).strip()
        refusal = None
        if not settings.allow_paid_harmar:
            refusal = "Paid transcription is disabled in this environment"
        elif view["error"] is not None:
            refusal = view["error"].message
        elif view["consent"] is None:
            refusal = "record the creator's consent first"
        elif view["needs_reconcile"] or view["unknowns"]:
            refusal = "a previous submission needs reconciliation first"
        elif not view["work"]:
            refusal = "nothing to transcribe: every selected clip already has a transcript"
        elif view["budget_error"]:
            refusal = view["budget_error"]
        elif not view["key_present"]:
            refusal = "no Harmar key in the server environment"
        elif not who or form.get("i_understand") != "yes":
            refusal = "type your name and tick the cost box to confirm"
        if refusal:
            return back(f"/p/{pid}/transcribe", refusal)
        ids = sorted(p.clip_id for p in view["work"])
        with conn() as c:
            job = enqueue(c, row, "transcribe", {"clip_ids": ids, "confirmed_by": who},
                          f"{pid}:transcribe:{','.join(ids)}", actor=who)
            queue.log_event(c, pid, "paid_confirmed", {"job_id": job["id"], "clip_ids": ids,
                                                         "new_seconds": view["new_seconds"]}, actor=who)
        return back(f"/p/{pid}", f"paid transcription queued (job {job['id']})")

    # ----- review ---------------------------------------------------------------------------------
    def srt_lines(path: Path) -> list[tuple[str, str]]:
        if not path.exists():
            return []
        out = []
        for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
            parts = block.split("\n", 2)
            if len(parts) == 3:
                out.append((parts[1][3:8], parts[2]))
        return out

    @app.get("/p/{pid}/review", response_class=HTMLResponse)
    def review_page(request: Request, pid: str, msg: str | None = None):
        with conn() as c:
            row = project_row(c, pid)
            decisions = c.execute("SELECT * FROM review_decisions WHERE project_id = %s ORDER BY id DESC", (pid,)).fetchall()
        repo = repo_for(row)
        project = repo.load()
        cards = []
        for cl in project.ordered():
            info = jsonio.read_json(repo.render_dir(cl.id) / "render.json", default=None)
            w = repo.load_window(cl.id)
            cards.append({"clip": cl, "info": info, "window": w, "transcribed": bool(transcribed(repo, [cl.id])),
                          "transcript": srt_lines(repo.render_dir(cl.id) / "captions.srt"),
                          "decisions": [d for d in decisions if d["clip_id"] == cl.id],
                          "cut": (info or {}).get("cut")})
        return page(request, "review.html", row=row, project=project, cards=cards, msg=msg)

    @app.post("/p/{pid}/clips/{cid}/review")
    async def review_post(request: Request, pid: str, cid: str):
        form = await request.form()
        with conn() as c:
            row = project_row(c, pid)
            clip_of(repo_for(row).load(), cid)
            would = form.get("would_post")
            style = form.get("style") or None
            minutes = form.get("minutes_to_fix")
            if would not in ("yes", "no", "maybe") or (style and style not in ("A", "B", "C")):
                return back(f"/p/{pid}/review", "choose yes / no / maybe")
            try:
                minutes = int(minutes) if minutes not in (None, "") else None
                if minutes is not None and not 0 <= minutes <= 10000:
                    raise ValueError
            except ValueError:
                return back(f"/p/{pid}/review", "minutes must be a whole number")
            m = effort_metrics(c, row, cid)
            with c.transaction():
                c.execute("""INSERT INTO review_decisions (project_id, clip_id, style, would_post, minutes_to_fix, notes, reviewer,
                                    final_title, final_hook, final_trim, warning_count, title_edited, hook_edited,
                                    trim_edited, framing_adjusted, transcript_edited)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)""",
                          (pid, cid, style, would, minutes, str(form.get("notes", ""))[:2000],
                           str(form.get("reviewer", ""))[:100] or None, m["final_title"], m["final_hook"],
                           json.dumps(m["final_trim"]) if m["final_trim"] else None, m["warning_count"],
                           m["title_edited"], m["hook_edited"], m["trim_edited"], m["framing_adjusted"]))
        return back(f"/p/{pid}/review", "review saved")

    def effort_metrics(c, row, cid: str) -> dict:
        """Facts about one clip at decision time, from real operator actions only."""
        from ..media.reframe import framing_adjusted
        repo = repo_for(row)
        clip = repo.load().clip(cid)
        info = jsonio.read_json(repo.render_dir(cid) / "render.json", default=None) or {}
        edited = set()
        for e in c.execute("SELECT detail FROM events WHERE project_id = %s AND clip_id = %s AND kind = 'clip_edited'",
                           (row["id"], cid)).fetchall():
            edited |= set(e["detail"].get("fields", []))
        plan = jsonio.read_json(repo.clip_dir(cid) / "crop.json", default=None)
        return {"final_title": clip.title, "final_hook": clip.hook,
                "final_trim": ({"caption_start": clip.trim.caption_start, "caption_end": clip.trim.caption_end}
                               if clip.trim else None),
                "warning_count": len(info.get("checks", [])) if info else None,
                "title_edited": "title" in edited, "hook_edited": "hook" in edited, "trim_edited": "trim" in edited,
                "framing_adjusted": framing_adjusted(plan)}

    @app.get("/p/{pid}/summary", response_class=HTMLResponse)
    def summary_page(request: Request, pid: str):
        import statistics
        with conn() as c:
            row = project_row(c, pid)
            latest = c.execute("""SELECT DISTINCT ON (clip_id) * FROM review_decisions WHERE project_id = %s
                                  ORDER BY clip_id, id DESC""", (pid,)).fetchall()
        project = repo_for(row).load()
        minutes = [d["minutes_to_fix"] for d in latest if d["minutes_to_fix"] is not None]
        styles: dict[str, int] = {}
        for d in latest:
            if d["style"]:
                styles[d["style"]] = styles.get(d["style"], 0) + 1
        titles = {cl.id: cl.title for cl in project.clips}
        stats = {"reviewed": len(latest), "would_post": sum(d["would_post"] == "yes" for d in latest),
                 "maybe": sum(d["would_post"] == "maybe" for d in latest),
                 "median_minutes": statistics.median(minutes) if minutes else None,
                 "styles": dict(sorted(styles.items())),
                 "trim_edits": sum(bool(d["trim_edited"]) for d in latest),
                 "hook_edits": sum(bool(d["hook_edited"]) for d in latest),
                 "framing_edits": sum(bool(d["framing_adjusted"]) for d in latest)}
        return page(request, "summary.html", row=row, project=project, stats=stats, latest=latest, titles=titles)

    # ----- media ------------------------------------------------------------------------------------
    @app.get("/p/{pid}/media/{cid}/{name}")
    def media(pid: str, cid: str, name: str, download: int = 0):
        if name not in MEDIA or not CLIP_ID.match(cid):
            raise NotFound()
        with conn() as c:
            row = project_row(c, pid)
        repo = repo_for(row)
        clip_of(repo.load(), cid)
        root = repo.root.resolve()
        path = (repo.clip_dir(cid) / MEDIA[name]).resolve()
        if not path.is_file() or not path.is_relative_to(root):
            raise NotFound()
        media_type = "application/x-subrip" if name.endswith(".srt") else "video/mp4"
        headers = {"Cache-Control": "private, max-age=0"}
        filename = f"{row['name'][:40]}-{cid}-{name}" if download else None
        return FileResponse(path, media_type=media_type, headers=headers,
                            filename=_slug(filename) + Path(name).suffix if filename else None,
                            content_disposition_type="attachment" if download else "inline")

    return app
