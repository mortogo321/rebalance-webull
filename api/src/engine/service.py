"""Rebalance orchestration: the only place that both decides and acts.

One evaluation, end to end:

    1. build a broker for the user (mock, or Webull UAT with their credentials)
    2. read cash, positions and quotes
    3. build a plan -- pure, no side effects
    4. ask the trigger whether this plan should actually be executed
    5. if not, still write a `skipped` run so the history explains the silence
    6. otherwise submit orders in order, recording each before and after
    7. close out the run and advance the bot's schedule

Step 3 before step 4 is deliberate: the drift trigger needs a plan to know how
far off target the portfolio is, and a plan is cheap because it is pure.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from .brokers.base import Broker, BrokerAuthError, BrokerError
from .brokers.mock import MockBroker
from .brokers.webull import WebullUatBroker
from .config import Settings
from .crypto import decrypt, hint_of, load_key
from .db import Database
from .models import (
    BotSpec,
    Holding,
    RebalancePlan,
    Side,
    TriggerDecision,
)
from .money import money
from .rebalance import RebalanceError, build_plan
from .repo import (
    create_run,
    finish_run,
    get_encrypted_credentials,
    record_order,
    set_connection_state,
    touch_bot,
    update_order_result,
)
from .triggers import evaluate_trigger, next_run_after

log = logging.getLogger(__name__)


class RunOutcome:
    """What happened, in a shape the API can return and a human can read."""

    def __init__(
        self,
        *,
        executed: bool,
        reason: str,
        run_id: str | None = None,
        plan: RebalancePlan | None = None,
        submitted: int = 0,
        error: str | None = None,
    ) -> None:
        self.executed = executed
        self.reason = reason
        self.run_id = run_id
        self.plan = plan
        self.submitted = submitted
        self.error = error

    def as_dict(self) -> dict:
        return {
            "executed": self.executed,
            "reason": self.reason,
            "run_id": self.run_id,
            "submitted_orders": self.submitted,
            "error": self.error,
            "plan": self.plan.as_dict() if self.plan else None,
        }


class RebalanceService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._key = load_key(settings.app_encryption_key)

    # -- broker construction -------------------------------------------------
    async def broker_for(self, user_id: str) -> Broker:
        """Build the configured broker for a user.

        Credentials are decrypted here and nowhere else, held only for the life
        of the call, and never written to a log or returned to a caller.
        """
        if self._settings.broker_mode == "mock":
            return MockBroker(
                self._db,
                user_id=user_id,
                starting_cash=self._settings.mock_starting_cash,
                price_seed=self._settings.mock_price_seed,
                volatility_bps=self._settings.mock_price_volatility_bps,
            )

        row = await get_encrypted_credentials(self._db, user_id=user_id)
        if row is None:
            raise BrokerAuthError("no broker credentials on file for this account")

        api_key = decrypt(row["api_key_encrypted"], key=self._key, aad=user_id)
        api_secret = decrypt(row["api_secret_encrypted"], key=self._key, aad=user_id)
        return WebullUatBroker(
            app_key=api_key,
            app_secret=api_secret,
            base_url=self._settings.webull_base_url,
            allowed_hosts=self._settings.allowed_broker_hosts,
            timeout=self._settings.webull_request_timeout_seconds,
        )

    async def verify_connection(self, user_id: str) -> dict:
        """Probe the broker and record the result on the user's connection row."""
        try:
            broker = await self.broker_for(user_id)
            account = await broker.get_account()
        except Exception as exc:  # noqa: BLE001 - every failure is surfaced to the user
            # A BrokerError carries a message written to be read by a user
            # ("Webull rejected these credentials..."). Anything else is an
            # internal fault, and its text could name a host or a driver, so it
            # is replaced rather than forwarded.
            message = str(exc) if isinstance(exc, BrokerError) else "connection check failed"
            await set_connection_state(
                self._db, user_id=user_id, status="error", last_error=message
            )
            log.warning("connection check failed user=%s: %s", user_id, message)
            return {"status": "error", "error": message}

        await set_connection_state(
            self._db,
            user_id=user_id,
            status="connected",
            environment=self._settings.broker_mode,
            account_id=account.account_id,
            account_currency=account.currency,
            last_error=None,
            verified=True,
        )
        return {
            "status": "connected",
            "environment": self._settings.broker_mode,
            "account_id": account.account_id,
            "currency": account.currency,
        }

    async def store_credentials(self, user_id: str, *, api_key: str) -> None:
        """Record the non-secret half of a credential update.

        The ciphertext itself is written by the edge function, which is where
        the plaintext arrives; the engine only refreshes the display hint and
        re-probes the connection.
        """
        await set_connection_state(
            self._db,
            user_id=user_id,
            status="disconnected",
            environment=self._settings.broker_mode,
            api_key_hint=hint_of(api_key),
        )

    # -- portfolio snapshot --------------------------------------------------
    async def snapshot(self, user_id: str, *, symbols: list[str] | None = None) -> dict:
        """Cash, positions and quotes -- what the connection screen displays."""
        broker = await self.broker_for(user_id)
        account = await broker.get_account()
        positions = await broker.get_positions()

        wanted = sorted({p.symbol for p in positions} | set(symbols or []))
        quotes = await broker.get_quotes(wanted) if wanted else {}

        holdings = []
        for p in positions:
            price = quotes.get(p.symbol, Decimal(0))
            holdings.append(
                {
                    "symbol": p.symbol,
                    "quantity": str(p.quantity),
                    "avg_cost": str(p.avg_cost),
                    "price": str(price),
                    "market_value": str(money(p.quantity * price)),
                }
            )

        equity = sum((Decimal(h["market_value"]) for h in holdings), start=Decimal(0))
        return {
            "account_id": account.account_id,
            "currency": account.currency,
            "cash": str(account.cash),
            "equity": str(money(equity)),
            "total_value": str(money(account.cash + equity)),
            "positions": holdings,
            "quotes": {k: str(v) for k, v in quotes.items()},
            "broker": broker.name,
        }

    # -- the main loop body --------------------------------------------------
    async def evaluate(self, bot: BotSpec, *, force: bool = False) -> RunOutcome:
        """Evaluate one bot and, if it is due, execute the resulting plan."""
        now = datetime.now(UTC)

        if not bot.targets:
            return await self._skip(bot, now, "bot has no target holdings")

        try:
            broker = await self.broker_for(bot.user_id)
            positions = await broker.get_positions()
            account = await broker.get_account()
            symbols = sorted({t.symbol for t in bot.targets} | {p.symbol for p in positions})
            quotes = await broker.get_quotes(symbols)
        except BrokerError as exc:
            return await self._fail(bot, now, f"broker unavailable: {exc}")

        try:
            plan = build_plan(
                cash=account.cash,
                holdings=[Holding(symbol=p.symbol, quantity=p.quantity) for p in positions],
                targets=bot.targets,
                prices=quotes,
                settings=bot.settings,
            )
        except RebalanceError as exc:
            return await self._fail(bot, now, f"cannot plan a rebalance: {exc}")

        decision = (
            TriggerDecision(True, "manual run requested")
            if force
            else evaluate_trigger(
                status=bot.status,
                trigger_type=bot.trigger_type,
                now=now,
                next_run_at=bot.next_run_at,
                max_drift_bps=plan.max_drift_bps,
                drift_threshold_bps=bot.drift_threshold_bps,
            )
        )

        if not decision.should_run:
            return await self._skip(bot, now, decision.reason, plan=plan)

        if plan.is_empty:
            # Due, but there is nothing worth trading -- every candidate order
            # was dust, unaffordable, or rounded to zero shares. Still a run:
            # the skipped list explains why.
            return await self._skip(
                bot, now, f"{decision.reason}; no orders met the execution criteria", plan=plan
            )

        return await self._execute(bot, now, broker, plan, decision.reason)

    # -- outcomes ------------------------------------------------------------
    async def _execute(
        self, bot: BotSpec, now: datetime, broker: Broker, plan: RebalancePlan, reason: str
    ) -> RunOutcome:
        run_id = await create_run(
            self._db,
            bot_id=bot.id,
            user_id=bot.user_id,
            status="running",
            trigger_reason=reason,
            plan=plan.as_dict(),
            portfolio_value=plan.portfolio_value,
            cash_before=plan.cash_before,
            max_drift_bps=plan.max_drift_bps,
            planned_order_count=len(plan.orders),
        )

        submitted = 0
        failures: list[str] = []

        for index, planned in enumerate(plan.orders):
            # Deterministic from (run, position in run): a retry of this exact
            # run reuses the same key and the unique index absorbs the duplicate
            # instead of the venue filling it twice.
            client_order_id = f"{run_id}:{index}:{planned.symbol}:{planned.side}"
            order_id = await record_order(
                self._db,
                run_id=run_id,
                bot_id=bot.id,
                user_id=bot.user_id,
                order=planned,
                client_order_id=client_order_id,
            )

            try:
                result = await broker.place_order(
                    symbol=planned.symbol,
                    side=planned.side,
                    quantity=planned.quantity,
                    client_order_id=client_order_id,
                )
            except BrokerError as exc:
                await update_order_result(
                    self._db, order_id=order_id, status="rejected", reject_reason=str(exc)
                )
                failures.append(f"{planned.symbol}: {exc}")
                # Keep going. A venue rejecting one symbol should not abandon a
                # half-rebalanced portfolio -- especially not after the sells
                # have already gone through.
                continue

            await update_order_result(
                self._db,
                order_id=order_id,
                status=result.status,
                filled_quantity=result.filled_quantity,
                avg_fill_price=result.avg_fill_price,
                broker_order_id=result.broker_order_id,
                reject_reason=result.reject_reason,
            )
            if result.status in ("filled", "partially_filled", "submitted"):
                submitted += 1
            else:
                failures.append(f"{planned.symbol}: {result.reject_reason or result.status}")

        # A run that placed nothing is a failure. A run that placed some orders
        # and had others rejected is a success with a recorded error: the
        # portfolio did move, and `error` names precisely what did not.
        status = "failed" if failures and submitted == 0 else "succeeded"
        error = "; ".join(failures) if failures else None
        await finish_run(
            self._db,
            run_id=run_id,
            status=status,
            submitted_order_count=submitted,
            error=error,
        )
        await touch_bot(
            self._db,
            bot_id=bot.id,
            last_evaluated_at=now,
            next_run_at=next_run_after(now, bot.interval_minutes),
            rebalanced=submitted > 0,
        )

        log.info(
            "run %s bot=%s submitted=%d/%d status=%s",
            run_id, bot.id, submitted, len(plan.orders), status,
        )
        return RunOutcome(
            executed=True, reason=reason, run_id=run_id, plan=plan,
            submitted=submitted, error=error,
        )

    async def _skip(
        self, bot: BotSpec, now: datetime, reason: str, plan: RebalancePlan | None = None
    ) -> RunOutcome:
        run_id = await create_run(
            self._db,
            bot_id=bot.id,
            user_id=bot.user_id,
            status="skipped",
            trigger_reason=reason,
            plan=plan.as_dict() if plan else None,
            portfolio_value=plan.portfolio_value if plan else None,
            cash_before=plan.cash_before if plan else None,
            max_drift_bps=plan.max_drift_bps if plan else None,
            planned_order_count=len(plan.orders) if plan else 0,
        )
        await finish_run(self._db, run_id=run_id, status="skipped")
        await touch_bot(
            self._db,
            bot_id=bot.id,
            last_evaluated_at=now,
            # Only a schedule-driven bot advances its clock on a skip; a drift
            # bot has no clock to advance.
            next_run_at=(
                bot.next_run_at
                if bot.next_run_at and bot.next_run_at > now
                else next_run_after(now, bot.interval_minutes)
            ),
        )
        return RunOutcome(executed=False, reason=reason, run_id=run_id, plan=plan)

    async def _fail(self, bot: BotSpec, now: datetime, reason: str) -> RunOutcome:
        run_id = await create_run(
            self._db,
            bot_id=bot.id,
            user_id=bot.user_id,
            status="failed",
            trigger_reason="evaluation attempted",
        )
        await finish_run(self._db, run_id=run_id, status="failed", error=reason)
        await touch_bot(
            self._db,
            bot_id=bot.id,
            last_evaluated_at=now,
            next_run_at=next_run_after(now, bot.interval_minutes),
        )
        log.warning("bot %s evaluation failed: %s", bot.id, reason)
        return RunOutcome(executed=False, reason=reason, run_id=run_id, error=reason)
