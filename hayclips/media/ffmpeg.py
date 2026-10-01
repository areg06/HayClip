"""ffmpeg operations used by fetching and rendering. All calls go through hayclips.proc.

Filter strings are built only from validated numbers and fixed file names that we choose; no user
text ever reaches a filter string (captions go through an .ass file run with cwd=render dir).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .. import proc
from ..config import Settings, load_settings
from ..errors import ValidationError
from .probe import Probe, probe

SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
LETTERBOX = ("[0:v]split=2[bg][fg];"
             "[bg]scale=720:1280:force_original_aspect_ratio=increase,"
             "crop=720:1280,boxblur=20:1[blur];"
             "[fg]scale=720:1280:force_original_aspect_ratio=decrease[sharp];"
             "[blur][sharp]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")


def export_letterbox(src: Path, out: Path, start: float, length: float, crf: int = 23,
                     settings: Settings | None = None) -> None:
    """Fit the full source image into 720x1280 over a blurred copy (the legacy preview layout)."""
    s = settings or load_settings()
    proc.run([s.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{float(start):.3f}",
              "-i", str(src), "-t", f"{float(length):.3f}", "-filter_complex", LETTERBOX,
              "-map", "[v]", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
              "-crf", str(int(crf)), "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)],
             timeout=s.ffmpeg_timeout(length), outputs=[out], tool="ffmpeg")


@dataclass
class AudioPlan:
    filter: str | None        # None when the source has no audio stream
    note: str                 # human-readable description for the review page
    measured_lufs: float | None


SILENT_LUFS = -70.0   # below this the loudnorm gain would only amplify noise


def plan_audio(src: Path, start: float, end: float, settings: Settings | None = None) -> AudioPlan:
    """Two-pass EBU R128 loudness (-14 LUFS / -1.5 dBTP) for [start, end] of src.

    - no audio stream: video-only render, note "no audio";
    - silent audio (measured -inf or below -70 LUFS): no normalisation, fades only, note says so."""
    s = settings or load_settings()
    length = end - start
    fade_out = max(length - 0.25, 0)
    fades = f"afade=t=in:d=0.03,afade=t=out:st={fade_out:.3f}:d=0.25"
    if not probe(src, s).has_audio:
        return AudioPlan(None, "no audio stream: rendered video-only", None)
    r = proc.run([s.ffmpeg, "-hide_banner", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
                  "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                 timeout=s.ffmpeg_timeout(length), tool="ffmpeg").stderr
    try:
        m = json.loads(r[r.rindex("{"):r.rindex("}") + 1])
        measured = float(m["input_i"])
    except (ValueError, KeyError) as exc:
        raise ValidationError("could not read the loudness measurement from ffmpeg") from exc
    if math.isinf(measured) or measured < SILENT_LUFS:
        return AudioPlan(f"aresample=48000,{fades}",
                         "silent audio: loudness not normalised (fades only)", None if math.isinf(measured) else measured)
    for k in ("input_tp", "input_lra", "input_thresh", "target_offset"):
        float(m[k])   # every value placed in the filter is a validated number
    filt = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
            f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:"
            f"linear=true,aresample=48000,{fades}")
    return AudioPlan(filt, "loudnorm -14 LUFS / -1.5 dBTP, 30 ms fade-in, 250 ms fade-out", measured)


def render_clip(*, src: Path, out: Path, ass_name: str, start: float, length: float, frame_filter: str,
                audio: AudioPlan, cwd: Path, settings: Settings | None = None) -> None:
    """Render [start, start+length] of src with a frame filter prefix and burned-in ASS captions.

    `ass_name` and `out.name` must be plain file names inside `cwd` (validated)."""
    s = settings or load_settings()
    if not SAFE_NAME.match(ass_name) or not SAFE_NAME.match(out.name):
        raise ValidationError(f"unsafe file name for the ffmpeg filter: {ass_name!r}")
    ass = f"ass={ass_name}"
    if s.fonts_dir is not None:
        ass += f":fontsdir={s.fonts_dir}"
    args = [s.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src),
            "-t", f"{length:.3f}", "-vf", f"{frame_filter}{ass}"]
    if audio.filter is None:
        args += ["-an"]
    else:
        args += ["-af", audio.filter, "-c:a", "aac", "-b:a", "160k"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", out.name]
    proc.run(args, timeout=s.ffmpeg_timeout(length), cwd=cwd, outputs=[out], tool="ffmpeg")


def check_output(path: Path, length: float, settings: Settings | None = None) -> tuple[Probe, list[str]]:
    p = probe(path, settings)
    problems = []
    if (p.width, p.height) != (720, 1280):
        problems.append(f"{path.name}: {p.width}x{p.height}, expected 720x1280")
    if abs(p.duration - length) > 0.1:
        problems.append(f"{path.name}: duration {p.duration:.2f}s, planned {length:.2f}s")
    return p, problems
