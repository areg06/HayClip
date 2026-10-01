"""Attempt records on disk (clips/<clip_id>/transcription/). Read side used by rendering.

Records are never deleted. Raw provider results live next to their attempt as <attempt_id>.result.json.
"""
from __future__ import annotations

from pathlib import Path

from .. import jsonio
from ..errors import ArtifactMismatch, NeedsReconciliation, ValidationError
from ..models import (COMPLETED, NEEDS_RECONCILIATION_STATES, TranscriptionAttempt, TranscriptRef)
from ..project import ProjectRepo


def list_attempts(repo: ProjectRepo, clip_id: str) -> list[TranscriptionAttempt]:
    tdir = repo.transcription_dir(clip_id)
    if not tdir.exists():
        return []
    out = []
    for f in sorted(tdir.glob("att_*.json")):
        if f.name.endswith(".result.json"):
            continue
        out.append(TranscriptionAttempt.from_dict(jsonio.read_json(f)))
    return sorted(out, key=lambda a: a.created_at)


def save_attempt(repo: ProjectRepo, attempt: TranscriptionAttempt) -> None:
    jsonio.write_json(repo.transcription_dir(attempt.clip_id) / f"{attempt.id}.json", attempt.to_dict())


def load_result(repo: ProjectRepo, attempt: TranscriptionAttempt) -> dict:
    if not attempt.result_file:
        raise ValidationError(f"attempt {attempt.id} has no stored result")
    return jsonio.read_json(repo.transcription_dir(attempt.clip_id) / attempt.result_file)


def completed_transcript(repo: ProjectRepo, clip_id: str) -> TranscriptRef | None:
    """The completed transcript for a clip, verified against the exact bytes it was made from.

    Returns None when no transcript exists yet. Raises NeedsReconciliation if any attempt has an
    unknown outcome, and ArtifactMismatch if the transcribed file's bytes no longer match."""
    attempts = list_attempts(repo, clip_id)
    unknown = [a for a in attempts if a.state in NEEDS_RECONCILIATION_STATES]
    if unknown:
        raise NeedsReconciliation(f"{clip_id}: attempt {unknown[-1].id} is {unknown[-1].state}",
                                  hint="run `python -m hayclips reconcile` before rendering or transcribing")
    done = [a for a in attempts if a.state == COMPLETED and a.result_file]
    if not done:
        return None
    a = done[-1]
    repo.verify_media(clip_id, a.media)
    return TranscriptRef(attempt_id=a.id, clip_id=clip_id, provider=a.provider, media=a.media,
                         raw=load_result(repo, a))
