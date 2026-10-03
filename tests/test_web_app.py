"""Local operator web app: workflow, security controls and paid-step gating (needs the local Postgres)."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hayclips import jsonio
from hayclips.jobs import queue
from hayclips.models import Candidate, Clip, candidate_id
from hayclips.project import ProjectRepo, media_record
from hayclips.web.app import create_app

BASE = "http://127.0.0.1:8765"
ORIGIN = {"Origin": BASE}
VID = "abcDEF12345"
SECRET = "dummy-secret-key-123"


@pytest.fixture
def env(pg_dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("HARMAR_API_KEY", SECRET)
    roots = {"projects": tmp_path / "projects", "repo": tmp_path / "repo"}
    roots["repo"].mkdir()
    app = create_app(dsn=pg_dsn, projects_root=roots["projects"], repo_root=roots["repo"], port=8765)
    client = TestClient(app, base_url=BASE)
    return client, roots, pg_dsn


def csrf(client, path="/dashboard"):
    r = client.get(path)
    assert r.status_code == 200, r.text[:300]
    return re.search(r'name="csrf" value="([0-9a-f]{64})"', r.text).group(1)


def post(client, path, data=None, token=None, headers=ORIGIN):
    data = dict(data or {})
    data.setdefault("csrf", token if token is not None else csrf(client))
    return client.post(path, data=data, headers=headers, follow_redirects=False)


def jobs(dsn, pid):
    from hayclips import db
    with db.connect(dsn) as c:
        return queue.for_project(c, pid)


def make_project(client, name="Test pod"):
    r = post(client, "/projects", {"name": name, "url": f"https://youtu.be/{VID}"})
    assert r.status_code == 303
    return r.headers["location"].split("/")[2]


def project_dir(dsn, pid) -> Path:
    from hayclips import db
    with db.connect(dsn) as c:
        return Path(c.execute("SELECT dir FROM projects WHERE id = %s", (pid,)).fetchone()["dir"])


def with_candidates(dsn, pid):
    repo = ProjectRepo(project_dir(dsn, pid))
    cands = [Candidate(id=candidate_id(a, b), start=a, end=b, score=3.0, text="Երեկ մենք գնացինք շուկա։",
                       features={"ending": 2.0}) for a, b in ((100, 140), (300, 345), (600, 650))]
    repo.save_candidates(cands, {})
    return repo, cands


def with_rendered_clip(dsn, pid, *, audio=True):
    """A selected clip with a fake render and audio artifact (bytes only; no video processing)."""
    repo, cands = with_candidates(dsn, pid)
    clip = repo.select(cands[0].id, title="Փորձնական", hook="Ի՞նչ ես կարդում հիմա")
    cdir = repo.clip_dir(clip.id)
    (cdir / "render").mkdir(parents=True, exist_ok=True)
    (cdir / "render" / "A.mp4").write_bytes(bytes(range(256)) * 40)
    (cdir / "render" / "captions.srt").write_text("1\n00:00:00,100 --> 00:00:01,500\nԵրեկ մենք գնացինք\n", encoding="utf-8")
    jsonio.write_json(cdir / "render" / "render.json", {
        "cut": {"start": 4.9, "end": 40.2, "mode": "auto", "note": None, "source_start": 99.9, "source_end": 135.2},
        "alignment": {"method": "provenance", "lag_s": 0.0, "confidence": 1.0, "note": ""},
        "framing": "speaker crop, 3 camera shots", "audio": "loudnorm", "word_source": "Harmar word timestamps",
        "hook": "Ի՞նչ ես կարդում հիմա", "outputs": {"A": {"path": "render/A.mp4", "width": 720, "height": 1280,
                                                          "duration": 35.3, "events": 40}},
        "checks": ["shot at 3.0s: 2 faces; check the framing"], "first_word_at": 0.1, "first_3s_text": "Երեկ"})
    from hayclips.models import COMPLETED, MediaFile, TranscriptionAttempt
    from hayclips.transcription.store import save_attempt
    att = TranscriptionAttempt(id="att_0000000001", clip_id=clip.id, provider="harmar", state=COMPLETED,
                               media=MediaFile(path="audio.m4a", sha256="0" * 64, bytes=1, kind="audio"),
                               options={"timestamps": "word"}, origin="test")
    save_attempt(repo, att)
    if audio:
        from hayclips.models import Window
        (cdir / "audio.m4a").write_bytes(b"fake-audio-bytes")
        (cdir / "wide.mp4").write_bytes(b"fake-wide-bytes")
        repo.save_window(Window(clip_id=clip.id, source_start=95.0, source_end=145.0, pad_start=5.0,
                                wide=media_record(cdir / "wide.mp4", "wide.mp4", "wide", 50.0),
                                audio=media_record(cdir / "audio.m4a", "audio.m4a", "audio", 50.0), source_ref=VID))
    return repo, clip


# ----- workflow ---------------------------------------------------------------------------------

def test_create_project_and_enqueue_first_steps(env):
    client, roots, dsn = env
    pid = make_project(client)
    d = project_dir(dsn, pid)
    assert d.parent == roots["projects"].resolve()
    assert ProjectRepo(d).load().source["url"] == f"https://www.youtube.com/watch?v={VID}"
    assert post(client, f"/p/{pid}/jobs/import_captions").status_code == 303
    (d / "source").mkdir()                        # as if the caption import job had finished
    (d / "source" / "src.hy-orig.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nԲարև։\n", encoding="utf-8")
    assert post(client, f"/p/{pid}/jobs/generate_candidates",
                {"min_seconds": "20", "max_seconds": "50", "count": "4"}).status_code == 303
    got = {j["type"]: j for j in jobs(dsn, pid)}
    assert got["import_captions"]["pool"] == "io"
    assert got["generate_candidates"]["payload"]["count"] == 4
    page = client.get(f"/p/{pid}")
    assert page.status_code == 200 and "Paid transcription is disabled in this environment" in page.text


@pytest.mark.parametrize("bad", ["--exec=touch /tmp/pwn", "-o/tmp/x", "file:///etc/passwd",
                                 f"https://evil.example/watch?v={VID}", "", "javascript:alert(1)"])
def test_invalid_source_links_create_nothing(env, bad):
    client, roots, dsn = env
    r = post(client, "/projects", {"name": "x", "url": bad})
    assert r.status_code == 400 and 'class="notice bad" role="alert"' in r.text
    assert not roots["projects"].exists() or not any(roots["projects"].iterdir())


def test_select_reorder_unselect_keep_stable_ids(env):
    client, roots, dsn = env
    pid = make_project(client)
    repo, cands = with_candidates(dsn, pid)
    for c in cands[:2]:
        assert post(client, f"/p/{pid}/select", {"candidate_id": c.id, "title": "t", "hook": "", "pick_note": ""}).status_code == 303
    a, b = [c.id for c in repo.load().ordered()]
    post(client, f"/p/{pid}/clips/{b}/move", {"dir": "up"})
    assert [c.id for c in repo.load().ordered()] == [b, a]
    post(client, f"/p/{pid}/clips/{a}/selected", {"value": "no"})
    p = repo.load()
    assert [c.id for c in p.ordered()] == [b] and p.clip(a).selected is False
    assert {c.candidate_id for c in p.clips} == {cands[0].id, cands[1].id}
    post(client, f"/p/{pid}/jobs/fetch_windows")
    fetch = [j for j in jobs(dsn, pid) if j["type"] == "fetch_windows"][0]
    assert fetch["payload"]["clip_ids"] == [b]


def test_edit_hook_validation_and_trim(env):
    client, roots, dsn = env
    pid = make_project(client)
    repo, clip = with_rendered_clip(dsn, pid)
    r = post(client, f"/p/{pid}/clips/{clip.id}/edit", {"hook": "x" * 46, "from": "review"})
    assert "45 characters" in r.headers["location"] or "45%20characters" in r.headers["location"]
    post(client, f"/p/{pid}/clips/{clip.id}/edit", {"title": "նոր", "trim": "5.0:30.5"})
    c = repo.load().clip(clip.id)
    assert c.title == "նոր" and (c.trim.caption_start, c.trim.caption_end) == (5.0, 30.5)
    post(client, f"/p/{pid}/clips/{clip.id}/edit", {"trim": "auto"})
    assert repo.load().clip(clip.id).trim is None


# ----- paid gating ------------------------------------------------------------------------------

def test_paid_disabled_banner_and_direct_post_refused(env):
    client, roots, dsn = env
    pid = make_project(client)
    with_rendered_clip(dsn, pid)
    post(client, f"/p/{pid}/consent", {"granted_by": "creator", "statement": "said yes by email", "recorded_by": "op", "confirm": "yes"})
    page = client.get(f"/p/{pid}/transcribe")
    assert "Paid transcription is disabled in this environment" in page.text and "Confirm paid transcription" not in page.text
    r = post(client, f"/p/{pid}/transcribe", {"confirmed_by": "op", "i_understand": "yes"})
    assert "disabled" in r.headers["location"]
    assert not [j for j in jobs(dsn, pid) if j["type"] == "transcribe"]


def test_consent_required_then_confirm_enqueues_once(env, monkeypatch):
    client, roots, dsn = env
    monkeypatch.setenv("HAYCLIPS_ALLOW_PAID_HARMAR", "1")      # server-side opt-in (no network in this test)
    pid = make_project(client)
    repo, clip = with_rendered_clip(dsn, pid)
    page = client.get(f"/p/{pid}/transcribe")
    assert "No consent recorded" in page.text and "Confirm paid transcription" not in page.text
    r = post(client, f"/p/{pid}/transcribe", {"confirmed_by": "op", "i_understand": "yes"})
    assert "consent" in r.headers["location"]
    post(client, f"/p/{pid}/consent", {"granted_by": "creator", "statement": "agreed in a call on 1 Oct", "recorded_by": "op", "confirm": "yes"})
    page = client.get(f"/p/{pid}/transcribe")
    assert "Confirm paid transcription" in page.text and "NEW paid submission" in page.text
    assert post(client, f"/p/{pid}/transcribe", {"confirmed_by": "op"}).headers["location"].count("tick") == 1
    for _ in range(2):
        assert post(client, f"/p/{pid}/transcribe", {"confirmed_by": "op", "i_understand": "yes"}).status_code == 303
    paid = [j for j in jobs(dsn, pid) if j["type"] == "transcribe"]
    assert len(paid) == 1 and paid[0]["payload"] == {"clip_ids": [clip.id], "confirmed_by": "op"}
    assert paid[0]["max_attempts"] == 1


def test_consent_is_never_inferred(env):
    client, roots, dsn = env
    pid = make_project(client)
    assert ProjectRepo(project_dir(dsn, pid)).load().consent == []
    r = post(client, f"/p/{pid}/consent", {"granted_by": "creator", "statement": "", "recorded_by": "op", "confirm": "yes"})
    assert r.status_code == 303 and ProjectRepo(project_dir(dsn, pid)).load().consent == []


def test_render_double_submit_is_one_job_and_edit_makes_a_new_one(env):
    client, roots, dsn = env
    pid = make_project(client)
    repo, clip = with_rendered_clip(dsn, pid)
    form = {"style_A": "on", "style_B": "on", "caption_bottom": "930", "hook_enabled": "on"}
    post(client, f"/p/{pid}/jobs/render", form)
    post(client, f"/p/{pid}/jobs/render", form)
    renders = [j for j in jobs(dsn, pid) if j["type"] == "render"]
    assert len(renders) == 1 and renders[0]["payload"]["styles"] == ["A", "B"]
    post(client, f"/p/{pid}/clips/{clip.id}/edit", {"title": "changed"})
    post(client, f"/p/{pid}/jobs/render", form)
    assert len([j for j in jobs(dsn, pid) if j["type"] == "render"]) == 2


# ----- request security -------------------------------------------------------------------------

def test_dns_rebinding_host_is_rejected(env):
    client, _, _ = env
    assert client.get("/", headers={"Host": "attacker.example:8765"}).status_code == 421


def test_cross_origin_and_missing_csrf_are_rejected(env):
    client, roots, dsn = env
    token = csrf(client)
    assert post(client, "/projects", {"name": "x", "url": VID}, token=token,
                headers={"Origin": "http://evil.example"}).status_code == 403
    assert post(client, "/projects", {"name": "x", "url": VID}, token=token, headers={}).status_code == 403
    assert post(client, "/projects", {"name": "x", "url": VID}, token="").status_code == 403
    assert post(client, "/projects", {"name": "x", "url": VID}, token="0" * 64).status_code == 403
    assert client.put("/projects", headers=ORIGIN).status_code == 405
    assert not roots["projects"].exists()


def test_security_headers(env):
    client, _, _ = env
    h = client.get("/").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"] and "unsafe-inline" not in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"


def test_media_range_download_and_traversal(env, tmp_path):
    client, roots, dsn = env
    pid = make_project(client)
    repo, clip = with_rendered_clip(dsn, pid)
    base = f"/p/{pid}/media/{clip.id}"
    r = client.get(f"{base}/A.mp4", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == bytes(range(10))
    d = client.get(f"{base}/captions.srt?download=1")
    assert d.status_code == 200 and "attachment" in d.headers["content-disposition"]
    for path in [f"{base}/../../project.json", f"{base}/%2e%2e%2fproject.json", f"{base}/window.json",
                 f"/p/{pid}/media/clp_0000000000/A.mp4", f"/p/{pid}/media/..%2f..%2f/A.mp4", f"{base}/B.mp4",
                 f"/p/prj_0000000000/media/{clip.id}/A.mp4"]:
        assert client.get(path).status_code == 404, path
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"secret")
    a = repo.render_dir(clip.id) / "A.mp4"
    a.unlink()
    a.symlink_to(outside)
    assert client.get(f"{base}/A.mp4").status_code == 404


def test_review_page_and_decision_stored(env):
    client, roots, dsn = env
    pid = make_project(client)
    repo, clip = with_rendered_clip(dsn, pid)
    page = client.get(f"/p/{pid}/review")
    assert page.status_code == 200 and "unreviewed machine output" in page.text
    assert "1 thing(s) to check before posting" in page.text and "shot at 3.0s" in page.text
    assert "machine output — not reviewed" in page.text and "Download MP4 (A)" in page.text
    assert post(client, f"/p/{pid}/clips/{clip.id}/review",
                {"would_post": "maybe", "style": "A", "minutes_to_fix": "12", "notes": "cut late", "reviewer": "ed"}).status_code == 303
    assert post(client, f"/p/{pid}/clips/{clip.id}/review", {"would_post": "definitely"}).headers["location"].endswith("maybe")
    from hayclips import db
    with db.connect(dsn) as c:
        rows = c.execute("SELECT * FROM review_decisions WHERE project_id = %s", (pid,)).fetchall()
    assert len(rows) == 1 and rows[0]["minutes_to_fix"] == 12 and rows[0]["style"] == "A"
    after = client.get(f"/p/{pid}/review").text
    assert "12 min" in after and "reviewed: maybe" in after and "machine output — not reviewed" not in after


def test_register_existing_only_within_allowlist(env, tmp_path):
    client, roots, dsn = env
    pilot = roots["repo"] / "pilot-07"
    ProjectRepo(pilot).init("pilot seven", {"kind": "youtube", "url": f"https://www.youtube.com/watch?v={VID}"})
    elsewhere = tmp_path / "elsewhere"
    ProjectRepo(elsewhere).init("nope", {})
    assert "pilot-07" in client.get("/dashboard").text
    r = post(client, "/projects/register", {"dir": "pilot-07"})
    assert r.status_code == 303 and r.headers["location"].startswith("/p/prj_")
    for bad in ["../elsewhere", str(elsewhere), "elsewhere", "pilot-07/../../elsewhere"]:
        r = post(client, "/projects/register", {"dir": bad})
        assert r.headers["location"].startswith("/dashboard?msg="), bad
    from hayclips import db
    with db.connect(dsn) as c:
        assert [r["dir"] for r in c.execute("SELECT dir FROM projects").fetchall()] == [str(pilot.resolve())]


def test_no_secret_in_any_page(env, monkeypatch):
    client, roots, dsn = env
    monkeypatch.setenv("HAYCLIPS_ALLOW_PAID_HARMAR", "1")
    pid = make_project(client)
    repo, clip = with_rendered_clip(dsn, pid)
    post(client, f"/p/{pid}/consent", {"granted_by": "c", "statement": "yes", "recorded_by": "op", "confirm": "yes"})
    for path in ["/", f"/p/{pid}", f"/p/{pid}/candidates", f"/p/{pid}/consent", f"/p/{pid}/transcribe",
                 f"/p/{pid}/review", f"/p/{pid}/jobs"]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert SECRET not in r.text and str(roots["projects"]) not in r.text, path


def test_cancel_job(env):
    client, roots, dsn = env
    pid = make_project(client)
    post(client, f"/p/{pid}/jobs/import_captions")
    job = jobs(dsn, pid)[0]
    r = post(client, f"/p/{pid}/jobs/{job['id']}/cancel")
    assert "cancelled" in r.headers["location"]
    assert jobs(dsn, pid)[0]["state"] == "CANCELLED"


# ----- found in operator testing 2026-10-03 -----------------------------------------------------

def test_candidates_cannot_be_queued_before_captions(env):
    client, roots, dsn = env
    pid = make_project(client)
    r = post(client, f"/p/{pid}/jobs/generate_candidates", {"count": "6"})
    assert "fetch the free captions first" in r.headers["location"].replace("%20", " ")
    assert not [j for j in jobs(dsn, pid) if j["type"] == "generate_candidates"]
    assert "Fetch the free captions first" in client.get(f"/p/{pid}").text


def test_render_refused_until_a_clip_is_transcribed(env):
    client, roots, dsn = env
    pid = make_project(client)
    repo, cands = with_candidates(dsn, pid)
    repo.select(cands[0].id)
    r = post(client, f"/p/{pid}/jobs/render", {"style_A": "on"})
    assert "needs a transcript" in r.headers["location"].replace("%20", " ")
    assert not [j for j in jobs(dsn, pid) if j["type"] == "render"]
    assert "Rendering needs a transcript" in client.get(f"/p/{pid}").text


def test_times_are_human_readable(env):
    client, roots, dsn = env
    pid = make_project(client)
    repo, cands = with_candidates(dsn, pid)
    repo.select(cands[0].id)
    page = client.get(f"/p/{pid}").text + client.get(f"/p/{pid}/candidates").text
    assert "1:40–2:20" in page and "00:01:40,000" not in page


def test_candidate_has_watch_link_at_its_start(env):
    client, roots, dsn = env
    pid = make_project(client)
    with_candidates(dsn, pid)
    page = client.get(f"/p/{pid}/candidates").text
    assert f'href="https://www.youtube.com/watch?v={VID}&amp;t=100s"' in page
    assert 'rel="noopener noreferrer"' in page


def test_review_shows_downloaded_window_and_reason_before_render(env):
    client, roots, dsn = env
    pid = make_project(client)
    repo, clip = with_rendered_clip(dsn, pid)
    (repo.render_dir(clip.id) / "render.json").unlink()
    (repo.transcription_dir(clip.id) / "att_0000000001.json").unlink()
    page = client.get(f"/p/{pid}/review").text
    assert f"/p/{pid}/media/{clip.id}/wide.mp4" in page
    assert "Not rendered yet: it needs a transcript" in page
    assert f'action="/p/{pid}/clips/{clip.id}/review"' not in page     # no decision form for nothing


# ----- MISSING_STORAGE (Phase 1c) ---------------------------------------------------------------

def test_missing_project_folder_is_explained_and_blocks_processing(env):
    import shutil
    client, roots, dsn = env
    pid = make_project(client, name="Gone pod")
    token = csrf(client)
    shutil.rmtree(project_dir(dsn, pid))
    dash = client.get("/dashboard").text
    assert "Gone pod" in dash and "files missing" in dash
    page = client.get(f"/p/{pid}")
    assert page.status_code == 200 and "local project files are missing" in page.text
    r = post(client, f"/p/{pid}/jobs/import_captions", token=token)
    assert r.status_code == 303 and "missing" in r.headers["location"]
    assert jobs(dsn, pid) == []
    assert not project_dir(dsn, pid).exists()                         # never recreated


def test_stale_entry_removal_requires_typed_name_and_is_audited(env):
    import shutil
    from hayclips import db
    client, roots, dsn = env
    pid = make_project(client, name="Gone pod")
    token = csrf(client)
    shutil.rmtree(project_dir(dsn, pid))
    r = post(client, f"/p/{pid}/remove", {"confirm_name": "wrong", "removed_by": "op"}, token=token)
    assert "type the project name" in r.headers["location"].replace("%20", " ")
    assert "Gone pod" in client.get("/dashboard").text
    r = post(client, f"/p/{pid}/remove", {"confirm_name": "Gone pod", "removed_by": "op"}, token=token)
    assert r.status_code == 303 and "Gone pod" not in client.get("/dashboard").text
    with db.connect(dsn) as c:
        assert c.execute("SELECT removed_by FROM projects WHERE id = %s", (pid,)).fetchone()["removed_by"] == "op"
        assert c.execute("SELECT count(*) AS n FROM events WHERE kind = 'stale_project_removed'").fetchone()["n"] == 1


def test_remove_is_refused_while_files_exist(env):
    client, roots, dsn = env
    pid = make_project(client, name="Here pod")
    r = post(client, f"/p/{pid}/remove", {"confirm_name": "Here pod", "removed_by": "op"})
    assert "only projects whose files are missing" in r.headers["location"].replace("%20", " ")
    assert project_dir(dsn, pid).exists() and "Here pod" in client.get("/dashboard").text


# ----- operator-effort metrics (Phase 1c) -------------------------------------------------------

def test_edits_log_only_fields_that_actually_changed(env):
    from hayclips import db
    client, roots, dsn = env
    pid = make_project(client)
    repo, clip = with_rendered_clip(dsn, pid)
    post(client, f"/p/{pid}/clips/{clip.id}/edit", {"title": clip.title, "hook": "Նոր կարճ hook", "pick_note": ""})
    with db.connect(dsn) as c:
        ev = c.execute("SELECT detail FROM events WHERE kind = 'clip_edited'").fetchall()
    assert [e["detail"]["fields"] for e in ev] == [["hook"]]


def test_review_decision_records_effort_metrics(env):
    from hayclips import db
    client, roots, dsn = env
    pid = make_project(client)
    repo, clip = with_rendered_clip(dsn, pid)
    post(client, f"/p/{pid}/clips/{clip.id}/edit", {"title": "Վերջնական", "hook": "Ի՞նչ ես կարդում", "trim": "4.5:30"})
    post(client, f"/p/{pid}/clips/{clip.id}/review",
         {"would_post": "yes", "style": "A", "minutes_to_fix": "7", "notes": "", "reviewer": "op"})
    with db.connect(dsn) as c:
        d = c.execute("SELECT * FROM review_decisions").fetchone()
    assert (d["would_post"], d["style"], d["minutes_to_fix"]) == ("yes", "A", 7)
    assert d["final_title"] == "Վերջնական" and d["final_hook"] == "Ի՞նչ ես կարդում"
    assert d["final_trim"] == {"caption_start": 4.5, "caption_end": 30.0}
    assert d["warning_count"] == 1 and d["created_at"] is not None
    assert d["title_edited"] and d["hook_edited"] and d["trim_edited"]
    assert d["framing_adjusted"] is None or d["framing_adjusted"] is False     # no crop plan in this fixture
    assert d["transcript_edited"] is None                                       # not supported yet: never guessed


def test_project_summary_reports_would_post_rate_and_effort(env):
    client, roots, dsn = env
    pid = make_project(client)
    repo, clip = with_rendered_clip(dsn, pid)
    page = client.get(f"/p/{pid}/summary").text
    assert "No clips reviewed yet" in page
    post(client, f"/p/{pid}/clips/{clip.id}/edit", {"trim": "4.5:30"})
    post(client, f"/p/{pid}/clips/{clip.id}/review", {"would_post": "no", "style": "A", "minutes_to_fix": "20"})
    post(client, f"/p/{pid}/clips/{clip.id}/review", {"would_post": "yes", "style": "B", "minutes_to_fix": "6"})
    page = client.get(f"/p/{pid}/summary").text
    assert "Clips reviewed: 1" in page and "Would post: 1 of 1" in page    # latest decision per clip counts
    assert "Median minutes to fix: 6" in page and "B: 1" in page and "Needed a trim edit: 1" in page


# ----- Phase 1d: product pages -------------------------------------------------------------------

def test_homepage_dashboard_projects_and_new_pages(env):
    client, roots, dsn = env
    home = client.get("/").text
    assert "Turn Armenian videos into ready-to-post shorts." in home and "Start creating" in home
    assert "viral" not in home.lower() and "testimonial" not in home.lower()
    assert "No videos yet" in client.get("/dashboard").text
    assert 'name="url"' in client.get("/new").text and 'id="up-file"' in client.get("/new?tab=upload").text
    pid = make_project(client, name="Listed pod")
    for path in ("/dashboard", "/projects"):
        assert "Listed pod" in client.get(path).text


def test_find_clips_chains_captions_then_candidates(env):
    client, roots, dsn = env
    r = post(client, "/projects", {"name": "Chain pod", "url": f"https://youtu.be/{VID}", "find": "1"})
    pid = r.headers["location"].split("/")[2]
    (j,) = jobs(dsn, pid)
    assert j["type"] == "import_captions" and j["payload"] == {"then": ["generate_candidates"]}


def test_social_connect_is_a_non_functional_shell(env):
    from hayclips import db
    client, roots, dsn = env
    page = client.get("/settings").text
    assert page.count(">Connect<") == 3 and "Not connected" in page
    r = client.get("/settings?msg=Social%20publishing%20is%20not%20available%20in%20this%20version.")
    assert "Social publishing is not available in this version." in r.text
    with db.connect(dsn) as c:
        assert c.execute("SELECT count(*) AS n FROM events").fetchone()["n"] == 0     # nothing happened


# ----- Phase 1d: choose clips, trim before transcription, add captions --------------------------

def choose(client, pid, cand_ids, trims=None, next_="save", token=None):
    data = {"csrf": token or csrf(client), "next": next_, "choose": list(cand_ids)}
    for k, v in (trims or {}).items():
        data[f"trim_{k}"] = v
    return client.post(f"/p/{pid}/choose", data=data, headers=ORIGIN, follow_redirects=False)


def test_batch_choose_only_some_candidates(env):
    client, roots, dsn = env
    pid = make_project(client)
    repo, cands = with_candidates(dsn, pid)
    r = choose(client, pid, [cands[0].id, cands[2].id], next_="captions")
    assert r.headers["location"] == f"/p/{pid}/captions"
    chosen = repo.load().ordered()
    assert [c.candidate_id for c in chosen] == [cands[0].id, cands[2].id]
    assert all(c.look and c.look["preset"] == "active" for c in chosen)          # brand defaults applied
    choose(client, pid, [cands[2].id])
    assert [c.candidate_id for c in repo.load().ordered()] == [cands[2].id]
    assert len(repo.load().clips) == 2                                           # unchosen kept, unselected
    page = client.get(f"/p/{pid}/captions").text
    assert "1 clip selected · 50s" in page and "1m 00s will be transcribed" in page


def test_trim_before_transcription_updates_the_window(env):
    client, roots, dsn = env
    pid = make_project(client)
    repo, cands = with_candidates(dsn, pid)
    choose(client, pid, [cands[0].id], trims={cands[0].id: "102.5:131.0"})
    clip = repo.load().ordered()[0]
    assert (clip.start, clip.end) == (102.5, 131.0)
    r = choose(client, pid, [cands[0].id], trims={cands[0].id: "100:101"})
    assert "5 s to 3 min" in r.headers["location"].replace("%20", " ")
    assert repo.load().ordered()[0].end == 131.0


def test_trim_after_transcription_is_refused(env):
    client, roots, dsn = env
    pid = make_project(client)
    repo, clip = with_rendered_clip(dsn, pid)
    r = choose(client, pid, [clip.candidate_id], trims={clip.candidate_id: "101:139"})
    assert "already has a transcript" in r.headers["location"].replace("%20", " ")
    assert repo.load().clip(clip.id).end == 140


def test_add_captions_refused_when_paid_is_off(env):
    client, roots, dsn = env
    pid = make_project(client)
    repo, cands = with_candidates(dsn, pid)
    choose(client, pid, [cands[1].id])
    page = client.get(f"/p/{pid}/captions").text
    assert "Paid transcription is disabled in this environment" in page and 'name="confirmed_by"' not in page
    r = post(client, f"/p/{pid}/captions", {"confirmed_by": "x", "i_understand": "yes"})
    assert "disabled" in r.headers["location"]
    assert jobs(dsn, pid) == []


def test_add_captions_chain_covers_only_chosen_clips(env, monkeypatch):
    client, roots, dsn = env
    monkeypatch.setenv("HAYCLIPS_ALLOW_PAID_HARMAR", "1")
    pid = make_project(client)
    repo, cands = with_candidates(dsn, pid)
    choose(client, pid, [cands[0].id, cands[2].id])
    post(client, f"/p/{pid}/consent", {"granted_by": "c", "statement": "ok", "recorded_by": "op", "confirm": "yes", "back": "captions"})
    r = post(client, f"/p/{pid}/captions", {"confirmed_by": "Founder", "i_understand": "yes"})
    assert r.status_code == 303
    (j,) = jobs(dsn, pid)
    ids = [c.id for c in repo.load().ordered()]
    assert j["type"] == "fetch_windows" and j["payload"] == {"clip_ids": ids, "then": ["transcribe", "render"],
                                                            "confirmed_by": "Founder"}
    post(client, f"/p/{pid}/captions", {"confirmed_by": "Founder", "i_understand": "yes"})
    assert len(jobs(dsn, pid)) == 1                                              # idempotent


def test_preview_and_find_more_are_free_jobs(env):
    client, roots, dsn = env
    pid = make_project(client)
    repo, cands = with_candidates(dsn, pid)
    (repo.root / "source").mkdir(exist_ok=True)
    (repo.root / "source" / "src.hy-orig.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nԲարև։\n", encoding="utf-8")
    post(client, f"/p/{pid}/candidates/{cands[0].id}/preview")
    post(client, f"/p/{pid}/jobs/find_more")
    types = sorted(j["type"] for j in jobs(dsn, pid))
    assert types == ["generate_candidates", "preview_candidate"]
    assert client.get(f"/p/{pid}/previews/{cands[0].id}/preview.mp4").status_code == 404
    assert client.get(f"/p/{pid}/previews/..%2F..%2Fproject.json/preview.mp4").status_code == 404
