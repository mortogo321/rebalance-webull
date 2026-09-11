"""The broker interface.

One narrow port with two adapters: a deterministic paper broker for local work
and CI, and a Webull TH UAT client. The rebalance service is written against
this protocol only, so swapping venues is a configuration change and the trading
logic never learns which one it is talking to.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from ..models import Account, BrokerOrder, Position, Side


class BrokerError(Exception):
    """A broker call failed. Carries a message safe to show a user."""


class BrokerAuthError(BrokerError):
    """Credentials were rejected. Distinct because it is the user's to fix."""


@runtime_checkable
class Broker(Protocol):
    """Everything the rebalance service needs from a venue."""

    name: str

    async def get_account(self) -> Account: ...

    async def get_positions(self) -> list[Position]: ...

    async def get_quotes(self, symbols: list[str]) -> dict[str, Decimal]: ...

    async def place_order(
        self,
        *,
        symbol: str,
        side: Side,
        quantity: Decimal,
        client_order_id: str,
        limit_price: Decimal | None = None,
    ) -> BrokerOrder: ...
