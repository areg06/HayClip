"""Projects whose local folder disappeared: explicit MISSING_STORAGE state, no processing, audited removal."""
import shutil

import pytest

from hayclips.errors import PipelineError
from hayclips.jobs import queue as q
from hayclips.jobs.worker import run_worker
from hayclips.project import ProjectRepo


@pytest.fixture
def proj(pg, tmp_path):
    repo = ProjectRepo(tmp_path / "proj")
    repo.init("proj", {"kind": "youtube", "url": "https://youtu.be/abcDEF12345"})
    q.register_project(pg, project_id="prj_aaaaaaaaaa", name="proj", dir=str(repo.root))
    return repo


def test_storage_state(pg, proj):
    row = q.project(pg, "prj_aaaaaaaaaa")
    assert q.storage_state(row) == "OK"
    shutil.rmtree(proj.root)
    assert q.storage_state(row) == q.MISSING_STORAGE
    assert q.project(pg, "prj_aaaaaaaaaa") is not None          # the row is never deleted silently


def test_no_job_can_be_queued_for_missing_storage(pg, proj):
    shutil.rmtree(proj.root)
    with pytest.raises(PipelineError, match="local project files are missing"):
        q.enqueue(pg, project_id="prj_aaaaaaaaaa", type="import_captions")
    assert q.for_project(pg, "prj_aaaaaaaaaa") == []


def test_worker_fails_queued_job_whose_folder_vanished(pg, pg_dsn, proj):
    job = q.enqueue(pg, project_id="prj_aaaaaaaaaa", type="import_captions")
    shutil.rmtree(proj.root)
    run_worker(["io"], dsn=pg_dsn, once=True)
    done = q.get(pg, job["id"])
    assert done["state"] == "FAILED" and "MISSING_STORAGE" in done["error"] and done["attempts"] == 1
    assert not proj.root.exists()                                 # nothing is recreated


def test_removal_needs_missing_storage_and_exact_confirmation_and_is_audited(pg, proj):
    with pytest.raises(PipelineError, match="only projects whose files are missing"):
        q.remove_stale_project(pg, "prj_aaaaaaaaaa", confirm_name="proj", actor="op")
    shutil.rmtree(proj.root)
    with pytest.raises(PipelineError, match="type the project name"):
        q.remove_stale_project(pg, "prj_aaaaaaaaaa", confirm_name="wrong", actor="op")
    q.remove_stale_project(pg, "prj_aaaaaaaaaa", confirm_name="proj", actor="op")
    row = q.project(pg, "prj_aaaaaaaaaa")
    assert row["removed_at"] is not None and row["removed_by"] == "op"
    assert [p["id"] for p in q.active_projects(pg)] == []
    ev = pg.execute("SELECT * FROM events WHERE kind = 'stale_project_removed'").fetchall()
    assert len(ev) == 1 and ev[0]["actor"] == "op" and ev[0]["detail"]["dir_name"] == "proj"
    with pytest.raises(PipelineError):
        q.enqueue(pg, project_id="prj_aaaaaaaaaa", type="import_captions")
