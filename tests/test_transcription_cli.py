"""harmar_clips.py and `hayclips reconcile` as real processes, against the fake Harmar server."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from hayclips.config import load_settings
from hayclips.errors import ValidationError
from hayclips.models import COMPLETED, RECONCILED_NOT_CREATED, SUBMITTED, SUBMITTING, UNKNOWN_SUBMISSION
from hayclips.transcription import reconcile
from hayclips.transcription.fake_harmar import FakeHarmar
from hayclips.transcription.store import list_attempts

from test_transcription_service import KEY, make_project, run

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fake(monkeypatch):
    with FakeHarmar(balance=600, api_key=KEY) as f:
        monkeypatch.setenv("HAYCLIPS_HARMAR_BASE_URL", f.base_url)
        yield f


def cli(repo, *args, crash=None, key=KEY, wait=True):
    env = dict(os.environ, HAYCLIPS_POLL_INTERVAL_MIN="0.01", HAYCLIPS_POLL_INTERVAL_MAX="0.02",
               HAYCLIPS_POLL_TIMEOUT="10")
    env.pop("HAYCLIPS_ALLOW_PAID_HARMAR", None)
    if key:
        env["HARMAR_API_KEY"] = key
    else:
        env.pop("HARMAR_API_KEY", None)
    if crash:
        env["HAYCLIPS_TEST_CRASH_AT"] = crash
    cmd = [sys.executable, str(ROOT / "harmar_clips.py"), str(repo.root), *args]
    if not wait:
        return subprocess.Popen(cmd, env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return subprocess.run(cmd, env=env, cwd=ROOT, capture_output=True, text=True, timeout=120)


def test_plan_only_without_confirmation(tmp_path, fake):
    repo, _ = make_project(tmp_path)
    r = cli(repo)
    assert r.returncode == 0 and "NEW paid submission: 5 s" in r.stdout and "nothing was submitted" in r.stdout
    assert fake.requests == []


def test_confirmed_run_and_cached_rerun_without_key(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)
    r = cli(repo, "--confirm-paid", "--by", "tester")
    assert r.returncode == 0, r.stderr
    assert list_attempts(repo, cid)[-1].state == COMPLETED
    n = len(fake.requests)
    r = cli(repo, key=None)
    assert r.returncode == 0 and "reuse completed transcript" in r.stdout
    assert len(fake.requests) == n and fake.submit_count == 1


def test_key_missing_without_cache(tmp_path, fake):
    repo, _ = make_project(tmp_path)
    r = cli(repo, "--confirm-paid", "--by", "tester", key=None)
    assert r.returncode == 2 and "HARMAR_API_KEY" in r.stderr
    assert fake.requests == []


def test_process_crash_after_submitting_persisted(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)
    r = cli(repo, "--confirm-paid", "--by", "tester", crash="after_submitting_persisted")
    assert r.returncode == 70
    assert list_attempts(repo, cid)[-1].state == SUBMITTING and fake.submit_count == 0
    r = cli(repo, "--confirm-paid", "--by", "tester")
    assert r.returncode == 3 and fake.submit_count == 0
    assert list_attempts(repo, cid)[-1].state == UNKNOWN_SUBMISSION


def test_process_crash_before_job_id_persisted(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)
    r = cli(repo, "--confirm-paid", "--by", "tester", crash="after_submit_response_before_persist")
    assert r.returncode == 70 and fake.submit_count == 1
    r = cli(repo, "--confirm-paid", "--by", "tester")
    assert r.returncode == 3 and fake.submit_count == 1
    assert list_attempts(repo, cid)[-1].state == UNKNOWN_SUBMISSION


def test_process_crash_after_job_id_persisted_resumes(tmp_path, fake):
    repo, (cid,) = make_project(tmp_path)
    r = cli(repo, "--confirm-paid", "--by", "tester", crash="after_submit_response")
    assert r.returncode == 70 and list_attempts(repo, cid)[-1].state == SUBMITTED
    r = cli(repo)                     # no confirmation needed to keep polling
    assert r.returncode == 0, r.stderr
    assert list_attempts(repo, cid)[-1].state == COMPLETED and fake.submit_count == 1


def test_two_concurrent_processes_submit_once(tmp_path, fake):
    fake.scenario["submit_hangs"] = 0.8
    repo, (cid,) = make_project(tmp_path)
    p1 = cli(repo, "--confirm-paid", "--by", "a", wait=False)
    p2 = cli(repo, "--confirm-paid", "--by", "b", wait=False)
    outs = [p.communicate(timeout=120) for p in (p1, p2)]
    assert fake.submit_count == 1, outs
    assert {p1.returncode, p2.returncode} == {0}, outs
    assert [a.state for a in list_attempts(repo, cid)] == [COMPLETED]


def test_crash_env_ignored_for_real_host():
    from hayclips.transcription.service import _hook

    class Real:
        is_real = True
    os.environ["HAYCLIPS_TEST_CRASH_AT"] = "x"
    try:
        _hook("x", {}, Real())        # must not exit the test process
    finally:
        del os.environ["HAYCLIPS_TEST_CRASH_AT"]


def test_reconcile_attach_job_then_poll(tmp_path, fake):
    fake.scenario.update({"fail_submit_status": 500, "create_job_on_failure": True})
    repo, (cid,) = make_project(tmp_path)
    run(repo)
    fake.scenario["fail_submit_status"] = None
    job_id = next(iter(fake.jobs))            # the operator finds it in the dashboard
    assert "expected charge" in reconcile.show(repo, cid)
    a = reconcile.resolve(repo, cid, by="founder", job_id=job_id, evidence="dashboard")
    assert a.state == SUBMITTED and a.reconciliation["by"] == "founder"
    out = run(repo, confirmed_by=None)
    assert out[0].action == "completed" and fake.submit_count == 1


def test_reconcile_not_created_allows_one_confirmed_retry(tmp_path, fake):
    fake.scenario["fail_submit_status"] = 503
    repo, (cid,) = make_project(tmp_path)
    run(repo)
    with pytest.raises(ValidationError):
        reconcile.resolve(repo, cid, by="founder", not_created=True)          # evidence required
    a = reconcile.resolve(repo, cid, by="founder", not_created=True, evidence="balance unchanged at 600")
    assert a.state == RECONCILED_NOT_CREATED and a.to_dict()["retry_safety"] == "FAILED_SAFE_TO_RETRY"
    fake.scenario["fail_submit_status"] = None
    out = run(repo)
    assert out[0].action == "completed" and fake.submit_count == 2


def test_reconcile_cli_show(tmp_path, fake):
    fake.scenario["fail_submit_status"] = 502
    repo, (cid,) = make_project(tmp_path)
    run(repo)
    r = subprocess.run([sys.executable, "-m", "hayclips", "reconcile", str(repo.root), cid, "--show"],
                       cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "UNKNOWN_SUBMISSION" in r.stdout and "balance before submit: 600s" in r.stdout
