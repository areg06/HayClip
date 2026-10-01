#!/usr/bin/env python3
"""Local, low-cost Armenian long-video-to-shorts prototype."""
from __future__ import annotations

import argparse
import hashlib
import html
import http.client
import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Line:
    start: float
    end: float
    text: str


@dataclass
class Clip:
    start: float
    end: float
    score: float
    text: str


TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
SRT_BLOCK = re.compile(r"(?:^|\n)(?:\d+\s*\n)?\s*(\d\d:\d\d:\d\d[,.]\d{3})\s*-->\s*(\d\d:\d\d:\d\d[,.]\d{3})[^\n]*\n([\s\S]*?)(?=\n\s*\n|\Z)")


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


def load_srt(path: Path) -> list[Line]:
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    lines = []
    for m in SRT_BLOCK.finditer(raw):
        start, end = seconds(m.group(1)), seconds(m.group(2))
        clean = re.sub(r"<[^>]+>", "", m.group(3))
        clean = " ".join(clean.split())
        if end > start and clean:
            lines.append(Line(start, end, clean))
    if not lines:
        raise ValueError("No timed subtitle cues found in the SRT file.")
    lines.sort(key=lambda x: x.start)
    # YouTube auto-captions roll: each cue stays up until the next-but-one starts.
    for a, b in zip(lines, lines[1:]):
        if a.start < b.start < a.end:
            a.end = b.start
    return lines


SENTENCE_END = re.compile(r"(?<=[.!?։…:])\s+")
TERMINAL = re.compile(r"[.!?։:]$")          # `՞` sits mid-sentence in Armenian, so it is not an end
UNFINISHED = re.compile(r"(\.\.\.|…|-)$")
TAG = re.compile(r"\[[^\]]*\]")


def sentence_units(lines: list[Line], gap: float = 0.7) -> list[Line]:
    """Regroup rolling caption cues into sentence-like units so clips start and end on sentence edges.

    Text inside a cue is timed by character share. A pause of `gap` seconds also closes a unit,
    because auto-captions are often unpunctuated.
    """
    pieces = []
    for line in lines:
        parts = [p for p in SENTENCE_END.split(line.text) if p.strip()]
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


# Eastern Armenian colloquial connectives/back-channels that make a weak first line.
# Guessed word list: a native editor must check it (research/cutting-retention.md P4).
WEAK_STARTERS = {"ու", "բայց", "դե", "հա", "ըհը", "բան", "այսինքն", "որովհետև", "ուրեմն", "էսինքն",
                 "նույնն", "էլի", "էդ", "տենց", "դրա", "և", "իսկ", "որ", "էն", "այդ", "ըըը", "ըը"}
STRONG_WORDS = re.compile(r"երբեք|բոլոր|ամենա|ոչ ոք|իրականում|գաղտնիք|սխալ", re.IGNORECASE)
FILLER = re.compile(r"(?<![\w\u0531-\u058F])(ը+|ըհը|էէ+|ըմ+|մմ+)(?![\w\u0531-\u058F])")
NOISE_TAG = re.compile(r"\[(?!ծիծաղ)[^\]]*\]")


def transcribe(video: Path, model_size: str) -> list[Line]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install optional transcription: pip install -r requirements.txt") from exc
    # Model downloads once on first run; afterwards processing stays local.
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(video), language="hy", beam_size=5, vad_filter=True)
    result = [Line(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]
    if not result:
        raise ValueError("No speech detected. Try a source with clear speech or use an SRT file.")
    return result


API_BASE = "https://api.harmar.ai"


def api_json(path: str, key: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(API_BASE + path, data=data,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not print headers or upload URLs; server errors can carry sensitive data.
        raise RuntimeError(f"Harmar API returned HTTP {exc.code} for {path}") from exc


def upload_to_signed_url(video: Path, url: str, content_type: str, attempts: int = 3) -> None:
    # Uploading is free (Harmar charges on transcript submit), so connection drops are retried.
    target = urllib.parse.urlparse(url)
    if target.scheme != "https" or not target.hostname:
        raise RuntimeError("Harmar did not return a valid HTTPS upload URL")
    for attempt in range(1, attempts + 1):
        conn = http.client.HTTPSConnection(target.hostname, target.port or 443, timeout=180)
        try:
            with video.open("rb") as stream:
                conn.request("PUT", target.path + ("?" + target.query if target.query else ""),
                             body=stream, headers={"Content-Type": content_type,
                                                   "Content-Length": str(video.stat().st_size)})
                response = conn.getresponse()
                response.read()
            if 200 <= response.status < 300:
                return
            if response.status < 500:
                raise RuntimeError(f"Harmar media upload returned HTTP {response.status}")
            problem = f"HTTP {response.status}"
        except (ConnectionError, TimeoutError, http.client.HTTPException) as exc:
            problem = type(exc).__name__
        finally:
            conn.close()
        print(f"Upload of {video.name} failed ({problem}); attempt {attempt}/{attempts}")
        if attempt < attempts:
            time.sleep(5 * attempt)
    raise RuntimeError(f"Harmar media upload failed after {attempts} attempts ({problem}); nothing was charged")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def harmar_transcribe(video: Path, out: Path, duration: float, max_seconds: int,
                      timestamps: str = "segment") -> list[Line]:
    """Use a cached completed response when possible, avoiding repeat charges."""
    fingerprint = file_hash(video)
    cache = out / "harmar_transcript.json"
    job_id = None
    if cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("video_sha256") != fingerprint:
            raise RuntimeError("Output folder has a transcript for another video; choose a new --out")
        result = saved.get("response")
        job_id = saved.get("job_id")
    else:
        result = None
    if result is None:
        key = os.environ.get("HARMAR_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Set HARMAR_API_KEY in your shell; never paste it into source code")
        if job_id is None:
            charged = math.ceil(duration)
            if charged > max_seconds:
                raise RuntimeError(f"Video needs about {charged}s; allowed maximum is {max_seconds}s")
            balance = api_json("/v1/balance", key)
            if int(balance.get("seconds_remaining", 0)) < charged:
                raise RuntimeError("Harmar balance is too low for this video")
            created = api_json("/v1/uploads", key,
                               {"filename": video.name, "file_size": video.stat().st_size})
            upload_to_signed_url(video, created["upload_url"], created["content_type"])
            job = api_json("/v1/transcripts", key,
                           {"media_id": created["media_id"], "source_lang": "hy",
                            "options": {"timestamps": timestamps, "punctuation": True}})
            job_id = job["id"]
            cache.write_text(json.dumps({"video_sha256": fingerprint, "job_id": job_id},
                                        ensure_ascii=False), encoding="utf-8")
        deadline = time.monotonic() + 15 * 60
        while time.monotonic() < deadline:
            result = api_json(f"/v1/transcripts/{urllib.parse.quote(job_id, safe='')}", key)
            if result.get("status") == "completed":
                break
            if result.get("status") == "failed":
                raise RuntimeError(f"Harmar transcription failed; job id: {job_id}")
            time.sleep(3)
        else:
            raise RuntimeError(f"Harmar job timed out; check job id before retrying: {job_id}")
        cache.write_text(json.dumps({"video_sha256": fingerprint, "job_id": job_id,
                                     "response": result},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [Line(float(s["start"]), float(s["end"]), s["text"].strip())
             for s in result.get("segments", []) if s.get("text", "").strip()]
    if not lines:
        raise RuntimeError("Harmar returned no timed segments")
    return lines


def first_word(text: str) -> str:
    m = re.match(r"[\s\-«\"]*([\w\u0531-\u058F]+)", TAG.sub("", text))
    return m.group(1).lower() if m else ""


def candidates(lines: list[Line], duration: float, minimum: float, maximum: float,
               skip_start: float = 0, skip_end: float = 0) -> list[Clip]:
    """Rank coherent sentence windows. Scores are heuristics, not virality predictions.

    `lines` should come from sentence_units(), so every window starts and ends on a unit edge.
    """
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
            # opening: reward a question, number, Latin-script name or strong claim; punish connectives
            opening_text = " ".join(x.text for x in lines[i:j + 1] if x.start < first.start + 3.0)
            opening = 0.0
            if re.search(r"[?՞]", opening_text):
                opening += 0.6
            if re.search(r"\d|[A-Za-z]{3,}", opening_text) or STRONG_WORDS.search(opening_text):
                opening += 0.3
            if first_word(first.text) in WEAK_STARTERS:
                opening -= 0.8
            fillers = len(FILLER.findall(spoken)) + len(NOISE_TAG.findall(words))
            filler_penalty = max(0.0, fillers / (length / 60) - 5) * 0.3
            laughs = min(words.count("[ծիծաղ]"), 4) * 0.25
            pace = min(len(tokens) / max(length, 1), 3.0) / 3.0
            score = ending + opening + pace + laughs - filler_penalty - min(silence, 8) / 4
            score += min(length / maximum, 1) * 0.4
            output.append(Clip(first.start, last.end, round(score, 3), words))
    return sorted(output, key=lambda c: (-c.score, c.start))


def pick(clips: list[Clip], count: int) -> list[Clip]:
    selected: list[Clip] = []
    for clip in clips:
        if all(max(0, min(clip.end, x.end) - max(clip.start, x.start)) /
               min(clip.end - clip.start, x.end - x.start) < 0.35 for x in selected):
            selected.append(clip)
            if len(selected) >= count:
                break
    return selected


def clip_subtitles(lines: list[Line], clip: Clip, path: Path) -> None:
    cues = []
    for line in lines:
        a, b = max(line.start, clip.start), min(line.end, clip.end)
        if b > a:
            cues.append(f"{len(cues)+1}\n{stamp(a-clip.start)} --> {stamp(b-clip.start)}\n{line.text}\n")
    path.write_text("\n".join(cues), encoding="utf-8")


def export(video: Path, clip: Clip, output: Path, crf: int = 23) -> None:
    # Fit full source image into a vertical frame; no cropping out guests or slides.
    filters = ("[0:v]split=2[bg][fg];"
               "[bg]scale=720:1280:force_original_aspect_ratio=increase,"
               "crop=720:1280,boxblur=20:1[blur];"
               "[fg]scale=720:1280:force_original_aspect_ratio=decrease[sharp];"
               "[blur][sharp]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(clip.start),
           "-i", str(video), "-t", str(clip.end - clip.start), "-filter_complex", filters,
           "-map", "[v]", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", str(crf), "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output)]
    subprocess.run(cmd, check=True)


def duration_of(video: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
                            capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def report(clips: list[Clip], directory: Path, rendered: bool) -> None:
    cards = []
    for i, c in enumerate(clips, 1):
        media = (f'<video controls preload="metadata" src="clip_{i:02}.mp4"></video>' if rendered else "")
        cards.append(f'<article><h2>#{i} · {stamp(c.start)}–{stamp(c.end)}</h2>{media}'
                     f'<p>{html.escape(c.text)}</p><a href="clip_{i:02}.srt">SRT</a></article>')
    directory.joinpath("review.html").write_text(
        '<!doctype html><html lang="hy"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>HayClips · review</title><style>body{font:18px/1.5 system-ui;background:#101827;color:#eef;'
        'max-width:850px;margin:2rem auto;padding:1rem}article{background:#1d2b40;padding:1.5rem;'
        'margin:1rem 0;border-radius:14px}video{width:100%}a{color:#8bd8ff}</style>'
        '<h1>HayClips · Ընտրած հատվածներ</h1><p>Ձեռքով ստուգիր խոսքը, կտրվածքները և ենթագրերը։ '
        'Գնահատականը վիրտուալության կանխատեսում չէ։</p>' + "".join(cards) + '</html>', encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Suggest and export Armenian video shorts locally.")
    parser.add_argument("video", type=Path, nargs="?",
                        help="Video you own or have permission to process (optional with --srt --no-render --duration)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--srt", type=Path, help="Existing timecoded Armenian subtitles")
    source.add_argument("--transcribe", action="store_true", help="Use local faster-whisper (model download on first run)")
    source.add_argument("--harmar", action="store_true", help="Use Harmar API; requires HARMAR_API_KEY")
    parser.add_argument("--model", default="small", help="Whisper model size; try medium for better accuracy")
    parser.add_argument("--max-api-seconds", type=int, default=600,
                        help="Maximum Harmar input length allowed per run (default: 600s)")
    parser.add_argument("--out", type=Path, default=Path("output"))
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--min-seconds", type=float, default=25)
    parser.add_argument("--max-seconds", type=float, default=60)
    parser.add_argument("--skip-start", type=float, default=0, help="Ignore the first N seconds (cold open, intro)")
    parser.add_argument("--skip-end", type=float, default=0, help="Ignore the last N seconds (outro)")
    parser.add_argument("--duration", type=float, help="Source length in seconds when no local video is given")
    parser.add_argument("--no-render", action="store_true", help="Find timestamps without encoding MP4s")
    args = parser.parse_args()
    if args.video is None and not (args.srt and args.no_render and args.duration):
        parser.error("Give a video, or use --srt with --no-render and --duration")
    if (args.video and not args.video.is_file()) or (args.srt and not args.srt.is_file()):
        parser.error("Input video or SRT file does not exist")
    if args.min_seconds <= 0 or args.max_seconds < args.min_seconds or args.count < 1:
        parser.error("Invalid length or count")
    if not shutil.which("ffprobe") or (not args.no_render and not shutil.which("ffmpeg")):
        parser.error("Install FFmpeg (ffmpeg and ffprobe) first")
    args.out.mkdir(parents=True, exist_ok=True)
    duration = args.duration or duration_of(args.video)
    if args.srt:
        lines = load_srt(args.srt)
    elif args.harmar:
        lines = harmar_transcribe(args.video, args.out, duration, args.max_api_seconds)
    else:
        lines = transcribe(args.video, args.model)
    units = sentence_units(lines)
    chosen = pick(candidates(units, duration, args.min_seconds, args.max_seconds,
                             args.skip_start, args.skip_end), args.count)
    if not chosen:
        parser.error("No clips found. Try --min-seconds 10 or use a longer video with more speech.")
    for i, clip in enumerate(chosen, 1):
        clip_subtitles(lines, clip, args.out / f"clip_{i:02}.srt")
        if not args.no_render:
            export(args.video, clip, args.out / f"clip_{i:02}.mp4")
    (args.out / "suggestions.json").write_text(json.dumps([asdict(c) for c in chosen],
                                                   ensure_ascii=False, indent=2), encoding="utf-8")
    report(chosen, args.out, not args.no_render)
    print(f"Created {len(chosen)} suggestions in {args.out.resolve()}; open review.html")


if __name__ == "__main__":
    main()
