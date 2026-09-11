"""Decimal helpers.

Every monetary and quantity value in the engine is a ``Decimal``. Floats are
never used for money: 0.1 + 0.2 != 0.3 is not a curiosity in a rebalancer, it
is a position that slowly stops matching the broker's books.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

#: Money is stored as numeric(20,4) in Postgres; mirror that here.
MONEY = Decimal("0.0001")
#: Quantities are numeric(20,8) -- fractional-share brokers quote 6-8 dp.
QUANTITY = Decimal("0.00000001")

BPS = Decimal(10000)
ZERO = Decimal(0)


def money(value: Decimal | int | str) -> Decimal:
    """Round a value to storable money precision, half-up (the accounting norm)."""
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def quantity(value: Decimal | int | str) -> Decimal:
    """Round a share quantity *down* to storable precision.

    Always down, never nearest: rounding a quantity up invents shares that the
    cash on hand may not cover, which surfaces as a broker rejection.
    """
    return Decimal(value).quantize(QUANTITY, rounding=ROUND_DOWN)


def whole_shares(value: Decimal) -> Decimal:
    """Truncate toward zero to a whole share count."""
    return Decimal(int(Decimal(value).to_integral_value(rounding=ROUND_DOWN)))


def bps_of(part: Decimal, whole: Decimal) -> int:
    """``part`` as basis points of ``whole``; 0 when ``whole`` is 0."""
    if whole <= ZERO:
        return 0
    return int((part / whole * BPS).to_integral_value(rounding=ROUND_HALF_UP))


def apply_bps(value: Decimal, bps: int) -> Decimal:
    """``bps`` basis points of ``value``."""
    return value * Decimal(bps) / BPS
