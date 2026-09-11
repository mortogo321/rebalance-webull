"""The rebalance core.

This module is pure: no database, no network, no clock, no logging. Given a cash
balance, the current holdings, a price map and the bot's settings, it returns
the orders that move the portfolio toward its target weights -- or explains, per
symbol, why it declined to.

Keeping it pure is the point. Trading logic that can only be exercised by
standing up Postgres and a broker does not get tested at the edges, and the
edges (rounding to whole shares, a sell that fails to raise the cash the buys
assumed, a holding that is no longer in the target basket) are exactly where a
rebalancer loses money.

Method
------
1.  portfolio_value = cash + sum(quantity * price)
2.  investable      = min(investment_amount, portfolio_value)
       The bot manages a *sleeve*. It never deploys more than the user
       allocated, and if the account holds less than that, it works with
       what is actually there.
3.  deployable      = investable - cash_buffer
       The buffer is cash the user wants held back on purpose.
4.  target_value(s) = deployable * weight(s)
       Weights are measured against `deployable`, not `investable`, so a bot
       at rest reads as 0 bps of drift. Measuring against `investable` would
       leave every holding permanently short by the size of the buffer and
       re-trigger a drift rebalance forever.
5.  Held symbols absent from the basket get target_value 0 -> full liquidation.
6.  Sells are planned first, then buys are filled from the cash those sells are
    expected to raise, most-underweight first.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal

from .models import (
    Drift,
    Holding,
    PlannedOrder,
    RebalancePlan,
    RebalanceSettings,
    Side,
    SkippedOrder,
    Target,
)
from .money import BPS, ZERO, apply_bps, bps_of, money, quantity, whole_shares

TOTAL_WEIGHT_BPS = 10_000


class RebalanceError(ValueError):
    """Input that cannot produce a meaningful plan."""


# -----------------------------------------------------------------------------
# validation
# -----------------------------------------------------------------------------
def validate_targets(targets: Iterable[Target]) -> tuple[Target, ...]:
    """Reject a basket that cannot be honoured, with a message a user can act on."""
    items = tuple(targets)
    if not items:
        raise RebalanceError("bot has no target holdings")

    seen: set[str] = set()
    for t in items:
        if t.symbol in seen:
            raise RebalanceError(f"duplicate target symbol: {t.symbol}")
        seen.add(t.symbol)
        if t.weight_bps < 0 or t.weight_bps > TOTAL_WEIGHT_BPS:
            raise RebalanceError(
                f"{t.symbol}: weight {t.weight_bps} bps is outside 0-10000"
            )

    total = sum(t.weight_bps for t in items)
    if total != TOTAL_WEIGHT_BPS:
        raise RebalanceError(
            f"target weights must total 10000 bps (100%), got {total} bps"
        )
    return items


def _price_for(symbol: str, prices: Mapping[str, Decimal]) -> Decimal:
    price = prices.get(symbol)
    if price is None:
        raise RebalanceError(f"no price available for {symbol}")
    price = Decimal(price)
    if price <= ZERO:
        raise RebalanceError(f"{symbol}: price must be positive, got {price}")
    return price


# -----------------------------------------------------------------------------
# planner
# -----------------------------------------------------------------------------
def build_plan(
    *,
    cash: Decimal,
    holdings: Iterable[Holding],
    targets: Iterable[Target],
    prices: Mapping[str, Decimal],
    settings: RebalanceSettings,
) -> RebalancePlan:
    """Compute the orders that bring ``holdings`` back to ``targets``."""
    cash = money(cash)
    if cash < ZERO:
        raise RebalanceError(f"cash balance cannot be negative, got {cash}")
    if settings.investment_amount <= ZERO:
        raise RebalanceError("investment_amount must be greater than zero")

    target_items = validate_targets(targets)

    held: dict[str, Decimal] = {}
    for h in holdings:
        if h.symbol in held:
            raise RebalanceError(f"duplicate holding: {h.symbol}")
        if h.quantity < ZERO:
            raise RebalanceError(f"{h.symbol}: negative quantity {h.quantity}")
        if h.quantity > ZERO:
            held[h.symbol] = Decimal(h.quantity)

    target_bps = {t.symbol: t.weight_bps for t in target_items}
    # Union, order-stable: targets first (the intended basket), then anything
    # held that has fallen out of it and needs liquidating.
    universe = list(target_bps) + [s for s in held if s not in target_bps]

    price_of = {s: _price_for(s, prices) for s in universe}

    # --- 1-3: what are we actually working with? -----------------------------
    positions_value = sum(
        (held[s] * price_of[s] for s in held), start=ZERO
    )
    portfolio_value = money(cash + positions_value)
    investable = money(min(Decimal(settings.investment_amount), portfolio_value))
    deployable = money(investable - apply_bps(investable, settings.cash_buffer_bps))

    # --- 4-5: where should each symbol be? -----------------------------------
    drifts: list[Drift] = []
    for symbol in universe:
        qty = held.get(symbol, ZERO)
        price = price_of[symbol]
        current_value = money(qty * price)
        weight = target_bps.get(symbol, 0)
        target_value = money(apply_bps(deployable, weight))
        drifts.append(
            Drift(
                symbol=symbol,
                price=price,
                current_quantity=qty,
                current_value=current_value,
                target_value=target_value,
                current_weight_bps=bps_of(current_value, deployable),
                target_weight_bps=weight,
            )
        )

    max_drift_bps = max((abs(d.drift_bps) for d in drifts), default=0)

    # --- 6a: sells ------------------------------------------------------------
    min_value = Decimal(settings.min_order_value)
    sells: list[PlannedOrder] = []
    skipped: list[SkippedOrder] = []

    for drift in drifts:
        delta = drift.target_value - drift.current_value
        if delta >= ZERO:
            continue

        held_qty = held.get(drift.symbol, ZERO)
        if drift.target_value == ZERO:
            # Out of the basket entirely: exit the whole position. Rounding to
            # whole shares here would strand a fractional remainder forever.
            sell_qty = quantity(held_qty)
        else:
            raw = abs(delta) / drift.price
            sell_qty = quantity(raw) if settings.allow_fractional else whole_shares(raw)
            sell_qty = min(sell_qty, quantity(held_qty))

        value = money(sell_qty * drift.price)

        if sell_qty <= ZERO:
            skipped.append(
                SkippedOrder(
                    symbol=drift.symbol,
                    side=Side.SELL,
                    intended_value=money(abs(delta)),
                    reason=(
                        f"{money(abs(delta))} of {drift.symbol} rounds to 0 whole "
                        f"shares at {drift.price}"
                    ),
                )
            )
            continue
        if value < min_value:
            skipped.append(
                SkippedOrder(
                    symbol=drift.symbol,
                    side=Side.SELL,
                    intended_value=value,
                    reason=f"order value {value} is below the {min_value} minimum",
                )
            )
            continue

        sells.append(
            PlannedOrder(
                symbol=drift.symbol,
                side=Side.SELL,
                quantity=sell_qty,
                price=drift.price,
                estimated_value=value,
                reason=(
                    f"overweight by {drift.drift_bps} bps"
                    if drift.target_weight_bps
                    else "not in target basket"
                ),
            )
        )

    proceeds = sum((o.estimated_value for o in sells), start=ZERO)

    # --- 6b: buys, funded by cash on hand plus expected sell proceeds ---------
    #
    # In exact arithmetic the budget always covers the buys. It stops being
    # exact the moment a sell is rounded down to whole shares or dropped for
    # being under the minimum -- so the budget is enforced, not assumed.
    reserved = money(apply_bps(investable, settings.cash_buffer_bps))
    budget = money(cash + proceeds - reserved)
    if budget < ZERO:
        budget = ZERO

    # Most underweight first: when the budget cannot cover everything, the
    # holding furthest below target is the one worth fixing.
    buy_candidates = sorted(
        (d for d in drifts if d.target_value - d.current_value > ZERO),
        key=lambda d: d.drift_bps,
    )

    buys: list[PlannedOrder] = []
    remaining = budget
    for drift in buy_candidates:
        wanted = money(drift.target_value - drift.current_value)
        affordable = min(wanted, remaining)

        if affordable <= ZERO:
            skipped.append(
                SkippedOrder(
                    symbol=drift.symbol,
                    side=Side.BUY,
                    intended_value=wanted,
                    reason="no cash left after higher-priority buys",
                )
            )
            continue

        raw = affordable / drift.price
        buy_qty = quantity(raw) if settings.allow_fractional else whole_shares(raw)
        value = money(buy_qty * drift.price)

        if buy_qty <= ZERO:
            skipped.append(
                SkippedOrder(
                    symbol=drift.symbol,
                    side=Side.BUY,
                    intended_value=wanted,
                    reason=(
                        f"{affordable} buys 0 whole shares of {drift.symbol} "
                        f"at {drift.price}"
                    ),
                )
            )
            continue
        if value < min_value:
            skipped.append(
                SkippedOrder(
                    symbol=drift.symbol,
                    side=Side.BUY,
                    intended_value=value,
                    reason=f"order value {value} is below the {min_value} minimum",
                )
            )
            continue

        buys.append(
            PlannedOrder(
                symbol=drift.symbol,
                side=Side.BUY,
                quantity=buy_qty,
                price=drift.price,
                estimated_value=value,
                reason=f"underweight by {abs(drift.drift_bps)} bps",
            )
        )
        remaining = money(remaining - value)

    # --- ordering and blast-radius cap ---------------------------------------
    # Sells lead: they are what funds the buys, and the engine submits in order.
    ordered = sells + buys

    if len(ordered) > settings.max_orders:
        for dropped in ordered[settings.max_orders :]:
            skipped.append(
                SkippedOrder(
                    symbol=dropped.symbol,
                    side=dropped.side,
                    intended_value=dropped.estimated_value,
                    reason=(
                        f"run capped at {settings.max_orders} orders; deferred to "
                        "the next rebalance"
                    ),
                )
            )
        ordered = ordered[: settings.max_orders]

    spent = sum((o.estimated_value for o in ordered if o.side is Side.BUY), start=ZERO)
    raised = sum((o.estimated_value for o in ordered if o.side is Side.SELL), start=ZERO)

    return RebalancePlan(
        orders=tuple(ordered),
        skipped=tuple(skipped),
        drifts=tuple(drifts),
        portfolio_value=portfolio_value,
        investable_value=investable,
        deployable_value=deployable,
        cash_before=cash,
        projected_cash_after=money(cash + raised - spent),
        max_drift_bps=max_drift_bps,
    )
