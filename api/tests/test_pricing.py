from __future__ import annotations

from datetime import date
from decimal import Decimal

from engine.pricing import base_price, price_for, quotes_for

DAY = date(2026, 9, 11)


class TestDeterminism:
    def test_the_same_inputs_always_give_the_same_price(self):
        assert price_for("AAPL", on=DAY) == price_for("AAPL", on=DAY)

    def test_a_different_day_moves_the_price(self):
        assert price_for("AAPL", on=DAY) != price_for("AAPL", on=date(2026, 9, 12))

    def test_a_different_seed_gives_a_different_series(self):
        assert price_for("AAPL", on=DAY, seed=1) != price_for("AAPL", on=DAY, seed=2)

    def test_symbols_move_independently(self):
        moves = {s: price_for(s, on=DAY) / base_price(s) for s in ("AAPL", "MSFT", "NVDA")}
        assert len(set(moves.values())) == 3


class TestSanity:
    def test_prices_are_always_positive(self):
        for day in (date(2026, 1, 1), DAY, date(2026, 12, 31)):
            for symbol in ("AAPL", "ZZZZ", "A"):
                assert price_for(symbol, on=day) > 0

    def test_a_known_ticker_stays_near_its_anchor(self):
        # Within the configured volatility plus the monthly trend, not wandering
        # off to zero or to a million.
        price = price_for("AAPL", on=DAY)
        assert Decimal(150) < price < Decimal(260)

    def test_an_unknown_ticker_gets_a_plausible_price(self):
        assert Decimal(1) <= price_for("WXYZ", on=DAY) <= Decimal(600)

    def test_prices_carry_money_precision(self):
        assert price_for("AAPL", on=DAY).as_tuple().exponent == -4


class TestQuotes:
    def test_it_returns_one_quote_per_symbol(self):
        quotes = quotes_for(["AAPL", "MSFT"], on=DAY)
        assert set(quotes) == {"AAPL", "MSFT"}

    def test_symbols_are_upper_cased_and_deduplicated(self):
        assert set(quotes_for(["aapl", "AAPL"], on=DAY)) == {"AAPL"}

    def test_an_empty_request_returns_nothing(self):
        assert quotes_for([], on=DAY) == {}
