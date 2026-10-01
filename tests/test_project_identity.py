"""Stable clip identity and state ownership (project.json vs candidates.json)."""
import json

import pytest

from hayclips import jsonio
from hayclips.errors import ArtifactMismatch, LegacyLayoutError
from hayclips.hashing import sha256_file
from hayclips.models import Candidate, candidate_id
from hayclips.project import ProjectRepo, migrate_legacy


def cands(*windows):
    return [Candidate(id=candidate_id(a, b), start=a, end=b, score=1.0, text=f"{a}-{b}") for a, b in windows]


@pytest.fixture
def repo(tmp_path):
    r = ProjectRepo(tmp_path / "proj")
    r.init("proj", {"kind": "youtube", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"})
    r.save_candidates(cands((10, 40), (100, 150), (200, 240)), {})
    return r


def test_candidate_ids_are_deterministic():
    assert candidate_id(10, 40) == candidate_id(10.0, 40.0)
    assert candidate_id(10, 40) != candidate_id(10, 41)


def test_regenerating_candidates_preserves_operator_metadata(repo):
    c = repo.select(candidate_id(100, 150), title="T", hook="H", pick_note="N")
    repo.update_clip(c.id, trim=None)
    repo.save_candidates(cands((5, 30)), {"rerun": True})       # completely different machine output
    p = repo.load()
    kept = p.clip(c.id)
    assert (kept.title, kept.hook, kept.pick_note, kept.start, kept.end, kept.selected) == ("T", "H", "N", 100, 150, True)


def test_reordering_does_not_change_identity_or_artifact_dirs(repo):
    a = repo.select(candidate_id(10, 40))
    b = repo.select(candidate_id(100, 150))
    repo.clip_dir(a.id).mkdir(parents=True)
    (repo.clip_dir(a.id) / "marker").write_text("a")
    repo.update_clip(a.id, order=5)
    repo.update_clip(b.id, order=1)
    ordered = [c.id for c in repo.load().ordered()]
    assert ordered == [b.id, a.id]
    assert (repo.clip_dir(a.id) / "marker").read_text() == "a"


def test_deleted_candidate_keeps_selected_clip(repo):
    a = repo.select(candidate_id(200, 240), title="keep me")
    repo.save_candidates(cands((10, 40)), {})
    clip = repo.load().clip(a.id)
    assert (clip.start, clip.end, clip.title) == (200, 240, "keep me")


def test_added_candidate_gets_new_id_and_select_is_idempotent(repo):
    a = repo.select(candidate_id(10, 40))
    repo.save_candidates(cands((10, 40), (300, 340)), {})
    b = repo.select(candidate_id(300, 340))
    assert b.id != a.id and b.order == a.order + 1
    assert repo.select(candidate_id(300, 340)).id == b.id
    assert len(repo.load().clips) == 2


def test_legacy_layout_is_detected(tmp_path):
    d = tmp_path / "old"
    d.mkdir()
    (d / "suggestions.json").write_text("[]")
    with pytest.raises(LegacyLayoutError):
        ProjectRepo(d).load()


def _legacy_pilot(tmp_path, tamper=False):
    d = tmp_path / "pilot"
    (d / "harmar" / "clip_01").mkdir(parents=True)
    (d / "clip_01.mp4").write_bytes(b"preview-bytes")
    (d / "clip_01.wide.mp4").write_bytes(b"wide-bytes")
    (d / "clip_01_A.mp4").write_bytes(b"render")
    sha = sha256_file(d / "clip_01.mp4")
    resp = {"status": "completed", "seconds_charged": 43, "segments": [{"start": 0, "end": 1, "text": "x։"}]}
    jsonio.write_json(d / "harmar" / "clip_01" / "harmar_transcript.json",
                      {"video_sha256": "0" * 64 if tamper else sha, "job_id": "job-1", "response": resp})
    jsonio.write_json(d / "suggestions.json", [{"start": 10, "end": 40, "title": "t", "hook": "h", "pad": 5.0,
                                                "pad_start": 5.0, "source": "https://youtu.be/aaaaaaaaaaa",
                                                "caption_start": 4.8, "caption_end": 35.2, "snap": "auto"}])
    return d


def test_migration_moves_by_stable_id_and_imports_paid_record(tmp_path):
    d = _legacy_pilot(tmp_path)
    p = migrate_legacy(d)
    clip = p.clips[0]
    cdir = ProjectRepo(d).clip_dir(clip.id)
    assert (cdir / "preview.mp4").read_bytes() == b"preview-bytes"
    assert (cdir / "render" / "A.mp4").exists()
    att = [json.loads(f.read_text()) for f in (cdir / "transcription").glob("att_*.json") if not f.name.endswith(".result.json")]
    assert att[0]["state"] == "COMPLETED" and att[0]["provider_job_id"] == "job-1"
    assert att[0]["media"]["sha256"] == sha256_file(cdir / "preview.mp4")
    assert clip.trim is None                      # auto snap is recomputed, not frozen as manual
    assert (d / "legacy" / "suggestions.json").exists() and (d / "legacy" / "harmar").exists()
    assert migrate_legacy(d).clips[0].id == clip.id   # idempotent


def test_migration_aborts_before_moving_when_cache_hash_differs(tmp_path):
    d = _legacy_pilot(tmp_path, tamper=True)
    with pytest.raises(ArtifactMismatch):
        migrate_legacy(d)
    assert (d / "clip_01.mp4").exists() and not (d / "clips").exists()
