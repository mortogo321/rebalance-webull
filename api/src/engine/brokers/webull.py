"""Webull TH OpenAPI client, pinned to a UAT host.

Signing follows developer.webull.co.th/apis/docs/authentication/{overview,signature}:

    str1      = "k=v&..." over query params + the six signing headers
                (x-app-key, x-timestamp, x-signature-algorithm,
                 x-signature-version, x-signature-nonce, host),
                merged and sorted by name
    str2      = uppercase hex SHA-256 of the raw request body, when there is one
    raw       = path + "&" + str1                (no body)
                path + "&" + str1 + "&" + str2   (with body)
    signature = base64(HMAC-SHA256(app_secret + "&", percent_encode(raw)))

HONEST LIMITATION
-----------------
This adapter is written from the published specification and is exercised by
unit tests against the worked example in those docs. It has **not** been run
against a live Webull UAT endpoint -- the POC has no UAT credentials -- and the
resource paths below are the documented shapes, not verified responses. Treat
``BROKER_MODE=webull_uat`` as ready-to-integrate, not as proven.
``BROKER_MODE=mock`` is the default in every environment for that reason, and
the README's Limitations section says so.

The production venue is unreachable by construction: the host is validated
against ``WEBULL_ALLOWED_HOSTS`` at startup (``config.py``) and re-checked here
on every request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from ..models import Account, BrokerOrder, Position, Side
from ..money import money, quantity
from .base import BrokerAuthError, BrokerError

log = logging.getLogger(__name__)

SIGNATURE_ALGORITHM = "HMAC-SHA256"
SIGNATURE_VERSION = "1.0"
API_VERSION = "v2"

# Only unreserved characters survive percent-encoding (RFC 3986).
_UNRESERVED = "-_.~"


def _percent_encode(value: str) -> str:
    return quote(value, safe=_UNRESERVED)


def build_string_to_sign(
    *,
    path: str,
    params: dict[str, str],
    headers: dict[str, str],
    body: str | None,
) -> str:
    """The canonical string, before percent-encoding and HMAC.

    Split out as a pure function so the worked example in the vendor docs can be
    asserted directly in tests -- signing code that is only reachable through a
    live HTTP call is signing code that never gets tested.
    """
    merged = {**params, **headers}
    str1 = "&".join(f"{k}={merged[k]}" for k in sorted(merged))
    raw = f"{path}&{str1}"
    if body:
        str2 = hashlib.sha256(body.encode("utf-8")).hexdigest().upper()
        raw = f"{raw}&{str2}"
    return raw


def sign(raw: str, app_secret: str) -> str:
    """base64(HMAC-SHA256(app_secret + "&", percent_encode(raw)))."""
    key = f"{app_secret}&".encode()
    digest = hmac.new(key, _percent_encode(raw).encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


class WebullUatBroker:
    name = "webull_uat"

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        base_url: str,
        allowed_hosts: tuple[str, ...],
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._allowed_hosts = allowed_hosts
        self._assert_host_allowed()
        self._client = client or httpx.AsyncClient(timeout=timeout)

    def _assert_host_allowed(self) -> None:
        parsed = urlparse(self._base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https":
            raise BrokerError(f"broker base url must be https, got {self._base_url!r}")
        if host not in self._allowed_hosts:
            # Belt and braces with the startup check: this also catches a base
            # url mutated at runtime rather than read from configuration.
            raise BrokerError(
                f"refusing to call {host!r}: not in the allowed broker hosts {self._allowed_hosts}"
            )

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- signing ------------------------------------------------------------
    def _signed_headers(
        self, *, path: str, params: dict[str, str], body: str | None
    ) -> dict[str, str]:
        host = (urlparse(self._base_url).hostname or "").lower()
        signing_headers = {
            "x-app-key": self._app_key,
            # ISO 8601, UTC, second precision -- the vendor rejects sub-second.
            "x-timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "x-signature-algorithm": SIGNATURE_ALGORITHM,
            "x-signature-version": SIGNATURE_VERSION,
            "x-signature-nonce": secrets.token_hex(16),
            "host": host,
        }
        raw = build_string_to_sign(path=path, params=params, headers=signing_headers, body=body)
        return {
            **signing_headers,
            "x-signature": sign(raw, self._app_secret),
            "x-version": API_VERSION,
            "content-type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        self._assert_host_allowed()
        params = params or {}
        # The body must be signed byte-for-byte as it is sent, so it is
        # serialised once here and passed through as content, never re-encoded.
        body = None
        if json_body is not None:
            import json as _json

            body = _json.dumps(json_body, separators=(",", ":"), sort_keys=True)

        headers = self._signed_headers(path=path, params=params, body=body)
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                content=body,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise BrokerError(f"broker request failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise BrokerAuthError(
                "Webull rejected these credentials. Check the API key and secret, "
                "and that the key is enabled for UAT."
            )
        if response.status_code >= 400:
            # Never echo the response body: it can contain account identifiers.
            raise BrokerError(f"broker returned {response.status_code} for {path}")
        return response.json()

    # -- protocol -----------------------------------------------------------
    async def get_account(self) -> Account:
        payload = await self._request("GET", "/account/balance")
        data = payload.get("data", payload)
        return Account(
            account_id=str(data.get("accountId", "")),
            cash=money(Decimal(str(data.get("cashBalance", "0")))),
            currency=str(data.get("currency", "USD")),
        )

    async def get_positions(self) -> list[Position]:
        payload = await self._request("GET", "/account/positions")
        rows = payload.get("data", payload) or []
        return [
            Position(
                symbol=str(r["symbol"]).upper(),
                quantity=quantity(Decimal(str(r.get("quantity", "0")))),
                avg_cost=money(Decimal(str(r.get("avgCost", "0")))),
            )
            for r in rows
            if Decimal(str(r.get("quantity", "0"))) > 0
        ]

    async def get_quotes(self, symbols: list[str]) -> dict[str, Decimal]:
        if not symbols:
            return {}
        payload = await self._request(
            "GET", "/market/quotes", params={"symbols": ",".join(sorted(symbols))}
        )
        rows = payload.get("data", payload) or []
        quotes = {str(r["symbol"]).upper(): money(Decimal(str(r["lastPrice"]))) for r in rows}
        missing = sorted(set(s.upper() for s in symbols) - quotes.keys())
        if missing:
            raise BrokerError(f"no quote returned for: {', '.join(missing)}")
        return quotes

    async def place_order(
        self,
        *,
        symbol: str,
        side: Side,
        quantity: Decimal,
        client_order_id: str,
        limit_price: Decimal | None = None,
    ) -> BrokerOrder:
        body: dict[str, Any] = {
            # Passed to the venue as the idempotency key: a retried submit after
            # a timeout must not become a second position.
            "clientOrderId": client_order_id,
            "symbol": symbol.upper(),
            "side": str(side).upper(),
            "quantity": str(quantity),
            "orderType": "LIMIT" if limit_price is not None else "MARKET",
            "timeInForce": "DAY",
        }
        if limit_price is not None:
            body["limitPrice"] = str(limit_price)

        payload = await self._request("POST", "/trade/place_order", json_body=body)
        data = payload.get("data", payload)
        filled = Decimal(str(data.get("filledQuantity", "0")))
        avg = data.get("avgFillPrice")
        return BrokerOrder(
            broker_order_id=str(data.get("orderId", client_order_id)),
            symbol=symbol.upper(),
            side=side,
            quantity=quantity,
            status=str(data.get("status", "submitted")).lower(),
            filled_quantity=filled,
            avg_fill_price=money(Decimal(str(avg))) if avg is not None else None,
            reject_reason=data.get("rejectReason"),
        )
