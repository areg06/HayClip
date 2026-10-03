"""Full-frame 9:16 speaker crop planning (OpenCV + YuNet, in-process).

Splits a landscape window into camera shots (HSV histogram change on every frame, or the face
jumping sideways), finds the largest face ~5 times per second, and gives each shot one static crop
centred on the median face. The crop only moves on the source's own camera cuts.

The plan records the sha256 of the video it was computed from; a plan for different bytes is stale
and is recomputed. Plans are validated before any value reaches an ffmpeg filter string.
"""
from __future__ import annotations

from pathlib import Path

from .. import jsonio
from ..errors import ValidationError
from ..hashing import sha256_file

MODEL = Path(__file__).resolve().parents[2] / "models" / "face_detection_yunet_2023mar.onnx"
DETECT_W = 960  # faces are found on a half-size 1080p frame
PLAN_VERSION = 2


def plan_crop(path: Path) -> dict:
    import cv2
    import numpy as np

    path = Path(path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValidationError(f"cannot read video {path.name} for reframing")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if w <= 0 or h <= 0:
        raise ValidationError(f"{path.name} has no readable video frames")
    crop_w = round(h * 9 / 16) // 2 * 2
    base = {"version": PLAN_VERSION, "source_sha256": sha256_file(path), "width": w, "height": h}
    if w <= crop_w:
        # portrait or narrower than 9:16: nothing to crop, render scales it to fit
        cap.release()
        return {**base, "mode": "fit", "crop_w": w, "shots": [{"start": 0.0, "x": 0, "face_hits": 0, "samples": 0,
                                                              "max_faces": 0, "drift": None}]}
    scale = DETECT_W / w
    if not MODEL.exists():
        raise ValidationError(f"face model missing: {MODEL}")
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (DETECT_W, round(h * scale)), 0.6, 0.3, 20)
    step = max(1, round(fps / 5))
    diffs, faces_at, prev, idx = [], {}, None, 0
    while cap.grab():
        ok, frame = cap.retrieve()
        if not ok:
            break
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
    cap.release()
    if not faces_at:
        raise ValidationError(f"{path.name}: no frames could be decoded for reframing")
    # a cut is a big colour change, or the face jumping sideways between two samples
    # (same person, same curtain, new camera: the histogram barely moves)
    cuts = {k for k, d in enumerate(diffs) if d > 0.35}
    keys = sorted(faces_at)
    for a, b in zip(keys, keys[1:]):
        (xa, na), (xb, nb) = faces_at[a], faces_at[b]
        # only single-face samples: with two people on screen the "largest face" alternates,
        # which is not a camera cut
        if na == 1 and nb == 1 and abs(xb - xa) > 0.08 * w \
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
    return {**base, "mode": "crop", "crop_w": crop_w, "shots": out}


def validate_plan(plan: dict) -> dict:
    """Raise ValidationError unless every value that reaches the ffmpeg filter is a sane integer."""
    try:
        w, h, cw = int(plan["width"]), int(plan["height"]), int(plan["crop_w"])
        shots = plan["shots"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("crop plan is missing width/height/crop_w/shots") from exc
    if not shots:
        raise ValidationError("crop plan has no shots")
    if not (0 < cw <= w and h > 0):
        raise ValidationError(f"crop plan has an impossible crop width {cw} for {w}x{h}")
    last = -1.0
    for sh in shots:
        x, start = sh.get("x"), sh.get("start")
        if not isinstance(x, int) or isinstance(x, bool) or not 0 <= x <= w - cw or x % 2:
            raise ValidationError(f"crop plan x={x!r} must be an even integer within 0..{w - cw}")
        if not isinstance(start, (int, float)) or start < last:
            raise ValidationError(f"crop plan shot start {start!r} is not increasing")
        last = float(start)
    return plan


def shots_digest(plan: dict) -> str:
    import hashlib
    import json
    shots = [[round(float(sh.get("start", 0)), 3), sh.get("x")] for sh in plan.get("shots", [])]
    return hashlib.sha256(json.dumps(shots).encode()).hexdigest()


def set_crop_x(plan_path: Path, xs: dict[int, int]) -> dict:
    """Operator moves the crop horizontally for some shots. Values are validated before saving."""
    plan = jsonio.read_json(plan_path)
    w, cw = int(plan["width"]), int(plan["crop_w"])
    for idx, x in xs.items():
        if not 0 <= idx < len(plan["shots"]):
            raise ValidationError("that camera shot does not exist")
        x = int(round(float(x) / 2)) * 2
        if not 0 <= x <= w - cw:
            raise ValidationError(f"crop position must be within 0..{w - cw}")
        plan["shots"][idx]["x"] = x
    validate_plan(plan)
    jsonio.write_json(plan_path, plan)
    return plan


def reset_crop(plan_path: Path) -> dict:
    plan = jsonio.read_json(plan_path)
    if plan.get("auto_shots"):
        plan["shots"] = [dict(sh) for sh in plan["auto_shots"]]
        validate_plan(plan)
        jsonio.write_json(plan_path, plan)
    return plan


def framing_adjusted(plan: dict | None) -> bool | None:
    """True if someone edited the crop plan after it was computed; None if unknown (no plan/no baseline)."""
    if not plan or not plan.get("auto_shots_sha256"):
        return None
    return shots_digest(plan) != plan["auto_shots_sha256"]


def load_or_plan(plan_path: Path, video: Path, video_sha256: str) -> tuple[dict, bool]:
    """Cached plan if it was computed from these exact bytes, else a fresh one. Returns (plan, recomputed)."""
    plan = jsonio.read_json(plan_path, default=None)
    if plan is not None and plan.get("source_sha256") in (video_sha256, None) and plan.get("width"):
        if plan.get("source_sha256") is None:
            # legacy plans (pilot-03) predate the hash field; adopt them for the bytes they were made from
            plan = {**plan, "source_sha256": video_sha256, "mode": plan.get("mode", "crop")}
            jsonio.write_json(plan_path, plan)
        if "auto_shots_sha256" not in plan or "auto_shots" not in plan:
            # baseline for "framing manually adjusted" and "reset to automatic": plans written before
            # Phase 1d count as automatic
            plan = {**plan, "auto_shots_sha256": plan.get("auto_shots_sha256") or shots_digest(plan),
                    "auto_shots": [dict(sh) for sh in plan["shots"]]}
            jsonio.write_json(plan_path, plan)
        return validate_plan(plan), False
    plan = plan_crop(video)
    plan["auto_shots_sha256"] = shots_digest(plan)
    plan["auto_shots"] = [dict(sh) for sh in plan["shots"]]
    jsonio.write_json(plan_path, plan)
    return validate_plan(plan), True


def frame_filter(plan: dict, start: float) -> tuple[str, list[str], str]:
    """(filter prefix ending in ',', flags, description) for a render starting at window time `start`."""
    validate_plan(plan)
    shots = plan["shots"]
    if plan.get("mode") == "fit":
        return ("scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,",
                [], "fit (source is already 9:16 or narrower)")
    flags = []
    used = 0
    for k, sh in enumerate(shots):
        end = shots[k + 1]["start"] if k + 1 < len(shots) else None
        if end is not None and end <= start:
            continue
        used += 1
        at = float(sh["start"]) - start
        where = f"shot at {at:.1f}s" if at > 0 else "first shot (from the start)"
        if sh["face_hits"] == 0:
            flags.append(f"{where}: no face found, " + ("kept the previous crop" if k else "used the centre of the frame"))
        elif sh["max_faces"] > 1 or (sh["drift"] or 0) > 0.35:
            flags.append(f"{where}: {sh['max_faces']} faces / drift {sh['drift']}; check the framing")
    # nested if(): the crop switches hard at each camera cut (trimmed input starts at t=0)
    expr = str(int(shots[-1]["x"]))
    for k in range(len(shots) - 2, -1, -1):
        expr = f"if(lt(t\\,{float(shots[k + 1]['start']) - start:.3f})\\,{int(shots[k]['x'])}\\,{expr})"
    filt = (f"crop={int(plan['crop_w'])}:{int(plan['height'])}:x={expr}:y=0,"
            "scale=720:1280:flags=lanczos,setsar=1,")
    return filt, flags, f"speaker crop, {used} camera shots (edit crop.json to adjust)"
