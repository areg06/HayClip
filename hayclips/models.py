"""Domain records shared by the CLI and (later) the web app.

Identity rules:
- Candidate.id is deterministic from the window (same window -> same id across regenerations).
- Clip.id is assigned once when the operator selects a window and never changes, whatever the order.
  All per-clip artifacts live in clips/<clip.id>/, so reordering or deleting other clips cannot
  attach one clip's transcript, crop, captions or renders to another.
- Clip.order is display order only.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


def candidate_id(start: float, end: float) -> str:
    return "cand_" + hashlib.sha1(f"{start:.2f}-{end:.2f}".encode()).hexdigest()[:10]


def _from_dict(cls, data: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class Line:
    start: float
    end: float
    text: str


@dataclass
class Candidate:
    """Machine output of selection. Owned by candidates.json; regenerating it never touches clips."""
    id: str
    start: float
    end: float
    score: float
    text: str
    features: dict = field(default_factory=dict)   # explainable score parts

    to_dict = asdict

    @classmethod
    def from_dict(cls, d):
        return _from_dict(cls, d)


@dataclass
class Trim:
    """Operator override of the final cut inside the padded window (seconds, window-relative)."""
    caption_start: float
    caption_end: float
    snap: str = "manual"   # only manual trims are stored here; automatic snaps live in render.json


@dataclass
class Clip:
    """Operator-owned selection. Lives in project.json; changed only by operator commands."""
    id: str
    order: int
    start: float                  # planned window in source seconds
    end: float
    candidate_id: str | None = None
    pad: float = 5.0
    title: str = ""
    hook: str = ""
    pick_note: str = ""
    selected: bool = True
    trim: Trim | None = None
    legacy_name: str | None = None   # e.g. "clip_02" for migrated pilots (display/audit only)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Clip":
        c = _from_dict(cls, d)
        if isinstance(c.trim, dict):
            c.trim = Trim(**c.trim)
        return c


@dataclass
class ConsentRecord:
    """Explicit, positive permission to send creator media to a third-party provider.

    Never inferred from project creation. A paid operation requires a record whose provider matches
    and whose scope covers the operation."""
    id: str
    provider: str            # "harmar"
    scope: str               # "selected_windows" (only clip windows, never the full episode)
    granted_by: str          # who gave permission (creator / channel), as stated by the operator
    recorded_by: str         # operator who recorded it
    statement: str           # the exact permission text / evidence
    recorded_at: str = field(default_factory=now_iso)
    source_ref: str = ""     # source video id or URL the consent covers
    revoked_at: str | None = None

    to_dict = asdict

    @classmethod
    def from_dict(cls, d):
        return _from_dict(cls, d)


@dataclass
class Project:
    name: str
    source: dict = field(default_factory=dict)      # {"kind": "youtube", "video_id", "url", "duration", "title"}
    clips: list[Clip] = field(default_factory=list)
    consent: list[ConsentRecord] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "name": self.name, "created_at": self.created_at,
                "source": self.source, "consent": [c.to_dict() for c in self.consent],
                "clips": [c.to_dict() for c in sorted(self.clips, key=lambda c: c.order)]}

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        return cls(name=d["name"], source=d.get("source", {}), schema_version=d.get("schema_version", 1),
                   created_at=d.get("created_at", now_iso()),
                   clips=[Clip.from_dict(c) for c in d.get("clips", [])],
                   consent=[ConsentRecord.from_dict(c) for c in d.get("consent", [])])

    def clip(self, clip_id: str) -> Clip:
        for c in self.clips:
            if c.id == clip_id:
                return c
        raise KeyError(clip_id)

    def ordered(self, selected_only: bool = True) -> list[Clip]:
        return [c for c in sorted(self.clips, key=lambda c: c.order) if c.selected or not selected_only]

    def active_consent(self, provider: str, scope: str = "selected_windows") -> ConsentRecord | None:
        for c in self.consent:
            if c.provider == provider and c.scope == scope and not c.revoked_at:
                return c
        return None


@dataclass
class MediaFile:
    """A file inside a clip directory, identified by its bytes."""
    path: str                 # relative to the clip directory
    sha256: str
    bytes: int
    duration: float | None = None
    kind: str = ""            # "wide", "preview_video", "audio"
    derived_from: dict | None = None   # {"path", "sha256"} of the file it was extracted from

    to_dict = asdict

    @classmethod
    def from_dict(cls, d):
        return None if d is None else _from_dict(cls, d)


@dataclass
class Window:
    """What fetching produced for a clip. Owned by window.json (fetch step only)."""
    clip_id: str
    source_start: float       # source seconds of window file t=0
    source_end: float
    pad_start: float          # seconds of padding before the planned start
    wide: MediaFile | None = None       # landscape window for rendering (None for legacy pilots 01/02)
    preview: MediaFile | None = None    # letterboxed 720x1280 preview (legacy Harmar input)
    audio: MediaFile | None = None      # audio-only artifact for paid transcription
    fetched_at: str = field(default_factory=now_iso)
    source_ref: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Window":
        w = _from_dict(cls, d)
        w.wide, w.preview, w.audio = (MediaFile.from_dict(d.get(k)) for k in ("wide", "preview", "audio"))
        return w


# --- paid transcription ----------------------------------------------------------------------------

# Attempt states, canonical names from docs/saas/job-state-machine.md section 3.
PREPARED = "PREPARED"                    # NOT_SUBMITTED: record exists, nothing sent
RESERVED = "RESERVED"                    # NOT_SUBMITTED: budgets + balance checked
UPLOAD_CREATED = "UPLOAD_CREATED"        # NOT_SUBMITTED: free upload slot created
UPLOADED = "UPLOADED"                    # NOT_SUBMITTED: bytes uploaded (free)
SUBMITTING = "SUBMITTING"                # committed BEFORE the charging POST
SUBMITTED = "SUBMITTED"                  # provider job id committed
POLLING = "POLLING"
COMPLETED = "COMPLETED"
PROVIDER_FAILED = "PROVIDER_FAILED"      # provider reported failure (refunded per docs) -> FAILED_SAFE_TO_RETRY
FAILED = "FAILED"                        # definite failure before the charge point -> FAILED_SAFE_TO_RETRY
UNKNOWN_SUBMISSION = "UNKNOWN_SUBMISSION"  # OUTCOME_UNKNOWN: the POST may have created a job -> NEEDS_RECONCILIATION
RECONCILED_NOT_CREATED = "RECONCILED_NOT_CREATED"  # operator verified no job/charge exists -> safe to retry

PRE_SUBMIT_STATES = {PREPARED, RESERVED, UPLOAD_CREATED, UPLOADED}
SAFE_TO_RETRY_STATES = {FAILED, PROVIDER_FAILED, RECONCILED_NOT_CREATED}
NEEDS_RECONCILIATION_STATES = {SUBMITTING, UNKNOWN_SUBMISSION}
IN_FLIGHT_STATES = {SUBMITTED, POLLING}


def retry_safety(state: str) -> str:
    """Founder vocabulary for an attempt state."""
    if state in PRE_SUBMIT_STATES:
        return "NOT_SUBMITTED"
    if state in SAFE_TO_RETRY_STATES:
        return "FAILED_SAFE_TO_RETRY"
    if state in NEEDS_RECONCILIATION_STATES:
        return "NEEDS_RECONCILIATION" if state == UNKNOWN_SUBMISSION else "SUBMITTING"
    if state in IN_FLIGHT_STATES:
        return "SUBMITTED"
    return state


@dataclass
class TranscriptionAttempt:
    """One paid submission of one exact artifact. Stored at clips/<clip_id>/transcription/<id>.json.

    The transcript is valid ONLY for the bytes whose sha256 is recorded here. A logical match
    (same clip/window/settings) with different bytes is an ArtifactMismatch, never a cache hit."""
    id: str
    clip_id: str
    provider: str
    state: str
    media: MediaFile
    options: dict
    logical_key: str = ""            # expected-artifact identity (source/window/recipe); not proof of payment
    seconds_estimate: int = 0
    balance_before: int | None = None
    provider_media_id: str | None = None
    provider_job_id: str | None = None
    submitted_at: str | None = None
    completed_at: str | None = None
    seconds_charged: int | None = None
    result_file: str | None = None   # <id>.result.json next to this record (raw provider response)
    error: str | None = None
    confirmed_by: str | None = None  # operator confirmation for this paid run
    consent_id: str | None = None
    origin: str = "pipeline"         # "pipeline" or "legacy_import"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    history: list[dict] = field(default_factory=list)
    reconciliation: dict | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["retry_safety"] = retry_safety(self.state)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TranscriptionAttempt":
        a = _from_dict(cls, d)
        a.media = MediaFile.from_dict(d["media"])
        return a

    def transition(self, state: str, note: str = "") -> None:
        self.history.append({"at": now_iso(), "from": self.state, "to": state, "note": note})
        self.state = state
        self.updated_at = now_iso()


@dataclass
class TranscriptRef:
    """What rendering consumes: a completed transcript plus the exact media whose timeline it describes."""
    attempt_id: str
    clip_id: str
    provider: str
    media: MediaFile
    raw: dict[str, Any]        # provider response ("segments", optional "words", ...)
