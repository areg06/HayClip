"""Prototype: per-shot static speaker crop plan for a 16:9 podcast video.

Samples frames, splits into shots by histogram change, finds the largest face
per shot with OpenCV YuNet, and emits an FFmpeg crop x-expression.
Usage: python facetrack.py video.mp4 src_x src_y src_w src_h yunet.onnx
"""
import json
import sys

import cv2
import numpy as np

path, sx, sy, sw, sh, model = sys.argv[1], *map(int, sys.argv[2:6]), sys.argv[6]
cap = cv2.VideoCapture(path)
fps = cap.get(cv2.CAP_PROP_FPS)
step = max(1, round(fps / 5))  # ~5 samples per second
det = cv2.FaceDetectorYN.create(model, "", (sw, sh), 0.6, 0.3, 50)

samples, prev_hist, idx = [], None, 0
while True:
    ok = cap.grab()
    if not ok:
        break
    # histogram on every frame so shot boundaries are frame-accurate
    _, frame = cap.retrieve()
    roi = frame[sy:sy + sh, sx:sx + sw]
    small = cv2.resize(roi, (160, 90))
    hist = cv2.calcHist([cv2.cvtColor(small, cv2.COLOR_BGR2HSV)], [0, 1], None, [32, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    cut = prev_hist is not None and cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA) > 0.35
    prev_hist = hist
    if cut or idx % step == 0:
        _, faces = det.detect(roi)
        cx = None
        if faces is not None and len(faces):
            f = max(faces, key=lambda r: r[2] * r[3])
            cx = float(f[0] + f[2] / 2)
        samples.append({"t": idx / fps, "cut": bool(cut), "cx": cx})
    idx += 1

# group samples into shots; a shot's crop center is the median face x
shots, cur = [], []
for s in samples:
    if s["cut"] and cur:
        shots.append(cur)
        cur = []
    cur.append(s)
shots.append(cur)

crop_w = round(sh * 9 / 16) // 2 * 2
plan = []
for i, shot in enumerate(shots):
    xs = [s["cx"] for s in shot if s["cx"] is not None]
    center = float(np.median(xs)) if xs else sw / 2
    x = int(min(max(center - crop_w / 2, 0), sw - crop_w))
    start = 0.0 if i == 0 else shot[0]["t"]
    plan.append({"start": round(start, 2), "x": x, "face_hits": len(xs), "samples": len(shot)})

# nested if() expression: crop x switches hard at each shot boundary (a cut stays a cut)
expr = str(plan[-1]["x"])
for p in reversed(plan[:-1]):
    nxt = plan[plan.index(p) + 1]["start"]
    expr = f"if(lt(t\\,{nxt})\\,{p['x']}\\,{expr})"
print(json.dumps({"crop_w": crop_w, "shots": plan}, indent=1), file=sys.stderr)
print(expr)
