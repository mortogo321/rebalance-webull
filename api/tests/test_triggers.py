from __future__ import annotations

from datetime import UTC, datetime, timedelta

from engine.models import BotStatus, TriggerType
from engine.triggers import evaluate_trigger, next_run_after

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def check(**kw):
    base = {
        "status": BotStatus.RUNNING,
        "trigger_type": TriggerType.SCHEDULE_OR_DRIFT,
        "now": NOW,
        "next_run_at": None,
        "max_drift_bps": 0,
        "drift_threshold_bps": 500,
    }
    base.update(kw)
    return evaluate_trigger(**base)


class TestStatusGate:
    def test_a_paused_bot_never_runs(self):
        decision = check(status=BotStatus.PAUSED, next_run_at=NOW - timedelta(days=1))
        assert not decision.should_run
        assert "paused" in decision.reason

    def test_a_stopped_bot_never_runs_even_when_badly_drifted(self):
        decision = check(status=BotStatus.STOPPED, max_drift_bps=9999)
        assert not decision.should_run

    def test_a_draft_bot_never_runs(self):
        assert not check(status=BotStatus.DRAFT).should_run


class TestSchedule:
    def test_a_bot_that_has_never_run_is_due_immediately(self):
        decision = check(trigger_type=TriggerType.SCHEDULE, next_run_at=None)
        assert decision.should_run

    def test_it_waits_until_the_interval_elapses(self):
        decision = check(
            trigger_type=TriggerType.SCHEDULE, next_run_at=NOW + timedelta(minutes=30)
        )
        assert not decision.should_run
        assert "next scheduled run" in decision.reason

    def test_it_fires_once_the_interval_has_elapsed(self):
        decision = check(
            trigger_type=TriggerType.SCHEDULE, next_run_at=NOW - timedelta(seconds=1)
        )
        assert decision.should_run

    def test_it_fires_exactly_on_the_boundary(self):
        assert check(trigger_type=TriggerType.SCHEDULE, next_run_at=NOW).should_run

    def test_drift_alone_does_not_fire_a_schedule_only_bot(self):
        decision = check(
            trigger_type=TriggerType.SCHEDULE,
            next_run_at=NOW + timedelta(hours=1),
            max_drift_bps=9999,
        )
        assert not decision.should_run


class TestDrift:
    def test_it_fires_when_drift_reaches_the_threshold(self):
        decision = check(
            trigger_type=TriggerType.DRIFT, max_drift_bps=500, drift_threshold_bps=500
        )
        assert decision.should_run
        assert "500 bps >= threshold 500 bps" in decision.reason

    def test_it_holds_below_the_threshold(self):
        decision = check(trigger_type=TriggerType.DRIFT, max_drift_bps=499)
        assert not decision.should_run
        assert "below threshold" in decision.reason

    def test_a_past_schedule_does_not_fire_a_drift_only_bot(self):
        decision = check(
            trigger_type=TriggerType.DRIFT,
            next_run_at=NOW - timedelta(days=7),
            max_drift_bps=10,
        )
        assert not decision.should_run


class TestEitherTrigger:
    def test_drift_fires_before_the_schedule_is_due(self):
        decision = check(next_run_at=NOW + timedelta(hours=5), max_drift_bps=800)
        assert decision.should_run
        assert "drift" in decision.reason

    def test_the_schedule_fires_while_drift_is_calm(self):
        decision = check(next_run_at=NOW - timedelta(minutes=1), max_drift_bps=10)
        assert decision.should_run
        assert "scheduled" in decision.reason

    def test_neither_condition_means_no_run_and_the_reason_says_both(self):
        decision = check(next_run_at=NOW + timedelta(hours=1), max_drift_bps=10)
        assert not decision.should_run
        assert "below threshold" in decision.reason
        assert "next scheduled run" in decision.reason


class TestTimezoneHandling:
    def test_a_naive_timestamp_is_read_as_utc_not_local_time(self):
        # Guards against a scheduler that fires hours early or late on a server
        # whose clock is not set to UTC.
        naive_future = datetime(2026, 9, 11, 13, 0)
        assert not check(trigger_type=TriggerType.SCHEDULE, next_run_at=naive_future).should_run

        naive_past = datetime(2026, 9, 11, 11, 0)
        assert check(trigger_type=TriggerType.SCHEDULE, next_run_at=naive_past).should_run


class TestNextRunAfter:
    def test_it_advances_by_the_interval(self):
        assert next_run_after(NOW, 60) == NOW + timedelta(hours=1)

    def test_a_drift_only_bot_has_no_next_run(self):
        assert next_run_after(NOW, None) is None
        assert next_run_after(NOW, 0) is None
