"""Harmar HTTP client. One method per endpoint; typed outcomes for the charging submit.

Safety:
- Refuses the real host unless settings.allow_paid_harmar (HAYCLIPS_ALLOW_PAID_HARMAR=1), and any
  host that is neither the real one nor loopback, before opening a socket.
- Never puts the API key, request headers or signed upload URLs into errors or logs.

Submit outcome classification (docs/saas/job-state-machine.md section 3):
  NotSent        connection could not be established: nothing reached Harmar (safe to retry)
  SubmitRejected 400/401/402/403/404/409/422/429: Harmar answered "no" (safe; 429 carries Retry-After)
  SubmitUnknown  5xx, timeout or reset after sending, malformed 2xx body, 2xx without an id:
                 a job MAY exist and MAY be charged -> reconciliation, never an automatic resubmit
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import time
import urllib.parse
from pathlib import Path

from ..config import REAL_HARMAR_BASE_URL, Settings
from ..errors import PaidOperationBlocked, ProviderError

REAL_HOST = urllib.parse.urlparse(REAL_HARMAR_BASE_URL).hostname
DEFINITE_REJECTIONS = {400, 401, 402, 403, 404, 409, 413, 415, 422, 429}


class NotSent(ProviderError):
    code = "not_sent"


class SubmitRejected(ProviderError):
    code = "submit_rejected"

    def __init__(self, status: int, message: str, retry_after: str | None = None):
        super().__init__(f"Harmar rejected the transcript request (HTTP {status}): {message}")
        self.status = status
        self.retry_after = retry_after


class SubmitUnknown(ProviderError):
    code = "submit_outcome_unknown"


def _is_loopback(host: str | None) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def _error_text(body: bytes) -> str:
    try:
        err = json.loads(body).get("error", {})
        return f"{err.get('code', 'error')}: {err.get('message', '')}".strip()[:300]
    except (ValueError, AttributeError):
        return "non-JSON error body"


class HarmarClient:
    def __init__(self, base_url: str, api_key: str, timeout: float, settings: Settings):
        u = urllib.parse.urlparse(base_url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise PaidOperationBlocked(f"invalid Harmar base URL scheme/host")
        if u.hostname == REAL_HOST:
            if u.scheme != "https":
                raise PaidOperationBlocked("the real Harmar API must be reached over https")
            if not settings.allow_paid_harmar:
                raise PaidOperationBlocked("real Harmar calls are disabled",
                                           hint="set HAYCLIPS_ALLOW_PAID_HARMAR=1 only for a founder-authorised paid run")
            if os.environ.get("HAYCLIPS_FORBID_REAL_HARMAR"):
                raise PaidOperationBlocked("real Harmar calls are forbidden in this process (test environment)")
        elif not _is_loopback(u.hostname):
            raise PaidOperationBlocked(f"refusing unknown provider host {u.hostname!r}",
                                       hint="only api.harmar.ai (with opt-in) or a local fake server are allowed")
        if not api_key:
            raise PaidOperationBlocked("HARMAR_API_KEY is not set")
        self.scheme, self.host, self.port = u.scheme, u.hostname, u.port
        self.prefix = u.path.rstrip("/")
        self._key = api_key
        self.timeout = timeout
        self.is_real = u.hostname == REAL_HOST

    # --- plumbing ---
    def _conn(self, scheme=None, host=None, port=None):
        scheme, host = scheme or self.scheme, host or self.host
        port = port if port is not None else self.port
        cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        return cls(host, port, timeout=self.timeout)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json", "Accept": "application/json"}

    def _json_call(self, method: str, path: str, payload: dict | None = None) -> dict:
        """Free, idempotent-or-harmless calls (balance, uploads, get). Errors -> ProviderError."""
        conn = self._conn()
        try:
            body = json.dumps(payload).encode() if payload is not None else None
            conn.request(method, self.prefix + path, body=body, headers=self._headers())
            resp = conn.getresponse()
            data = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            raise ProviderError(f"Harmar {method} {path} failed: {type(exc).__name__}") from None
        finally:
            conn.close()
        if not 200 <= resp.status < 300:
            raise ProviderError(f"Harmar {method} {path} returned HTTP {resp.status} ({_error_text(data)})")
        try:
            return json.loads(data)
        except ValueError:
            raise ProviderError(f"Harmar {method} {path} returned a non-JSON body") from None

    # --- endpoints ---
    def balance(self) -> dict:
        return self._json_call("GET", "/v1/balance")

    def create_upload(self, filename: str, file_size: int) -> dict:
        r = self._json_call("POST", "/v1/uploads", {"filename": filename, "file_size": file_size})
        if not r.get("media_id") or not r.get("upload_url"):
            raise ProviderError("Harmar upload response is missing media_id/upload_url")
        return r

    def put_upload(self, upload_url: str, content_type: str, path: Path, attempts: int = 3) -> None:
        """Upload bytes to the signed URL. Free, so 5xx/connection errors are retried."""
        u = urllib.parse.urlparse(upload_url)
        if u.scheme != "https" and not (self.scheme == "http" and _is_loopback(u.hostname)):
            raise ProviderError("Harmar did not return a valid HTTPS upload URL")
        target = u.path + ("?" + u.query if u.query else "")
        problem = ""
        for attempt in range(1, attempts + 1):
            conn = self._conn(u.scheme, u.hostname, u.port)
            try:
                with Path(path).open("rb") as stream:
                    conn.request("PUT", target, body=stream,
                                 headers={"Content-Type": content_type, "Content-Length": str(Path(path).stat().st_size)})
                    resp = conn.getresponse()
                    resp.read()
                if 200 <= resp.status < 300:
                    return
                if resp.status < 500:
                    raise ProviderError(f"media upload returned HTTP {resp.status}")
                problem = f"HTTP {resp.status}"
            except (OSError, http.client.HTTPException) as exc:
                problem = type(exc).__name__
            finally:
                conn.close()
            if attempt < attempts:
                time.sleep(min(5 * attempt, 0.05 if not self.is_real else 5 * attempt))
        raise ProviderError(f"media upload failed after {attempts} attempts ({problem}); nothing was charged")

    def submit(self, media_id: str, options: dict, source_lang: str = "hy") -> dict:
        """POST /v1/transcripts: THE CHARGE POINT. Returns the job dict (with "id") or raises a typed outcome."""
        payload = json.dumps({"media_id": media_id, "source_lang": source_lang, "options": options}).encode()
        conn = self._conn()
        try:
            try:
                conn.connect()
            except (OSError, http.client.HTTPException) as exc:
                raise NotSent(f"could not connect to Harmar ({type(exc).__name__}); request was not sent") from None
            try:
                conn.request("POST", self.prefix + "/v1/transcripts", body=payload, headers=self._headers())
                resp = conn.getresponse()
                data = resp.read()
            except (OSError, http.client.HTTPException) as exc:   # timeout, reset, remote disconnect after send
                raise SubmitUnknown(f"connection failed after the request was sent ({type(exc).__name__})") from None
        finally:
            conn.close()
        if resp.status in DEFINITE_REJECTIONS:
            raise SubmitRejected(resp.status, _error_text(data), resp.getheader("Retry-After"))
        if not 200 <= resp.status < 300:
            raise SubmitUnknown(f"Harmar answered HTTP {resp.status} ({_error_text(data)}); a job may exist")
        try:
            job = json.loads(data)
        except ValueError:
            raise SubmitUnknown("Harmar answered 2xx with a malformed body; a job may exist") from None
        if not isinstance(job, dict) or not job.get("id"):
            raise SubmitUnknown("Harmar answered 2xx without a job id; a job may exist")
        return job

    def get(self, job_id: str) -> dict:
        return self._json_call("GET", f"/v1/transcripts/{urllib.parse.quote(job_id, safe='')}")
