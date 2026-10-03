#!/usr/bin/env python3
"""Fake yt-dlp for tests (set HAYCLIPS_YTDLP to this file). Never touches the network.

Env:
  FAKE_YTDLP_LOG       JSON-lines file; every invocation appends {"argv": [...]}
  FAKE_YTDLP_MODE      ok | sleep | fail_unavailable | fail_private | no_subs | flaky
  FAKE_YTDLP_DURATION  metadata duration in seconds (default 3600)
  FAKE_YTDLP_FILESIZE  metadata filesize_approx in bytes (default unset)
  FAKE_YTDLP_FAILS     flaky: number of download failures before succeeding (default 1)
  FAKE_YTDLP_COUNTER   flaky: counter file
  FAKE_YTDLP_SHORTEN   download writes a file this many seconds shorter than requested
  FAKE_YTDLP_NOAUDIO   download writes video without an audio stream
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
if os.environ.get("FAKE_YTDLP_LOG"):
    with open(os.environ["FAKE_YTDLP_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv}) + "\n")

mode = os.environ.get("FAKE_YTDLP_MODE", "ok")
url = argv[argv.index("--") + 1] if "--" in argv else ""
vid = url.rsplit("=", 1)[-1]


def opt(name):
    return argv[argv.index(name) + 1] if name in argv else None


if mode == "sleep":
    out = opt("-o")
    if out:
        Path(out + ".part").write_bytes(b"partial")
    time.sleep(30)
    sys.exit(0)
if mode == "fail_unavailable":
    print(f"ERROR: [youtube] {vid}: Video unavailable. This video has been removed by the uploader", file=sys.stderr)
    sys.exit(1)
if mode == "fail_private":
    print(f"ERROR: [youtube] {vid}: Sign in to confirm your age. This video may be inappropriate", file=sys.stderr)
    sys.exit(1)

if "-J" in argv:
    meta = {"id": vid, "title": "Fake episode", "duration": float(os.environ.get("FAKE_YTDLP_DURATION", "3600")),
            "automatic_captions": {"hy-orig": [{"ext": "srt"}], "hy": [{"ext": "srt"}]}, "subtitles": {}}
    if os.environ.get("FAKE_YTDLP_FILESIZE"):
        meta["filesize_approx"] = int(os.environ["FAKE_YTDLP_FILESIZE"])
    print(json.dumps(meta))
    sys.exit(0)

if "--skip-download" in argv:
    if mode == "no_subs":
        print(f"[info] There are no subtitles for the requested languages", file=sys.stderr)
        sys.exit(0)
    out = opt("-o")
    lang = opt("--sub-langs")
    custom = os.environ.get("FAKE_YTDLP_SRT_FILE")   # lets browser tests supply longer synthetic captions
    Path(f"{out}.{lang}.srt").write_text(
        Path(custom).read_text(encoding="utf-8") if custom else
        "1\n00:00:01,000 --> 00:00:03,000\nԲարև ձեզ։\n\n2\n00:00:03,000 --> 00:00:05,500\nԻնչպե՞ս եք։\n",
        encoding="utf-8")
    sys.exit(0)

section = opt("--download-sections")
if section:
    if mode == "flaky":
        counter = Path(os.environ["FAKE_YTDLP_COUNTER"])
        n = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(n + 1))
        if n < int(os.environ.get("FAKE_YTDLP_FAILS", "1")):
            out = opt("-o")
            Path(out + ".part").write_bytes(b"partial")
            print("ERROR: unable to download video data: HTTP Error 503: Service Unavailable", file=sys.stderr)
            sys.exit(1)
    m = re.fullmatch(r"\*(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", section)
    dur = float(m.group(2)) - float(m.group(1)) - float(os.environ.get("FAKE_YTDLP_SHORTEN", "0"))
    out = opt("-o")
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=25:duration={dur:.3f}"]
    if not os.environ.get("FAKE_YTDLP_NOAUDIO"):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={dur:.3f}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if not os.environ.get("FAKE_YTDLP_NOAUDIO"):
        cmd += ["-c:a", "aac", "-ac", "2"]
    cmd += ["-shortest", out]
    subprocess.run(cmd, check=True, timeout=60)
    sys.exit(0)

print("fake yt-dlp: unsupported invocation", file=sys.stderr)
sys.exit(2)
