"""Speaker-crop planning and plan validation."""
from pathlib import Path

import pytest

from fixtures import synth
from hayclips import jsonio
from hayclips.errors import ValidationError
from hayclips.hashing import sha256_file
from hayclips.media.reframe import frame_filter, load_or_plan, plan_crop, validate_plan

LOCAL = Path(__file__).parent / "fixtures" / "local"


def test_no_faces_keeps_a_centre_crop_and_flags_it(tmp_path):
    v = synth.video(tmp_path / "w.mp4", 3, "1280x720")
    plan = plan_crop(v)
    assert plan["mode"] == "crop" and plan["crop_w"] == 404 and plan["source_sha256"] == sha256_file(v)
    assert all(s["face_hits"] == 0 for s in plan["shots"])
    _, flags, _ = frame_filter(plan, 0.0)
    assert flags and "no face" in flags[0]


def test_portrait_source_is_fitted_not_cropped(tmp_path):
    v = synth.video(tmp_path / "p.mp4", 2, "720x1280")
    plan = plan_crop(v)
    vf, flags, desc = frame_filter(plan, 0.0)
    assert plan["mode"] == "fit" and vf.startswith("scale=720:1280") and "fit" in desc


def test_unreadable_video_raises_clear_error(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(ValidationError):
        plan_crop(bad)


@pytest.mark.parametrize("x", [-2, 3, "100", 99999, None, True])
def test_bad_crop_x_is_rejected_before_ffmpeg(x):
    plan = {"width": 1920, "height": 1080, "crop_w": 608, "shots": [{"start": 0.0, "x": x}]}
    with pytest.raises(ValidationError):
        validate_plan(plan)


def test_empty_or_unordered_shots_are_rejected():
    with pytest.raises(ValidationError):
        validate_plan({"width": 1920, "height": 1080, "crop_w": 608, "shots": []})
    with pytest.raises(ValidationError):
        validate_plan({"width": 1920, "height": 1080, "crop_w": 608,
                       "shots": [{"start": 2.0, "x": 0}, {"start": 1.0, "x": 0}]})


def test_stale_plan_for_other_bytes_is_recomputed(tmp_path):
    v = synth.video(tmp_path / "w.mp4", 2)
    jsonio.write_json(tmp_path / "crop.json", {"source_sha256": "0" * 64, "width": 1280, "height": 720,
                                               "crop_w": 404, "shots": [{"start": 0.0, "x": 0}]})
    plan, recomputed = load_or_plan(tmp_path / "crop.json", v, sha256_file(v))
    assert recomputed and plan["source_sha256"] == sha256_file(v)
    plan2, again = load_or_plan(tmp_path / "crop.json", v, sha256_file(v))
    assert not again and plan2 == plan


@pytest.mark.local_fixture
def test_single_face_is_centred():
    plan = plan_crop(LOCAL / "one_face.mp4")
    assert len(plan["shots"]) == 1 and plan["shots"][0]["face_hits"] > 20 and plan["shots"][0]["max_faces"] == 1
    _, flags, _ = frame_filter(plan, 0.0)
    assert not flags


@pytest.mark.local_fixture
def test_two_faces_are_one_shot_and_flagged_for_a_human():
    plan = plan_crop(LOCAL / "two_faces.mp4")
    assert len(plan["shots"]) == 1 and plan["shots"][0]["max_faces"] == 2   # no ping-pong between faces
    _, flags, _ = frame_filter(plan, 0.0)
    assert flags and "2 faces" in flags[0]


def test_framing_adjusted_detects_hand_edits_only():
    from hayclips.media.reframe import framing_adjusted, shots_digest
    plan = {"width": 1920, "height": 1080, "crop_w": 608, "mode": "crop",
            "shots": [{"start": 0.0, "x": 600}, {"start": 3.2, "x": 900}]}
    assert framing_adjusted(plan) is None                      # no baseline recorded: unknown, not "no"
    plan["auto_shots_sha256"] = shots_digest(plan)
    assert framing_adjusted(plan) is False
    plan["shots"][1]["x"] = 904
    assert framing_adjusted(plan) is True
