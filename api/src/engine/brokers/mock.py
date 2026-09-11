"""Deterministic paper broker.

Default everywhere, and the only broker development is permitted to use. Market
orders fill immediately at the current quote; state lives in ``private.paper_*``
rather than in process memory, so restarting the engine mid-demo does not
silently reset everyone's portfolio.

It is deliberately strict -- it rejects a sell of shares you do not hold and a
buy you cannot afford -- because a mock that always succeeds hides exactly the
execution bugs it exists to catch.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from ..db import Database
from ..models import Account, BrokerOrder, Position, Side
from ..money import ZERO, money, quantity
from ..pricing import quotes_for
from .base import BrokerError

log = logging.getLogger(__name__)


class MockBroker:
    name = "mock"

    def __init__(
        self,
        db: Database,
        *,
        user_id: str,
        starting_cash: Decimal = Decimal(100_000),
        price_seed: int = 20260101,
        volatility_bps: int = 150,
        now: datetime | None = None,
    ) -> None:
        self._db = db
        self._user_id = user_id
        self._starting_cash = starting_cash
        self._seed = price_seed
        self._volatility_bps = volatility_bps
        self._now = now or datetime.now(UTC)

    # -- account ------------------------------------------------------------
    async def _ensure_account(self) -> dict:
        row = await self._db.fetch_one(
            "select user_id, cash, currency from private.paper_accounts where user_id = %s",
            (self._user_id,),
        )
        if row is None:
            row = await self._db.fetch_one(
                """
                insert into private.paper_accounts (user_id, cash, currency)
                values (%s, %s, 'USD')
                on conflict (user_id) do update set cash = private.paper_accounts.cash
                returning user_id, cash, currency
                """,
                (self._user_id, self._starting_cash),
            )
        assert row is not None
        return row

    async def get_account(self) -> Account:
        row = await self._ensure_account()
        return Account(
            account_id=f"PAPER-{self._user_id[:8]}",
            cash=money(row["cash"]),
            currency=row["currency"],
        )

    async def get_positions(self) -> list[Position]:
        rows = await self._db.fetch_all(
            """
            select symbol, quantity, avg_cost
              from private.paper_positions
             where user_id = %s and quantity > 0
             order by symbol
            """,
            (self._user_id,),
        )
        return [
            Position(
                symbol=r["symbol"],
                quantity=quantity(r["quantity"]),
                avg_cost=money(r["avg_cost"]),
            )
            for r in rows
        ]

    async def get_quotes(self, symbols: list[str]) -> dict[str, Decimal]:
        if not symbols:
            return {}
        return quotes_for(
            symbols,
            on=self._now.date(),
            seed=self._seed,
            volatility_bps=self._volatility_bps,
        )

    # -- execution ----------------------------------------------------------
    async def place_order(
        self,
        *,
        symbol: str,
        side: Side,
        quantity: Decimal,  # noqa: A002 - matches the Broker protocol
        client_order_id: str,
        limit_price: Decimal | None = None,
    ) -> BrokerOrder:
        symbol = symbol.upper()
        qty = Decimal(quantity)
        if qty <= ZERO:
            raise BrokerError(f"{symbol}: order quantity must be positive")

        price = (await self.get_quotes([symbol]))[symbol]
        notional = money(qty * price)

        # One transaction per order: cash and position must move together, or a
        # crash between the two leaves a portfolio that does not add up.
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "select cash from private.paper_accounts where user_id = %s for update",
                (self._user_id,),
            )
            account = await cur.fetchone()
            if account is None:
                raise BrokerError("paper account not initialised")
            cash = money(account["cash"])

            cur = await conn.execute(
                """
                select quantity, avg_cost from private.paper_positions
                 where user_id = %s and symbol = %s for update
                """,
                (self._user_id, symbol),
            )
            held_row = await cur.fetchone()
            held = money(held_row["quantity"]) if held_row else ZERO
            avg_cost = money(held_row["avg_cost"]) if held_row else ZERO

            if side is Side.BUY:
                if notional > cash:
                    return BrokerOrder(
                        broker_order_id=client_order_id,
                        symbol=symbol,
                        side=side,
                        quantity=qty,
                        status="rejected",
                        reject_reason=(
                            f"insufficient buying power: need {notional}, have {cash}"
                        ),
                    )
                new_qty = held + qty
                # Weighted average cost, the convention every broker statement uses.
                new_cost = money(((held * avg_cost) + notional) / new_qty)
                new_cash = money(cash - notional)
            else:
                if qty > held:
                    return BrokerOrder(
                        broker_order_id=client_order_id,
                        symbol=symbol,
                        side=side,
                        quantity=qty,
                        status="rejected",
                        reject_reason=f"cannot sell {qty} {symbol}, holding {held}",
                    )
                new_qty = held - qty
                new_cost = avg_cost  # selling does not change the cost basis
                new_cash = money(cash + notional)

            await conn.execute(
                "update private.paper_accounts set cash = %s, updated_at = now() where user_id = %s",
                (new_cash, self._user_id),
            )
            await conn.execute(
                """
                insert into private.paper_positions (user_id, symbol, quantity, avg_cost)
                values (%s, %s, %s, %s)
                on conflict (user_id, symbol)
                do update set quantity = excluded.quantity,
                              avg_cost = excluded.avg_cost,
                              updated_at = now()
                """,
                (self._user_id, symbol, new_qty, new_cost),
            )

        log.info(
            "paper fill user=%s %s %s %s @ %s", self._user_id, side, qty, symbol, price
        )
        return BrokerOrder(
            broker_order_id=client_order_id,
            symbol=symbol,
            side=side,
            quantity=qty,
            status="filled",
            filled_quantity=qty,
            avg_fill_price=price,
        )
