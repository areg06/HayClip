"""Caption parsing, sentence units and explainable candidate scoring (moved from clipper.py).

Scores are ranking heuristics for a human operator. They are NOT virality predictions.
Every candidate carries a `features` dict that shows how its score was built.
"""
from __future__ import annotations

import re
from pathlib import Path

from .errors import ValidationError
from .models import Candidate, Line, candidate_id

TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
SRT_BLOCK = re.compile(r"(?:^|\n)(?:\d+\s*\n)?\s*(\d\d:\d\d:\d\d[,.]\d{3})\s*-->\s*(\d\d:\d\d:\d\d[,.]\d{3})[^\n]*\n([\s\S]*?)(?=\n\s*\n|\Z)")

SENTENCE_SPLIT = re.compile(r"(?<=[.!?։…:])\s+")
TERMINAL = re.compile(r"[.!?։:]$")          # `՞` sits mid-sentence in Armenian, so it is not an end
UNFINISHED = re.compile(r"(\.\.\.|…|-)$")
TAG = re.compile(r"\[[^\]]*\]")

# Eastern Armenian colloquial connectives/back-channels that make a weak first line.
# Guessed word list: a native editor must check it (research/cutting-retention.md P4).
WEAK_STARTERS = {"ու", "բայց", "դե", "հա", "ըհը", "բան", "այսինքն", "որովհետև", "ուրեմն", "էսինքն",
                 "նույնն", "էլի", "էդ", "տենց", "դրա", "և", "իսկ", "որ", "էն", "այդ", "ըըը", "ըը"}
STRONG_WORDS = re.compile(r"երբեք|բոլոր|ամենա|ոչ ոք|իրականում|գաղտնիք|սխալ", re.IGNORECASE)
FILLER = re.compile(r"(?<![\w\u0531-\u058F])(ը+|ըհը|էէ+|ըմ+|մմ+)(?![\w\u0531-\u058F])")
NOISE_TAG = re.compile(r"\[(?!ծիծաղ)[^\]]*\]")


def seconds(timestamp: str) -> float:
    m = TIME_RE.fullmatch(timestamp.strip())
    if not m:
        raise ValueError(f"Invalid timestamp: {timestamp}")
    h, minute, sec, ms = map(int, m.groups())
    return h * 3600 + minute * 60 + sec + ms / 1000


def stamp(value: float) -> str:
    ms = round(value * 1000)
    h, rest = divmod(ms, 3_600_000)
    minute, rest = divmod(rest, 60_000)
    sec, milli = divmod(rest, 1000)
    return f"{h:02}:{minute:02}:{sec:02},{milli:03}"


def parse_srt(raw: str) -> list[Line]:
    raw = raw.lstrip("\ufeff").replace("\r\n", "\n")
    lines = []
    for m in SRT_BLOCK.finditer(raw):
        start, end = seconds(m.group(1)), seconds(m.group(2))
        clean = re.sub(r"<[^>]+>", "", m.group(3))
        clean = " ".join(clean.split())
        if end > start and clean:
            lines.append(Line(start, end, clean))
    if not lines:
        raise ValidationError("No timed subtitle cues found in the SRT file.",
                              hint="the source may have no Armenian captions; check the caption language")
    lines.sort(key=lambda x: x.start)
    # YouTube auto-captions roll: each cue stays up until the next-but-one starts.
    for a, b in zip(lines, lines[1:]):
        if a.start < b.start < a.end:
            a.end = b.start
    return lines


def load_srt(path: Path) -> list[Line]:
    return parse_srt(Path(path).read_text(encoding="utf-8-sig"))


def write_srt(lines: list[Line], start: float, end: float, path: Path) -> None:
    """Write the cues overlapping [start, end] re-timed to start at 0."""
    cues = []
    for line in lines:
        a, b = max(line.start, start), min(line.end, end)
        if b > a:
            cues.append(f"{len(cues)+1}\n{stamp(a-start)} --> {stamp(b-start)}\n{line.text}\n")
    Path(path).write_text("\n".join(cues), encoding="utf-8")


def sentence_units(lines: list[Line], gap: float = 0.7) -> list[Line]:
    """Regroup rolling caption cues into sentence-like units so clips start and end on sentence edges.

    Text inside a cue is timed by character share. A pause of `gap` seconds also closes a unit,
    because auto-captions are often unpunctuated.
    """
    pieces = []
    for line in lines:
        parts = [p for p in SENTENCE_SPLIT.split(line.text) if p.strip()]
        total, t = sum(len(p) for p in parts), line.start
        for k, part in enumerate(parts):
            nxt = line.end if k == len(parts) - 1 else t + (line.end - line.start) * len(part) / total
            pieces.append(Line(t, nxt, part))
            t = nxt
    units: list[Line] = []
    current: Line | None = None
    for piece in pieces:
        if current and piece.start - current.end < gap and not TERMINAL.search(TAG.sub("", current.text).strip()):
            current = Line(current.start, piece.end, f"{current.text} {piece.text}")
        else:
            if current:
                units.append(current)
            current = Line(piece.start, piece.end, piece.text)
        if len(current.text) > 400:  # never let an unpunctuated monologue become one unit
            units.append(current)
            current = None
    if current:
        units.append(current)
    return units


def first_word(text: str) -> str:
    m = re.match(r"[\s\-«\"]*([\w\u0531-\u058F]+)", TAG.sub("", text))
    return m.group(1).lower() if m else ""


def candidates(lines: list[Line], duration: float, minimum: float, maximum: float,
               skip_start: float = 0, skip_end: float = 0) -> list[Candidate]:
    """Rank coherent sentence windows. `lines` should come from sentence_units()."""
    output = []
    last_allowed = duration - skip_end
    for i, first in enumerate(lines):
        if first.start < skip_start:
            continue
        if first.start >= last_allowed:
            break
        for j in range(i, len(lines)):
            last = lines[j]
            length = last.end - first.start
            if length > maximum:
                break
            if length < minimum or last.end > last_allowed:
                continue
            words = " ".join(x.text for x in lines[i:j + 1])
            spoken = TAG.sub("", words)
            tokens = re.findall(r"[\w\u0531-\u058F]+", spoken, re.UNICODE)
            if len(tokens) < 16 or UNFINISHED.search(TAG.sub("", last.text).strip()):
                continue
            silence = sum(max(0, lines[k + 1].start - lines[k].end) for k in range(i, j))
            ending = 2.0 if TERMINAL.search(TAG.sub("", last.text).strip()) else 0.0
            opening_text = " ".join(x.text for x in lines[i:j + 1] if x.start < first.start + 3.0)
            question = 0.6 if re.search(r"[?՞]", opening_text) else 0.0
            specific = 0.3 if (re.search(r"\d|[A-Za-z]{3,}", opening_text) or STRONG_WORDS.search(opening_text)) else 0.0
            starter = first_word(first.text)
            weak = -0.8 if starter in WEAK_STARTERS else 0.0
            fillers = len(FILLER.findall(spoken)) + len(NOISE_TAG.findall(words))
            per_min = fillers / (length / 60)
            filler_penalty = max(0.0, per_min - 5) * 0.3
            laugh_count = words.count("[ծիծաղ]")
            laughs = min(laugh_count, 4) * 0.25
            pace = min(len(tokens) / max(length, 1), 3.0) / 3.0
            silence_penalty = min(silence, 8) / 4
            length_bonus = min(length / maximum, 1) * 0.4
            score = ending + question + specific + weak + pace + laughs - filler_penalty - silence_penalty
            score += length_bonus
            features = {
                "ending_sentence": ending, "opening_question": question, "opening_specific": specific,
                "weak_starter": weak, "weak_starter_word": starter if weak else None,
                "fillers_per_min": round(per_min, 2), "filler_penalty": round(-filler_penalty, 3),
                "laughs": laugh_count, "laugh_bonus": laughs, "pace": round(pace, 3),
                "silence_s": round(silence, 2), "silence_penalty": round(-silence_penalty, 3),
                "length_s": round(length, 2), "length_bonus": round(length_bonus, 3),
                "note": "ranking heuristic, not a virality prediction",
            }
            output.append(Candidate(id=candidate_id(first.start, last.end), start=first.start, end=last.end,
                                    score=round(score, 3), text=words, features=features))
    return sorted(output, key=lambda c: (-c.score, c.start))


def pick(clips: list[Candidate], count: int) -> list[Candidate]:
    """Greedy diversity: skip windows that overlap an already picked one by 35% or more."""
    selected: list[Candidate] = []
    for clip in clips:
        if all(max(0, min(clip.end, x.end) - max(clip.start, x.start)) /
               min(clip.end - clip.start, x.end - x.start) < 0.35 for x in selected):
            selected.append(clip)
            if len(selected) >= count:
                break
    return selected


def explain(c: Candidate) -> str:
    f = c.features
    parts = [f"ending {f['ending_sentence']:+.1f}", f"question {f['opening_question']:+.1f}",
             f"specific {f['opening_specific']:+.1f}"]
    if f.get("weak_starter_word"):
        parts.append(f"weak start «{f['weak_starter_word']}» {f['weak_starter']:+.1f}")
    parts += [f"pace {f['pace']:+.2f}", f"laughs {f['laughs']} {f['laugh_bonus']:+.2f}",
              f"fillers {f['fillers_per_min']}/min {f['filler_penalty']:+.2f}",
              f"silence {f['silence_s']}s {f['silence_penalty']:+.2f}", f"length {f['length_bonus']:+.2f}"]
    return ", ".join(parts)
