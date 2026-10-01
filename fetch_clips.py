"""Download only the picked windows of a consented YouTube video and render padded 720x1280 clips.

Usage: python3 fetch_clips.py pilot-03
Reads suggestions.json (start, end, source, optional pad seconds, default 5). Each window is fetched
with `pad` seconds on both sides so burn_captions.py can re-snap the cut to Harmar's sentence edges
without a second Harmar job. clip_NN.mp4 is the letterboxed preview that goes to Harmar;
clip_NN.wide.mp4 keeps the landscape window (only this window, never the full episode) so
burn_captions.py can crop a full-frame 9:16 speaker shot from it.
"""
import json
import subprocess
import sys
from pathlib import Path

import clipper as c

pilot = Path(sys.argv[1]).resolve()
sug = json.loads((pilot / "suggestions.json").read_text(encoding="utf-8"))
captions = sorted((pilot / "source").glob("*.srt"))
lines = c.load_srt(captions[0]) if captions else []
for i, s in enumerate(sug, 1):
    name = f"clip_{i:02}"
    out = pilot / f"{name}.mp4"
    pad = float(s.setdefault("pad", 5.0))
    a, b = max(0.0, s["start"] - pad), s["end"] + pad
    s["pad_start"] = s["start"] - a          # actual padding before the planned start
    if lines:  # free YouTube captions for the planned window, for comparison in review.html
        c.clip_subtitles(lines, c.Clip(s["start"], s["end"], 0, ""), pilot / f"{name}.youtube.srt")
    wide = pilot / f"{name}.wide.mp4"      # landscape source window, kept for vertical reframing
    for attempt in range(3):  # section downloads sometimes drop mid-stream; retry from scratch
        if wide.exists():
            break
        for part in pilot.glob(f"{name}.wide*"):
            part.unlink()
        subprocess.run(["yt-dlp", "-q", "--no-warnings", "-f", "bv*[height<=1080]+ba/b[height<=1080]",
                        "--download-sections", f"*{a:.2f}-{b:.2f}", "--force-keyframes-at-cuts",
                        "--merge-output-format", "mp4", "-o", str(wide), s["source"]])
    else:
        if not wide.exists():
            raise SystemExit(f"{name}: section download failed 3 times")
    if abs(c.duration_of(wide) - (b - a)) > 0.5:
        raise SystemExit(f"{name}.wide.mp4 is {c.duration_of(wide):.2f}s, expected {b - a:.2f}s")
    if out.exists():   # never re-render: harmar/ caches are keyed to this file's hash
        print(f"{name}.mp4 exists; {wide.name} ready")
        continue
    c.export(wide, c.Clip(0, c.duration_of(wide), 0, ""), out, crf=18)
    print(f"{name}.mp4: {c.duration_of(out):.2f}s (source {c.stamp(a)}-{c.stamp(b)})")
(pilot / "suggestions.json").write_text(json.dumps(sug, ensure_ascii=False, indent=2), encoding="utf-8")
