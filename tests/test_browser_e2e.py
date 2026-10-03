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

import json
import os
import re
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
            "HAYCLIPS_DISCOVERY_ENGINE": "fake",
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


def set_range(locator, value):
    """Move a range slider like a user would (value + input/change events)."""
    locator.evaluate("(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true}));"
                     " el.dispatchEvent(new Event('change', {bubbles: true})); }", str(value))


def wait_until(fn, timeout=90, step=0.3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition not met in time")


def test_creator_workflow_youtube(stack, browser, tmp_path):
    ctx = browser.new_context(base_url=stack.url, accept_downloads=True, viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    expect = pw.expect

    # homepage -> dashboard
    page.goto("/")
    expect(page.get_by_role("heading", name="Turn Armenian videos into ready-to-post shorts.")).to_be_visible()
    shot(page, "1d-0-home")
    page.get_by_role("link", name="Start creating").click()
    expect(page.get_by_role("heading", name="Your videos")).to_be_visible()

    # social connect is a shell
    page.goto("/settings")
    page.locator('[data-connect="instagram"]').click()
    expect(page.locator(".flash")).to_contain_text("Social publishing is not available in this version.")
    expect(page.get_by_text("Not connected").first).to_be_visible()

    # Brand Kit: Karaoke by default from now on
    page.goto("/brand")
    page.locator('label.preset:has(input[value="karaoke"])').click()
    page.get_by_role("button", name="Save Brand Kit").click()
    expect(page.locator(".flash")).to_contain_text("Brand Kit saved")

    # new video: invalid link refused visibly, then a valid one ("Find clips" chains captions -> candidates)
    page.goto("/new")
    page.fill("#name", "Browser test")
    page.fill("#url", "--exec=touch /tmp/pwn")
    page.get_by_role("button", name="Find clips").click()
    expect(page.get_by_role("alert")).to_contain_text("YouTube")
    page.fill("#name", "Browser test")
    page.fill("#url", f"https://youtu.be/{VID}")
    page.get_by_role("button", name="Find clips").click()
    pid = page.url.split("/p/")[1].split("?")[0]
    wait_until(lambda: (stack.jobs(pid) and all(j["state"] == "SUCCEEDED" for j in stack.jobs(pid))
                        and len(stack.jobs(pid)) == 2))
    page.reload()
    page.get_by_role("link", name="Choose clips").first.click()
    cards = page.locator("[data-cand]")
    expect(cards.first).to_be_visible()
    n = cards.count()
    assert n >= 3, n

    # preview a candidate in the app, then trim it before transcription
    cards.nth(0).get_by_role("button", name="Preview").click()
    wait_until(lambda: any(j["type"] == "preview_candidate" and j["state"] == "SUCCEEDED" for j in stack.jobs(pid)))
    page.goto(f"/p/{pid}/candidates")
    card0 = page.locator("[data-cand]").nth(0)
    expect(card0.locator("video")).to_have_count(1)
    start = float(card0.get_attribute("data-start"))
    set_range(card0.locator('[data-handle="start"]'), start + 2)
    expect(card0.locator("[data-trim-label]")).to_contain_text("trimmed")

    # choose 2 of the candidates; the sticky bar sums them; "Add captions"
    card0.locator('input[name="choose"]').check()
    page.locator("[data-cand]").nth(2).locator('input[name="choose"]').check()
    expect(page.locator("[data-summary]")).to_contain_text("2 selected")
    shot(page, "1d-1-choose")
    page.get_by_role("button", name="Add captions").click()
    expect(page.locator("[data-cost-summary]")).to_contain_text("2 clips selected")
    expect(page.locator("[data-cost-transcribed]")).to_contain_text("will be transcribed")

    # paid mode is off: blocked in the UI and on the server
    expect(page.get_by_role("alert")).to_contain_text("Paid transcription is disabled in this environment")
    token = page.locator('#csrf-holder input[name="csrf"]').get_attribute("value")
    page.evaluate("""([pid, token]) => { const f = document.createElement('form'); f.method = 'post';
        f.action = `/p/${pid}/captions`;
        for (const [k, v] of Object.entries({csrf: token, confirmed_by: 'sneaky', i_understand: 'yes'})) {
          const i = document.createElement('input'); i.type = 'hidden'; i.name = k; i.value = v; f.appendChild(i); }
        document.body.appendChild(f); f.submit(); }""", [pid, token])
    page.wait_for_load_state()
    expect(page.locator(".flash")).to_contain_text("Paid transcription is disabled in this environment")
    assert stack.fake.submit_count == 0 and not [j for j in stack.jobs(pid) if j["type"] in ("fetch_windows", "transcribe")]

    # fake-only paid mode: record permission, confirm; only the 2 chosen clips are processed
    stack.stop("web")
    stack.start_web(paid=True)
    page.reload()
    page.fill('input[name="granted_by"]', "Synthetic Creator")
    page.fill('input[name="statement"]', "Agreed in a test message")
    page.fill('input[name="recorded_by"]', "browser-test")
    page.check('input[name="confirm"]')
    page.get_by_role("button", name="Record permission").click()
    page.check('input[name="i_understand"]')
    page.fill('input[name="confirmed_by"]', "browser-test")
    page.get_by_role("button", name="Add captions").click()
    wait_until(lambda: [j for j in stack.jobs(pid) if j["type"] == "render" and j["state"] == "SUCCEEDED"], timeout=180)
    assert stack.fake.submit_count == 2                                   # exactly the chosen clips
    from hayclips.project import ProjectRepo
    with db.connect(stack.dsn) as c:
        repo = ProjectRepo(Path(q.project(c, pid)["dir"]))
    chosen = repo.load().ordered()
    assert len(chosen) == 2 and all(repo.load_window(c.id) for c in chosen)
    assert all(c.look["preset"] == "karaoke" for c in chosen)             # Brand Kit default applied
    assert abs(chosen[0].start - (start + 2)) < 0.05                       # the pre-transcription trim stuck
    assert len(list((repo.root / "clips").iterdir())) == 2                # unchosen moments were never downloaded

    # project page -> editor
    page.goto(f"/p/{pid}")
    expect(page.locator("[data-clip]")).to_have_count(2)
    shot(page, "1d-2-project")
    page.locator("[data-clip]").first.get_by_role("link", name="Edit").click()
    expect(page.locator("[data-preview-box]")).to_be_visible()
    cid = page.url.split("/clips/")[1].split("/")[0]

    # caption style + live position drag + safe-zone overlay + move to safe area
    page.locator('label.preset:has(input[value="clean"])').click()
    capbox = page.locator("[data-caption]").bounding_box()
    page.mouse.move(capbox["x"] + capbox["width"] / 2, capbox["y"] + capbox["height"] / 2)
    page.mouse.down()
    page.mouse.move(capbox["x"] + capbox["width"] / 2 - 20, capbox["y"] + capbox["height"] / 2 + 120, steps=8)
    page.mouse.up()
    y_after = float(page.locator('[data-look="y"]').input_value())
    assert y_after > chosen[0].look["y"] + 0.03, y_after
    page.get_by_role("button", name="TikTok").click()
    expect(page.locator("[data-zones] .zone")).to_have_count(3)
    expect(page.locator("[data-safe-warn]")).to_contain_text("TikTok")
    if page.locator("[data-move-safe]").is_visible():
        page.locator("[data-move-safe]").click()
        expect(page.locator("[data-safe-warn]")).to_contain_text("clear of TikTok")
    page.get_by_role("button", name="Reels").click()
    expect(page.locator(".zone-label").first).to_contain_text("Reels")
    shot(page, "1d-3-editor")
    page.get_by_role("button", name="Save style").click()
    expect(page.locator(".flash")).to_contain_text("caption look saved")
    assert repo.load().clip(cid).look["preset"] == "clean"

    # hook edit (visual position is a hidden field the drag updates; text + duration here)
    page.fill("[data-hook-text]", "Ո՞րն է քո սիրած գիրքը")
    page.fill("[data-hook-duration]", "4")
    page.get_by_role("button", name="Save hook").click()
    expect(page.locator(".flash")).to_contain_text("hook saved")
    assert repo.load().clip(cid).hook == "Ո՞րն է քո սիրած գիրքը"

    # transcript text correction (no new transcription)
    page.locator("[data-text-editor] summary").click()
    seg = page.locator("[data-text-editor] form.seg").first
    seg.locator('input[name="text"]').fill("Ուղղված տեքստ")
    seg.get_by_role("button", name="Save").click()
    expect(page.locator(".flash")).to_contain_text("no new transcription")

    # crop adjustment
    page.get_by_role("tab", name="Frame").click()
    slider = page.locator("[data-shot]").first
    set_range(slider, int(slider.get_attribute("max")))
    page.get_by_role("button", name="Save framing").click()
    expect(page.locator(".flash")).to_contain_text("framing saved")

    # re-render after edits (local render only), then export
    submits = stack.fake.submit_count
    page.get_by_role("button", name="Render").click()
    wait_until(lambda: len([j for j in stack.jobs(pid) if j["type"] == "render" and j["state"] == "SUCCEEDED"]) >= 2, timeout=120)
    assert stack.fake.submit_count == submits                             # no retranscription
    info = json.loads((repo.render_dir(cid) / "render.json").read_text(encoding="utf-8"))
    assert info["transcript_edited"] is True and info["look"]["preset"] == "clean"
    page.goto(f"/p/{pid}/clips/{cid}/export")
    expect(page.get_by_text("9:16")).to_be_visible()
    page.get_by_role("button", name="Shorts").click()
    expect(page.locator("[data-export-frame] .zone")).to_have_count(3)
    shot(page, "1d-4-export")
    with page.expect_download() as d:
        page.get_by_role("link", name="Download MP4").click()
    mp4 = tmp_path / "out.mp4"
    d.value.save_as(mp4)
    assert mp4.read_bytes()[4:8] == b"ftyp"
    with page.expect_download() as d:
        page.get_by_role("link", name="Download SRT").click()
    srt = tmp_path / "out.srt"
    d.value.save_as(srt)
    assert "Ուղղված" in srt.read_text(encoding="utf-8")

    # add to calendar -> the item appears
    page.fill("#plan_date", "2026-11-05")
    page.fill("#plan_time", "19:00")
    page.check('input[name="platforms"][value="tiktok"]')
    page.get_by_role("button", name="Add to calendar").click()
    expect(page.get_by_role("heading", name="November 2026")).to_be_visible()
    expect(page.locator('[data-date="2026-11-05"] .item')).to_contain_text("19:00")
    shot(page, "1d-5-calendar")
    assert stack.fake.submit_count == 2                                   # nothing else was ever submitted
    ctx.close()


def test_creator_workflow_upload(stack, browser, tmp_path):
    from fixtures import synth
    video = synth.video(tmp_path / "talk.mp4", seconds=80)
    ctx = browser.new_context(base_url=stack.url)
    page = ctx.new_page()
    expect = pw.expect
    page.goto("/new")
    page.get_by_role("tab", name="Upload a file").click()
    page.fill("#up-name", "Uploaded talk")
    page.set_input_files("#up-file", str(video))
    page.get_by_role("button", name="Upload and find clips").click()
    page.wait_for_url(re.compile(r".*/p/prj_[0-9a-f]{10}$"))
    pid = page.url.split("/p/")[1]
    wait_until(lambda: len(stack.jobs(pid)) == 2 and all(j["state"] == "SUCCEEDED" for j in stack.jobs(pid)))
    page.reload()
    expect(page.locator("[data-stage]")).to_have_text("Choose clips")
    expect(page.get_by_text("Uploaded video")).to_be_visible()
    page.get_by_role("link", name="Choose clips").first.click()
    expect(page.locator("[data-cand]").first).to_be_visible()
    assert stack.fake.requests == []                                      # discovery is local and free
    ctx.close()
