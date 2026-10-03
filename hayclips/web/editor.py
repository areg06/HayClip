"""Clip editor (captions / trim / frame), export view, calendar and Brand Kit routes.

Cost rule: nothing here transcribes. Live feedback (caption position, style, crop, trim) happens in the
browser; saving writes small JSON settings; only the explicit "Render" button queues a (local) render.
"""
from __future__ import annotations

import calendar as cal
import datetime as dt
import json
import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse

from .. import jsonio
from ..captions import ass as A
from ..captions.edits import apply_edits, load_edits, save_edit
from ..errors import PipelineError, ValidationError
from ..look import DEFAULT_HOOK, PRESET_LABELS, PRESETS, normalise_hook, normalise_look
from ..media.probe import probe
from ..platforms import SAFE_ZONES, safe_bottom
from ..transcription.store import completed_transcript

PLATFORMS = ("tiktok", "reels", "shorts")
STATUSES = ("Draft", "Editing", "Ready", "Scheduled")
LOGO_TYPES = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg"}


def register(app, h) -> None:  # noqa: C901 - one place for the editor's routes
    def clip_ctx(c, pid: str, cid: str):
        row = h.project_row(c, pid)
        repo = h.repo_for(row)
        project = repo.load()
        clip = h.clip_of(project, cid)
        return row, repo, project, clip

    def editor_data(repo, clip) -> dict:
        from ..render import _clip_words, _cut
        tr = completed_transcript(repo, clip.id)
        if tr is None:
            raise PipelineError("add captions to this clip first")
        window = repo.load_window(clip.id)
        info = jsonio.read_json(repo.render_dir(clip.id) / "render.json", default=None) or {}
        edits = load_edits(repo.clip_dir(clip.id))
        raw = apply_edits(tr.raw, edits)
        src = window.wide or window.preview
        full = float(src.duration or probe(repo.media_path(clip.id, src)).duration)
        cs, ce, mode, _ = _cut(clip, window, raw, full)
        words, _segs, _ = _clip_words(raw, cs, ce)
        segments = []
        for i, sg in enumerate(raw.get("segments", [])):
            a, b = float(sg["start"]), float(sg["end"])
            if b > cs and a < ce and str(sg.get("text", "")).strip():
                segments.append({"index": i, "start": round(max(a - cs, 0), 2), "text": A.clean(sg["text"]),
                                 "edited": str(i) in edits.get("segments", {})})
        plan = jsonio.read_json(repo.clip_dir(clip.id) / "crop.json", default=None)
        shots = []
        if plan and plan.get("mode") == "crop":
            for k, sh in enumerate(plan["shots"]):
                nxt = plan["shots"][k + 1]["start"] if k + 1 < len(plan["shots"]) else None
                if (nxt is None or nxt > cs) and float(sh["start"]) < ce:
                    shots.append({"index": k, "start": float(sh["start"]), "x": int(sh["x"]),
                                  "auto_x": int((plan.get("auto_shots") or plan["shots"])[k]["x"])})
        return {"cut": {"start": cs, "end": ce, "mode": mode, "window": full},
                "words": [[round(w[0], 3), round(w[1], 3), w[2]] for w in words],
                "segments": segments, "look": normalise_look(clip.look), "hook_look": normalise_hook(clip.hook_look),
                "hook": clip.hook, "has_look": clip.look is not None,
                "crop": ({"width": plan["width"], "height": plan["height"], "crop_w": plan["crop_w"], "shots": shots}
                         if shots else None),
                "zones": {k: v for k, v in SAFE_ZONES.items()},
                "safe_bottom": {k: safe_bottom(k) for k in SAFE_ZONES},
                "rendered": sorted((info.get("outputs") or {})), "render": info, "edits": bool(edits.get("segments"))}

    # ----- editor -----------------------------------------------------------------------------------
    @app.get("/p/{pid}/clips/{cid}/editor", response_class=HTMLResponse)
    def editor_page(request: Request, pid: str, cid: str, mode: str = "captions", msg: str | None = None):
        with h.conn() as c:
            row, repo, project, clip = clip_ctx(c, pid, cid)
            busy = [j for j in h.queue.for_project(c, pid, 10)
                    if j["type"] == "render" and j["state"] in ("QUEUED", "RUNNING", "RETRY_WAIT")]
        try:
            data = editor_data(repo, clip)
        except PipelineError as exc:
            return h.back(f"/p/{pid}/captions", exc.message)
        return h.page(request, "editor.html", row=row, project=project, clip=clip, data=data,
                      data_json=json.dumps(data, ensure_ascii=False, default=str).replace("<", "\\u003c")
                      .replace(">", "\\u003e").replace("&", "\\u0026"), mode=mode if mode in ("captions", "trim", "frame") else "captions",
                      presets=PRESET_LABELS, msg=msg, rendering=bool(busy))

    def editor_back(pid, cid, mode, msg):
        from urllib.parse import quote
        from fastapi.responses import RedirectResponse
        return RedirectResponse(f"/p/{pid}/clips/{cid}/editor?mode={mode}&msg={quote(msg)}", status_code=303)

    @app.post("/p/{pid}/clips/{cid}/look")
    async def save_look(request: Request, pid: str, cid: str):
        form = await request.form()
        with h.conn() as c:
            row, repo, project, clip = clip_ctx(c, pid, cid)
            try:
                look = normalise_look({**normalise_look(clip.look), **{k: form.get(k) for k in
                                       ("preset", "size", "x", "y", "color", "highlight", "words_per_line") if form.get(k) not in (None,)},
                                       "background": form.get("background") == "on"})
            except ValidationError as exc:
                return editor_back(pid, cid, "captions", exc.message)
            repo.update_clip(cid, look=look)
            h.queue.log_event(c, pid, "look_edited", {"preset": look["preset"]}, clip_id=cid, actor="operator")
        return editor_back(pid, cid, "captions", "caption look saved · Render to apply it")

    @app.post("/p/{pid}/clips/{cid}/hook")
    async def save_hook(request: Request, pid: str, cid: str):
        form = await request.form()
        with h.conn() as c:
            row, repo, project, clip = clip_ctx(c, pid, cid)
            try:
                text = A.validate_hook(str(form.get("hook", "")))
                hook_look = normalise_hook({"show": form.get("show") == "on", "x": form.get("x", 0.5),
                                            "y": form.get("y", DEFAULT_HOOK["y"]), "duration": form.get("duration", 3)})
            except ValidationError as exc:
                return editor_back(pid, cid, "captions", exc.message)
            changed = ["hook"] if text != clip.hook else []
            repo.update_clip(cid, hook=text, hook_look=hook_look)
            if changed:
                h.queue.log_event(c, pid, "clip_edited", {"fields": changed}, clip_id=cid, actor="operator")
        return editor_back(pid, cid, "captions", "hook saved · Render to apply it")

    @app.post("/p/{pid}/clips/{cid}/transcript")
    async def save_transcript(request: Request, pid: str, cid: str):
        form = await request.form()
        with h.conn() as c:
            row, repo, project, clip = clip_ctx(c, pid, cid)
            tr = completed_transcript(repo, cid)
            if tr is None:
                return editor_back(pid, cid, "captions", "this clip has no transcript yet")
            try:
                save_edit(repo.clip_dir(cid), tr.raw, int(form.get("index", -1)), str(form.get("text", "")), "operator")
            except (ValidationError, ValueError) as exc:
                return editor_back(pid, cid, "captions", getattr(exc, "message", "that line could not be saved"))
            h.queue.log_event(c, pid, "transcript_edited", {"segment": int(form.get("index"))}, clip_id=cid, actor="operator")
        return editor_back(pid, cid, "captions", "caption text saved · Render to apply it (no new transcription)")

    @app.post("/p/{pid}/clips/{cid}/crop")
    async def save_crop(request: Request, pid: str, cid: str):
        from ..media.reframe import reset_crop, set_crop_x
        form = await request.form()
        with h.conn() as c:
            row, repo, project, clip = clip_ctx(c, pid, cid)
            path = repo.clip_dir(cid) / "crop.json"
            if not path.exists():
                return editor_back(pid, cid, "frame", "framing is available after the first render")
            try:
                if form.get("reset") == "1":
                    reset_crop(path)
                    note = "framing reset to automatic"
                else:
                    xs = {int(k[2:]): v for k, v in form.items() if re.fullmatch(r"x_\d+", k)}
                    set_crop_x(path, xs)
                    note = "framing saved · Render to apply it"
            except (ValidationError, ValueError) as exc:
                return editor_back(pid, cid, "frame", getattr(exc, "message", "framing values were not valid"))
            h.queue.log_event(c, pid, "framing_edited", {"reset": form.get("reset") == "1"}, clip_id=cid, actor="operator")
        return editor_back(pid, cid, "frame", note)

    @app.post("/p/{pid}/clips/{cid}/render")
    async def render_clip(request: Request, pid: str, cid: str):
        import hashlib
        with h.conn() as c:
            row, repo, project, clip = clip_ctx(c, pid, cid)
            if not h.transcribed(repo, [cid]):
                return editor_back(pid, cid, "captions", "add captions to this clip first")
            look = normalise_look(clip.look)
            if clip.look is None:
                repo.update_clip(cid, look=look)
                project = repo.load()
            plan = jsonio.read_json(repo.clip_dir(cid) / "crop.json", default={})
            edits = load_edits(repo.clip_dir(cid))
            key = hashlib.sha256(json.dumps([h.edit_revision(project), plan.get("shots"), edits.get("segments")],
                                            sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
            h.enqueue(c, row, "render", {"clip_ids": [cid], "styles": [PRESETS[look["preset"]]], "use_look": True},
                      f"{pid}:render:{cid}:{key}")
        return editor_back(pid, cid, "captions", "rendering… this takes a few seconds per clip")

    # ----- export -----------------------------------------------------------------------------------
    @app.get("/p/{pid}/clips/{cid}/export", response_class=HTMLResponse)
    def export_page(request: Request, pid: str, cid: str, msg: str | None = None):
        with h.conn() as c:
            row, repo, project, clip = clip_ctx(c, pid, cid)
            items = c.execute("SELECT * FROM calendar_items WHERE project_id = %s AND clip_id = %s ORDER BY plan_date",
                              (pid, cid)).fetchall()
        info = jsonio.read_json(repo.render_dir(cid) / "render.json", default=None)
        style = None
        if info and info.get("outputs"):
            look = info.get("look") or {}
            pref = PRESETS.get(look.get("preset", ""), None)
            style = pref if pref in info["outputs"] else sorted(info["outputs"])[0]
        label = next((PRESET_LABELS[k] for k, v in PRESETS.items() if v == style), style)
        return h.page(request, "export.html", row=row, project=project, clip=clip, info=info, style=style,
                      style_label=label, items=items, zones_json=json.dumps(SAFE_ZONES), msg=msg,
                      today=dt.date.today().isoformat())

    # ----- calendar (planning only) -----------------------------------------------------------------
    @app.get("/calendar", response_class=HTMLResponse)
    def calendar_page(request: Request, month: str | None = None, msg: str | None = None):
        today = dt.date.today()
        try:
            y, m = (int(x) for x in (month or today.strftime("%Y-%m")).split("-"))
            first = dt.date(y, m, 1)
        except ValueError:
            first = today.replace(day=1)
        last = first.replace(day=cal.monthrange(first.year, first.month)[1])
        with h.conn() as c:
            rows = c.execute("""SELECT ci.*, p.name AS project_name, p.dir AS project_dir FROM calendar_items ci
                                JOIN projects p ON p.id = ci.project_id
                                WHERE p.removed_at IS NULL ORDER BY plan_date, plan_time NULLS LAST, ci.id""").fetchall()
        titles = {}
        for r in rows:
            try:
                titles[(r["project_id"], r["clip_id"])] = h.ProjectRepo(Path(r["project_dir"])).load().clip(r["clip_id"]).title
            except (PipelineError, KeyError):
                titles[(r["project_id"], r["clip_id"])] = r["clip_id"]
        for r in rows:
            r["title"] = titles[(r["project_id"], r["clip_id"])] or "Untitled clip"
        weeks = cal.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month)
        by_day: dict = {}
        for r in rows:
            by_day.setdefault(r["plan_date"], []).append(r)
        prev_m = (first - dt.timedelta(days=1)).strftime("%Y-%m")
        next_m = (last + dt.timedelta(days=1)).strftime("%Y-%m")
        upcoming = [r for r in rows if r["plan_date"] >= today][:12]
        return h.page(request, "calendar.html", nav_active="calendar", weeks=weeks, by_day=by_day, first=first,
                      today=today, prev_m=prev_m, next_m=next_m, upcoming=upcoming, msg=msg, statuses=STATUSES)

    def calendar_fields(form) -> dict:
        try:
            d = dt.date.fromisoformat(str(form.get("plan_date", "")))
        except ValueError:
            raise ValidationError("choose a date") from None
        t = str(form.get("plan_time", "")).strip()
        if t:
            try:
                t = dt.time.fromisoformat(t)
            except ValueError:
                raise ValidationError("the time was not understood") from None
        platforms = [p for p in form.getlist("platforms") if p in PLATFORMS]
        status = str(form.get("status", "Draft"))
        if status not in STATUSES:
            raise ValidationError("unknown status")
        return {"plan_date": d, "plan_time": t or None, "platforms": platforms,
                "note": str(form.get("note", "")).strip()[:500] or None, "status": status}

    @app.post("/p/{pid}/clips/{cid}/calendar")
    async def add_to_calendar(request: Request, pid: str, cid: str):
        form = await request.form()
        with h.conn() as c:
            row, repo, project, clip = clip_ctx(c, pid, cid)
            try:
                f = calendar_fields(form)
            except ValidationError as exc:
                return h.back(f"/p/{pid}/clips/{cid}/export", exc.message)
            with c.transaction():
                c.execute("""INSERT INTO calendar_items (project_id, clip_id, plan_date, plan_time, platforms, note, status)
                             VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                          (pid, cid, f["plan_date"], f["plan_time"], f["platforms"], f["note"], f["status"]))
            h.queue.log_event(c, pid, "calendar_added", {"date": f["plan_date"].isoformat(), "status": f["status"]},
                              clip_id=cid, actor="operator")
        return h.back(f"/calendar?month={f['plan_date'].strftime('%Y-%m')}", "added to the calendar (planning only; nothing is posted)")

    @app.post("/calendar/{item}")
    async def update_calendar(request: Request, item: int):
        form = await request.form()
        with h.conn() as c:
            row = c.execute("SELECT * FROM calendar_items WHERE id = %s", (item,)).fetchone()
            if not row:
                raise h.NotFound()
            if form.get("delete") == "1":
                with c.transaction():
                    c.execute("DELETE FROM calendar_items WHERE id = %s", (item,))
                h.queue.log_event(c, row["project_id"], "calendar_removed", {"item": item}, clip_id=row["clip_id"], actor="operator")
                return h.back("/calendar", "removed from the calendar")
            status = str(form.get("status", ""))
            if status not in STATUSES:
                return h.back("/calendar", "unknown status")
            with c.transaction():
                c.execute("UPDATE calendar_items SET status = %s, updated_at = now() WHERE id = %s", (status, item))
        return h.back(f"/calendar?month={row['plan_date'].strftime('%Y-%m')}", f"marked {status}")

    # ----- Brand Kit ----------------------------------------------------------------------------------
    @app.get("/brand", response_class=HTMLResponse)
    def brand_page(request: Request, msg: str | None = None):
        from ..brandkit import get_brand
        from ..look import FONTS
        with h.conn() as c:
            brand = get_brand(c)
        return h.page(request, "brand.html", nav_active="brand", brand=brand, presets=PRESET_LABELS, fonts=FONTS, msg=msg)

    @app.post("/brand")
    async def brand_save(request: Request):
        from ..brandkit import get_brand, save_brand
        form = await request.form()
        pos = {"bottom": 0.7266, "middle": 0.6, "low": 0.80}.get(str(form.get("position")), None)
        with h.conn() as c:
            cur = get_brand(c)
            look = {**cur["look"], "preset": form.get("preset", cur["look"]["preset"]), "font": form.get("font", cur["look"]["font"]),
                    "color": form.get("color", cur["look"]["color"]), "highlight": form.get("highlight", cur["look"]["highlight"])}
            if pos is not None:
                look["y"] = pos
            hook = {**cur["hook"], "show": form.get("hook_show") == "on",
                    "y": {"top": 0.156, "upper": 0.25}.get(str(form.get("hook_position")), cur["hook"]["y"])}
            try:
                save_brand(c, look=look, hook=hook)
            except ValidationError as exc:
                return h.back("/brand", exc.message)
            h.queue.log_event(c, None, "brand_kit_saved", {"preset": look["preset"]}, actor="operator")
        return h.back("/brand", "Brand Kit saved · applies to clips you choose from now on")

    @app.post("/brand/logo")
    async def brand_logo(request: Request):
        from fastapi.responses import JSONResponse
        from ..brandkit import get_brand, save_brand
        data = b""
        async for chunk in request.stream():
            data += chunk
            if len(data) > 2_000_000:
                return JSONResponse({"error": "the logo must be under 2 MB"}, status_code=413)
        ext = next((e for sig, e in LOGO_TYPES.items() if data.startswith(sig)), None)
        if ext is None:
            return JSONResponse({"error": "upload a PNG or JPEG image"}, status_code=400)
        d = h.projects_root / ".brand"
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("logo.*"):
            old.unlink()
        (d / f"logo{ext}").write_bytes(data)
        with h.conn() as c:
            cur = get_brand(c)
            save_brand(c, look=cur["look"], hook=cur["hook"], logo_file=f"logo{ext}")
        return JSONResponse({"ok": True})

    @app.get("/brand/logo")
    def brand_logo_file():
        from ..brandkit import get_brand
        with h.conn() as c:
            name = get_brand(c)["logo_file"]
        path = (h.projects_root / ".brand" / name) if name else None
        if not path or not path.is_file() or name not in ("logo.png", "logo.jpg"):
            raise h.NotFound()
        return FileResponse(path, media_type="image/png" if name.endswith(".png") else "image/jpeg")
