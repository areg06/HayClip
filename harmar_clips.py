"""Transcribe a project's selected clips with Harmar (audio-only, word timestamps).

Usage:
  .venv/bin/python harmar_clips.py <project> [--clip ID ...]                      # plan only, no paid call
  HARMAR_API_KEY=... HAYCLIPS_ALLOW_PAID_HARMAR=1 \\
  .venv/bin/python harmar_clips.py <project> --confirm-paid --by NAME             # paid run

Completed transcripts are reused only for the exact bytes they were made from; reruns never resubmit.
A submission whose outcome is unknown stops everything until `python -m hayclips reconcile` is used.
Exit codes: 0 ok, 2 blocked or error, 3 needs reconciliation.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from hayclips.config import load_settings
from hayclips.errors import NeedsReconciliation, PipelineError
from hayclips.project import ProjectRepo
from hayclips.transcription import budget, service
from hayclips.transcription.store import list_attempts


def print_plan(repo, plans, settings) -> int:
    project = repo.load()
    consent = project.active_consent("harmar")
    print(f"{project.name}: consent for Harmar: {'yes (' + consent.id + ')' if consent else 'NONE recorded'}")
    attempts = [a for c in project.clips for a in list_attempts(repo, c.id)]
    lim = settings.limits
    new = 0
    for p in plans:
        label = {"reuse": "reuse completed transcript", "resume": "resume polling existing job (free)",
                 "reconcile": "NEEDS RECONCILIATION", "in_progress": "submission in progress elsewhere",
                 "new": f"NEW paid submission: {p.seconds} s of audio"}[p.action]
        print(f"  #{p.order} {p.clip_id}: {label}" + (f" ({p.note})" if p.note else ""))
        new += p.seconds if p.action == "new" else 0
    print(f"  new seconds {new} / operation limit {lim.max_paid_seconds_per_operation}; "
          f"project used {budget.project_used(attempts)} / {lim.max_paid_seconds_per_project}; "
          f"24 h used {budget.day_used(settings)} / {lim.max_paid_seconds_per_day}")
    return new


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path)
    ap.add_argument("--clip", action="append", dest="clips", help="limit to these clip ids")
    ap.add_argument("--confirm-paid", action="store_true", help="actually submit new paid jobs after the plan")
    ap.add_argument("--by", default="", help="operator name recorded with the confirmation")
    args = ap.parse_args(argv)
    settings = load_settings()
    repo = ProjectRepo(args.project)
    key = os.environ.get("HARMAR_API_KEY", "").strip() or None
    try:
        plans = service.plan(repo, args.clips, settings=settings)
        new = print_plan(repo, plans, settings)
        if any(p.action == "reconcile" for p in plans):
            raise NeedsReconciliation("some clips need reconciliation (see above)",
                                      hint="python -m hayclips reconcile <project> <clip_id> --show")
        if new and not args.confirm_paid:
            print("plan only: nothing was submitted. Add --confirm-paid --by NAME to pay for the new submissions.")
            return 0
        outcomes = service.transcribe(repo, args.clips, settings=settings, api_key=key,
                                      confirmed_by=args.by if args.confirm_paid else None)
    except NeedsReconciliation as exc:
        print(f"needs reconciliation: {exc}", file=sys.stderr)
        return 3
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    code = 0
    for o in outcomes:
        state = o.attempt.state if o.attempt else "-"
        print(f"  {o.clip_id}: {o.action} [{state}] {o.note}")
        if o.action == "needs_reconciliation":
            code = 3
        elif o.action in ("failed", "blocked") and code == 0:
            code = 2
    return code


if __name__ == "__main__":
    sys.exit(main())
