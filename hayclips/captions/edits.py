"""Operator corrections to transcript text, applied at render time. The machine transcript (the paid
result file) is never modified; corrections live in clips/<id>/transcript_edits.json and are audited.

Word timings are not edited: a corrected segment's new words are spread over the time span the
original words of that segment occupied (by character share), so captions stay in sync.
"""
from __future__ import annotations

import copy

from .. import jsonio
from ..errors import ValidationError
from ..models import now_iso
from .ass import LINE_BREAKS, clean

EDITS_FILE = "transcript_edits.json"
MAX_SEGMENT_CHARS = 500


def load_edits(cdir) -> dict:
    return jsonio.read_json(cdir / EDITS_FILE, default={"segments": {}, "history": []})


def save_edit(cdir, raw: dict, index: int, text: str, by: str) -> dict:
    segs = raw.get("segments", [])
    if not 0 <= index < len(segs):
        raise ValidationError("that caption line does not exist")
    text = LINE_BREAKS.sub(" ", text or "").strip()
    if not text:
        raise ValidationError("a caption line cannot be empty")
    if len(text) > MAX_SEGMENT_CHARS:
        raise ValidationError(f"a caption line is limited to {MAX_SEGMENT_CHARS} characters")
    edits = load_edits(cdir)
    original = clean(segs[index]["text"])
    key = str(index)
    if text == original:
        edits["segments"].pop(key, None)
    else:
        edits["segments"][key] = text
    edits["history"].append({"at": now_iso(), "by": by or "operator", "segment": index, "from": original, "to": text})
    jsonio.write_json(cdir / EDITS_FILE, edits)
    return edits


def apply_edits(raw: dict, edits: dict | None) -> dict:
    """A copy of the provider result with corrected segment texts and re-spread words."""
    if not edits or not edits.get("segments"):
        return raw
    out = copy.deepcopy(raw)
    segs = out.get("segments", [])
    words = out.get("words") or []
    for key, text in edits["segments"].items():
        i = int(key)
        if not 0 <= i < len(segs):
            continue
        seg = segs[i]
        a, b = float(seg["start"]), float(seg["end"])
        prefix = "- " if str(seg["text"]).lstrip().startswith("-") else ""
        seg["text"] = prefix + text
        if not words:
            continue
        inside = [w for w in words if a - 0.05 <= float(w["start"]) <= b + 0.05]
        if inside:
            a, b = float(inside[0]["start"]), float(inside[-1]["end"])
        speaker = inside[0].get("speaker") if inside else seg.get("speaker")
        tokens = text.split()
        total, t, new = sum(len(x) for x in tokens) or 1, a, []
        for k, tok in enumerate(tokens):
            nxt = t + (b - a) * len(tok) / total
            new.append({"text": (prefix if k == 0 else "") + tok, "start": round(t, 3), "end": round(nxt, 3), "speaker": speaker})
            t = nxt
        ids = {id(w) for w in inside}
        pos = next((k for k, w in enumerate(words) if id(w) in ids), None)
        rest = [w for w in words if id(w) not in ids]
        if pos is None:
            pos = next((k for k, w in enumerate(rest) if float(w["start"]) > a), len(rest))
        words = rest[:pos] + new + rest[pos:]
    if out.get("words"):
        out["words"] = sorted(words, key=lambda w: float(w["start"]))
    return out
