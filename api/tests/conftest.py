from __future__ import annotations

from decimal import Decimal

import pytest

from engine.models import Holding, RebalanceSettings, Target


def d(value: str | int) -> Decimal:
    return Decimal(str(value))


@pytest.fixture
def settings() -> RebalanceSettings:
    return RebalanceSettings(
        investment_amount=d(100_000),
        min_order_value=d(0),
        cash_buffer_bps=0,
        allow_fractional=False,
    )


@pytest.fixture
def basket() -> tuple[Target, ...]:
    """40 / 35 / 25 -- deliberately not three equal thirds, which would hide
    rounding bugs that only appear when weights differ."""
    return (
        Target("AAPL", 4000),
        Target("MSFT", 3500),
        Target("NVDA", 2500),
    )


@pytest.fixture
def prices() -> dict[str, Decimal]:
    return {"AAPL": d(200), "MSFT": d(400), "NVDA": d(125), "TSLA": d(250)}


def holding(symbol: str, qty: str | int) -> Holding:
    return Holding(symbol=symbol, quantity=d(qty))
