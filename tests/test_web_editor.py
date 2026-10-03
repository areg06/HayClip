"""Phase 1d editor, export, calendar and Brand Kit routes (server side; the browser test covers the UI)."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from fixtures import synth
from hayclips import db, jsonio
from hayclips.config import load_settings
from hayclips.jobs import queue as q
from hayclips.render import render_clip
from hayclips.web.app import create_app

BASE = "http://127.0.0.1:8765"
ORIGIN = {"Origin": BASE}


@pytest.fixture
def env(pg_dsn, tmp_path):
    app = create_app(dsn=pg_dsn, projects_root=tmp_path / "projects", repo_root=tmp_path / "repo", port=8765)
    (tmp_path / "repo").mkdir()
    client = TestClient(app, base_url=BASE)
    repo, clip = synth.make_project(tmp_path / "projects" / "syn", seconds=6, hook="Փորձ")
    with db.connect(pg_dsn) as c:
        q.register_project(c, project_id="prj_aaaaaaaaaa", name="syn", dir=str(repo.root))
    res = render_clip(repo, repo.load().clip(clip.id), styles=["A"], caption_bottom=930, hook_enabled=True,
                      settings=load_settings())
    assert res.status == "rendered", res.message
    return client, repo, clip, pg_dsn


def token(client, path="/dashboard"):
    return re.search(r'name="csrf" value="([0-9a-f]{64})"', client.get(path).text).group(1)


def post(client, path, data):
    return client.post(path, data={"csrf": token(client), **data}, headers=ORIGIN, follow_redirects=False)


def editor(client, clip, mode="captions"):
    return client.get(f"/p/prj_aaaaaaaaaa/clips/{clip.id}/editor?mode={mode}")


def test_editor_page_has_live_preview_data_and_modes(env):
    client, repo, clip, dsn = env
    r = editor(client, clip)
    assert r.status_code == 200
    data = json.loads(re.search(r'<script type="application/json" id="editor-data">(.*?)</script>', r.text, re.S).group(1))
    assert set(data["zones"]) == {"tiktok", "reels", "shorts"} and data["words"] and data["crop"]
    assert data["look"]["preset"] == "active" and 0 < data["look"]["y"] < 1
    for label in ("Captions", "Trim", "Frame", "Render", "Clean", "Active word", "Punch", "Karaoke"):
        assert label in r.text
    assert "</script><" not in data.get("hook", "")


def test_look_hook_text_and_crop_edits_save_without_rendering(env):
    client, repo, clip, dsn = env
    base = f"/p/prj_aaaaaaaaaa/clips/{clip.id}"
    assert "saved" in post(client, f"{base}/look", {"preset": "clean", "size": "1.2", "x": "0.4", "y": "0.62",
                                                     "color": "#ffffff", "highlight": "#ffd700"}).headers["location"]
    look = repo.load().clip(clip.id).look
    assert (look["preset"], look["size"], look["x"], look["y"]) == ("clean", 1.2, 0.4, 0.62)
    assert "45" in post(client, f"{base}/hook", {"hook": "Ա" * 46, "show": "on"}).headers["location"]
    post(client, f"{base}/hook", {"hook": "Նոր հուք", "show": "on", "x": "0.5", "y": "0.3", "duration": "4"})
    c2 = repo.load().clip(clip.id)
    assert c2.hook == "Նոր հուք" and c2.hook_look["duration"] == 4 and c2.hook_look["y"] == 0.3
    post(client, f"{base}/transcript", {"index": "0", "text": "Ուղղված բառեր"})
    assert jsonio.read_json(repo.clip_dir(clip.id) / "transcript_edits.json")["segments"] == {"0": "Ուղղված բառեր"}
    plan = jsonio.read_json(repo.clip_dir(clip.id) / "crop.json")
    post(client, f"{base}/crop", {"x_0": str(plan["width"] - plan["crop_w"])})
    assert jsonio.read_json(repo.clip_dir(clip.id) / "crop.json")["shots"][0]["x"] == plan["width"] - plan["crop_w"]
    post(client, f"{base}/crop", {"reset": "1"})
    assert jsonio.read_json(repo.clip_dir(clip.id) / "crop.json")["shots"][0]["x"] == plan["auto_shots"][0]["x"]
    with db.connect(dsn) as c:
        assert q.for_project(c, "prj_aaaaaaaaaa") == []                       # nothing was queued by editing
    r = post(client, f"{base}/render", {})
    with db.connect(dsn) as c:
        (j,) = q.for_project(c, "prj_aaaaaaaaaa")
    assert j["type"] == "render" and j["payload"]["use_look"] is True and j["payload"]["styles"] == ["L"]
    assert j["pool"] == "cpu"                                                 # a local render, never paid


def test_export_calendar_and_status(env):
    client, repo, clip, dsn = env
    page = client.get(f"/p/prj_aaaaaaaaaa/clips/{clip.id}/export").text
    assert "Download MP4" in page and "Download SRT" in page and "9:16" in page and "Add to calendar" in page
    r = post(client, f"/p/prj_aaaaaaaaaa/clips/{clip.id}/calendar",
             {"plan_date": "2026-10-20", "plan_time": "18:30", "platforms": ["tiktok", "reels"], "status": "Ready", "note": "first"})
    assert r.headers["location"].startswith("/calendar?month=2026-10")
    cal = client.get("/calendar?month=2026-10").text
    assert "synthetic" in cal and "18:30" in cal
    with db.connect(dsn) as c:
        item = c.execute("SELECT * FROM calendar_items").fetchone()
    assert item["platforms"] == ["tiktok", "reels"] and item["status"] == "Ready"
    post(client, f"/calendar/{item['id']}", {"status": "Scheduled"})
    with db.connect(dsn) as c:
        assert c.execute("SELECT status FROM calendar_items").fetchone()["status"] == "Scheduled"
    assert "Published" not in client.get("/calendar?month=2026-10").text


def test_brand_kit_defaults_apply_to_newly_chosen_clips(env):
    client, repo, clip, dsn = env
    post(client, "/brand", {"preset": "karaoke", "font": "Noto Sans Armenian", "color": "#ffffff", "highlight": "#00ff00",
                            "position": "middle", "hook_show": "on", "hook_position": "upper"})
    from hayclips.models import Candidate, candidate_id
    repo.save_candidates([Candidate(id=candidate_id(300, 340), start=300, end=340, score=1, text="x")], {})
    post(client, "/p/prj_aaaaaaaaaa/choose", {"choose": [candidate_id(300, 340)], "next": "save"})
    new = [c for c in repo.load().clips if c.candidate_id == candidate_id(300, 340)][0]
    assert new.look["preset"] == "karaoke" and new.look["highlight"] == "#00FF00" and new.look["y"] == 0.6
    assert new.hook_look["y"] == 0.25
    assert repo.load().clip(clip.id).look is None                             # existing clips untouched


def test_logo_upload_checks_type_and_size(env):
    client, repo, clip, dsn = env
    t = token(client)
    hdr = {**ORIGIN, "X-CSRF-Token": t, "Content-Type": "application/octet-stream"}
    assert client.post("/brand/logo", content=b"<svg onload=alert(1)>", headers=hdr).status_code == 400
    assert client.post("/brand/logo", content=b"\x89PNG" + b"0" * 2_100_000, headers=hdr).status_code == 413
    assert client.post("/brand/logo", content=b"\x89PNG\r\n\x1a\n" + b"0" * 100, headers=hdr).status_code == 200
    assert client.get("/brand/logo").status_code == 200
    assert client.post("/brand/logo", content=b"\x89PNG", headers={**ORIGIN, "Content-Type": "application/octet-stream"}).status_code == 403
