"""Real-browser click-through of the local operator app (Playwright, headless Chromium).

The browser only clicks and types; nothing here calls app endpoints directly, except where a step
deliberately tries to bypass the UI (paid gating) the way a curious operator could from devtools.

Everything runs offline and cannot spend money:
- the web app and worker are subprocesses pointed at a throwaway Postgres database, the fake yt-dlp
  and an in-process fake Harmar on 127.0.0.1;
- HAYCLIPS_FORBID_REAL_HARMAR=1 is set for every process (the real host is refused even in paid mode),
  and the fixture refuses to start if the Harmar base URL is not loopback.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")

from hayclips import db  # noqa: E402
from hayclips.jobs import queue as q  # noqa: E402
from hayclips.transcription.fake_harmar import FakeHarmar  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.browser]

ROOT = Path(__file__).resolve().parents[1]
FAKE_YTDLP = Path(__file__).parent / "fixtures" / "fake_ytdlp.py"
VID = "abcDEF12345"
KEY = "dummy-browser-test-key"
LONG_SRT = "".join(
    f"{k + 1}\n00:{(k * 4) // 60:02}:{(k * 4) % 60:02},000 --> 00:{(k * 4 + 4) // 60:02}:{(k * 4 + 4) % 60:02},000\n"
    f"{['Ո՞րն է քո ամենասիրած գիրքը։', 'Իրականում սա շատ կարևոր հարց է մեր համար։', '[ծիծաղ] Լավ, հետո ինչ եղավ։'][k % 3]}"
    f" {' '.join(['բառ'] * 6)}։\n\n" for k in range(60))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stack:
    """Web app + worker subprocesses with an offline, fake-only environment."""

    def __init__(self, tmp: Path, dsn: str, fake: FakeHarmar):
        self.tmp, self.dsn, self.fake = tmp, dsn, fake
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        (tmp / "captions.srt").write_text(LONG_SRT, encoding="utf-8")
        assert fake.base_url.startswith("http://127.0.0.1:"), "fake Harmar must be loopback"
        self.env = {
            "PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "LANG": "en_US.UTF-8",
            "HAYCLIPS_DATABASE_URL": dsn, "HAYCLIPS_PROJECTS_ROOT": str(tmp / "projects"),
            "HAYCLIPS_HOME": str(tmp / "home"),
            "HAYCLIPS_YTDLP": str(FAKE_YTDLP), "FAKE_YTDLP_MODE": "ok", "FAKE_YTDLP_DURATION": "600",
            "FAKE_YTDLP_LOG": str(tmp / "ytdlp.jsonl"), "FAKE_YTDLP_SRT_FILE": str(tmp / "captions.srt"),
            "HAYCLIPS_HARMAR_BASE_URL": fake.base_url, "HARMAR_API_KEY": KEY,
            "HAYCLIPS_FORBID_REAL_HARMAR": "1",
            "HAYCLIPS_POLL_INTERVAL_MIN": "0.05", "HAYCLIPS_POLL_INTERVAL_MAX": "0.1",
        }
        self.web = self.worker = None

    def start_web(self, paid: bool = False):
        env = dict(self.env, **({"HAYCLIPS_ALLOW_PAID_HARMAR": "1"} if paid else {}))
        self.web = subprocess.Popen([sys.executable, "-m", "hayclips.web", "--port", str(self.port)], cwd=ROOT,
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("web app did not start: " + (self.web.stdout.read() if self.web.poll() else ""))

    def start_worker(self, extra: dict | None = None):
        self.worker = subprocess.Popen([sys.executable, "-m", "hayclips.worker", "--pools", "io,cpu,paid", "--lease", "10"],
                                       cwd=ROOT, env=dict(self.env, **(extra or {})),
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def stop(self, which: str):
        p = getattr(self, which)
        if p and p.poll() is None:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()
        setattr(self, which, None)

    def jobs(self, pid: str) -> list[dict]:
        with db.connect(self.dsn) as c:
            return q.for_project(c, pid)


@pytest.fixture
def stack(tmp_path, pg_dsn):
    with FakeHarmar(balance=600, api_key=KEY) as fake:
        s = Stack(tmp_path, pg_dsn, fake)
        s.start_web()
        s.start_worker()
        try:
            yield s
        finally:
            s.stop("web")
            s.stop("worker")


@pytest.fixture
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium for Playwright is not installed ({exc.__class__.__name__}); "
                        "run .venv/bin/python -m playwright install chromium")
        yield b
        b.close()


def wait_for_job(page, stack, pid, jtype, states=("SUCCEEDED",), timeout=90):
    """Wait until the newest job of this type is finished, reloading the page like an operator would."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        js = [j for j in stack.jobs(pid) if j["type"] == jtype]
        if js and js[0]["state"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            assert js[0]["state"] in states, f"{jtype}: {js[0]['state']} {js[0]['error']}"
            page.reload()
            return js[0]
        time.sleep(0.3)
    raise AssertionError(f"{jtype} did not finish in {timeout}s")


def shot(page, name: str) -> None:
    """Optional screenshots for a human UX review: HAYCLIPS_E2E_SCREENSHOTS=<dir>."""
    d = os.environ.get("HAYCLIPS_E2E_SCREENSHOTS")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(d) / f"{name}.png"), full_page=True)


def flash(page) -> str:
    return page.locator(".flash").inner_text() if page.locator(".flash").count() else ""


def test_operator_click_through(stack, browser, tmp_path):
    ctx = browser.new_context(base_url=stack.url, accept_downloads=True)
    page = ctx.new_page()
    expect = pw.expect

    # 1. dashboard
    page.goto("/")
    expect(page.get_by_role("heading", name="Projects", exact=True)).to_be_visible()

    # 3. invalid YouTube link rejected visibly, nothing created
    page.fill("#name", "Browser test")
    page.fill("#url", "--exec=touch /tmp/pwn")
    page.get_by_role("button", name="Create project").click()
    expect(page.get_by_role("alert")).to_contain_text("YouTube")
    with db.connect(stack.dsn) as c:
        assert c.execute("SELECT count(*) AS n FROM projects").fetchone()["n"] == 0

    # 2 + 4. valid project created
    page.fill("#name", "Browser test")
    page.fill("#url", f"https://youtu.be/{VID}")
    page.get_by_role("button", name="Create project").click()
    expect(page.get_by_role("heading", name="Browser test", exact=True)).to_be_visible()
    pid = page.url.split("/p/")[1].split("?")[0]
    expect(page.locator(".steps li.current")).to_contain_text("Captions")

    # 5. fetch captions (generate is disabled until then)
    expect(page.get_by_role("button", name="Generate candidates")).to_be_disabled()
    page.get_by_role("button", name="Fetch free captions").click()
    wait_for_job(page, stack, pid, "import_captions")
    expect(page.locator(".steps li.current")).to_contain_text("Candidates")

    # 6. generate candidates
    page.fill('input[name="min_seconds"]', "15")
    page.fill('input[name="max_seconds"]', "40")
    page.fill('input[name="count"]', "3")
    page.get_by_role("button", name="Generate candidates").click()
    wait_for_job(page, stack, pid, "generate_candidates")

    # 7. select two candidates, then reorder
    page.get_by_role("link", name="See candidates →").click()
    cards = page.locator("section.card")
    assert cards.count() >= 2
    expect(page.get_by_text("not a virality prediction")).to_be_visible()
    page.locator("details.score").first.evaluate("el => el.open = true")
    shot(page, "0-candidates")
    for k, title in ((0, "Առաջին հատված"), (1, "Երկրորդ հատված")):
        card = page.locator("section.card").nth(k)
        card.locator('input[name="title"]').fill(title)
        card.get_by_role("button", name="Select this window").click()
        expect(page.locator(".flash")).to_contain_text("selected as clp_")
    page.get_by_role("link", name="Project", exact=True).click()
    rows = page.locator("tbody tr").filter(has=page.locator(".tag.ok", has_text="selected"))
    expect(rows).to_have_count(2)
    expect(rows.nth(0)).to_contain_text("Առաջին հատված")
    rows.nth(1).get_by_role("button", name="move up").click()
    expect(page.locator("tbody tr").nth(0)).to_contain_text("Երկրորդ հատված")

    shot(page, "1-project-after-selection")

    # 8. refresh and confirm the state persisted
    page.reload()
    expect(page.locator("tbody tr").nth(0)).to_contain_text("Երկրորդ հատված")
    expect(page.locator("tbody tr").nth(1)).to_contain_text("Առաջին հատված")

    # 9. download the selected windows with the fake source tooling
    page.get_by_role("button", name="Download windows").click()
    wait_for_job(page, stack, pid, "fetch_windows")
    expect(page.locator("tbody tr").nth(0)).to_contain_text("downloaded")

    # 10. record consent (never inferred)
    page.get_by_role("link", name="Transcription", exact=True).click()
    expect(page.get_by_text("No consent recorded")).to_be_visible()
    page.get_by_role("link", name="Record the creator's consent").click()
    page.fill('input[name="granted_by"]', "Synthetic Creator")
    page.fill('textarea[name="statement"]', "Agreed in a test message")
    page.fill('input[name="recorded_by"]', "browser-test")
    page.check('input[name="confirm"]')
    page.get_by_role("button", name="Record consent").click()
    expect(page.locator(".flash")).to_contain_text("consent recorded")

    # 11. paid transcription is disabled in this environment: no confirm form
    expect(page.get_by_role("alert")).to_contain_text("Paid transcription is disabled in this environment")
    expect(page.locator('input[name="confirmed_by"]')).to_have_count(0)

    # 12. bypass attempt from the page itself (devtools-style form injection) is refused by the server
    token = page.locator('input[name="csrf"]').first.get_attribute("value") if page.locator('input[name="csrf"]').count() else \
        page.evaluate("() => fetch('/').then(r => r.text()).then(t => t.match(/name=\"csrf\" value=\"([0-9a-f]+)\"/)[1])")
    page.evaluate("""([pid, token]) => { const f = document.createElement('form'); f.method = 'post';
        f.action = `/p/${pid}/transcribe`;
        for (const [k, v] of Object.entries({csrf: token, confirmed_by: 'sneaky', i_understand: 'yes'})) {
          const i = document.createElement('input'); i.type = 'hidden'; i.name = k; i.value = v; f.appendChild(i); }
        document.body.appendChild(f); f.submit(); }""", [pid, token])
    page.wait_for_load_state()
    expect(page.locator(".flash")).to_contain_text("Paid transcription is disabled in this environment")
    assert not [j for j in stack.jobs(pid) if j["type"] == "transcribe"] and stack.fake.submit_count == 0

    # 13. fake transcript path: restart the web app in paid mode (fake Harmar only) and confirm in the UI
    stack.stop("web")
    stack.start_web(paid=True)
    page.reload()
    expect(page.get_by_text("NEW paid submission").first).to_be_visible()
    page.check('input[name="i_understand"]')
    page.fill('input[name="confirmed_by"]', "browser-test")
    page.get_by_role("button", name="Confirm paid transcription").click()
    wait_for_job(page, stack, pid, "transcribe")
    assert stack.fake.submit_count == 2                      # one per clip, against the fake only

    # 14. render style A
    page.goto(f"/p/{pid}")
    page.get_by_role("button", name="Render selected clips").click()
    wait_for_job(page, stack, pid, "render", timeout=180)

    # 15. open review
    page.get_by_role("link", name="Review", exact=True).click()
    card = page.locator("section.card").first
    expect(card.locator("video").first).to_be_visible()
    expect(card).to_contain_text("machine output — not reviewed")
    shot(page, "2-review-before-edits")

    # 16-19. edit title, valid hook, hook > 45 rejected, trim
    form = card.locator(f'form[action$="/edit"]')
    form.locator('input[name="title"]').fill("Վերջնական վերնագիր")
    form.locator('input[name="hook"]').fill("Ո՞րն է քո ամենասիրած գիրքը")
    form.get_by_role("button", name="Save").click()
    expect(page.locator(".flash")).to_contain_text("saved")
    card = page.locator("section.card").first
    form = card.locator(f'form[action$="/edit"]')
    expect(form.locator('input[name="title"]')).to_have_value("Վերջնական վերնագիր")
    long_hook = "Ա" * 60
    form.locator('input[name="hook"]').fill(long_hook)
    assert len(form.locator('input[name="hook"]').input_value()) == 45     # the input itself caps the length
    form.locator('input[name="hook"]').evaluate("el => { el.removeAttribute('maxlength'); }")
    form.locator('input[name="hook"]').fill(long_hook)
    form.get_by_role("button", name="Save").click()
    expect(page.locator(".flash")).to_contain_text("45 characters or fewer")
    card = page.locator("section.card").first
    form = card.locator(f'form[action$="/edit"]')
    expect(form.locator('input[name="hook"]')).to_have_value("Ո՞րն է քո ամենասիրած գիրքը")   # not truncated or saved
    form.locator('input[name="trim"]').fill("4.5:20")
    form.get_by_role("button", name="Save").click()
    expect(page.locator(".flash")).to_contain_text("saved")
    expect(page.locator("section.card").first).to_contain_text("manual 4.5:20")

    # 20. save a review decision
    card = page.locator("section.card").first
    dec = card.locator('form[action$="/review"]')
    dec.get_by_label("Would post", exact=True).check()
    dec.locator('input[name="minutes_to_fix"]').fill("8")
    dec.locator('input[name="reviewer"]').fill("browser-test")
    dec.get_by_role("button", name="Save decision").click()
    expect(page.locator(".flash")).to_contain_text("review saved")
    expect(page.locator("section.card").first).to_contain_text("reviewed: would post")
    shot(page, "3-review-after-decision")
    page.get_by_role("link", name="Summary", exact=True).click()
    expect(page.get_by_text("Would post: 1 of 1")).to_be_visible()
    expect(page.get_by_text("Needed a trim edit: 1")).to_be_visible()
    shot(page, "4-summary")

    # 21. download MP4 and SRT through the visible buttons
    page.get_by_role("link", name="Review", exact=True).click()
    card = page.locator("section.card").first
    with page.expect_download() as d:
        card.get_by_role("link", name="Download MP4 (A)").click()
    mp4 = tmp_path / "out.mp4"
    d.value.save_as(mp4)
    assert mp4.stat().st_size > 10_000 and mp4.read_bytes()[4:8] == b"ftyp"
    with page.expect_download() as d:
        card.get_by_role("link", name="Download SRT").click()
    srt = tmp_path / "out.srt"
    d.value.save_as(srt)
    assert "-->" in srt.read_text(encoding="utf-8")

    # 22. cancel a safe running job: a slow worker, then the Cancel button
    stack.stop("worker")
    stack.start_worker({"HAYCLIPS_TEST_SLEEP_IN_HANDLER": "60"})
    page.get_by_role("button", name="Re-render this clip").first.click()
    page.goto(f"/p/{pid}")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not [j for j in stack.jobs(pid) if j["type"] == "render" and j["state"] == "RUNNING"]:
        time.sleep(0.2)
    page.reload()
    running = page.locator('tr[data-job-state="RUNNING"]')
    expect(running).to_have_count(1)
    running.get_by_role("button", name="Cancel").click()
    expect(page.locator(".flash")).to_contain_text("cancel requested")
    wait_for_job(page, stack, pid, "render", states=("CANCELLED",), timeout=30)
    expect(page.locator('tr[data-job-state="CANCELLED"]')).to_have_count(1)

    # 23. restart the worker, reload: job and project state are intact
    stack.stop("worker")
    stack.start_worker()
    page.reload()
    expect(page.locator('tr[data-job-state="CANCELLED"]')).to_have_count(1)
    expect(page.locator('tr[data-job-state="RUNNING"]')).to_have_count(0)
    expect(page.locator("tbody tr").nth(0)).to_contain_text("Վերջնական վերնագիր")   # the title edited in step 16
    page.get_by_role("link", name="Review", exact=True).click()
    expect(page.locator("section.card").first).to_contain_text("reviewed: would post")
    assert stack.fake.submit_count == 2                      # still nothing new was submitted
    ctx.close()
