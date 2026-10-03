"""Job handlers run through the real queue and worker (local Postgres, fake yt-dlp, fake Harmar, synthetic media)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fixtures import synth
from hayclips import db
from hayclips.jobs import queue as q
from hayclips.jobs.worker import run_worker
from hayclips.models import COMPLETED
from hayclips.project import ProjectRepo
from hayclips.transcription.fake_harmar import FakeHarmar
from hayclips.transcription.store import list_attempts
from test_transcription_service import KEY, make_project as make_paid_project

FAKE_YTDLP = Path(__file__).parent / "fixtures" / "fake_ytdlp.py"
VID = "abcDEF12345"


def register(conn, repo: ProjectRepo, pid: str = "prj_t") -> str:
    q.register_project(conn, project_id=pid, name=repo.root.name, dir=str(repo.root))
    return pid


def run_job(conn, dsn, pid, jtype, payload=None, pools=("io", "cpu", "paid")):
    job = q.enqueue(conn, project_id=pid, type=jtype, payload=payload or {})
    run_worker(list(pools), dsn=dsn, once=True)
    return q.get(conn, job["id"])


@pytest.fixture
def ytdlp(monkeypatch, tmp_path):
    monkeypatch.setenv("HAYCLIPS_YTDLP", str(FAKE_YTDLP))
    monkeypatch.setenv("FAKE_YTDLP_LOG", str(tmp_path / "argv.jsonl"))
    monkeypatch.setenv("FAKE_YTDLP_MODE", "ok")
    monkeypatch.setenv("FAKE_YTDLP_DURATION", "600")


@pytest.fixture
def fast_poll(monkeypatch):
    monkeypatch.setenv("HAYCLIPS_POLL_INTERVAL_MIN", "0.01")
    monkeypatch.setenv("HAYCLIPS_POLL_INTERVAL_MAX", "0.02")
    monkeypatch.setenv("HAYCLIPS_POLL_TIMEOUT", "10")


LONG_SRT = "".join(
    f"{k + 1}\n00:{(k * 4) // 60:02}:{(k * 4) % 60:02},000 --> 00:{(k * 4 + 4) // 60:02}:{(k * 4 + 4) % 60:02},000\n"
    f"{['Ո՞րն է քո ամենասիրած գիրքը։', 'Իրականում սա շատ կարևոր հարց է մեր համար։', '[ծիծաղ] Լավ, հետո ինչ եղավ։'][k % 3]}"
    f" {' '.join(['բառ'] * 6)}։\n\n" for k in range(60))


def test_import_generate_select_fetch(pg, pg_dsn, tmp_path, ytdlp):
    repo = ProjectRepo(tmp_path / "proj")
    repo.init("proj", {"kind": "youtube", "url": f"https://youtu.be/{VID}"})
    pid = register(pg, repo)

    job = run_job(pg, pg_dsn, pid, "import_captions")
    assert job["state"] == "SUCCEEDED", job["error"]
    src = repo.load().source
    assert src["video_id"] == VID and src["url"] == f"https://www.youtube.com/watch?v={VID}"
    assert src["duration"] == 600 and src["captions"] == "source/src.hy-orig.srt"

    (repo.root / src["captions"]).write_text(LONG_SRT, encoding="utf-8")   # longer synthetic captions
    job = run_job(pg, pg_dsn, pid, "generate_candidates", {"min_seconds": 15, "max_seconds": 40, "count": 3})
    assert job["state"] == "SUCCEEDED", job["error"]
    cands = repo.load_candidates()
    assert job["result"]["count"] == len(cands) >= 1

    clip = repo.select(cands[0].id, pad=1.0)
    job = run_job(pg, pg_dsn, pid, "fetch_windows", {"clip_ids": [clip.id]})
    assert job["state"] == "SUCCEEDED", job["error"]
    assert job["result"]["clips"] == [{"clip_id": clip.id, "status": "fetched", "message": job["result"]["clips"][0]["message"]}]
    w = repo.load_window(clip.id)
    assert w.wide and w.audio and w.audio.derived_from["sha256"] == w.wide.sha256


def test_fetch_unknown_clip_is_rejected(pg, pg_dsn, tmp_path, ytdlp):
    repo = ProjectRepo(tmp_path / "proj")
    repo.init("proj", {"kind": "youtube", "url": f"https://youtu.be/{VID}"})
    pid = register(pg, repo)
    job = run_job(pg, pg_dsn, pid, "fetch_windows", {"clip_ids": ["clp_0000000000"]})
    assert job["state"] == "FAILED" and "not selected" in job["error"]


def test_unavailable_video_fails_without_retry(pg, pg_dsn, tmp_path, ytdlp, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_MODE", "fail_unavailable")
    repo = ProjectRepo(tmp_path / "proj")
    repo.init("proj", {"kind": "youtube", "url": f"https://youtu.be/{VID}"})
    pid = register(pg, repo)
    job = run_job(pg, pg_dsn, pid, "import_captions")
    assert job["state"] == "FAILED" and "unavailable" in job["error"] and job["attempts"] == 1


@pytest.mark.slow
def test_render_handler(pg, pg_dsn, tmp_path):
    repo, clip = synth.make_project(tmp_path / "p", seconds=4)
    pid = register(pg, repo)
    job = run_job(pg, pg_dsn, pid, "render", {"styles": ["A"], "clip_ids": [clip.id]})
    assert job["state"] == "SUCCEEDED", job["error"]
    [c] = job["result"]["clips"]
    assert c["status"] == "rendered" and c["outputs"]["A"]["width"] == 720
    assert (repo.render_dir(clip.id) / "A.mp4").exists() and (repo.root / "review.html").exists()


def test_transcribe_handler_against_fake(pg, pg_dsn, tmp_path, monkeypatch, fast_poll):
    with FakeHarmar(balance=600, api_key=KEY) as fake:
        monkeypatch.setenv("HAYCLIPS_HARMAR_BASE_URL", fake.base_url)
        monkeypatch.setenv("HARMAR_API_KEY", KEY)
        repo, (cid,) = make_paid_project(tmp_path)
        pid = register(pg, repo)
        job = run_job(pg, pg_dsn, pid, "transcribe", {"clip_ids": [cid], "confirmed_by": "tester"})
        assert job["state"] == "SUCCEEDED", job["error"]
        assert job["result"]["clips"][0]["state"] == COMPLETED and fake.submit_count == 1
        again = run_job(pg, pg_dsn, pid, "transcribe", {"clip_ids": [cid], "confirmed_by": "tester"})
        assert again["state"] == "SUCCEEDED" and again["result"]["clips"][0]["action"] == "reused"
        assert fake.submit_count == 1


def test_transcribe_without_consent_fails_before_any_request(pg, pg_dsn, tmp_path, monkeypatch, fast_poll):
    with FakeHarmar(balance=600, api_key=KEY) as fake:
        monkeypatch.setenv("HAYCLIPS_HARMAR_BASE_URL", fake.base_url)
        monkeypatch.setenv("HARMAR_API_KEY", KEY)
        repo, (cid,) = make_paid_project(tmp_path, consent=False)
        pid = register(pg, repo)
        job = run_job(pg, pg_dsn, pid, "transcribe", {"clip_ids": [cid], "confirmed_by": "tester"})
        assert job["state"] == "FAILED" and "consent" in job["error"]
        assert job["attempts"] == 1 and fake.requests == [] and fake.submit_count == 0


def test_transcribe_real_host_without_opt_in_is_blocked(pg, pg_dsn, tmp_path, monkeypatch):
    monkeypatch.delenv("HAYCLIPS_HARMAR_BASE_URL")          # real api.harmar.ai default
    monkeypatch.setenv("HARMAR_API_KEY", "hk_live_not_a_real_key")
    repo, (cid,) = make_paid_project(tmp_path)
    pid = register(pg, repo)
    job = run_job(pg, pg_dsn, pid, "transcribe", {"clip_ids": [cid], "confirmed_by": "tester"})
    assert job["state"] == "FAILED" and "HAYCLIPS_ALLOW_PAID_HARMAR" in job["error"]
    assert "hk_live_not_a_real_key" not in job["error"] and list_attempts(repo, cid) == []


def test_invalid_payload_fails_cleanly(pg, pg_dsn, tmp_path):
    repo = ProjectRepo(tmp_path / "proj")
    repo.init("proj", {"kind": "youtube", "url": f"https://youtu.be/{VID}"})
    pid = register(pg, repo)
    job = q.enqueue(pg, project_id=pid, type="render", payload={"styles": ["Z"]})
    run_worker(["cpu"], dsn=pg_dsn, once=True)
    job = q.get(pg, job["id"])
    assert job["state"] == "FAILED" and "styles" in job["error"]


def test_render_with_nothing_transcribed_fails_with_reason(pg, pg_dsn, tmp_path):
    """Found in operator testing 2026-10-03: the job said SUCCEEDED although no clip could be rendered."""
    from hayclips.models import Candidate, candidate_id
    repo = ProjectRepo(tmp_path / "proj")
    repo.init("proj", {"kind": "youtube", "url": f"https://youtu.be/{VID}"})
    repo.save_candidates([Candidate(id=candidate_id(10, 40), start=10, end=40, score=1, text="x")], {})
    clip = repo.select(candidate_id(10, 40))
    pid = register(pg, repo)
    job = run_job(pg, pg_dsn, pid, "render", {"styles": ["A"]})
    assert job["state"] == "FAILED"
    assert "nothing was rendered" in job["error"] and "not fetched yet" in job["error"]
    assert "transcript" in job["error"]          # the hint names the missing steps
    assert job["result"]["clips"][0]["clip_id"] == clip.id
