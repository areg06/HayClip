"""Render HayClips visual/audio research samples from a COPY of pilot-02/clip_02.mp4.

Research-only; not wired into clipper.py. Needs ffmpeg >= 7 (uses -/filter_complex).
The input copy is an already-rendered 720x1280 letterbox, so the 16:9 source band
(720x405 at y=437) is cropped back out first. With the original 1080p source the
speaker crop would be 608x1080 instead of 228x405 and far sharper.

Usage: python render_samples.py <facetrack-crop-x-expression-file>
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / "clip_02_original_copy.mp4"
BAND = "crop=720:405:0:437"          # recover the 16:9 source picture
CROP_W = 228                         # 405 * 9/16, even
X_EXPR = Path(sys.argv[1]).read_text().strip().replace("\\,", ",")  # quoted below, so no escapes
DUR = float(subprocess.check_output(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(SRC)]))

# Emphasis moments taken from the Harmar segment timings of this clip:
# 21.764 "... not trauma, but big growth" (contrast/punchline), 43.038 "that difficulty brings you to trauma".
# Each punch-in ends at the next camera cut or sentence start so the zoom never straddles a cut.
PUNCH = [(21.764, 26.12), (43.038, 45.618)]
PUSH_END = 21.764                    # slow 1.00 -> 1.06 push-in over the opening shot


def zoom_expr():
    punch = "+".join(f"between(t,{a},{b})" for a, b in PUNCH)
    return f"if({punch},1.15,if(lt(t,{PUSH_END}),1+0.06*t/{PUSH_END},1))"


def run(args, graph, out):
    gfile = HERE / f".{out}.graph"
    gfile.write_text(graph)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(SRC), *args,
           "-/filter_complex", str(gfile), "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(HERE / out)]
    subprocess.run(cmd, check=True)
    gfile.unlink()
    print("wrote", out)


def loudnorm_filter(target=-14.0, tp=-1.5, lra=11.0):
    # TP -1.5 not -1.0: the first render at -1.0 measured -0.6 dBTP after AAC encoding.
    """Two-pass EBU R128 loudnorm: measure, then apply linearly with measured values."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(SRC), "-af",
                        f"loudnorm=I={target}:TP={tp}:LRA={lra}:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = json.loads(p.stderr[p.stderr.rindex("{"):p.stderr.rindex("}") + 1])
    return (f"loudnorm=I={target}:TP={tp}:LRA={lra}:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
            f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
            f":offset={m['target_offset']}:linear=true")


speaker = f"[0:v]{BAND},crop={CROP_W}:405:'{X_EXPR}':0,scale=720:1280:flags=lanczos,setsar=1"
zoom = (f"scale=w='trunc(720*({zoom_expr()})/2)*2':h='trunc(1280*({zoom_expr()})/2)*2'"
        f":eval=frame:flags=bicubic,crop=720:1280:y='(in_h-out_h)*0.25'")
# y anchor at 25% height (roughly eye line) so zooms keep headroom instead of cropping the top of the head.
progress = f"drawbox=x=0:y=ih-8:w='iw*t/{DUR:.3f}':h=8:color=white@0.85:t=fill"
ln = loudnorm_filter()

# 1. Static per-shot speaker crop: crop x jumps only on the source's own camera cuts.
run([], f"{speaker}[v];[0:a]anull[a]", "01_speaker_crop_static.mp4")

# 2. Speaker crop + slow push-in on the opening shot + two 1.15x punch-ins on emphasis lines.
run([], f"{speaker},{zoom}[v];[0:a]anull[a]", "02_speaker_crop_punchin_115.mp4")

# 3. Current letterbox layout, audio only: two-pass loudnorm to -14 LUFS / -1 dBTP.
run([], f"[0:v]null[v];[0:a]{ln}[a]", "03_letterbox_loudnorm_-14.mp4")

# 4. Combined: speaker crop + zooms + progress bar + loudnorm + 30 ms de-click / 250 ms tail fade.
run([], f"{speaker},{zoom},{progress},fade=t=out:st={DUR - 0.25:.3f}:d=0.25[v];"
        f"[0:a]{ln},afade=t=in:d=0.03,afade=t=out:st={DUR - 0.25:.3f}:d=0.25[a]",
    "04_combined_crop_zoom_bar_loudnorm.mp4")

# 5. Music-ducking mechanics demo. The "music" is a synthetic pad (no licensed track here);
#    swap in a royalty-free file with -stream_loop -1 -i music.mp3 in real use.
pad = (f"sine=f=220:d={DUR},volume=0.5[s1];sine=f=277.18:d={DUR},volume=0.4[s2];"
       f"sine=f=329.63:d={DUR},volume=0.4[s3];[s1][s2][s3]amix=inputs=3,"
       "tremolo=f=0.25:d=0.3,aformat=sample_rates=48000:channel_layouts=stereo,volume=-18dB[music]")
run([], f"[0:v]null[v];{pad};[0:a]{ln},aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[voice][key];"
        "[music][key]sidechaincompress=threshold=0.01:ratio=20:attack=10:release=600:makeup=1[duck];"
        "[voice][duck]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]",
    "05_music_ducking_demo.mp4")
