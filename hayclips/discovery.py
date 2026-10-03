"""Discovery transcript: a cheap, LOCAL, approximate transcript used only to find candidate moments.

It is never the final caption transcript and never involves a paid service. Final captions come from
the paid transcription of the clips the operator chose (hayclips.transcription), exactly as for YouTube.

Engines (HAYCLIPS_DISCOVERY_ENGINE):
  auto     faster-whisper if installed, else a clear error (default)
  whisper  faster-whisper on this machine (HAYCLIPS_DISCOVERY_MODEL, default "small"; int8 on CPU)
  fake     test engine: copies HAYCLIPS_DISCOVERY_FAKE_SRT, or writes synthetic Armenian cues
"""
from __future__ import annotations

import os
from pathlib import Path

from .errors import PipelineError
from .models import Line
from .selection import stamp

FAKE_LINES = ["Ո՞րն է քո ամենասիրած գիրքը։", "Իրականում սա շատ կարևոր հարց է մեր համար։",
              "[ծիծաղ] Լավ, հետո ինչ եղավ։", "Մի պատմություն պատմեմ, հիշում եմ այդ օրը։"]


def engine_name() -> str:
    name = os.environ.get("HAYCLIPS_DISCOVERY_ENGINE", "auto")
    if name not in ("auto", "whisper", "fake"):
        raise PipelineError(f"unknown discovery engine {name!r}")
    if name == "auto":
        try:
            import faster_whisper  # noqa: F401
            return "whisper"
        except ImportError:
            raise PipelineError("local discovery transcription is not installed on this computer",
                                hint="install it with `.venv/bin/pip install faster-whisper` (free, runs locally)") from None
    return name


def write_srt(lines: list[Line], path: Path) -> None:
    blocks = [f"{k}\n{stamp(l.start)} --> {stamp(l.end)}\n{l.text}\n" for k, l in enumerate(lines, 1)]
    path.write_text("\n".join(blocks), encoding="utf-8")


def discover(audio: Path, out: Path, duration: float, progress=lambda f, n: None) -> dict:
    """Write an approximate SRT for `audio` to `out`. Returns {engine, cues}."""
    name = engine_name()
    if name == "fake":
        src = os.environ.get("HAYCLIPS_DISCOVERY_FAKE_SRT")
        if src:
            out.write_text(Path(src).read_text(encoding="utf-8"), encoding="utf-8")
            return {"engine": "fake", "cues": out.read_text(encoding="utf-8").count("-->")}
        lines, t, k = [], 0.0, 0
        while t + 4 <= duration:
            lines.append(Line(t, t + 4, f"{FAKE_LINES[k % len(FAKE_LINES)]} {' '.join(['բառ'] * 6)}։"))
            t += 4.2
            k += 1
        write_srt(lines, out)
        return {"engine": "fake", "cues": len(lines)}
    from faster_whisper import WhisperModel
    model = WhisperModel(os.environ.get("HAYCLIPS_DISCOVERY_MODEL", "small"), device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio), language="hy", beam_size=1, vad_filter=True)
    lines = []
    for seg in segments:
        if seg.text.strip():
            lines.append(Line(float(seg.start), float(seg.end), seg.text.strip()))
            if duration:
                progress(min(seg.end / duration, 0.99), f"listening… {int(seg.end // 60)} of {int(duration // 60)} min")
    if not lines:
        raise PipelineError("no speech found in the uploaded video")
    write_srt(lines, out)
    return {"engine": "whisper", "cues": len(lines)}
