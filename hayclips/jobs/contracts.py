"""Job types and payload contracts shared by the web app (enqueue side) and the worker (execute side).

Every payload is validated on BOTH sides. The browser never supplies tool arguments: only these fields.

  import_captions      pool io    {}                                   inspect source, fetch free captions
  generate_candidates  pool cpu   {min_seconds, max_seconds, count, skip_start, skip_end}
  fetch_windows        pool io    {clip_ids: [..] | null}              download selected windows (+audio)
  transcribe           pool paid  {clip_ids: [..], confirmed_by: str}  PAID; one attempt; never auto-retried
  render               pool cpu   {clip_ids: [..] | null, styles: [A|B|C], caption_bottom: int, hook_enabled: bool}
"""
from __future__ import annotations

import re

from ..errors import ValidationError

CLIP_ID = re.compile(r"^clp_[0-9a-f]{10}$")
STYLES = {"A", "B", "C"}


def _num(p: dict, k: str, lo: float, hi: float, default: float) -> float:
    v = p.get(k, default)
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= v <= hi:
        raise ValidationError(f"{k} must be a number between {lo} and {hi}")
    return float(v)


def _clip_ids(p: dict, required: bool) -> list[str] | None:
    ids = p.get("clip_ids")
    if ids is None and not required:
        return None
    if not isinstance(ids, list) or not ids or len(ids) > 20 or not all(isinstance(i, str) and CLIP_ID.match(i) for i in ids):
        raise ValidationError("clip_ids must be a non-empty list of clip ids")
    return list(dict.fromkeys(ids))


def validate(job_type: str, payload: dict) -> dict:
    """Return a normalised payload or raise ValidationError. Unknown keys are rejected."""
    if not isinstance(payload, dict):
        raise ValidationError("payload must be an object")
    allowed = {
        "import_captions": set(),
        "generate_candidates": {"min_seconds", "max_seconds", "count", "skip_start", "skip_end"},
        "fetch_windows": {"clip_ids"},
        "transcribe": {"clip_ids", "confirmed_by"},
        "render": {"clip_ids", "styles", "caption_bottom", "hook_enabled"},
    }
    if job_type not in allowed:
        raise ValidationError(f"unknown job type {job_type!r}")
    extra = set(payload) - allowed[job_type]
    if extra:
        raise ValidationError(f"unexpected fields: {', '.join(sorted(extra))}")
    if job_type == "import_captions":
        return {}
    if job_type == "generate_candidates":
        out = {"min_seconds": _num(payload, "min_seconds", 5, 180, 25), "max_seconds": _num(payload, "max_seconds", 5, 180, 60),
               "count": int(_num(payload, "count", 1, 20, 6)), "skip_start": _num(payload, "skip_start", 0, 3600, 0),
               "skip_end": _num(payload, "skip_end", 0, 3600, 0)}
        if out["max_seconds"] < out["min_seconds"]:
            raise ValidationError("max_seconds must be >= min_seconds")
        return out
    if job_type == "fetch_windows":
        return {"clip_ids": _clip_ids(payload, required=False)}
    if job_type == "transcribe":
        who = payload.get("confirmed_by")
        if not isinstance(who, str) or not who.strip() or len(who) > 100:
            raise ValidationError("a paid transcription needs the name of the person confirming it")
        return {"clip_ids": _clip_ids(payload, required=True), "confirmed_by": who.strip()}
    styles = payload.get("styles", ["A"])
    if not isinstance(styles, list) or not styles or not set(styles) <= STYLES:
        raise ValidationError("styles must be a list of A, B, C")
    hook = payload.get("hook_enabled", True)
    if not isinstance(hook, bool):
        raise ValidationError("hook_enabled must be true or false")
    return {"clip_ids": _clip_ids(payload, required=False), "styles": sorted(set(styles)),
            "caption_bottom": int(_num(payload, "caption_bottom", 700, 1100, 930)), "hook_enabled": hook}
