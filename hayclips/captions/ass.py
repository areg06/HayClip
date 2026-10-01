"""ASS caption styles (research/caption-style.md), built as a string from explicit parameters.

  A  active word: 2-4 words per screen, spoken word yellow with a small pop (default)
  B  one-word punch: 1-2 big words just below the face
  C  karaoke line in a translucent box

Safety: all transcript and hook text goes through ass_text(), which collapses line breaks/tabs and
neutralises `{`, `}` and `\\`, so text can never add override tags or extra Dialogue lines.
Never `.upper()` Armenian text: Python turns Eastern Armenian և into ԵՒ.
"""
from __future__ import annotations

import re

from ..errors import ValidationError

CAPTION_BOTTOM = 930   # default y of the caption text's bottom edge in the 720x1280 frame
HOOK_MAX_CHARS = 45
STYLE_NAMES = {"A": "active word", "B": "one-word punch", "C": "karaoke box"}
YELLOW = "&H00D7FF&"  # ASS is BGR: #FFD700
BREAK_AFTER = re.compile(r"[,։.:՝?!…]$")
SENTENCE_END = re.compile(r"[.!?։:…]$")
GROUPS = {"A": (22, 4), "B": (14, 2), "C": (30, 6)}   # (max chars, max words) per screen
BASE_SIZE = {"A": 58, "C": 46}
LINE_BREAKS = re.compile(r"[ \t\r\n\f\v  \u0085]+")   # NBSP (U+00A0) from Harmar is kept

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style}
Style: Hook,{family} Black,44,&H00FFFFFF,&H00FFFFFF,&H60000000,&H00000000,0,0,0,0,100,100,0,0,3,12,0,8,60,60,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
STYLES = {
    "A": "Style: A,{family} Black,58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,0,0,0,0,100,100,0,0,1,5,2,2,60,60,{mv},1",
    "B": "Style: B,{family} Black,76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,0,0,0,0,100,100,0,0,1,6,3,5,60,60,0,1",
    "C": "Style: C,{family} SemiBold,46,&H00FFFFFF,&H00B4B4B4,&H80000000,&H00000000,0,0,0,0,100,100,0,0,3,10,0,2,60,60,{mv},1",
}


def ass_time(t: float) -> str:
    cs = round(t * 100)
    h, r = divmod(cs, 360000)
    m, r = divmod(r, 6000)
    s, cs = divmod(r, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def ass_text(text: str) -> str:
    text = LINE_BREAKS.sub(" ", text).strip()
    return text.replace("{", "(").replace("}", ")").replace("\\", "/")


def clean(text: str) -> str:
    return text.strip().lstrip("-").strip()


def validate_hook(hook: str) -> str:
    """Collapse whitespace; refuse (never truncate) creator-approved text over the limit."""
    hook = LINE_BREAKS.sub(" ", hook or "").strip()
    if len(hook) > HOOK_MAX_CHARS:
        raise ValidationError(f"hook is {len(hook)} characters; the limit is {HOOK_MAX_CHARS}",
                              hint="shorten it with `python -m hayclips edit <project> <clip_id> --hook ...`")
    return hook


def words_of(resp: dict) -> tuple[list[tuple], str]:
    """[(start, end, word, turn_start)] from Harmar words, else synthesised per segment."""
    if resp.get("words"):
        out, prev_speaker = [], None
        for w in resp["words"]:
            text = clean(w["text"])
            if text:
                turn = w["text"].lstrip().startswith("-") or w.get("speaker") != prev_speaker
                out.append((float(w["start"]), float(w["end"]), text, turn))
                prev_speaker = w.get("speaker")
        return out, "Harmar word timestamps"
    out = []
    for s in resp.get("segments", []):
        ws = clean(s["text"]).split()
        a, b = float(s["start"]), float(s["end"])
        total, t = sum(len(w) for w in ws) or 1, a
        for k, w in enumerate(ws):
            nxt = t + (b - a) * len(w) / total
            out.append((t, nxt, w, k == 0 and s["text"].lstrip().startswith("-")))
            t = nxt
    return out, "estimated from segment times (not real word sync)"


def chunks(words, max_chars, max_words):
    """Group words into screens. Hard breaks: sentence end, a >0.5 s gap or a speaker turn. Inside that,
    screens are chosen by dynamic programming: even widths, commas preferred as break points and
    no lone short word left over. A single word longer than max_chars becomes its own screen."""
    phrases, cur = [], []
    for w in words:
        if cur and (w[0] - cur[-1][1] > 0.5 or w[3]):
            phrases.append(cur)
            cur = []
        cur.append(w)
        if SENTENCE_END.search(w[2]):
            phrases.append(cur)
            cur = []
    if cur:
        phrases.append(cur)
    ideal = max_chars * 0.75
    out = []
    for ph in phrases:
        n = len(ph)
        best = [0.0] + [float("inf")] * n   # best[k]: cost of screening ph[:k]
        back = [0] * (n + 1)
        for k in range(1, n + 1):
            for j in range(max(0, k - max_words), k):
                screen = ph[j:k]
                width = len(" ".join(x[2] for x in screen))
                if width > max_chars and len(screen) > 1:
                    continue
                cost = ((width - ideal) / max_chars) ** 2
                if len(screen) == 1 and len(screen[0][2]) < 8 and max_words > 2:
                    cost += 0.6              # lone short word flashes by
                if k < n and BREAK_AFTER.search(screen[-1][2]):
                    cost -= 0.3              # break on a comma when there is a choice
                if best[j] + cost < best[k]:
                    best[k], back[k] = best[j] + cost, j
        screens, k = [], n
        while k:
            screens.append(ph[back[k]:k])
            k = back[k]
        out.extend(reversed(screens))
    return out


def hold(ch, nxt_start, length):
    """End of a screen: hold across small gaps, at least 0.25 s per word, never past the clip."""
    end = ch[-1][1]
    if nxt_start - end < 0.4:
        end = nxt_start
    return min(max(end, ch[0][0] + 0.25 * len(ch)), nxt_start, length)


def _fit(style: str, text: str) -> str:
    """Shrink an over-long single-word screen so it stays inside the frame (WrapStyle 2 never wraps)."""
    max_chars = GROUPS[style][0]
    if style in BASE_SIZE and len(text) > max_chars:
        return f"\\fs{int(BASE_SIZE[style] * max_chars / len(text))}"
    return ""


def events(style: str, words, length: float, caption_bottom: int = CAPTION_BOTTOM) -> list[tuple]:
    ev = []
    screens = chunks(words, *GROUPS[style])
    for n, ch in enumerate(screens):
        nxt = screens[n + 1][0][0] if n + 1 < len(screens) else length
        end = hold(ch, nxt, length)
        plain = " ".join(ass_text(w) for _, _, w, _ in ch)
        if style == "A":
            fit = _fit("A", plain)
            for k, (a, _, _, _) in enumerate(ch):
                b = ch[k + 1][0] if k + 1 < len(ch) else end
                text = " ".join(f"{{\\c{YELLOW}\\fscx112\\fscy112\\t(0,120,\\fscx100\\fscy100)}}{ass_text(w)}{{\\r}}"
                                if m == k else ass_text(w) for m, (_, _, w, _) in enumerate(ch))
                ev.append((a, b, "A", (f"{{{fit}}}" if fit else "") + text))
        elif style == "B":
            fs = min(76, int(76 * 16 / max(len(plain), 1)))  # 18-char word -> 67
            ev.append((ch[0][0], end, "B", f"{{\\pos(360,{caption_bottom - 45})\\fs{fs}\\fscx80\\fscy80\\t(0,80,\\fscx106\\fscy106)"
                                           f"\\t(80,160,\\fscx100\\fscy100)}}{plain}"))
        else:
            fit = _fit("C", plain)
            parts, t = [], ch[0][0]
            for a, b, w, _ in ch:
                parts.append(f"{{\\kf{max(1, round((b - t) * 100))}}}{ass_text(w)}")
                t = b
            ev.append((ch[0][0], end, "C", (f"{{{fit}}}" if fit else "") + " ".join(parts)))
    return [e for e in ev if e[1] - e[0] >= 0.04]


def hook_event(hook: str) -> tuple:
    """Hook text in the top band for 0-3 s, split into two balanced lines if long."""
    hook = validate_hook(hook)
    words, lines = hook.split(), [hook]
    if len(hook) > 22 and len(words) > 1:
        best = min(range(1, len(words)), key=lambda k: abs(len(" ".join(words[:k])) - len(" ".join(words[k:]))))
        lines = [" ".join(words[:best]), " ".join(words[best:])]
    return (0.0, 3.0, "Hook", "{\\fad(150,200)}" + "\\N".join(ass_text(x) for x in lines))


def build_ass(style: str, words, length: float, *, caption_bottom: int = CAPTION_BOTTOM, hook: str = "",
              family: str = "Noto Sans Armenian") -> tuple[str, list[tuple]]:
    """Return (ass_document, events). `words` are clip-relative (start, end, text, turn)."""
    if style not in STYLES:
        raise ValidationError(f"unknown caption style {style!r}; use A, B or C")
    if not 0 < caption_bottom <= 1280:
        raise ValidationError("caption_bottom must be within the 1280 px frame")
    ev = events(style, words, length, caption_bottom) + ([hook_event(hook)] if hook else [])
    doc = HEADER.format(style=STYLES[style].format(family=family, mv=1280 - caption_bottom), family=family) + "".join(
        f"Dialogue: {1 if st == 'Hook' else 0},{ass_time(a)},{ass_time(b)},{st},,0,0,0,,{t}\n" for a, b, st, t in ev)
    return doc, ev
