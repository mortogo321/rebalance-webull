"""Value objects shared by the rebalance core, the brokers and the service layer.

Everything here is frozen. The planner is a pure function over immutable input,
which is what makes it cheap to test exhaustively and impossible to have
"the plan changed while we were submitting it" bugs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class TriggerType(StrEnum):
    SCHEDULE = "schedule"
    DRIFT = "drift"
    SCHEDULE_OR_DRIFT = "schedule_or_drift"


class BotStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class Holding:
    """A position as the broker reports it.

    Deliberately carries no price. Quotes arrive separately as a single price
    map covering held *and* not-yet-held target symbols, so there is exactly one
    source of truth for what a share is worth during a given run. Two price
    sources is how a planner ends up valuing the sell leg and the buy leg of the
    same rebalance differently.
    """

    symbol: str
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class Target:
    """A desired portfolio weight, in basis points (4000 == 40.00%)."""

    symbol: str
    weight_bps: int


@dataclass(frozen=True, slots=True)
class RebalanceSettings:
    """The knobs a user sets on a bot, normalised for the planner."""

    investment_amount: Decimal
    min_order_value: Decimal = Decimal(0)
    cash_buffer_bps: int = 0
    allow_fractional: bool = False
    max_orders: int = 25


@dataclass(frozen=True, slots=True)
class Drift:
    """Per-symbol distance between where we are and where we want to be."""

    symbol: str
    price: Decimal
    current_quantity: Decimal
    current_value: Decimal
    target_value: Decimal
    current_weight_bps: int
    target_weight_bps: int

    @property
    def drift_bps(self) -> int:
        """Positive means overweight, negative means underweight."""
        return self.current_weight_bps - self.target_weight_bps

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": str(self.price),
            "current_quantity": str(self.current_quantity),
            "current_value": str(self.current_value),
            "target_value": str(self.target_value),
            "current_weight_bps": self.current_weight_bps,
            "target_weight_bps": self.target_weight_bps,
            "drift_bps": self.drift_bps,
        }


@dataclass(frozen=True, slots=True)
class PlannedOrder:
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    estimated_value: Decimal
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": str(self.side),
            "quantity": str(self.quantity),
            "price": str(self.price),
            "estimated_value": str(self.estimated_value),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SkippedOrder:
    """An order the planner considered and deliberately did not place.

    Recorded rather than dropped: "why didn't my bot buy NVDA?" is the single
    most common question a rebalancer has to answer.
    """

    symbol: str
    side: Side
    intended_value: Decimal
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": str(self.side),
            "intended_value": str(self.intended_value),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RebalancePlan:
    orders: tuple[PlannedOrder, ...] = ()
    skipped: tuple[SkippedOrder, ...] = ()
    drifts: tuple[Drift, ...] = ()
    portfolio_value: Decimal = Decimal(0)
    investable_value: Decimal = Decimal(0)
    deployable_value: Decimal = Decimal(0)
    cash_before: Decimal = Decimal(0)
    projected_cash_after: Decimal = Decimal(0)
    max_drift_bps: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.orders

    def as_dict(self) -> dict[str, Any]:
        return {
            "orders": [o.as_dict() for o in self.orders],
            "skipped": [s.as_dict() for s in self.skipped],
            "drifts": [d.as_dict() for d in self.drifts],
            "portfolio_value": str(self.portfolio_value),
            "investable_value": str(self.investable_value),
            "deployable_value": str(self.deployable_value),
            "cash_before": str(self.cash_before),
            "projected_cash_after": str(self.projected_cash_after),
            "max_drift_bps": self.max_drift_bps,
        }


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    should_run: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Account:
    account_id: str
    cash: Decimal
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    quantity: Decimal
    avg_cost: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    broker_order_id: str
    symbol: str
    side: Side
    quantity: Decimal
    status: str
    filled_quantity: Decimal = Decimal(0)
    avg_fill_price: Decimal | None = None
    reject_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BotSpec:
    """A bot as the engine needs it: the row plus its targets, already typed."""

    id: str
    user_id: str
    name: str
    status: BotStatus
    trigger_type: TriggerType
    settings: RebalanceSettings
    targets: tuple[Target, ...] = ()
    interval_minutes: int | None = None
    drift_threshold_bps: int | None = None
    next_run_at: Any = None
    base_currency: str = "USD"
    extra: dict[str, Any] = field(default_factory=dict)
