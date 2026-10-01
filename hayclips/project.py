"""File-based project repository and state ownership.

Layout of a project directory (e.g. pilot-03/):

  project.json            OPERATOR-owned: source, consent records, selected clips (id, order, title,
                          hook, pick_note, planned window, manual trim). Changed only by operator
                          commands (`python -m hayclips select|consent|edit`) or by hand.
  candidates.json         MACHINE-owned (selection). Regenerating it never touches project.json.
  source/                 free captions of the source
  clips/<clip_id>/        everything about one clip, keyed by its stable id:
     window.json          FETCH-owned: source range, padding, hashes of wide/preview/audio files
     wide.mp4             landscape window (render input)
     preview.mp4          letterboxed preview (legacy Harmar input for pilots 01-03)
     audio.m4a            audio-only artifact sent to Harmar (new clips)
     youtube.srt          free captions for comparison
     crop.json            REFRAME-owned crop plan (records the wide.mp4 sha it was computed from)
     transcription/       TRANSCRIPTION-owned attempt records + raw results (never deleted)
     render/              RENDER-owned: <style>.ass/.mp4, captions.srt, render.json
  review.html             RENDER-owned review page

A Postgres repository will implement the same operations in Phase 1b; the CLI keeps this one.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import jsonio
from .errors import ArtifactMismatch, LegacyLayoutError, ValidationError
from .hashing import sha256_file
from .models import (COMPLETED, SUBMITTED, Candidate, Clip, ConsentRecord, MediaFile, Project,
                     TranscriptionAttempt, Trim, Window, candidate_id, new_id, now_iso)

PROJECT_FILE = "project.json"
CANDIDATES_FILE = "candidates.json"
LEGACY_FILE = "suggestions.json"


class ProjectRepo:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    # ----- paths -----
    @property
    def project_path(self) -> Path:
        return self.root / PROJECT_FILE

    @property
    def candidates_path(self) -> Path:
        return self.root / CANDIDATES_FILE

    def clip_dir(self, clip_id: str) -> Path:
        if not clip_id or "/" in clip_id or clip_id.startswith("."):
            raise ValidationError(f"bad clip id {clip_id!r}")
        return self.root / "clips" / clip_id

    def transcription_dir(self, clip_id: str) -> Path:
        return self.clip_dir(clip_id) / "transcription"

    def render_dir(self, clip_id: str) -> Path:
        return self.clip_dir(clip_id) / "render"

    # ----- project (operator-owned) -----
    def exists(self) -> bool:
        return self.project_path.exists()

    def ensure_not_legacy(self) -> None:
        if not self.exists() and (self.root / LEGACY_FILE).exists():
            raise LegacyLayoutError(f"{self.root.name} uses the old suggestions.json layout",
                                    hint=f"run: .venv/bin/python -m hayclips migrate {self.root}")

    def load(self) -> Project:
        self.ensure_not_legacy()
        if not self.exists():
            raise ValidationError(f"no {PROJECT_FILE} in {self.root}",
                                  hint="create it with `.venv/bin/python clipper.py --srt ... --duration ... --out <project>`")
        return Project.from_dict(jsonio.read_json(self.project_path))

    def save(self, project: Project) -> None:
        jsonio.write_json(self.project_path, project.to_dict())

    def init(self, name: str, source: dict) -> Project:
        if self.exists():
            return self.load()
        self.ensure_not_legacy()
        p = Project(name=name, source=source)
        self.save(p)
        return p

    # ----- candidates (machine-owned) -----
    def save_candidates(self, candidates: list[Candidate], params: dict) -> None:
        jsonio.write_json(self.candidates_path, {"generated_at": now_iso(), "params": params,
                                                 "candidates": [c.to_dict() for c in candidates]})

    def load_candidates(self) -> list[Candidate]:
        data = jsonio.read_json(self.candidates_path, default={"candidates": []})
        return [Candidate.from_dict(c) for c in data["candidates"]]

    # ----- operator operations on clips -----
    def select(self, cand_id: str, *, title: str = "", hook: str = "", pick_note: str = "",
               start: float | None = None, end: float | None = None, pad: float = 5.0) -> Clip:
        """Add a candidate to the project as a new clip with a fresh stable id (idempotent per candidate)."""
        project = self.load()
        for c in project.clips:
            if c.candidate_id == cand_id:
                return c
        cand = next((c for c in self.load_candidates() if c.id == cand_id), None)
        if cand is None and (start is None or end is None):
            raise ValidationError(f"candidate {cand_id} not in {CANDIDATES_FILE}")
        clip = Clip(id=new_id("clp"), order=max((c.order for c in project.clips), default=0) + 1,
                    start=float(start if start is not None else cand.start),
                    end=float(end if end is not None else cand.end),
                    candidate_id=cand_id, pad=pad, title=title, hook=hook, pick_note=pick_note)
        project.clips.append(clip)
        self.save(project)
        return clip

    def update_clip(self, clip_id: str, **changes) -> Clip:
        project = self.load()
        clip = project.clip(clip_id)
        for k, v in changes.items():
            if not hasattr(clip, k) or k == "id":
                raise ValidationError(f"unknown or read-only clip field {k!r}")
            setattr(clip, k, v)
        self.save(project)
        return clip

    def add_consent(self, record: ConsentRecord) -> ConsentRecord:
        project = self.load()
        project.consent.append(record)
        self.save(project)
        return record

    # ----- window (fetch-owned) -----
    def load_window(self, clip_id: str) -> Window | None:
        d = jsonio.read_json(self.clip_dir(clip_id) / "window.json", default=None)
        return None if d is None else Window.from_dict(d)

    def save_window(self, window: Window) -> None:
        jsonio.write_json(self.clip_dir(window.clip_id) / "window.json", window.to_dict())

    def media_path(self, clip_id: str, media: MediaFile) -> Path:
        return self.clip_dir(clip_id) / media.path

    def verify_media(self, clip_id: str, media: MediaFile) -> Path:
        """Return the file path if its bytes still match the record, else raise ArtifactMismatch."""
        path = self.media_path(clip_id, media)
        if not path.exists():
            raise ArtifactMismatch(f"{clip_id}: {media.path} is missing (expected sha256 {media.sha256[:12]}…)",
                                   hint="restore the file from backup; do not re-create it, its hash is on record")
        actual = sha256_file(path)
        if actual != media.sha256:
            raise ArtifactMismatch(f"{clip_id}: {media.path} bytes changed (expected {media.sha256[:12]}…, got {actual[:12]}…)",
                                   hint="investigate before rendering or transcribing; nothing was reused or submitted")
        return path


def media_record(path: Path, rel: str, kind: str, duration: float | None = None,
                 derived_from: dict | None = None) -> MediaFile:
    return MediaFile(path=rel, sha256=sha256_file(path), bytes=path.stat().st_size, duration=duration,
                     kind=kind, derived_from=derived_from)


# ----- migration from the suggestions.json layout ------------------------------------------------------

LEGACY_MOVES = [  # (legacy name pattern, new path inside clips/<id>/)
    ("{n}.mp4", "preview.mp4"),
    ("{n}.wide.mp4", "wide.mp4"),
    ("{n}.crop.json", "crop.json"),
    ("{n}.youtube.srt", "youtube.srt"),
    ("{n}.srt", "render/captions.srt"),
    ("{n}.ass", "render/legacy.ass"),
    ("{n}_captions.mp4", "render/legacy.mp4"),
    ("{n}_A.ass", "render/A.ass"), ("{n}_A.mp4", "render/A.mp4"),
    ("{n}_B.ass", "render/B.ass"), ("{n}_B.mp4", "render/B.mp4"),
    ("{n}_C.ass", "render/C.ass"), ("{n}_C.mp4", "render/C.mp4"),
]


def migrate_legacy(root: Path, *, consent: list[ConsentRecord] | None = None, probe=None) -> Project:
    """Convert a suggestions.json pilot to the stable-id layout. Idempotent; validates before moving.

    Paid-artifact safety: every legacy Harmar cache is checked against the sha256 of the clip file it
    was made from BEFORE any file moves. A mismatch aborts the whole migration. Raw caches are copied
    (not moved) into each clip's transcription/ folder, and the originals are kept under legacy/.
    """
    repo = ProjectRepo(root)
    if repo.exists():
        return repo.load()
    legacy = repo.root / LEGACY_FILE
    if not legacy.exists():
        raise ValidationError(f"{repo.root} has neither {PROJECT_FILE} nor {LEGACY_FILE}")
    sug = jsonio.read_json(legacy)
    # The id plan is written first so an interrupted migration resumes with the same ids.
    plan_path = repo.root / ".migration-plan.json"
    ids = jsonio.read_json(plan_path, default=None)
    resuming = ids is not None
    if ids is None:
        ids = {f"clip_{i:02}": new_id("clp") for i in range(1, len(sug) + 1)}
    plan = []
    for i, s in enumerate(sug, 1):
        n = f"clip_{i:02}"
        preview = repo.root / f"{n}.mp4"
        if resuming and not preview.exists():
            preview = repo.clip_dir(ids[n]) / "preview.mp4"
        cache = repo.root / "harmar" / n / "harmar_transcript.json"
        cached = jsonio.read_json(cache, default=None)
        if cached is not None and preview.exists():
            actual = sha256_file(preview)
            if cached.get("video_sha256") != actual:
                raise ArtifactMismatch(f"{n}: Harmar cache was made for different bytes than {preview.name}",
                                       hint="migration aborted before moving anything; investigate")
        elif cached is not None:
            raise ArtifactMismatch(f"{n}: Harmar cache exists but {preview.name} is missing",
                                   hint="migration aborted before moving anything; restore the file")
        plan.append((n, s, cached))

    jsonio.write_json(plan_path, ids)
    first = sug[0] if sug else {}
    url = first.get("source", "")
    project = Project(name=repo.root.name, source={"kind": "youtube" if "youtu" in url else "unknown", "url": url})
    project.consent = list(consent or [])
    for i, (n, s, cached) in enumerate(plan, 1):
        cid = ids[n]
        cdir = repo.clip_dir(cid)
        (cdir / "render").mkdir(parents=True, exist_ok=True)
        for pattern, target in LEGACY_MOVES:
            src = repo.root / pattern.format(n=n)
            dst = cdir / target
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)
        trim = None
        if s.get("snap") == "manual" or ("caption_end" in s and "pad" not in s):
            trim = Trim(caption_start=float(s.get("caption_start", 0.0)), caption_end=float(s["caption_end"]))
        clip = Clip(id=cid, order=i, start=float(s["start"]), end=float(s["end"]),
                    candidate_id=candidate_id(float(s["start"]), float(s["end"])),
                    pad=float(s.get("pad", 0.0)), title=s.get("title", ""), hook=s.get("hook", ""),
                    pick_note=s.get("pick_note", ""), trim=trim, legacy_name=n)
        project.clips.append(clip)

        pad_start = float(s.get("pad_start", s.get("pad", 0.0)))
        dur = (lambda p: probe(p).duration) if probe else (lambda p: None)
        wide = cdir / "wide.mp4"
        prev = cdir / "preview.mp4"
        window = Window(clip_id=cid, source_start=float(s["start"]) - pad_start,
                        source_end=float(s["end"]) + float(s.get("pad", 0.0)), pad_start=pad_start,
                        wide=media_record(wide, "wide.mp4", "wide", dur(wide)) if wide.exists() else None,
                        preview=media_record(prev, "preview.mp4", "preview_video", dur(prev)) if prev.exists() else None,
                        source_ref=url)
        repo.save_window(window)

        tdir = cdir / "transcription"
        if cached is not None and not (tdir / "legacy_harmar_transcript.json").exists():
            tdir.mkdir(parents=True, exist_ok=True)
            resp = cached.get("response")
            attempt = TranscriptionAttempt(
                id=new_id("att"), clip_id=cid, provider="harmar",
                state=COMPLETED if resp and resp.get("status") == "completed" else SUBMITTED,
                media=window.preview,
                options={"timestamps": "word" if resp and resp.get("words") else "segment",
                         "punctuation": True, "source_lang": "hy"},
                provider_job_id=cached.get("job_id"), origin="legacy_import",
                seconds_charged=(resp or {}).get("seconds_charged"),
                completed_at=(resp or {}).get("completed_at"), submitted_at=(resp or {}).get("created_at"))
            attempt.history.append({"at": now_iso(), "from": None, "to": attempt.state,
                                    "note": f"imported from harmar/{n}/harmar_transcript.json"})
            if resp:
                attempt.result_file = f"{attempt.id}.result.json"
                jsonio.write_json(tdir / attempt.result_file, resp)
            jsonio.write_json(tdir / f"{attempt.id}.json", attempt.to_dict())
            # written last: its presence marks this clip's import as complete (resume-safe)
            jsonio.write_json(tdir / "legacy_harmar_transcript.json", cached)

    # keep the originals for audit: suggestions.json and harmar/ move under legacy/
    legacy_dir = repo.root / "legacy"
    legacy_dir.mkdir(exist_ok=True)
    if (repo.root / "harmar").exists():
        shutil.move(str(repo.root / "harmar"), str(legacy_dir / "harmar"))
    if (repo.root / "review.html").exists():
        (repo.root / "review.html").rename(legacy_dir / "review.html")
    repo.save(project)
    legacy.rename(legacy_dir / LEGACY_FILE)
    plan_path.unlink()
    jsonio.write_json(legacy_dir / "migration.json", {
        "migrated_at": now_iso(),
        "clips": [{"legacy_name": c.legacy_name, "clip_id": c.id} for c in project.clips]})
    return project
