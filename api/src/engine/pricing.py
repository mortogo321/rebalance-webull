"""Deterministic quote generation for the mock broker.

A demo needs prices that *move* -- otherwise drift never appears and the drift
trigger cannot be shown working -- but a test needs prices that do not. Both are
satisfied by making the price a pure function of (symbol, date, seed): it walks
day to day, and it is identical on every machine and every re-run.

No network, no randomness, no clock beyond the date handed in.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

from .money import money

# Recognisable anchors so the demo reads like a real portfolio. Anything absent
# falls back to a hash-derived price in the same range.
_BASE_PRICES: dict[str, Decimal] = {
    "AAPL": Decimal(200),
    "MSFT": Decimal(400),
    "NVDA": Decimal(125),
    "GOOGL": Decimal(180),
    "AMZN": Decimal(185),
    "TSLA": Decimal(250),
    "META": Decimal(520),
    "SPY": Decimal(560),
    "QQQ": Decimal(480),
    "VTI": Decimal(280),
}

_MIN_PRICE = Decimal("1.00")


def _digest(*parts: str) -> int:
    return int.from_bytes(
        hashlib.sha256("|".join(parts).encode("utf-8")).digest()[:8], "big"
    )


def base_price(symbol: str) -> Decimal:
    """The symbol's anchor price: known tickers keep a familiar level."""
    known = _BASE_PRICES.get(symbol.upper())
    if known is not None:
        return known
    # Unknown symbols land somewhere in $20-$520, stable for that symbol forever.
    return Decimal(20) + Decimal(_digest("base", symbol.upper()) % 50_000) / Decimal(100)


def price_for(
    symbol: str,
    *,
    on: date,
    seed: int = 20260101,
    volatility_bps: int = 150,
) -> Decimal:
    """The quote for ``symbol`` on ``on``.

    The daily move is a signed offset of at most ``volatility_bps``, derived from
    the symbol, the date and the seed. Two different symbols move independently;
    the same symbol on the same day always returns the same number.
    """
    symbol = symbol.upper()
    anchor = base_price(symbol)

    # Two independent draws: one for magnitude, one for direction. Deriving both
    # from a single value correlates them and produces a visible sawtooth.
    raw = _digest(str(seed), symbol, on.isoformat())
    magnitude = Decimal(raw % (volatility_bps + 1))
    direction = Decimal(1) if (raw >> 32) % 2 else Decimal(-1)

    # A slow drift across the month keeps a multi-day demo from oscillating
    # around a fixed point without ever going anywhere.
    trend_bps = Decimal((_digest("trend", str(seed), symbol) % 61) - 30) * Decimal(
        on.day
    ) / Decimal(10)

    move_bps = direction * magnitude + trend_bps
    price = anchor * (Decimal(10_000) + move_bps) / Decimal(10_000)
    return money(max(price, _MIN_PRICE))


def quotes_for(
    symbols: list[str],
    *,
    on: date,
    seed: int = 20260101,
    volatility_bps: int = 150,
) -> dict[str, Decimal]:
    return {
        s: price_for(s, on=on, seed=seed, volatility_bps=volatility_bps)
        for s in dict.fromkeys(s.upper() for s in symbols)
    }
