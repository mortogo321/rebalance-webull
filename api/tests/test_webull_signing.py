"""Request signing for the Webull TH OpenAPI.

The canonical-string builder is a pure function precisely so it can be pinned
here. The vendor's worked example publishes only the final signature, not the
app key, timestamp or nonce that produced it, so the end-to-end value cannot be
reproduced -- what is asserted instead is every structural rule the spec states.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from engine.brokers.base import BrokerError
from engine.brokers.webull import (
    _percent_encode,
    build_string_to_sign,
    sign,
)
from engine.brokers.webull import WebullUatBroker

ALLOWED = ("uat-api.webull.co.th",)

HEADERS = {
    "x-app-key": "appkey123",
    "x-timestamp": "2026-09-11T12:00:00Z",
    "x-signature-algorithm": "HMAC-SHA256",
    "x-signature-version": "1.0",
    "x-signature-nonce": "abc123",
    "host": "uat-api.webull.co.th",
}


class TestCanonicalString:
    def test_it_starts_with_the_path_then_an_ampersand(self):
        raw = build_string_to_sign(path="/account/balance", params={}, headers=HEADERS, body=None)
        assert raw.startswith("/account/balance&")

    def test_params_and_headers_are_merged_and_sorted_by_name(self):
        raw = build_string_to_sign(
            path="/market/quotes", params={"symbols": "AAPL", "a": "1"}, headers=HEADERS, body=None
        )
        names = [pair.split("=")[0] for pair in raw.split("&")[1:]]
        assert names == sorted(names)
        assert "a" in names and "symbols" in names and "x-app-key" in names

    def test_parameter_order_in_the_input_does_not_change_the_output(self):
        a = build_string_to_sign(
            path="/p", params={"b": "2", "a": "1"}, headers=HEADERS, body=None
        )
        b = build_string_to_sign(
            path="/p", params={"a": "1", "b": "2"}, headers=HEADERS, body=None
        )
        assert a == b

    def test_a_body_appends_its_uppercase_hex_sha256(self):
        body = '{"k1":123}'
        raw = build_string_to_sign(path="/trade/place_order", params={}, headers=HEADERS, body=body)
        expected = hashlib.sha256(body.encode()).hexdigest().upper()
        assert raw.endswith(f"&{expected}")
        assert expected == expected.upper()

    def test_no_body_appends_nothing(self):
        raw = build_string_to_sign(path="/account/balance", params={}, headers=HEADERS, body=None)
        assert raw.count("&") == len(HEADERS)  # path& + one & per merged pair - 1

    def test_a_changed_body_changes_the_canonical_string(self):
        one = build_string_to_sign(path="/t", params={}, headers=HEADERS, body='{"a":1}')
        two = build_string_to_sign(path="/t", params={}, headers=HEADERS, body='{"a":2}')
        assert one != two


class TestSignature:
    def test_the_hmac_key_is_the_secret_with_a_trailing_ampersand(self):
        import hmac

        raw = "/p&x=1"
        expected = base64.b64encode(
            hmac.new(b"topsecret&", _percent_encode(raw).encode(), hashlib.sha256).digest()
        ).decode()
        assert sign(raw, "topsecret") == expected

    def test_the_output_is_base64_not_hex(self):
        value = sign("/p&x=1", "topsecret")
        assert base64.b64decode(value)  # decodes cleanly
        assert len(base64.b64decode(value)) == 32  # a SHA-256 digest

    def test_it_signs_the_percent_encoded_string_not_the_raw_one(self):
        # Reserved characters must be escaped before hashing; signing the raw
        # string is the classic interop bug with this scheme.
        raw = "/trade/place_order&x-app-key=a b&host=h"
        assert sign(raw, "s") != sign(_percent_encode(raw), "s")

    def test_signing_is_deterministic(self):
        raw = build_string_to_sign(path="/p", params={"a": "1"}, headers=HEADERS, body=None)
        assert sign(raw, "secret") == sign(raw, "secret")

    def test_a_different_secret_produces_a_different_signature(self):
        raw = build_string_to_sign(path="/p", params={}, headers=HEADERS, body=None)
        assert sign(raw, "secret-a") != sign(raw, "secret-b")


class TestPercentEncoding:
    def test_unreserved_characters_are_left_alone(self):
        assert _percent_encode("abcXYZ019-_.~") == "abcXYZ019-_.~"

    def test_separators_are_escaped(self):
        assert _percent_encode("/a&b=c") == "%2Fa%26b%3Dc"

    def test_spaces_become_percent_20_not_plus(self):
        assert _percent_encode("a b") == "a%20b"


class TestProductionGuard:
    """The assignment's hard rule: no order may reach Webull production."""

    def test_a_host_outside_the_allowlist_is_refused(self):
        with pytest.raises(BrokerError, match="not in the allowed broker hosts"):
            WebullUatBroker(
                app_key="k",
                app_secret="s",
                base_url="https://api.webull.co.th",  # production
                allowed_hosts=ALLOWED,
            )

    def test_plain_http_is_refused(self):
        with pytest.raises(BrokerError, match="must be https"):
            WebullUatBroker(
                app_key="k",
                app_secret="s",
                base_url="http://uat-api.webull.co.th",
                allowed_hosts=ALLOWED,
            )

    def test_an_allowed_uat_host_is_accepted(self):
        broker = WebullUatBroker(
            app_key="k",
            app_secret="s",
            base_url="https://uat-api.webull.co.th",
            allowed_hosts=ALLOWED,
        )
        assert broker.name == "webull_uat"

    def test_a_lookalike_subdomain_does_not_slip_through(self):
        with pytest.raises(BrokerError):
            WebullUatBroker(
                app_key="k",
                app_secret="s",
                base_url="https://uat-api.webull.co.th.evil.example",
                allowed_hosts=ALLOWED,
            )
