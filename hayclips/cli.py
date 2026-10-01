"""Operator commands: `.venv/bin/python -m hayclips <command> ...`

  migrate <project>                       convert a suggestions.json pilot to stable clip ids
  status <project>                        clips in display order, windows, transcripts, renders
  select <project> <candidate_id> [...]   add a candidate as a clip (new stable id)
  edit <project> <clip_id> [...]          change title/hook/pick_note/order/selected/trim
  consent <project> --provider harmar ... record explicit creator permission
  reconcile <project> <clip_id> ...       resolve an unknown paid submission (see transcription)

These are the only commands that write project.json.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .errors import PipelineError
from .models import ConsentRecord, Trim, new_id, retry_safety
from .project import ProjectRepo, migrate_legacy


def _repo(path: str) -> ProjectRepo:
    return ProjectRepo(Path(path))


def cmd_migrate(a) -> None:
    from .media.probe import probe
    consent = []
    if a.consent_statement:
        consent.append(ConsentRecord(id=new_id("cns"), provider="harmar", scope="selected_windows",
                                     granted_by=a.consent_granted_by, recorded_by=a.recorded_by,
                                     statement=a.consent_statement, source_ref=a.consent_source or ""))
    p = migrate_legacy(Path(a.project), consent=consent, probe=probe)
    print(f"{p.name}: {len(p.clips)} clips with stable ids")
    for c in p.ordered(selected_only=False):
        print(f"  #{c.order} {c.id}  (was {c.legacy_name})  {c.title}")


def cmd_status(a) -> None:
    from .transcription.store import list_attempts
    repo = _repo(a.project)
    p = repo.load()
    print(f"{p.name}  source={p.source.get('url', '')}")
    if p.consent:
        for c in p.consent:
            print(f"  consent {c.id}: {c.provider}/{c.scope} by {c.granted_by!r} recorded {c.recorded_at}"
                  + (" (REVOKED)" if c.revoked_at else ""))
    else:
        print("  consent: none recorded (paid transcription is blocked)")
    for c in p.ordered(selected_only=False):
        w = repo.load_window(c.id)
        att = list_attempts(repo, c.id)
        last = att[-1] if att else None
        renders = sorted(x.stem for x in repo.render_dir(c.id).glob("*.mp4")) if repo.render_dir(c.id).exists() else []
        print(f"  #{c.order} {c.id} {'selected' if c.selected else 'unselected'} {c.start:.1f}-{c.end:.1f}s {c.title}")
        print(f"      window: {'yes' if w else 'no'}"
              + (f" (wide={'yes' if w.wide else 'no'}, audio={'yes' if w.audio else 'no'})" if w else "")
              + f"; transcript: {last.state + ' / ' + retry_safety(last.state) if last else 'none'}"
              + f"; renders: {','.join(renders) or 'none'}")


def cmd_select(a) -> None:
    clip = _repo(a.project).select(a.candidate_id, title=a.title, hook=a.hook, pick_note=a.pick_note,
                                   start=a.start, end=a.end, pad=a.pad)
    print(f"{clip.id}  #{clip.order}  {clip.start:.2f}-{clip.end:.2f}s")


def cmd_edit(a) -> None:
    changes = {k: getattr(a, k) for k in ("title", "hook", "pick_note", "order") if getattr(a, k) is not None}
    if a.selected is not None:
        changes["selected"] = a.selected == "yes"
    if a.trim:
        s, e = (float(x) for x in a.trim.split(":"))
        changes["trim"] = Trim(caption_start=s, caption_end=e)
    if a.auto_trim:
        changes["trim"] = None
    clip = _repo(a.project).update_clip(a.clip_id, **changes)
    print(f"{clip.id} updated: {', '.join(changes) or 'nothing'}")


def cmd_consent(a) -> None:
    if not a.statement.strip() or not a.granted_by.strip():
        raise PipelineError("consent needs --granted-by and a non-empty --statement")
    rec = ConsentRecord(id=new_id("cns"), provider=a.provider, scope="selected_windows",
                        granted_by=a.granted_by, recorded_by=a.recorded_by, statement=a.statement,
                        source_ref=a.source_ref or "")
    _repo(a.project).add_consent(rec)
    print(f"recorded {rec.id}: {rec.provider} may receive selected clip windows (not the full episode)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m hayclips")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate")
    m.add_argument("project")
    m.add_argument("--consent-statement", help="record consent that the founder already confirmed for this pilot")
    m.add_argument("--consent-granted-by", default="creator (as confirmed by founder)")
    m.add_argument("--consent-source")
    m.add_argument("--recorded-by", default="operator")
    m.set_defaults(fn=cmd_migrate)
    s = sub.add_parser("status")
    s.add_argument("project")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("select")
    s.add_argument("project")
    s.add_argument("candidate_id")
    for f in ("title", "hook", "pick_note"):
        s.add_argument(f"--{f.replace('_', '-')}", dest=f, default="")
    s.add_argument("--start", type=float)
    s.add_argument("--end", type=float)
    s.add_argument("--pad", type=float, default=5.0)
    s.set_defaults(fn=cmd_select)
    e = sub.add_parser("edit")
    e.add_argument("project")
    e.add_argument("clip_id")
    for f in ("title", "hook", "pick_note"):
        e.add_argument(f"--{f.replace('_', '-')}", dest=f)
    e.add_argument("--order", type=int)
    e.add_argument("--selected", choices=["yes", "no"])
    e.add_argument("--trim", help="manual cut inside the padded window, START:END seconds")
    e.add_argument("--auto-trim", action="store_true", help="drop the manual trim; snap automatically")
    e.set_defaults(fn=cmd_edit)
    c = sub.add_parser("consent")
    c.add_argument("project")
    c.add_argument("--provider", default="harmar", choices=["harmar"])
    c.add_argument("--granted-by", required=True, help="who gave permission (creator/channel)")
    c.add_argument("--statement", required=True, help="the permission text or where it was given")
    c.add_argument("--recorded-by", default="operator")
    c.add_argument("--source-ref")
    c.set_defaults(fn=cmd_consent)
    try:
        from .transcription.reconcile import add_parser as add_reconcile
        add_reconcile(sub)
    except ImportError:
        pass
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0
