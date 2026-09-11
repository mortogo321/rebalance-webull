"""Async Postgres access.

The engine connects with the service role, which bypasses RLS. That is
necessary -- a background worker has no user JWT -- and it is also the single
most dangerous privilege in the system. Every query in ``repo.py`` therefore
carries an explicit ``user_id`` predicate: the database is not filtering for us
here, so the code must.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)


class Database:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 8) -> None:
        self._pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row, "autocommit": True},
        )

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=30)
        log.info("database pool ready")

    async def close(self) -> None:
        await self._pool.close()

    async def healthy(self) -> bool:
        try:
            async with self.connection() as conn:
                await conn.execute("select 1")
            return True
        except Exception:
            log.exception("database health check failed")
            return False

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        async with self._pool.connection() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """A connection wrapped in an explicit transaction.

        Used where a run must be all-or-nothing: writing the run row, its
        orders and the resulting paper positions cannot partially apply, or the
        audit trail stops matching the portfolio.
        """
        async with self._pool.connection() as conn:
            async with conn.transaction():
                yield conn

    async def fetch_all(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        async with self.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchall()

    async def fetch_one(self, sql: str, params: Any = None) -> dict[str, Any] | None:
        async with self.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchone()

    async def execute(self, sql: str, params: Any = None) -> None:
        async with self.connection() as conn:
            await conn.execute(sql, params)
