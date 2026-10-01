"""Tiny synthetic media and project fixtures built with ffmpeg lavfi (no creator content, no network)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from hayclips import jsonio
from hayclips.hashing import sha256_file
from hayclips.models import COMPLETED, Clip, MediaFile, Project, TranscriptionAttempt, Window, new_id
from hayclips.project import ProjectRepo, media_record
from hayclips.transcription.store import save_attempt


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True, timeout=120)


def video(path: Path, seconds: float = 6.0, size: str = "1280x720", audio: str = "noise", delay: float = 0.0) -> Path:
    """testsrc video with pink-noise audio ("noise"), silence ("silent") or no audio track ("none").

    `delay` shifts the audio later by that many seconds (for alignment-mismatch tests)."""
    args = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={seconds}"]
    if audio == "noise":
        args += ["-f", "lavfi", "-i", f"anoisesrc=d={seconds + delay}:c=pink:seed=7:a=0.3"]
    elif audio == "silent":
        args += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={seconds}"]
    filters = []
    if audio != "none":
        if delay:
            filters = ["-af", f"adelay={int(delay * 1000)}:all=1,atrim=0:{seconds}"]
        args += ["-map", "0:v", "-map", "1:a", *filters, "-c:a", "aac", "-ar", "48000", "-ac", "2"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-t", str(seconds), str(path)]
    _ff(*args)
    return path


def audio_from(src: Path, dst: Path) -> Path:
    _ff("-i", str(src), "-vn", "-ac", "1", "-c:a", "aac", "-b:a", "96k", str(dst))
    return dst


def segments_for(seconds: float, punctuated: bool = True) -> dict:
    """A fake Harmar response with word timestamps covering most of the clip."""
    texts = ["Բարև ձեզ", "սա փորձնական տեքստ է", "որը ստուգում է ենթագրերը", "և ժամանակները"]
    segs, words, t = [], [], 0.4
    step = (seconds - 1.0) / len(texts)
    for k, text in enumerate(texts):
        end = t + step - 0.2
        mark = "։" if punctuated and k % 2 == 1 else ""
        segs.append({"text": text + mark, "start": round(t, 3), "end": round(end, 3), "speaker": 1})
        ws = (text + mark).split()
        for i, w in enumerate(ws):
            a = t + (end - t) * i / len(ws)
            b = t + (end - t) * (i + 1) / len(ws)
            words.append({"text": w, "start": round(a, 3), "end": round(b, 3), "speaker": 1})
        t += step
    return {"status": "completed", "segments": segs, "words": words, "seconds_charged": int(seconds) + 1}


def make_project(root: Path, *, seconds: float = 6.0, size: str = "1280x720", audio: str = "noise",
                 response: dict | None = None, transcript_media: str = "derived", hook: str = "",
                 pad: float = 0.0) -> tuple[ProjectRepo, Clip]:
    """A one-clip project in the stable-id layout with a completed (fake) transcript.

    transcript_media: "derived" (audio.m4a extracted from wide.mp4, provenance recorded),
    "independent" (same audio, no provenance -> xcorr), "shifted" (audio 0.3 s late), or "wide"."""
    repo = ProjectRepo(root)
    project = Project(name=root.name, source={"kind": "test", "url": ""})
    clip = Clip(id=new_id("clp"), order=1, start=100.0, end=100.0 + seconds - 2 * pad, pad=pad, hook=hook,
                title="synthetic")
    project.clips.append(clip)
    repo.save(project)
    cdir = repo.clip_dir(clip.id)
    cdir.mkdir(parents=True)
    wide = video(cdir / "wide.mp4", seconds, size, audio)
    wide_rec = media_record(wide, "wide.mp4", "wide", seconds)
    if transcript_media == "wide":
        tmedia = wide_rec
    else:
        if transcript_media == "shifted":
            src = video(cdir / "shifted.mp4", seconds, size, audio, delay=0.3)
        else:
            src = wide
        a = audio_from(src, cdir / "audio.m4a")
        derived = {"path": "wide.mp4", "sha256": wide_rec.sha256} if transcript_media == "derived" else None
        tmedia = media_record(a, "audio.m4a", "audio", seconds, derived_from=derived)
    repo.save_window(Window(clip_id=clip.id, source_start=clip.start - pad, source_end=clip.end + pad,
                            pad_start=pad, wide=wide_rec, audio=tmedia if tmedia.kind == "audio" else None))
    att = TranscriptionAttempt(id=new_id("att"), clip_id=clip.id, provider="fake", state=COMPLETED,
                               media=tmedia, options={"timestamps": "word"}, origin="test")
    att.result_file = f"{att.id}.result.json"
    jsonio.write_json(repo.transcription_dir(clip.id) / att.result_file, response or segments_for(seconds))
    save_attempt(repo, att)
    return repo, clip


def media(path: Path, kind: str = "") -> MediaFile:
    return MediaFile(path=path.name, sha256=sha256_file(path), bytes=path.stat().st_size, kind=kind)
