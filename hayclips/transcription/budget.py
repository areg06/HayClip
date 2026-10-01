"""Cost guards for paid transcription, checked before any paid call.

Seconds that count against a budget are those of attempts that were charged or MAY have been
charged: SUBMITTING, SUBMITTED, POLLING, COMPLETED, UNKNOWN_SUBMISSION. Legacy-imported completed
attempts count toward their project through seconds_charged.

The per-day budget is account-wide and comes from the append-only ledger (settings.ledger_path):
  {"event": "reserve", seconds}   written when an attempt enters SUBMITTING
  {"event": "release", seconds}   definite non-charge (rejection, not sent, provider refund, reconciled)
  {"event": "charge",  seconds}   completed (informational)
  {"event": "unknown"} / {"event": "reconciled"}  outcome-unknown bookkeeping (blocks new submits)
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from .. import jsonio
from ..config import Settings
from ..errors import BudgetExceeded
from ..models import (COMPLETED, POLLING, SUBMITTED, SUBMITTING, UNKNOWN_SUBMISSION, TranscriptionAttempt,
                      now_iso)

COUNTED = {SUBMITTING, SUBMITTED, POLLING, COMPLETED, UNKNOWN_SUBMISSION}


def billable_seconds(duration: float) -> int:
    return int(math.ceil(duration - 1e-9))


def attempt_seconds(a: TranscriptionAttempt) -> int:
    if a.seconds_charged is not None:
        return int(a.seconds_charged)
    return int(a.seconds_estimate or 0)


def project_used(attempts: list[TranscriptionAttempt]) -> int:
    return sum(attempt_seconds(a) for a in attempts if a.state in COUNTED)


def ledger(settings: Settings) -> list[dict]:
    return jsonio.read_jsonl(settings.ledger_path)


def record(settings: Settings, event: str, **fields) -> None:
    jsonio.append_jsonl(settings.ledger_path, {"at": now_iso(), "event": event, **fields})


def day_used(settings: Settings, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    total = 0
    for e in ledger(settings):
        try:
            at = datetime.fromisoformat(e["at"])
        except (KeyError, ValueError):
            continue
        if at < since:
            continue
        if e.get("event") == "reserve":
            total += int(e.get("seconds", 0))
        elif e.get("event") == "release":
            total -= int(e.get("seconds", 0))
    return max(total, 0)


def unreconciled_unknowns(settings: Settings) -> list[dict]:
    """Account-wide attempts whose submit outcome is unknown and not yet reconciled."""
    open_: dict[str, dict] = {}
    for e in ledger(settings):
        if e.get("event") == "unknown":
            open_[e.get("attempt_id", "")] = e
        elif e.get("event") == "reconciled":
            open_.pop(e.get("attempt_id", ""), None)
    return list(open_.values())


def check_window(settings: Settings, seconds: int, clip_id: str) -> None:
    cap = settings.limits.max_window_seconds
    if seconds > cap:
        raise BudgetExceeded(f"{clip_id}: window of {seconds}s exceeds the {cap}s cap for one paid clip",
                             hint="only selected clip windows may be transcribed, never the full episode")


def check_budgets(settings: Settings, *, new_seconds: int, project_attempts: list[TranscriptionAttempt]) -> None:
    lim = settings.limits
    if new_seconds > lim.max_paid_seconds_per_operation:
        raise BudgetExceeded(f"this operation needs {new_seconds}s, over the {lim.max_paid_seconds_per_operation}s per-operation limit")
    used = project_used(project_attempts)
    if used + new_seconds > lim.max_paid_seconds_per_project:
        raise BudgetExceeded(f"project would reach {used + new_seconds}s, over the {lim.max_paid_seconds_per_project}s per-project limit"
                             f" ({used}s already used)")
    today = day_used(settings)
    if today + new_seconds > lim.max_paid_seconds_per_day:
        raise BudgetExceeded(f"account would reach {today + new_seconds}s in 24 h, over the {lim.max_paid_seconds_per_day}s per-day limit"
                             f" ({today}s already used)")


def check_balance(settings: Settings, balance: int, need: int) -> None:
    required = need * (1 + settings.limits.balance_margin)
    if balance < required:
        raise BudgetExceeded(f"Harmar balance {balance}s is below the {required:.0f}s required ({need}s + "
                             f"{settings.limits.balance_margin:.0%} margin)")
