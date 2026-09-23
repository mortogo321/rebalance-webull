"""Engine HTTP surface.

Two kinds of route:

* ``/health`` and ``/ready`` -- unauthenticated, used by Docker, nginx and the
  blue/green deploy script. ``/health`` says the process is up; ``/ready`` says
  it can actually serve, which is the one the deploy gate waits on.
* ``/internal/*`` -- called only by the Supabase Edge Function, authenticated
  with a shared bearer token. These are never exposed publicly (nginx does not
  route to them), because they take a ``user_id`` as a parameter and therefore
  trust their caller to have authenticated the user already.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .brokers.base import BrokerError
from .config import Settings, get_settings
from .crypto import encrypt, hint_of, load_key
from .db import Database
from .repo import delete_credentials, get_bot, set_connection_state, upsert_credentials
from .scheduler import Scheduler
from .service import RebalanceService

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )

    db = Database(
        settings.database_url,
        min_size=settings.database_pool_min,
        max_size=settings.database_pool_max,
    )
    await db.open()

    service = RebalanceService(db, settings)
    scheduler = Scheduler(db, settings, service)
    scheduler.start()

    app.state.settings = settings
    app.state.db = db
    app.state.service = service
    app.state.scheduler = scheduler
    log.info("engine ready env=%s broker=%s", settings.app_env, settings.broker_mode)

    try:
        yield
    finally:
        # Order matters: stop taking work, drain it, then close the pool.
        # Closing the pool first would fail every in-flight order write.
        await scheduler.stop()
        await db.close()


app = FastAPI(
    title="Rebalance Engine",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


# -----------------------------------------------------------------------------
# dependencies
# -----------------------------------------------------------------------------
def get_service(request: Request) -> RebalanceService:
    return cast(RebalanceService, request.app.state.service)


def get_db(request: Request) -> Database:
    return cast(Database, request.app.state.db)


def get_config(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


async def require_internal_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Gate for ``/internal/*``.

    Compared in constant time: a naive ``==`` on a secret leaks its prefix to
    anyone who can measure response latency across many attempts.
    """
    import secrets as _secrets

    expected = request.app.state.settings.engine_internal_token
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not _secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal token"
        )


Internal = Depends(require_internal_token)


# -----------------------------------------------------------------------------
# schemas
# -----------------------------------------------------------------------------
class CredentialsIn(BaseModel):
    user_id: str
    api_key: str = Field(min_length=4, max_length=512)
    api_secret: str = Field(min_length=4, max_length=512)


class UserIn(BaseModel):
    user_id: str


class RebalanceIn(BaseModel):
    user_id: str
    force: bool = False


# -----------------------------------------------------------------------------
# health
# -----------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is running. Never touches the database."""
    return {"status": "ok"}


@app.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness: dependencies are reachable, so traffic can be sent here.

    The blue/green deploy gates the nginx switch on this endpoint. Returning 200
    while the database is unreachable would move live traffic onto a container
    that cannot serve it, which is the exact failure the gate exists to prevent.
    """
    db: Database = request.app.state.db
    settings: Settings = request.app.state.settings
    scheduler: Scheduler = request.app.state.scheduler

    db_ok = await db.healthy()
    body: dict[str, Any] = {
        "status": "ready" if db_ok else "degraded",
        "env": settings.app_env,
        "broker": settings.broker_mode,
        "database": "up" if db_ok else "down",
        "scheduler": {
            "enabled": settings.scheduler_enabled,
            # Which colour is actually driving rebalances. During a blue/green
            # swap exactly one of the two engines reports leader=true.
            "leader": scheduler.is_leader,
            "last_tick_at": scheduler.last_tick_at.isoformat() if scheduler.last_tick_at else None,
            "bots_last_tick": scheduler.last_tick_bots,
        },
        "revision": os.environ.get("GIT_SHA", "dev"),
    }
    return JSONResponse(body, status_code=200 if db_ok else 503)


# -----------------------------------------------------------------------------
# internal API
# -----------------------------------------------------------------------------
@app.post("/internal/credentials", dependencies=[Internal])
async def store_credentials(
    payload: CredentialsIn,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_config)],
    service: Annotated[RebalanceService, Depends(get_service)],
) -> dict[str, Any]:
    """Encrypt and store a user's broker credentials, then probe the connection.

    The plaintext exists only inside this function. It is bound to the user id
    as AES-GCM additional data, so the stored row is worthless on any other
    account, and it is never logged or echoed back in the response.
    """
    key = load_key(settings.app_encryption_key)
    await upsert_credentials(
        db,
        user_id=payload.user_id,
        api_key_encrypted=encrypt(
            payload.api_key,
            key=key,
            aad=payload.user_id,
            key_version=settings.app_encryption_key_version,
        ),
        api_secret_encrypted=encrypt(
            payload.api_secret,
            key=key,
            aad=payload.user_id,
            key_version=settings.app_encryption_key_version,
        ),
        key_version=settings.app_encryption_key_version,
    )
    await set_connection_state(
        db,
        user_id=payload.user_id,
        status="disconnected",
        environment=settings.broker_mode,
        api_key_hint=hint_of(payload.api_key),
    )
    return await service.verify_connection(payload.user_id)


@app.delete("/internal/credentials", dependencies=[Internal])
async def remove_credentials(
    payload: UserIn,
    db: Annotated[Database, Depends(get_db)],
) -> dict[str, str]:
    await delete_credentials(db, user_id=payload.user_id)
    await set_connection_state(
        db, user_id=payload.user_id, status="disconnected", api_key_hint=None
    )
    return {"status": "disconnected"}


@app.post("/internal/connection/verify", dependencies=[Internal])
async def verify_connection(
    payload: UserIn,
    service: Annotated[RebalanceService, Depends(get_service)],
) -> dict[str, Any]:
    return await service.verify_connection(payload.user_id)


@app.post("/internal/account", dependencies=[Internal])
async def account_snapshot(
    payload: UserIn,
    service: Annotated[RebalanceService, Depends(get_service)],
) -> dict[str, Any]:
    try:
        return await service.snapshot(payload.user_id)
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/internal/bots/{bot_id}/rebalance", dependencies=[Internal])
async def rebalance_now(
    bot_id: str,
    payload: RebalanceIn,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_config)],
    service: Annotated[RebalanceService, Depends(get_service)],
) -> dict[str, Any]:
    bot = await get_bot(
        db, bot_id=bot_id, user_id=payload.user_id, max_orders=settings.max_orders_per_run
    )
    if bot is None:
        # The user_id predicate is the authorisation check: a bot belonging to
        # someone else is indistinguishable from one that does not exist.
        raise HTTPException(status_code=404, detail="bot not found")
    outcome = await service.evaluate(bot, force=payload.force)
    return outcome.as_dict()
