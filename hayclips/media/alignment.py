"""Prove that the caption timeline (the media the transcript was made from) matches the render timeline.

Methods, in order:
  identical   the transcript media IS the render source (same sha256)
  provenance  the transcript media was extracted from the render source (derived_from.sha256 equals
              the render source sha256) and both have the same duration
  xcorr       decode both to 8 kHz mono and cross-correlate several windows; every window must agree
              on a lag within the tolerance with a clear correlation peak
If none succeeds, AlignmentError is raised and the clip must not be rendered.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .. import proc
from ..config import Settings, load_settings
from ..errors import AlignmentError
from ..models import MediaFile

RATE = 8000
MIN_PEAK = 0.5        # normalised correlation; pilot-03 preview vs wide measures ~1.0, unrelated audio < 0.2
WINDOW_S = 8.0
MAX_LAG_S = 1.0
SILENCE_RMS = 1e-4


@dataclass
class AlignmentResult:
    method: str
    lag_s: float
    confidence: float
    note: str

    to_dict = asdict


def _pcm(path: Path, settings: Settings):
    import numpy as np
    r = proc.run([settings.ffmpeg, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(RATE),
                  "-f", "f32le", "-"], timeout=settings.ffmpeg_timeout(600), text=False, tool="ffmpeg")
    return np.frombuffer(r.stdout, dtype=np.float32).astype(np.float64)


def _lag(a, b, start: int, n: int, max_lag: int):
    """Best lag (samples, b relative to a) and normalised peak for a[start:start+n] against b."""
    import numpy as np
    seg = a[start:start + n]
    lo, hi = max(0, start - max_lag), min(len(b), start + n + max_lag)
    ref = b[lo:hi]
    if len(seg) < n or len(ref) < n or np.sqrt(np.mean(seg ** 2)) < SILENCE_RMS:
        return None
    seg = seg - seg.mean()
    size = 1 << (len(seg) + len(ref)).bit_length()
    corr = np.fft.irfft(np.fft.rfft(ref, size) * np.conj(np.fft.rfft(seg, size)), size)[:len(ref) - len(seg) + 1]
    # normalise by the energy of each sliding window of ref
    csum = np.concatenate(([0.0], np.cumsum(ref ** 2)))
    energy = np.sqrt((csum[len(seg):] - csum[:-len(seg)])[:len(corr)] * np.sum(seg ** 2)) + 1e-12
    norm = corr / energy
    k = int(np.argmax(norm))
    return (lo + k) - start, float(norm[k])


def verify_alignment(transcript_path: Path, transcript_media: MediaFile, render_path: Path, render_media: MediaFile,
                     *, tolerance_s: float = 0.040, settings: Settings | None = None) -> AlignmentResult:
    s = settings or load_settings()
    if transcript_media.sha256 == render_media.sha256:
        return AlignmentResult("identical", 0.0, 1.0, "captions and render use the same file")
    df = transcript_media.derived_from or {}
    if df.get("sha256") == render_media.sha256:
        dt, dr = transcript_media.duration, render_media.duration
        if dt is not None and dr is not None and abs(dt - dr) <= tolerance_s:
            return AlignmentResult("provenance", 0.0, 1.0, "transcribed audio was extracted from this render source")
    try:
        a, b = _pcm(transcript_path, s), _pcm(render_path, s)
    except proc.ToolError as exc:
        raise AlignmentError(f"cannot decode audio to check alignment: {exc.message}",
                             hint="rendering stopped for this clip") from exc
    n = int(WINDOW_S * RATE)
    usable = min(len(a), len(b))
    if usable < n:
        raise AlignmentError("clip too short or without audio to verify alignment by audio",
                             hint="rendering stopped for this clip")
    starts = [int(f * (usable - n)) for f in (0.15, 0.5, 0.85)]
    results = [r for r in (_lag(a, b, st, n, int(MAX_LAG_S * RATE)) for st in starts) if r is not None]
    if len(results) < 2:
        raise AlignmentError("audio is silent: alignment cannot be verified by audio",
                             hint="rendering stopped for this clip; re-extract the transcribed audio from the render source")
    lags = [lag / RATE for lag, _ in results]
    peaks = [p for _, p in results]
    worst = max(lags, key=abs)
    if min(peaks) < MIN_PEAK or max(abs(x) for x in lags) > tolerance_s or max(lags) - min(lags) > tolerance_s:
        raise AlignmentError(
            f"caption timeline does not match the render source: lag {worst * 1000:+.0f} ms "
            f"(windows {', '.join(f'{x * 1000:+.0f}' for x in lags)} ms), peak {min(peaks):.2f}",
            hint=f"tolerance is ±{tolerance_s * 1000:.0f} ms; rendering stopped for this clip. "
                 "Check that wide.mp4 and the transcribed file cover the same source window")
    return AlignmentResult("xcorr", round(worst, 4), round(min(peaks), 3),
                           f"audio cross-correlation over {len(results)} windows")
