"""Render caption-style preset samples from pilot-02/clip_02 (research only; does not touch pilot files).

Usage: python3 research/caption-samples/make_samples.py
Word timings here are SYNTHETIC: each clip_02.ass cue's time is shared across its words by
character count. A real implementation would use Harmar "timestamps": "word".
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SRC_ASS = ROOT / "pilot-02" / "clip_02.ass"
SRC_MP4 = ROOT / "pilot-02" / "clip_02.mp4"
SECONDS = 14  # sample length

HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

STYLES = {
    "A_active_word": "Style: A,Noto Sans Armenian Black,58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,0,0,0,0,100,100,0,0,1,5,2,2,48,96,450,1",
    "B_one_word_pop": "Style: B,Noto Sans Armenian Black,76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,0,0,0,0,100,100,0,0,1,6,3,5,48,96,0,1",
    "C_karaoke_box": "Style: C,Noto Sans Armenian SemiBold,46,&H00FFFFFF,&H00B4B4B4,&H80000000,&H00000000,0,0,0,0,100,100,0,0,3,10,0,2,48,96,450,1",
}
YELLOW = "&H00D7FF&"  # ASS is BGR: #FFD700


def t2s(t):
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def ass_time(t):
    cs = round(t * 100)
    h, r = divmod(cs, 360000)
    m, r = divmod(r, 6000)
    s, cs = divmod(r, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def synthetic_words():
    """[(start, end, word)] with per-word times shared by character count inside each cue."""
    words = []
    for line in SRC_ASS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        a, b, text = t2s(parts[1]), t2s(parts[2]), parts[9]
        ws = text.split()
        total = sum(len(w) for w in ws)
        t = a
        for w in ws:
            nxt = t + (b - a) * len(w) / total
            words.append((t, nxt, w))
            t = nxt
    return [w for w in words if w[0] < SECONDS]


def chunks(words, max_chars, max_words):
    """Group words; break after sentence/clause punctuation or on length."""
    out, cur = [], []
    for w in words:
        if cur and (len(" ".join(x[2] for x in cur + [w])) > max_chars or len(cur) >= max_words
                    or w[0] - cur[-1][1] > 0.5):
            out.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[,։.:՝?!]$", w[2]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def preset_a(words):
    """2-4 words per screen, active word yellow with a small scale pop; one event per word."""
    ev = []
    for ch in chunks(words, 22, 4):
        for k, (a, b, _) in enumerate(ch):
            end = ch[k + 1][0] if k + 1 < len(ch) else b
            text = " ".join(
                f"{{\\c{YELLOW}\\fscx112\\fscy112\\t(0,120,\\fscx100\\fscy100)}}{w}{{\\r}}" if j == k else w
                for j, (_, _, w) in enumerate(ch))
            ev.append(f"Dialogue: 0,{ass_time(a)},{ass_time(end)},A,,0,0,0,,{text}")
    return ev


def preset_b(words):
    """1-2 words, big, centred just below the face band, pop-in per event; long words shrink."""
    ev = []
    for ch in chunks(words, 14, 2):
        a, b = ch[0][0], ch[-1][1]
        text = " ".join(w for _, _, w in ch)
        fs = min(76, int(76 * 16 / len(text)))  # 18-char word -> 67
        ev.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},B,,0,0,0,,"
                  f"{{\\pos(336,790)\\fs{fs}\\fscx80\\fscy80\\t(0,80,\\fscx106\\fscy106)\\t(80,160,\\fscx100\\fscy100)}}{text}")
    return ev


def preset_c(words):
    """Full short line (<=30 chars) in a translucent box, native \\kf sweep from grey to white."""
    ev = []
    for ch in chunks(words, 30, 6):
        a, b = ch[0][0], ch[-1][1]
        text = " ".join(f"{{\\kf{max(1, round((we - ws) * 100))}}}{w}" for ws, we, w in ch)
        ev.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},C,,0,0,0,,{text}")
    return ev


def render(name, style, events, stills):
    ass = OUT / f"{name}.ass"
    ass.write_text(HEAD.format(styles=style) + "\n".join(events) + "\n", encoding="utf-8")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(SRC_MP4), "-t", str(SECONDS),
                    "-vf", f"ass={ass.name}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", f"{name}.mp4"],
                   check=True, cwd=OUT)
    for t in stills:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t}", "-i", f"{name}.mp4",
                        "-frames:v", "1", f"{name}_t{t}.png"], check=True, cwd=OUT)


words = synthetic_words()
render("A_active_word", STYLES["A_active_word"], preset_a(words), [1.2, 12.0])
render("B_one_word_pop", STYLES["B_one_word_pop"], preset_b(words), [1.2, 12.0])
render("C_karaoke_box", STYLES["C_karaoke_box"], preset_c(words), [1.2, 12.0])
print(f"{len(words)} synthetic words rendered into {OUT}")
