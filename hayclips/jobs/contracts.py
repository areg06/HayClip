"""Job types and payload contracts shared by the web app (enqueue side) and the worker (execute side).

Every payload is validated on BOTH sides. The browser never supplies tool arguments: only these fields.

  import_captions      io    {then?}                               inspect a YouTube source, fetch free captions
  discover_transcript  cpu   {then?}                               uploaded source: cheap LOCAL transcript for finding
                                                                   moments only (never the final captions, never paid)
  generate_candidates  cpu   {min_seconds, max_seconds, count, skip_start, skip_end, more?}
  preview_candidate    io    {candidate_id}                        low-res preview of one candidate window (free)
  fetch_windows        io    {clip_ids: [..] | null, then?, confirmed_by?}
  transcribe           paid  {clip_ids: [..], confirmed_by: str, then?}   PAID; one attempt; never auto-retried
  render               cpu   {clip_ids: [..] | null, styles: [A|B|C|L], caption_bottom: int, hook_enabled: bool, use_look?}

`then` chains the next steps after success (the worker enqueues them). A chain that reaches the paid
`transcribe` step must carry `confirmed_by`, the name the operator typed when confirming that exact set of
clips; the chain never adds clips.
"""
from __future__ import annotations

import re

from ..errors import ValidationError

CLIP_ID = re.compile(r"^clp_[0-9a-f]{10}$")
CAND_ID = re.compile(r"^cand_[0-9a-f]{10}$")
STYLES = {"A", "B", "C", "L"}
NEXT = {   # allowed `then` chains per job type
    "import_captions": (["generate_candidates"],),
    "discover_transcript": (["generate_candidates"],),
    "fetch_windows": (["transcribe", "render"], ["render"]),
    "transcribe": (["render"],),
}


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


def _who(p: dict) -> str:
    who = p.get("confirmed_by")
    if not isinstance(who, str) or not who.strip() or len(who) > 100:
        raise ValidationError("a paid transcription needs the name of the person confirming it")
    return who.strip()


def _then(job_type: str, p: dict) -> list[str] | None:
    then = p.get("then")
    if then is None:
        return None
    if not isinstance(then, list) or then not in [list(x) for x in NEXT.get(job_type, ())]:
        raise ValidationError(f"{job_type} cannot be followed by {then!r}")
    return list(then)


def validate(job_type: str, payload: dict) -> dict:
    """Return a normalised payload or raise ValidationError. Unknown keys are rejected."""
    if not isinstance(payload, dict):
        raise ValidationError("payload must be an object")
    allowed = {
        "import_captions": {"then"},
        "discover_transcript": {"then"},
        "generate_candidates": {"min_seconds", "max_seconds", "count", "skip_start", "skip_end", "more"},
        "preview_candidate": {"candidate_id"},
        "fetch_windows": {"clip_ids", "then", "confirmed_by"},
        "transcribe": {"clip_ids", "confirmed_by", "then"},
        "render": {"clip_ids", "styles", "caption_bottom", "hook_enabled", "use_look"},
    }
    if job_type not in allowed:
        raise ValidationError(f"unknown job type {job_type!r}")
    extra = set(payload) - allowed[job_type]
    if extra:
        raise ValidationError(f"unexpected fields: {', '.join(sorted(extra))}")
    out: dict = {}
    if job_type in ("import_captions", "discover_transcript"):
        then = _then(job_type, payload)
        return {"then": then} if then else {}
    if job_type == "generate_candidates":
        out = {"min_seconds": _num(payload, "min_seconds", 5, 180, 25), "max_seconds": _num(payload, "max_seconds", 5, 180, 60),
               "count": int(_num(payload, "count", 1, 20, 6)), "skip_start": _num(payload, "skip_start", 0, 3600, 0),
               "skip_end": _num(payload, "skip_end", 0, 3600, 0)}
        if out["max_seconds"] < out["min_seconds"]:
            raise ValidationError("max_seconds must be >= min_seconds")
        if payload.get("more") is not None:
            if not isinstance(payload["more"], bool):
                raise ValidationError("more must be true or false")
            if payload["more"]:
                out["more"] = True
        return out
    if job_type == "preview_candidate":
        cid = payload.get("candidate_id")
        if not isinstance(cid, str) or not CAND_ID.match(cid):
            raise ValidationError("candidate_id must be a candidate id")
        return {"candidate_id": cid}
    if job_type == "fetch_windows":
        out = {"clip_ids": _clip_ids(payload, required=False)}
        then = _then(job_type, payload)
        if then:
            out["then"] = then
            if then[0] == "transcribe":
                out["confirmed_by"] = _who(payload)
                if out["clip_ids"] is None:
                    raise ValidationError("a chain that transcribes must list its clips")
        elif "confirmed_by" in payload:
            raise ValidationError("confirmed_by is only valid for a chain that transcribes")
        return out
    if job_type == "transcribe":
        out = {"clip_ids": _clip_ids(payload, required=True), "confirmed_by": _who(payload)}
        then = _then(job_type, payload)
        if then:
            out["then"] = then
        return out
    styles = payload.get("styles", ["A"])
    if not isinstance(styles, list) or not styles or not set(styles) <= STYLES:
        raise ValidationError("styles must be a list of A, B, C, L")
    hook = payload.get("hook_enabled", True)
    if not isinstance(hook, bool):
        raise ValidationError("hook_enabled must be true or false")
    out = {"clip_ids": _clip_ids(payload, required=False), "styles": sorted(set(styles)),
           "caption_bottom": int(_num(payload, "caption_bottom", 700, 1100, 930)), "hook_enabled": hook}
    if payload.get("use_look") is not None:
        if not isinstance(payload["use_look"], bool):
            raise ValidationError("use_look must be true or false")
        if payload["use_look"]:
            out["use_look"] = True
    return out


def next_job(job_type: str, payload: dict) -> tuple[str, dict] | None:
    """The job a chain enqueues after `job_type` succeeded, or None."""
    then = payload.get("then")
    if not then:
        return None
    nxt, rest = then[0], then[1:]
    ids = payload.get("clip_ids")
    if nxt == "generate_candidates":
        p: dict = {}
    elif nxt == "transcribe":
        p = {"clip_ids": ids, "confirmed_by": payload["confirmed_by"]}
    elif nxt == "render":
        p = {"clip_ids": ids, "styles": ["A"], "use_look": True}
    else:
        raise ValidationError(f"unknown chain step {nxt!r}")
    if rest:
        p["then"] = rest
    return nxt, validate(nxt, p)
