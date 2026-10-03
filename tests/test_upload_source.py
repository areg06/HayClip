"""Phase 1d: local uploads as a source. Streamed, size-capped, validated, never sent whole to a paid service."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fixtures import synth
from hayclips import db
from hayclips.jobs import queue as q
from hayclips.jobs.worker import run_worker
from hayclips.project import ProjectRepo
from hayclips.web.app import create_app

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def env(pg_dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("HAYCLIPS_DISCOVERY_ENGINE", "fake")
    app = create_app(dsn=pg_dsn, projects_root=tmp_path / "projects", repo_root=tmp_path / "repo", port=8765)
    (tmp_path / "repo").mkdir()
    client = TestClient(app, base_url=BASE)
    client.get("/dashboard")
    import re
    token = re.search(r'name="csrf" value="([0-9a-f]{64})"', client.get("/dashboard").text).group(1)
    return client, token, tmp_path, pg_dsn


def upload(client, token, data: bytes, filename="talk.mp4", name="Uploaded talk", origin=BASE, csrf=True):
    from urllib.parse import quote
    headers = {"Origin": origin, "X-Filename": quote(filename), "X-Project-Name": quote(name),
               "Content-Type": "application/octet-stream"}
    if csrf:
        headers["X-CSRF-Token"] = token
    return client.post("/new/upload", content=data, headers=headers)


def test_valid_upload_becomes_a_project_and_starts_free_discovery(env, tmp_path):
    client, token, root, dsn = env
    vid = synth.video(tmp_path / "in.mp4", seconds=8)
    r = upload(client, token, vid.read_bytes(), filename="../../evil name.mp4")
    assert r.status_code == 200, r.text
    pid = r.json()["redirect"].split("/")[2]
    with db.connect(dsn) as c:
        d = Path(q.project(c, pid)["dir"])
        (job,) = q.for_project(c, pid)
    assert (d / "source" / "original.mp4").read_bytes() == vid.read_bytes()     # stored under a fixed name
    src = ProjectRepo(d).load().source
    assert src["kind"] == "upload" and src["original_name"] == "evil name.mp4" and abs(src["duration"] - 8) < 0.3
    assert job["type"] == "discover_transcript" and job["payload"] == {"then": ["generate_candidates"]}
    assert not any((root / "projects" / ".uploads").iterdir())


@pytest.mark.parametrize("filename,data,msg", [
    ("talk.avi", b"x" * 100, "MP4, MOV or M4V"),
    ("talk.mp4", b"definitely not a video" * 50, "could not be read as a video"),
])
def test_bad_uploads_are_refused_and_leave_nothing(env, filename, data, msg):
    client, token, root, dsn = env
    r = upload(client, token, data, filename=filename)
    assert r.status_code == 400 and msg in r.json()["error"]
    uploads = root / "projects" / ".uploads"
    assert not uploads.exists() or not any(uploads.iterdir())
    with db.connect(dsn) as c:
        assert c.execute("SELECT count(*) AS n FROM projects").fetchone()["n"] == 0


def test_upload_size_cap(env, monkeypatch, tmp_path):
    client, token, root, dsn = env
    monkeypatch.setenv("HAYCLIPS_MAX_SOURCE_BYTES", "1000")
    r = upload(client, token, synth.video(tmp_path / "in.mp4", seconds=6).read_bytes())
    assert r.status_code in (400, 413) and "limit" in r.json()["error"]


def test_upload_needs_csrf_header_and_same_origin(env, tmp_path):
    client, token, root, dsn = env
    data = synth.video(tmp_path / "in.mp4", seconds=6).read_bytes()
    assert upload(client, token, data, csrf=False).status_code == 403
    assert upload(client, token, data, origin="http://evil.example").status_code == 403


def test_upload_pipeline_discovery_preview_and_window_never_touch_harmar(env, tmp_path, monkeypatch):
    """upload -> local discovery -> candidates -> preview -> chosen window, all free and local."""
    from hayclips.transcription.fake_harmar import FakeHarmar
    client, token, root, dsn = env
    with FakeHarmar(balance=600, api_key="k") as fake:
        monkeypatch.setenv("HAYCLIPS_HARMAR_BASE_URL", fake.base_url)
        r = upload(client, token, synth.video(tmp_path / "in.mp4", seconds=90).read_bytes())
        pid = r.json()["redirect"].split("/")[2]
        for _ in range(2):                                   # discovery, then the chained candidates job
            run_worker(["cpu"], dsn=dsn, once=True)
        with db.connect(dsn) as c:
            states = {j["type"]: j["state"] for j in q.for_project(c, pid)}
            d = Path(q.project(c, pid)["dir"])
        assert states == {"discover_transcript": "SUCCEEDED", "generate_candidates": "SUCCEEDED"}, states
        repo = ProjectRepo(d)
        assert repo.load().source["captions_kind"] == "discovery"
        cands = repo.load_candidates()
        assert cands
        with db.connect(dsn) as c:
            q.enqueue(c, project_id=pid, type="preview_candidate", payload={"candidate_id": cands[0].id})
        run_worker(["io"], dsn=dsn, once=True)
        assert (d / "previews" / cands[0].id / "preview.mp4").stat().st_size > 1000
        clip = repo.select(cands[0].id, pad=1.0)
        with db.connect(dsn) as c:
            q.enqueue(c, project_id=pid, type="fetch_windows", payload={"clip_ids": [clip.id]})
        run_worker(["io"], dsn=dsn, once=True)
        w = repo.load_window(clip.id)
        assert w.wide and w.audio and w.audio.derived_from["sha256"] == w.wide.sha256
        assert w.wide.duration < 90                         # only the chosen window, not the whole upload
        assert fake.requests == []                          # nothing reached the (fake) paid provider
