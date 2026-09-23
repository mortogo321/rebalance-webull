"""Background evaluation loop.

A plain asyncio task rather than a cron library: the work is "wake up, ask the
database which bots are running, evaluate each", and a scheduler framework would
add a dependency and a second source of truth about timing without removing any
of that.

Three properties matter more than throughput here:

* Exactly one engine may evaluate at a time, however many containers are
  running. A blue/green deploy has both colours live simultaneously, and two
  schedulers on the same database would each plan the same rebalance and place
  it twice. A Postgres session-level advisory lock elects a single leader; the
  standby ticks, fails to take the lock, and does nothing. The lock is released
  automatically if the leader's connection dies, so a crashed leader is replaced
  on the next tick with no manual intervention.
* One slow or failing bot must not stall the others, so each evaluation is
  isolated and its exceptions are swallowed into the run record.
* A tick that overruns must not pile up, so ticks are sequential -- the next one
  starts after the previous finishes, not on a fixed wall clock.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from .config import Settings
from .db import Database
from .repo import list_running_bots
from .service import RebalanceService

log = logging.getLogger(__name__)

# Arbitrary but fixed: any engine process on this database competes for this one
# lock id. Changing it would let an old and a new deployment both think they are
# the leader, so it is a constant, not configuration.
LEADER_LOCK_ID = 0x5245_4241  # "REBA"


class Scheduler:
    def __init__(self, db: Database, settings: Settings, service: RebalanceService) -> None:
        self._db = db
        self._settings = settings
        self._service = service
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self.last_tick_at: datetime | None = None
        self.last_tick_bots: int = 0
        self.is_leader: bool = False

    def start(self) -> None:
        if not self._settings.scheduler_enabled:
            log.info("scheduler disabled by configuration")
            return
        self._task = asyncio.create_task(self._loop(), name="rebalance-scheduler")
        log.info("scheduler started, tick=%ss", self._settings.scheduler_tick_seconds)

    async def stop(self) -> None:
        """Stop accepting new work and let the current tick finish.

        Called from the shutdown hook. Draining rather than cancelling is what
        makes a blue/green swap safe: a container being retired must not abandon
        a rebalance with its sells filled and its buys not yet placed.
        """
        self._stopping.set()
        if self._task:
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(self._task, timeout=30)
            self._task = None
        log.info("scheduler stopped")

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._tick()
            except Exception:
                # Never let a tick kill the loop; the next one may well succeed.
                log.exception("scheduler tick failed")

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._settings.scheduler_tick_seconds
                )

    async def _tick(self) -> None:
        # The lock is held for the duration of this connection only. Taking it
        # per tick rather than for the process lifetime means a deploy hands
        # leadership over within one tick instead of waiting for a container to
        # exit, and a wedged leader cannot hold the lock indefinitely.
        async with self._db.connection() as conn:
            cur = await conn.execute(
                "select pg_try_advisory_lock(%s) as acquired", (LEADER_LOCK_ID,)
            )
            row = await cur.fetchone()
            acquired = bool(row and row["acquired"])

            if not acquired:
                if self.is_leader:
                    log.info("another engine holds the scheduler lock; standing by")
                self.is_leader = False
                self.last_tick_at = datetime.now(UTC)
                return

            if not self.is_leader:
                log.info("acquired the scheduler lock; this engine is the leader")
            self.is_leader = True

            try:
                await self._evaluate_all()
            finally:
                # Released explicitly so the connection can go back to the pool
                # still holding no locks; the standby can take over immediately.
                await conn.execute("select pg_advisory_unlock(%s)", (LEADER_LOCK_ID,))

    async def _evaluate_all(self) -> None:
        bots = await list_running_bots(self._db, max_orders=self._settings.max_orders_per_run)
        self.last_tick_at = datetime.now(UTC)
        self.last_tick_bots = len(bots)
        if not bots:
            return

        log.debug("evaluating %d running bot(s)", len(bots))
        for bot in bots:
            if self._stopping.is_set():
                log.info("shutdown requested, deferring %s to the next container", bot.id)
                return
            try:
                outcome = await self._service.evaluate(bot)
                if outcome.executed:
                    log.info("bot %s rebalanced: %s", bot.name, outcome.reason)
            except Exception:
                log.exception("bot %s evaluation raised", bot.id)
