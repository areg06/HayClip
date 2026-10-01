"""Re-snap a planned cut to Harmar sentence edges inside the padded window.

Defined behaviour:
- normal case: start on the sentence start nearest the planned start, end on the sentence end
  (terminal punctuation, not "...") nearest the planned end; keep a short reaction from the other
  speaker as the ending "button";
- transcript without any sentence-ending punctuation: fall back to the nearest segment edges
  (never raise) and say so in the note;
- empty transcript: keep the planned cut and say so in the note.
All times are window-relative seconds. Returns (start, end, note).
"""
from __future__ import annotations

import re

TERMINAL = re.compile(r"[.!?։:]$")


def _clean(text: str) -> str:
    return text.strip().lstrip("-").strip()


def snap(segments: list[dict], planned_start: float, planned_end: float, reach: float = 4.0) -> tuple[float, float, str]:
    segs = [(float(s["start"]), float(s["end"]), s["text"].strip(), s.get("speaker"))
            for s in segments if str(s.get("text", "")).strip()]
    if not segs:
        return round(planned_start, 3), round(planned_end, 3), "empty transcript: kept the planned cut"
    notes = []
    starts = [k for k, s in enumerate(segs)
              if k == 0 or TERMINAL.search(segs[k - 1][2]) or s[2].startswith("-") or s[0] - segs[k - 1][1] > 0.7]
    if not any(TERMINAL.search(s[2]) for s in segs):
        starts = list(range(len(segs)))      # unpunctuated transcript: any segment edge is a candidate
    i = min(starts, key=lambda k: abs(segs[k][0] - planned_start))
    ends = [k for k, s in enumerate(segs) if k >= i and TERMINAL.search(s[2]) and not s[2].endswith("...")]
    if not ends:
        ends = list(range(i, len(segs)))
        notes.append("no sentence-ending punctuation: snapped to the nearest segment edges")
    j = min(ends, key=lambda k: abs(segs[k][1] - planned_end))
    if abs(segs[i][0] - planned_start) > reach or abs(segs[j][1] - planned_end) > reach:
        notes.append("snap moved more than 4 s from the planned cut; check it")
    # a short reaction from the other speaker right after the payoff is the "button"
    if j + 1 < len(segs):
        n = segs[j + 1]
        if n[3] != segs[j][3] and n[1] - n[0] < 2.0 and n[0] - segs[j][1] < 1.0 and TERMINAL.search(n[2]):
            j += 1
            notes.append(f"kept the reaction «{_clean(n[2])}» as the ending")
    start = max(segs[i][0] - 0.15, segs[i - 1][1] + 0.02 if i else 0.0, 0.0)
    nxt = segs[j + 1][0] if j + 1 < len(segs) else segs[j][1] + 0.6
    end = max(min(segs[j][1] + 0.4, nxt - 0.05), segs[j][1] + 0.1)
    return round(start, 3), round(end, 3), "; ".join(notes)
