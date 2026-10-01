"""In-process fake Harmar API for tests and offline development. Listens on 127.0.0.1 only.

Implements the endpoints the pipeline uses, with Harmar-shaped JSON (error format
{"error": {"code", "message", ...}}, completed jobs with "words" and "segments"), plus scenario
controls to reproduce every submit outcome:

    with FakeHarmar(balance=100) as fake:
        fake.scenario["fail_submit_status"] = 500      # ambiguous
        fake.scenario["reset_after_submit"] = True     # job created, connection dropped
        fake.scenario["submit_hangs"] = 2.0            # job created, reply after 2 s
        fake.scenario["malformed_submit_body"] = True  # 200 with a non-JSON body
        fake.scenario["job_fails"] = True              # job ends "failed" (refunded)
        fake.scenario["polls_until_complete"] = 2
        fake.base_url, fake.submit_count, fake.requests, fake.jobs
"""
from __future__ import annotations

import json
import math
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONTENT_TYPES = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
                 "m4a": "audio/mp4", "mp3": "audio/mpeg", "wav": "audio/wav"}

DEFAULT_SCENARIO = {
    "fail_submit_status": None,      # int: respond with this status on POST /v1/transcripts
    "create_job_on_failure": False,  # with fail_submit_status: also create (and charge) a job
    "reset_after_submit": False,
    "submit_hangs": 0.0,
    "malformed_submit_body": False,
    "job_fails": False,
    "polls_until_complete": 1,
    "duration_seconds": 5.0,         # media duration the fake "detects"
    "upload_fail_times": 0,          # PUT returns 503 this many times first
    "retry_after": 7,
}


def _result(job_id: str, duration: float) -> dict:
    words = [{"text": "- Բարև", "start": 0.40, "end": 0.80, "speaker": 1},
             {"text": "ձեզ։", "start": 0.85, "end": 1.20, "speaker": 1},
             {"text": "- Ողջույն։", "start": 1.60, "end": 2.20, "speaker": 2}]
    segments = [{"text": "- Բարև ձեզ։", "start": 0.40, "end": 1.20, "speaker": 1},
                {"text": "- Ողջույն։", "start": 1.60, "end": 2.20, "speaker": 2}]
    return {"id": job_id, "status": "completed", "duration_seconds": duration, "media_retained": False,
            "seconds_charged": math.ceil(duration), "quality": "ok", "text": "- Բարև ձեզ։\n- Ողջույն։",
            "words": words, "segments": segments}


class FakeHarmar:
    def __init__(self, balance: int = 600, api_key: str = "hk_test_fake", **scenario):
        self.balance = balance
        self.api_key = api_key
        self.scenario = dict(DEFAULT_SCENARIO, **scenario)
        self.requests: list[tuple[str, str]] = []
        self.submit_count = 0
        self.jobs: dict[str, dict] = {}
        self.uploads: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> "FakeHarmar":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    def start(self) -> "FakeHarmar":
        return self.__enter__()

    def stop(self) -> None:
        self.__exit__()

    def _create_job(self, media_id: str) -> str:
        job_id = str(uuid.uuid4())
        duration = float(self.scenario["duration_seconds"])
        self.balance -= math.ceil(duration)
        self.jobs[job_id] = {"id": job_id, "media_id": media_id, "polls": 0, "duration": duration}
        return job_id

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # keep test output clean
                pass

            def _send(self, status: int, body, extra_headers=None):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (extra_headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _error(self, status: int, code: str, message: str, **extra):
                self._send(status, {"error": {"code": code, "message": message, **extra}})

            def _body(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _authorized(self) -> bool:
                if self.headers.get("Authorization") != f"Bearer {fake.api_key}":
                    self._error(401, "unauthorized", "Invalid API key.")
                    return False
                return True

            def do_GET(self):
                path = self.path.split("?")[0]
                with fake._lock:
                    fake.requests.append(("GET", path))
                if not self._authorized():
                    return
                if path == "/v1/balance":
                    with fake._lock:
                        b = fake.balance
                    return self._send(200, {"seconds_remaining": b, "minutes_remaining": b // 60})
                if path.startswith("/v1/transcripts/"):
                    job_id = path.rsplit("/", 1)[1]
                    with fake._lock:
                        job = fake.jobs.get(job_id)
                        if job is None:
                            return self._error(404, "not_found", "Transcript not found.")
                        job["polls"] += 1
                        polls = job["polls"]
                    if polls < fake.scenario["polls_until_complete"]:
                        return self._send(200, {"id": job_id, "status": "processing"})
                    if fake.scenario["job_fails"]:
                        sec = math.ceil(job["duration"])
                        with fake._lock:
                            if not job.get("refunded"):
                                fake.balance += sec
                                job["refunded"] = True
                        return self._send(200, {"id": job_id, "status": "failed", "seconds_charged": sec,
                                                "seconds_refunded": sec, "error": "transcription_failed"})
                    return self._send(200, _result(job_id, job["duration"]))
                return self._error(404, "not_found", "Unknown path.")

            def do_PUT(self):
                path = self.path.split("?")[0]
                body = self._body()
                with fake._lock:
                    fake.requests.append(("PUT", path))
                    if fake.scenario["upload_fail_times"] > 0:
                        fake.scenario["upload_fail_times"] -= 1
                        fail = True
                    else:
                        fail = False
                if fail:
                    return self._send(503, b"")
                media_id = path.rsplit("/", 1)[1]
                with fake._lock:
                    up = fake.uploads.get(media_id)
                    if up is None:
                        return self._send(404, b"")
                    up["received"] = len(body)
                return self._send(200, b"")

            def do_POST(self):
                path = self.path.split("?")[0]
                raw = self._body()
                with fake._lock:
                    fake.requests.append(("POST", path))
                if not self._authorized():
                    return
                try:
                    data = json.loads(raw or b"{}")
                except ValueError:
                    return self._error(400, "invalid_request", "Body is not JSON.")
                if path == "/v1/uploads":
                    media_id = str(uuid.uuid4())
                    ext = str(data.get("filename", "")).rsplit(".", 1)[-1].lower()
                    if ext not in CONTENT_TYPES or not data.get("file_size"):
                        return self._error(400, "invalid_request", "Unsupported file.")
                    with fake._lock:
                        fake.uploads[media_id] = {"filename": data["filename"], "size": data["file_size"]}
                    return self._send(200, {"media_id": media_id,
                                            "upload_url": f"{fake.base_url}/upload/{media_id}?X-Signature=secret",
                                            "content_type": CONTENT_TYPES[ext], "expires_in_seconds": 1800})
                if path == "/v1/transcripts":
                    with fake._lock:
                        fake.submit_count += 1
                        sc = dict(fake.scenario)
                        up = fake.uploads.get(data.get("media_id"))
                    status = sc["fail_submit_status"]
                    if status:
                        if sc["create_job_on_failure"]:
                            with fake._lock:
                                fake._create_job(data.get("media_id"))
                        extra = {"Retry-After": str(sc["retry_after"])} if status == 429 else None
                        if status == 429:
                            return self._send(429, {"error": {"code": "rate_limited", "message": "Slow down."}}, extra)
                        return self._error(status, "server_error" if status >= 500 else "invalid_request",
                                           f"Fake failure {status}.")
                    if up is None or up.get("received") != up.get("size"):
                        return self._error(400, "invalid_request", "Media not uploaded.")
                    need = math.ceil(sc["duration_seconds"])
                    with fake._lock:
                        if fake.balance < need:
                            return self._error(402, "insufficient_credits", "Not enough credits for this media.",
                                               seconds_needed=need, seconds_available=fake.balance)
                        job_id = fake._create_job(data["media_id"])
                    if sc["reset_after_submit"]:
                        self.close_connection = True
                        try:
                            import socket as _s
                            self.connection.shutdown(_s.SHUT_RDWR)
                        except OSError:
                            pass
                        return
                    if sc["submit_hangs"]:
                        time.sleep(sc["submit_hangs"])
                    if sc["malformed_submit_body"]:
                        return self._send(200, b"<html>gateway</html>")
                    return self._send(201, {"id": job_id, "status": "processing", "charged_before_processing": True})
                return self._error(404, "not_found", "Unknown path.")

        return Handler
