"""When should a bot rebalance?

Separated from the planner because the two answer different questions and fail
in different ways. The planner asks "what would I trade?"; this asks "should I
trade at all right now?". Both are pure, and both are tested independently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .models import BotStatus, TriggerDecision, TriggerType


def next_run_after(
    now: datetime, interval_minutes: int | None
) -> datetime | None:
    """The next scheduled evaluation time, or None for drift-only bots."""
    if not interval_minutes:
        return None
    return now + timedelta(minutes=interval_minutes)


def evaluate_trigger(
    *,
    status: BotStatus,
    trigger_type: TriggerType,
    now: datetime,
    next_run_at: datetime | None = None,
    max_drift_bps: int | None = None,
    drift_threshold_bps: int | None = None,
) -> TriggerDecision:
    """Decide whether a bot is due, and say why in words a user can read.

    The reason string is stored on every ``rebalance_runs`` row -- including
    skipped ones -- so the bot's history explains its own inactivity rather
    than just going quiet.
    """
    if status is not BotStatus.RUNNING:
        return TriggerDecision(False, f"bot is {status}, not running")

    now = _as_utc(now)
    schedule_due = False
    drift_breached = False

    if trigger_type in (TriggerType.SCHEDULE, TriggerType.SCHEDULE_OR_DRIFT):
        # A bot that has never run is due immediately -- otherwise starting a
        # bot would appear to do nothing for a full interval.
        schedule_due = next_run_at is None or now >= _as_utc(next_run_at)

    if trigger_type in (TriggerType.DRIFT, TriggerType.SCHEDULE_OR_DRIFT):
        if drift_threshold_bps is not None and max_drift_bps is not None:
            drift_breached = max_drift_bps >= drift_threshold_bps

    if trigger_type is TriggerType.SCHEDULE:
        if schedule_due:
            return TriggerDecision(True, "scheduled interval elapsed")
        return TriggerDecision(False, f"next scheduled run at {_iso(next_run_at)}")

    if trigger_type is TriggerType.DRIFT:
        if drift_breached:
            return TriggerDecision(
                True, f"drift {max_drift_bps} bps >= threshold {drift_threshold_bps} bps"
            )
        return TriggerDecision(
            False,
            f"drift {max_drift_bps or 0} bps below threshold {drift_threshold_bps} bps",
        )

    # schedule_or_drift: whichever fires first wins, and the reason names it.
    if drift_breached:
        return TriggerDecision(
            True, f"drift {max_drift_bps} bps >= threshold {drift_threshold_bps} bps"
        )
    if schedule_due:
        return TriggerDecision(True, "scheduled interval elapsed")
    return TriggerDecision(
        False,
        f"drift {max_drift_bps or 0} bps below threshold "
        f"{drift_threshold_bps} bps; next scheduled run at {_iso(next_run_at)}",
    )


def _as_utc(value: datetime) -> datetime:
    """Treat a naive datetime as UTC rather than as local time.

    A scheduler that silently reads naive timestamps as machine-local time
    fires at the wrong hour on any server whose clock is not UTC.
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str:
    return _as_utc(value).isoformat() if value else "unscheduled"
