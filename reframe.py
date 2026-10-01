"""Plan a full-frame 9:16 speaker crop for a landscape podcast clip (runs in .venv, needs OpenCV).

Usage: .venv/bin/python reframe.py clip_01.wide.mp4 > clip_01.crop.json
Splits the clip into camera shots (HSV histogram change on every frame), finds the largest face
~5 times per second with YuNet, and gives each shot one static crop centred on the median face.
The crop only moves on the source's own camera cuts, so it never adds motion the editor didn't make.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

MODEL = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"
DETECT_W = 960  # faces are found on a half-size 1080p frame


def plan(path):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    scale = DETECT_W / w
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (DETECT_W, round(h * scale)), 0.6, 0.3, 20)
    step = max(1, round(fps / 5))
    diffs, faces_at, prev, idx = [], {}, None, 0
    while cap.grab():
        _, frame = cap.retrieve()
        small = cv2.resize(frame, (160, 90))
        hist = cv2.calcHist([cv2.cvtColor(small, cv2.COLOR_BGR2HSV)], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        diffs.append(0.0 if prev is None else cv2.compareHist(prev, hist, cv2.HISTCMP_BHATTACHARYYA))
        prev = hist
        if idx % step == 0:
            _, faces = det.detect(cv2.resize(frame, (DETECT_W, round(h * scale))))
            if faces is not None and len(faces):
                f = max(faces, key=lambda r: r[2] * r[3])
                faces_at[idx] = (float(f[0] + f[2] / 2) / scale, len(faces))
            else:
                faces_at[idx] = (None, 0)
        idx += 1
    # a cut is a big colour change, or the face jumping sideways between two samples
    # (same person, same curtain, new camera: the histogram barely moves)
    cuts = {k for k, d in enumerate(diffs) if d > 0.35}
    keys = sorted(faces_at)
    for a, b in zip(keys, keys[1:]):
        xa, xb = faces_at[a][0], faces_at[b][0]
        if xa is not None and xb is not None and abs(xb - xa) > 0.08 * w \
                and not any(a < k <= b for k in cuts):
            cuts.add(max(range(a + 1, b + 1), key=lambda k: diffs[k]))
    samples = [{"t": k / fps, "cut": False, "cx": faces_at[k][0], "faces": faces_at[k][1]} for k in keys]
    samples += [{"t": k / fps, "cut": True, "cx": None, "faces": 0} for k in cuts]
    samples.sort(key=lambda s: (s["t"], not s["cut"]))
    shots, cur = [], []
    for s in samples:
        if s["cut"] and cur:
            shots.append(cur)
            cur = []
        cur.append(s)
    if cur:
        shots.append(cur)
    crop_w = round(h * 9 / 16) // 2 * 2
    out, last_x = [], (w - crop_w) // 2
    for k, shot in enumerate(shots):
        xs = [s["cx"] for s in shot if s["cx"] is not None]
        x = int(min(max(float(np.median(xs)) - crop_w / 2, 0), w - crop_w)) if xs else last_x
        spread = (max(xs) - min(xs)) / crop_w if xs else None
        out.append({"start": 0.0 if k == 0 else round(shot[0]["t"], 3), "x": x // 2 * 2,
                    "face_hits": len(xs), "samples": len(shot),
                    "max_faces": max(s["faces"] for s in shot),
                    "drift": None if spread is None else round(spread, 2)})
        last_x = x
    return {"width": w, "height": h, "crop_w": crop_w, "shots": out}


if __name__ == "__main__":
    print(json.dumps(plan(sys.argv[1]), indent=1))
