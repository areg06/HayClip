"""Burn Harmar captions into a pilot folder's clips and rebuild its review page.

Usage: python3 burn_captions.py pilot-03 [--styles A,B,C] [--no-hook]
Reads clip_NN.mp4 (already 720x1280) and harmar/clip_NN/harmar_transcript.json; writes
clip_NN_<style>.ass/.mp4, clip_NN.srt and review.html. Styles come from research/caption-style.md:
  A  active word: 2-4 words per screen, spoken word yellow with a small pop (default)
  B  one-word punch: 1-2 big words just below the face
  C  karaoke line in a translucent box
Word times come from Harmar "words"; without them they are estimated from segment times by
character share (the review page says which).

Clips fetched with padding (a "pad" field in suggestions.json) are re-snapped to Harmar's sentence
edges: caption_start/caption_end are written back and can be edited by hand, then set
"snap": "manual" so later runs keep them. Audio is loudness-normalised to -14 LUFS / -1.5 dBTP.
An optional "hook" (founder-approved text, <=45 chars) is shown in the top band for the first 3 s.
"""
import argparse
import html
import json
import re
import subprocess
from pathlib import Path

import clipper as c

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style}
Style: Hook,Noto Sans Armenian Black,44,&H00FFFFFF,&H00FFFFFF,&H60000000,&H00000000,0,0,0,0,100,100,0,0,3,12,0,8,60,60,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
# Captions are centred on x=360 (symmetric 60 px margins). The text's bottom edge sits at
# CAPTION_BOTTOM: 930 of 1280 (73%) leaves ~100 px below the speaker's chin in the face crop
# (chin p90 y=724, max 763 on pilot-03) and stays above TikTok's and YouTube Shorts' bottom 25%
# (y=960) and organic Reels' caption/username block. It is inside Meta's stricter bottom-35% zone,
# which is written for ads; use --caption-bottom 830 for paid placements.
CAPTION_BOTTOM = 930
STYLES = {
    "A": "Style: A,Noto Sans Armenian Black,58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,0,0,0,0,100,100,0,0,1,5,2,2,60,60,{mv},1",
    "B": "Style: B,Noto Sans Armenian Black,76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,0,0,0,0,100,100,0,0,1,6,3,5,60,60,0,1",
    "C": "Style: C,Noto Sans Armenian SemiBold,46,&H00FFFFFF,&H00B4B4B4,&H80000000,&H00000000,0,0,0,0,100,100,0,0,3,10,0,2,60,60,{mv},1",
}
STYLE_NAMES = {"A": "active word", "B": "one-word punch", "C": "karaoke box"}
YELLOW = "&H00D7FF&"  # ASS is BGR: #FFD700
BREAK_AFTER = re.compile(r"[,։.:՝?!…]$")
TERMINAL = re.compile(r"[.!?։:]$")
SENTENCE_END = re.compile(r"[.!?։:…]$")


def ass_time(t):
    cs = round(t * 100)
    h, r = divmod(cs, 360000)
    m, r = divmod(r, 6000)
    s, cs = divmod(r, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def ass_text(text):
    # never .upper(): Python turns Eastern Armenian և into ԵՒ
    return text.replace("{", "(").replace("}", ")").replace("\\", "/")


def clean(text):
    return text.strip().lstrip("-").strip()


def words_of(resp):
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
    for s in resp["segments"]:
        ws = clean(s["text"]).split()
        a, b = float(s["start"]), float(s["end"])
        total, t = sum(len(w) for w in ws) or 1, a
        for k, w in enumerate(ws):
            nxt = t + (b - a) * len(w) / total
            out.append((t, nxt, w, k == 0 and s["text"].lstrip().startswith("-")))
            t = nxt
    return out, "estimated from segment times (not real word sync)"


def snap(segments, planned_start, planned_end, reach=4.0):
    """Clip-relative (start, end, note) on Harmar sentence edges nearest the planned cut."""
    segs = [(float(s["start"]), float(s["end"]), s["text"].strip(), s.get("speaker")) for s in segments]
    starts = [k for k, s in enumerate(segs)
              if k == 0 or TERMINAL.search(segs[k - 1][2]) or s[2].startswith("-") or s[0] - segs[k - 1][1] > 0.7]
    ends = [k for k, s in enumerate(segs) if TERMINAL.search(s[2]) and not s[2].endswith("...")]
    i = min(starts, key=lambda k: abs(segs[k][0] - planned_start))
    j = min((k for k in ends if k >= i), key=lambda k: abs(segs[k][1] - planned_end))
    notes = []
    if abs(segs[i][0] - planned_start) > reach or abs(segs[j][1] - planned_end) > reach:
        notes.append("snap moved more than 4 s from the planned cut; check it")
    # a short reaction from the other speaker right after the payoff is the "button"
    if j + 1 < len(segs):
        n = segs[j + 1]
        if n[3] != segs[j][3] and n[1] - n[0] < 2.0 and n[0] - segs[j][1] < 1.0 and TERMINAL.search(n[2]):
            j += 1
            notes.append(f"kept the reaction «{clean(n[2])}» as the ending")
    start = max(segs[i][0] - 0.15, segs[i - 1][1] + 0.02 if i else 0.0, 0.0)
    nxt = segs[j + 1][0] if j + 1 < len(segs) else segs[j][1] + 0.6
    end = max(min(segs[j][1] + 0.4, nxt - 0.05), segs[j][1] + 0.1)
    return round(start, 3), round(end, 3), "; ".join(notes)


def chunks(words, max_chars, max_words):
    """Group words into screens. Hard breaks: sentence end, a >0.5 s gap or a speaker turn. Inside that,
    screens are chosen by dynamic programming: even widths, commas preferred as break points and
    no lone short word left over."""
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


def events(style, words, length):
    ev, groups = [], {"A": (22, 4), "B": (14, 2), "C": (30, 6)}[style]
    screens = chunks(words, *groups)
    for n, ch in enumerate(screens):
        nxt = screens[n + 1][0][0] if n + 1 < len(screens) else length
        end = hold(ch, nxt, length)
        if style == "A":
            for k, (a, _, _, _) in enumerate(ch):
                b = ch[k + 1][0] if k + 1 < len(ch) else end
                text = " ".join(f"{{\\c{YELLOW}\\fscx112\\fscy112\\t(0,120,\\fscx100\\fscy100)}}{ass_text(w)}{{\\r}}"
                                if m == k else ass_text(w) for m, (_, _, w, _) in enumerate(ch))
                ev.append((a, b, "A", text))
        elif style == "B":
            text = " ".join(w for _, _, w, _ in ch)
            fs = min(76, int(76 * 16 / max(len(text), 1)))  # 18-char word -> 67
            ev.append((ch[0][0], end, "B", f"{{\\pos(360,{CAPTION_BOTTOM - 45})\\fs{fs}\\fscx80\\fscy80\\t(0,80,\\fscx106\\fscy106)"
                                             f"\\t(80,160,\\fscx100\\fscy100)}}{ass_text(text)}"))
        else:
            parts, t = [], ch[0][0]
            for a, b, w, _ in ch:
                parts.append(f"{{\\kf{max(1, round((b - t) * 100))}}}{ass_text(w)}")
                t = b
            ev.append((ch[0][0], end, "C", " ".join(parts)))
    return [e for e in ev if e[1] - e[0] >= 0.04]


def hook_event(hook):
    """Hook text in the empty top band for 0-3 s, split into two balanced lines if long."""
    words, lines = hook.split(), [hook]
    if len(hook) > 22 and len(words) > 1:
        best = min(range(1, len(words)), key=lambda k: abs(len(" ".join(words[:k])) - len(" ".join(words[k:]))))
        lines = [" ".join(words[:best]), " ".join(words[best:])]
    return (0.0, 3.0, "Hook", "{\\fad(150,200)}" + "\\N".join(ass_text(x) for x in lines))


def loudnorm(src, a, b):
    """Two-pass EBU R128 filter string measured on the trimmed range."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src),
                        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, check=True).stderr
    m = json.loads(r[r.rindex("{"):r.rindex("}") + 1])
    fade_out = max(b - a - 0.25, 0)
    return (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
            f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:"
            f"linear=true,aresample=48000,afade=t=in:d=0.03,afade=t=out:st={fade_out:.3f}:d=0.25")


VENV_PY = Path(__file__).resolve().parent / ".venv" / "bin" / "python"


def speaker_crop(pilot, name, cs):
    """(input, filter prefix, description, flags) for a 9:16 crop of clip_NN.wide.mp4, or None.

    The per-shot plan from reframe.py is cached in clip_NN.crop.json; edit a shot's "x" there by
    hand to fix a framing, then re-run. Times in the plan are relative to the padded clip.
    """
    wide = pilot / f"{name}.wide.mp4"
    if not wide.exists():
        return None
    cache = pilot / f"{name}.crop.json"
    if not cache.exists():
        if not VENV_PY.exists():
            return None
        out = subprocess.run([str(VENV_PY), str(Path(__file__).resolve().parent / "reframe.py"), str(wide)],
                             capture_output=True, text=True, check=True).stdout
        cache.write_text(out, encoding="utf-8")
    p = json.loads(cache.read_text(encoding="utf-8"))
    shots = p["shots"]
    flags = []
    for k, sh in enumerate(shots):
        end = shots[k + 1]["start"] if k + 1 < len(shots) else None
        where = f"shot at {sh['start'] - cs:.1f}s"
        if end is not None and end <= cs:
            continue
        if sh["face_hits"] == 0:
            flags.append(f"{where}: no face found, kept the previous crop")
        elif sh["max_faces"] > 1 or (sh["drift"] or 0) > 0.35:
            flags.append(f"{where}: {sh['max_faces']} faces / drift {sh['drift']}; check the framing")
    # nested if(): the crop switches hard at each camera cut (trimmed input starts at t=0)
    expr = str(shots[-1]["x"])
    for k in range(len(shots) - 2, -1, -1):
        expr = f"if(lt(t\\,{shots[k + 1]['start'] - cs:.3f})\\,{shots[k]['x']}\\,{expr})"
    filt = (f"crop={p['crop_w']}:{p['height']}:x={expr}:y=0,"
            "scale=720:1280:flags=lanczos,setsar=1,")
    used = [s for k, s in enumerate(shots) if (shots[k + 1]["start"] if k + 1 < len(shots) else 1e9) > cs]
    return wide.name, filt, f"speaker crop, {len(used)} camera shots (edit {cache.name} to adjust)", flags


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    return j["streams"][0]["width"], j["streams"][0]["height"], float(j["format"]["duration"])


def main():
    global CAPTION_BOTTOM
    ap = argparse.ArgumentParser()
    ap.add_argument("pilot", type=Path)
    ap.add_argument("--styles", default="A", help="comma list of A, B, C (first one is the main render)")
    ap.add_argument("--caption-bottom", type=int, default=CAPTION_BOTTOM,
                    help=f"y of the caption text's bottom edge in the 720x1280 frame (default {CAPTION_BOTTOM})")
    ap.add_argument("--no-hook", action="store_true", help="skip the top hook text even if suggestions have one")
    args = ap.parse_args()
    CAPTION_BOTTOM = args.caption_bottom
    styles = [s.strip().upper() for s in args.styles.split(",") if s.strip()]
    pilot = args.pilot.resolve()
    sug = json.loads((pilot / "suggestions.json").read_text(encoding="utf-8"))
    cards, problems = [], []
    for i, s in enumerate(sug, 1):
        name = f"clip_{i:02}"
        resp = json.loads((pilot / "harmar" / name / "harmar_transcript.json").read_text(encoding="utf-8"))["response"]
        full = c.duration_of(pilot / f"{name}.mp4")
        note = ""
        if "pad" in s and s.get("snap") != "manual":
            pad = s.get("pad_start", s["pad"])
            s["caption_start"], s["caption_end"], note = snap(resp["segments"], pad, pad + s["end"] - s["start"])
            s["snap"] = "auto"
        cs, ce = float(s.get("caption_start", 0.0)), float(s.get("caption_end") or full)
        length = ce - cs
        words, word_source = words_of(resp)
        words = [(a - cs, min(b, ce) - cs, w, t) for a, b, w, t in words if a >= cs - 0.05 and a < ce]
        segs = [c.Line(float(x["start"]) - cs, float(x["end"]) - cs, clean(x["text"])) for x in resp["segments"]
                if float(x["start"]) >= cs - 0.05 and float(x["start"]) < ce]
        c.clip_subtitles(segs, c.Clip(0, length, 0, ""), pilot / f"{name}.srt")
        audio = loudnorm(pilot / f"{name}.mp4", cs, ce)
        hook = (s.get("hook") or "").strip() if not args.no_hook else ""
        if len(hook) > 45:
            problems.append(f"{name}: hook longer than 45 characters")
        video_in, frame_filter, framing = f"{name}.mp4", "", "letterbox (no landscape window or no .venv)"
        crop = speaker_crop(pilot, name, cs)
        if crop:
            video_in, frame_filter, framing, flags = crop
            problems.extend(f"{name}: {f}" for f in flags)
        players = []
        for style in styles:
            ev = events(style, words, length) + ([hook_event(hook)] if hook else [])
            (pilot / f"{name}_{style}.ass").write_text(HEADER.format(style=STYLES[style].format(mv=1280 - CAPTION_BOTTOM)) + "".join(
                f"Dialogue: {1 if st == 'Hook' else 0},{ass_time(a)},{ass_time(b)},{st},,0,0,0,,{t}\n"
                for a, b, st, t in ev), encoding="utf-8")
            out = pilot / f"{name}_{style}.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{cs:.3f}", "-i",
                            video_in, "-t", f"{length:.3f}", "-vf", f"{frame_filter}ass={name}_{style}.ass",
                            "-af", audio, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                            out.name], check=True, cwd=pilot)
            w, h, d = probe(out)
            last_cue = max((e[1] for e in ev if e[2] != "Hook"), default=0)
            first_cue = min((e[0] for e in ev if e[2] != "Hook"), default=0)
            if (w, h) != (720, 1280) or abs(d - length) > 0.1 or last_cue > d + 0.01 or first_cue > 0.3:
                problems.append(f"{out.name}: {w}x{h}, {d:.2f}s (planned {length:.2f}s), "
                                f"cues {first_cue:.2f}-{last_cue:.2f}s")
            players.append(f'<figure><figcaption>{style} · {STYLE_NAMES[style]}</figcaption>'
                           f'<video controls preload="metadata" src="{out.name}"></video></figure>')
            print(f"{out.name}: {w}x{h}, {d:.2f}s, {len(ev)} events")
        s["transcript_source"] = f"Harmar, {word_source}, unreviewed"
        s["edits"] = {"source_start": round(s["start"] - s.get("pad_start", 0) + cs, 2),
                      "source_end": round(s["start"] - s.get("pad_start", 0) + ce, 2),
                      "trim_in_padded_clip": [cs, ce], "hook": hook or None,
                      "audio": "loudnorm -14 LUFS / -1.5 dBTP, 30 ms fade-in, 250 ms fade-out",
                      "snap_note": note or None, "framing": framing}
        first3 = " ".join(w for a, _, w, _ in words if a < 3.0)
        first_word = words[0][0] if words else 0
        yt = pilot / f"{name}.youtube.srt"
        ytxt = " / ".join(b.split("\n", 2)[2].replace("\n", " ")
                          for b in yt.read_text(encoding="utf-8").strip().split("\n\n")) if yt.exists() else ""
        lines = "".join(f"<li><code>{x.start:05.2f}</code> {html.escape(x.text)}</li>" for x in segs)
        src = s["edits"]
        cards.append(
            f'<article><h2>#{i} · {html.escape(s.get("title", ""))}</h2>'
            f'<p class="meta">Source {c.stamp(src["source_start"])}–{c.stamp(src["source_end"])} · {length:.1f} s · '
            f'first word at {first_word:.2f} s · {html.escape(s.get("pick_note", ""))}</p>'
            + (f'<p class="warn">{html.escape(note)}</p>' if note else "")
            + (f'<p>Hook (draft, needs founder approval): «{html.escape(hook)}»</p>' if hook else "")
            + f'<p class="meta">Framing: {html.escape(framing)}</p>'
            + f'<p>First 3 s: «{html.escape(first3)}»</p><div class="grid">{"".join(players)}</div>'
            f'<details><summary>Uncut padded clip ({full:.1f} s)</summary>'
            f'<video controls preload="none" src="{name}.mp4"></video></details>'
            f'<h3>Harmar ({html.escape(word_source)})</h3><ul>{lines}</ul>'
            + (f'<details><summary>YouTube auto-captions</summary><p>{html.escape(ytxt)}</p></details>' if ytxt else "")
            + f'<p><a href="{name}.srt">SRT (Harmar)</a></p></article>')
    (pilot / "suggestions.json").write_text(json.dumps(sug, ensure_ascii=False, indent=2), encoding="utf-8")
    source = html.escape(sug[0].get("source", "")) if sug else ""
    (pilot / "review.html").write_text(
        '<!doctype html><html lang="hy"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f'<title>HayClips · {pilot.name}</title><style>body{{font:17px/1.5 system-ui;background:#101827;color:#eef;'
        'max-width:1100px;margin:2rem auto;padding:1rem}article{background:#1d2b40;padding:1.5rem;margin:1rem 0;'
        'border-radius:14px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem}'
        'figure{margin:0}figcaption{color:#9ab}video{width:100%;max-height:75vh}a{color:#8bd8ff}'
        'ul{padding-left:1rem;list-style:none}code{color:#9ab;margin-right:.5rem}details{color:#bbc;margin:.5rem 0}'
        '.meta{color:#9ab}.warn{color:#ffcf66}</style>'
        f'<h1>HayClips · {pilot.name}</h1><p>{source}</p><p>Ձեռքով ստուգիր խոսքը, կտրվածքները և ենթագրերը։ '
        'Ընտրությունը վիրուսային տարածման կանխատեսում չէ։ Which caption style would you post, and how long would '
        'fixing it take?</p>' + "".join(cards) + '</html>', encoding="utf-8")
    for p in problems:
        print("CHECK:", p)


if __name__ == "__main__":
    main()
