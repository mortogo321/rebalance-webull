"""Data access.

Every statement here runs as the service role, which bypasses RLS. The database
is therefore *not* enforcing tenant isolation on this path -- so each query
carries its own explicit ``user_id`` predicate. The rule for this module: if a
function touches user-owned data and does not take a ``user_id``, it is a bug.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from .db import Database
from .models import (
    BotSpec,
    BotStatus,
    PlannedOrder,
    RebalanceSettings,
    Target,
    TriggerType,
)
from .money import money

_BOT_COLUMNS = """
    b.id, b.user_id, b.name, b.status, b.base_currency, b.investment_amount,
    b.trigger_type, b.interval_minutes, b.drift_threshold_bps,
    b.min_order_value, b.cash_buffer_bps, b.allow_fractional,
    b.next_run_at, b.last_evaluated_at, b.last_rebalanced_at
"""


def _to_spec(row: dict[str, Any], targets: list[dict[str, Any]], max_orders: int) -> BotSpec:
    return BotSpec(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        name=row["name"],
        status=BotStatus(row["status"]),
        trigger_type=TriggerType(row["trigger_type"]),
        settings=RebalanceSettings(
            investment_amount=money(row["investment_amount"]),
            min_order_value=money(row["min_order_value"]),
            cash_buffer_bps=row["cash_buffer_bps"],
            allow_fractional=row["allow_fractional"],
            max_orders=max_orders,
        ),
        targets=tuple(
            Target(symbol=t["symbol"], weight_bps=t["target_weight_bps"]) for t in targets
        ),
        interval_minutes=row["interval_minutes"],
        drift_threshold_bps=row["drift_threshold_bps"],
        next_run_at=row["next_run_at"],
        base_currency=row["base_currency"],
    )


async def get_bot(
    db: Database, *, bot_id: str, user_id: str, max_orders: int = 25
) -> BotSpec | None:
    row = await db.fetch_one(
        f"select {_BOT_COLUMNS} from public.bots b where b.id = %s and b.user_id = %s",
        (bot_id, user_id),
    )
    if row is None:
        return None
    targets = await db.fetch_all(
        "select symbol, target_weight_bps from public.bot_targets where bot_id = %s order by symbol",
        (bot_id,),
    )
    return _to_spec(row, targets, max_orders)


async def list_running_bots(db: Database, *, max_orders: int = 25) -> list[BotSpec]:
    """Every running bot, with its basket, in one round trip.

    Fetching targets per bot would be an N+1 against the scheduler's hot loop;
    one join and a group in Python keeps a tick at two queries regardless of how
    many bots exist.
    """
    rows = await db.fetch_all(
        f"select {_BOT_COLUMNS} from public.bots b where b.status = 'running' order by b.next_run_at nulls first"
    )
    if not rows:
        return []

    bot_ids = [r["id"] for r in rows]
    target_rows = await db.fetch_all(
        """
        select bot_id, symbol, target_weight_bps
          from public.bot_targets
         where bot_id = any(%s)
         order by symbol
        """,
        (bot_ids,),
    )
    by_bot: dict[str, list[dict[str, Any]]] = {}
    for t in target_rows:
        by_bot.setdefault(str(t["bot_id"]), []).append(t)

    return [_to_spec(r, by_bot.get(str(r["id"]), []), max_orders) for r in rows]


# -- credentials ---------------------------------------------------------------
async def get_encrypted_credentials(db: Database, *, user_id: str) -> dict[str, Any] | None:
    return await db.fetch_one(
        """
        select api_key_encrypted, api_secret_encrypted, key_version
          from private.broker_credentials
         where user_id = %s
        """,
        (user_id,),
    )


async def upsert_credentials(
    db: Database,
    *,
    user_id: str,
    api_key_encrypted: str,
    api_secret_encrypted: str,
    key_version: int,
) -> None:
    await db.execute(
        """
        insert into private.broker_credentials
              (user_id, api_key_encrypted, api_secret_encrypted, key_version)
        values (%s, %s, %s, %s)
        on conflict (user_id) do update
            set api_key_encrypted    = excluded.api_key_encrypted,
                api_secret_encrypted = excluded.api_secret_encrypted,
                key_version          = excluded.key_version,
                updated_at           = now()
        """,
        (user_id, api_key_encrypted, api_secret_encrypted, key_version),
    )


async def delete_credentials(db: Database, *, user_id: str) -> None:
    await db.execute(
        "delete from private.broker_credentials where user_id = %s", (user_id,)
    )


async def set_connection_state(
    db: Database,
    *,
    user_id: str,
    status: str,
    environment: str | None = None,
    account_id: str | None = None,
    account_currency: str | None = None,
    api_key_hint: str | None = None,
    last_error: str | None = None,
    verified: bool = False,
) -> None:
    await db.execute(
        """
        insert into public.broker_connections
              (user_id, status, environment, account_id, account_currency,
               api_key_hint, last_error, last_verified_at)
        values (%s, %s, coalesce(%s, 'mock'), %s, %s, %s, %s,
                case when %s then now() else null end)
        on conflict (user_id) do update
            set status           = excluded.status,
                environment      = coalesce(excluded.environment, public.broker_connections.environment),
                account_id       = coalesce(excluded.account_id, public.broker_connections.account_id),
                account_currency = coalesce(excluded.account_currency, public.broker_connections.account_currency),
                api_key_hint     = coalesce(excluded.api_key_hint, public.broker_connections.api_key_hint),
                last_error       = excluded.last_error,
                last_verified_at = coalesce(excluded.last_verified_at, public.broker_connections.last_verified_at),
                updated_at       = now()
        """,
        (
            user_id, status, environment, account_id, account_currency,
            api_key_hint, last_error, verified,
        ),
    )


# -- runs and orders -----------------------------------------------------------
async def create_run(
    db: Database,
    *,
    bot_id: str,
    user_id: str,
    status: str,
    trigger_reason: str,
    plan: dict[str, Any] | None = None,
    portfolio_value: Decimal | None = None,
    cash_before: Decimal | None = None,
    max_drift_bps: int | None = None,
    planned_order_count: int = 0,
) -> str:
    import json

    row = await db.fetch_one(
        """
        insert into public.rebalance_runs
              (bot_id, user_id, status, trigger_reason, plan, portfolio_value,
               cash_before, max_drift_bps, planned_order_count)
        values (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
        returning id
        """,
        (
            bot_id, user_id, status, trigger_reason,
            json.dumps(plan) if plan is not None else None,
            portfolio_value, cash_before, max_drift_bps, planned_order_count,
        ),
    )
    assert row is not None
    return str(row["id"])


async def finish_run(
    db: Database,
    *,
    run_id: str,
    status: str,
    submitted_order_count: int = 0,
    error: str | None = None,
) -> None:
    await db.execute(
        """
        update public.rebalance_runs
           set status = %s, submitted_order_count = %s, error = %s, finished_at = now()
         where id = %s
        """,
        (status, submitted_order_count, error, run_id),
    )


async def record_order(
    db: Database,
    *,
    run_id: str,
    bot_id: str,
    user_id: str,
    order: PlannedOrder,
    client_order_id: str,
) -> str:
    row = await db.fetch_one(
        """
        insert into public.orders
              (run_id, bot_id, user_id, symbol, side, quantity, requested_value,
               client_order_id, status)
        values (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
        on conflict (client_order_id) do nothing
        returning id
        """,
        (
            run_id, bot_id, user_id, order.symbol, str(order.side),
            order.quantity, order.estimated_value, client_order_id,
        ),
    )
    if row is not None:
        return str(row["id"])
    # The insert collided: this order was already recorded by an earlier attempt
    # at the same run. Reuse it rather than creating a duplicate.
    existing = await db.fetch_one(
        "select id from public.orders where client_order_id = %s", (client_order_id,)
    )
    assert existing is not None
    return str(existing["id"])


async def update_order_result(
    db: Database,
    *,
    order_id: str,
    status: str,
    filled_quantity: Decimal = Decimal(0),
    avg_fill_price: Decimal | None = None,
    broker_order_id: str | None = None,
    reject_reason: str | None = None,
) -> None:
    await db.execute(
        """
        update public.orders
           set status = %s, filled_quantity = %s, avg_fill_price = %s,
               broker_order_id = %s, reject_reason = %s
         where id = %s
        """,
        (status, filled_quantity, avg_fill_price, broker_order_id, reject_reason, order_id),
    )


async def touch_bot(
    db: Database,
    *,
    bot_id: str,
    last_evaluated_at: datetime,
    next_run_at: datetime | None,
    rebalanced: bool = False,
) -> None:
    await db.execute(
        """
        update public.bots
           set last_evaluated_at  = %s,
               next_run_at        = %s,
               last_rebalanced_at = case when %s then %s else last_rebalanced_at end
         where id = %s
        """,
        (last_evaluated_at, next_run_at, rebalanced, last_evaluated_at, bot_id),
    )
