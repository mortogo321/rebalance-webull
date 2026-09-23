"""Tests for the rebalance planner.

Grouped by the property under test rather than by function, because the thing
worth protecting is behaviour ("never spends cash it does not have"), not
line coverage.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from engine.models import Holding, RebalanceSettings, Side, Target
from engine.rebalance import RebalanceError, build_plan, validate_targets

from .conftest import d, holding


def plan(**kw):
    base = {
        "cash": d(0),
        "holdings": (),
        "prices": {},
        "settings": RebalanceSettings(investment_amount=d(100_000)),
    }
    base.update(kw)
    return build_plan(**base)


# -----------------------------------------------------------------------------
# validation
# -----------------------------------------------------------------------------
class TestValidation:
    def test_weights_must_total_100_percent(self):
        with pytest.raises(RebalanceError, match="must total 10000 bps"):
            validate_targets((Target("AAPL", 5000), Target("MSFT", 4000)))

    def test_weights_totalling_over_100_percent_are_rejected(self):
        with pytest.raises(RebalanceError, match="must total 10000 bps"):
            validate_targets((Target("AAPL", 6000), Target("MSFT", 5000)))

    def test_empty_basket_is_rejected(self):
        with pytest.raises(RebalanceError, match="no target holdings"):
            validate_targets(())

    def test_duplicate_symbol_is_rejected(self):
        with pytest.raises(RebalanceError, match="duplicate target symbol"):
            validate_targets((Target("AAPL", 5000), Target("AAPL", 5000)))

    def test_exact_100_percent_passes(self):
        assert len(validate_targets((Target("AAPL", 4000), Target("MSFT", 6000)))) == 2

    def test_missing_price_is_rejected(self, basket, settings):
        with pytest.raises(RebalanceError, match="no price available for NVDA"):
            plan(
                cash=d(1000),
                targets=basket,
                prices={"AAPL": d(200), "MSFT": d(400)},
                settings=settings,
            )

    def test_zero_price_is_rejected(self, basket, settings):
        with pytest.raises(RebalanceError, match="price must be positive"):
            plan(
                cash=d(1000),
                targets=basket,
                prices={"AAPL": d(200), "MSFT": d(400), "NVDA": d(0)},
                settings=settings,
            )

    def test_negative_cash_is_rejected(self, basket, prices, settings):
        with pytest.raises(RebalanceError, match="cash balance cannot be negative"):
            plan(cash=d(-1), targets=basket, prices=prices, settings=settings)

    def test_zero_investment_amount_is_rejected(self, basket, prices):
        with pytest.raises(RebalanceError, match="investment_amount must be"):
            plan(
                cash=d(1000),
                targets=basket,
                prices=prices,
                settings=RebalanceSettings(investment_amount=d(0)),
            )

    def test_duplicate_holding_is_rejected(self, basket, prices, settings):
        with pytest.raises(RebalanceError, match="duplicate holding"):
            plan(
                cash=d(1000),
                holdings=(holding("AAPL", 1), holding("AAPL", 2)),
                targets=basket,
                prices=prices,
                settings=settings,
            )


# -----------------------------------------------------------------------------
# the happy path
# -----------------------------------------------------------------------------
class TestAllCashPortfolio:
    def test_buys_the_whole_basket_in_target_proportions(self, basket, prices, settings):
        result = plan(cash=d(100_000), targets=basket, prices=prices, settings=settings)

        assert all(o.side is Side.BUY for o in result.orders)
        by_symbol = {o.symbol: o for o in result.orders}

        # 40% of 100k = 40,000 / $200 = 200 shares
        assert by_symbol["AAPL"].quantity == d(200)
        # 35% = 35,000 / $400 = 87.5 -> 87 whole shares
        assert by_symbol["MSFT"].quantity == d(87)
        # 25% = 25,000 / $125 = 200 shares
        assert by_symbol["NVDA"].quantity == d(200)

    def test_never_spends_more_than_the_cash_on_hand(self, basket, prices, settings):
        result = plan(cash=d(100_000), targets=basket, prices=prices, settings=settings)
        spent = sum(o.estimated_value for o in result.orders)
        assert spent <= d(100_000)
        assert result.projected_cash_after >= 0

    def test_reports_full_drift_when_holding_nothing(self, basket, prices, settings):
        result = plan(cash=d(100_000), targets=basket, prices=prices, settings=settings)
        # Every position is 100% below its target; the worst is the largest weight.
        assert result.max_drift_bps == 4000


class TestBalancedPortfolio:
    def test_a_portfolio_already_on_target_trades_nothing(self, basket, prices, settings):
        result = plan(
            cash=d(0),
            holdings=(holding("AAPL", 200), holding("MSFT", 87), holding("NVDA", 200)),
            targets=basket,
            prices=prices,
            settings=RebalanceSettings(investment_amount=d(99_800)),
        )
        assert result.orders == ()
        # Whole-share granularity means "on target" is a band, not a point:
        # 87 shares of MSFT is the closest reachable weight to 35%, 13 bps off.
        # What matters is that the planner recognises it as close enough to
        # leave alone rather than churning commission chasing the last basis point.
        assert result.max_drift_bps <= 20


# -----------------------------------------------------------------------------
# drifted portfolios
# -----------------------------------------------------------------------------
class TestDriftedPortfolio:
    def test_sells_the_overweight_and_buys_the_underweight(self, basket, prices, settings):
        # 300 AAPL ($60k) against a 40% target in a $100k sleeve -> heavily over.
        result = plan(
            cash=d(20_000),
            holdings=(holding("AAPL", 300), holding("MSFT", 20)),
            targets=basket,
            prices=prices,
            settings=settings,
        )
        sides = {o.symbol: o.side for o in result.orders}
        assert sides["AAPL"] is Side.SELL
        assert sides["NVDA"] is Side.BUY

    def test_sells_are_planned_before_buys(self, basket, prices, settings):
        result = plan(
            cash=d(20_000),
            holdings=(holding("AAPL", 300), holding("MSFT", 20)),
            targets=basket,
            prices=prices,
            settings=settings,
        )
        sides = [o.side for o in result.orders]
        assert sides == sorted(sides, key=lambda s: 0 if s is Side.SELL else 1)

    def test_moves_every_holding_closer_to_its_target(self, basket, prices, settings):
        result = plan(
            cash=d(20_000),
            holdings=(holding("AAPL", 300), holding("MSFT", 20)),
            targets=basket,
            prices=prices,
            settings=settings,
        )
        before = {dr.symbol: abs(dr.drift_bps) for dr in result.drifts}
        delta: dict[str, Decimal] = {}
        for o in result.orders:
            sign = Decimal(1) if o.side is Side.BUY else Decimal(-1)
            delta[o.symbol] = delta.get(o.symbol, Decimal(0)) + sign * o.estimated_value

        for dr in result.drifts:
            after_value = dr.current_value + delta.get(dr.symbol, Decimal(0))
            after_bps = abs(
                int(after_value / result.deployable_value * 10000) - dr.target_weight_bps
            )
            assert after_bps <= before[dr.symbol], f"{dr.symbol} moved away from target"


class TestLiquidation:
    def test_a_holding_dropped_from_the_basket_is_sold_in_full(self, basket, prices, settings):
        result = plan(
            cash=d(0),
            holdings=(holding("AAPL", 200), holding("MSFT", 87), holding("TSLA", 40)),
            targets=basket,
            prices=prices,
            settings=settings,
        )
        tsla = next(o for o in result.orders if o.symbol == "TSLA")
        assert tsla.side is Side.SELL
        assert tsla.quantity == d(40)
        assert tsla.reason == "not in target basket"

    def test_fractional_remainders_are_liquidated_despite_whole_share_rounding(
        self, basket, prices, settings
    ):
        # 40.5 shares must all go, even though the bot trades whole shares.
        result = plan(
            cash=d(0),
            holdings=(
                holding("AAPL", 200),
                Holding("TSLA", d("40.5")),
            ),
            targets=basket,
            prices=prices,
            settings=settings,
        )
        tsla = next(o for o in result.orders if o.symbol == "TSLA")
        assert tsla.quantity == d("40.5")


# -----------------------------------------------------------------------------
# rounding, minimums and the cash constraint -- where the money actually leaks
# -----------------------------------------------------------------------------
class TestWholeShareRounding:
    def test_whole_share_mode_never_overshoots_available_cash(self, prices):
        result = plan(
            cash=d(1000),
            targets=(Target("MSFT", 10000),),
            prices=prices,
            settings=RebalanceSettings(investment_amount=d(1000), allow_fractional=False),
        )
        # $1000 / $400 = 2.5 -> 2 shares, never 3.
        assert result.orders[0].quantity == d(2)
        assert result.orders[0].estimated_value == d(800)

    def test_fractional_mode_uses_the_whole_budget(self, prices):
        result = plan(
            cash=d(1000),
            targets=(Target("MSFT", 10000),),
            prices=prices,
            settings=RebalanceSettings(investment_amount=d(1000), allow_fractional=True),
        )
        assert result.orders[0].quantity == d("2.5")
        assert result.orders[0].estimated_value == d(1000)

    def test_an_order_that_rounds_to_zero_shares_is_skipped_with_a_reason(self, prices):
        result = plan(
            cash=d(100),
            targets=(Target("MSFT", 10000),),
            prices=prices,
            settings=RebalanceSettings(investment_amount=d(100), allow_fractional=False),
        )
        assert result.orders == ()
        assert "0 whole shares" in result.skipped[0].reason


class TestMinimumOrderValue:
    def test_dust_orders_are_filtered_out(self, prices):
        # AAPL sits exactly on target; NVDA is $250 light, which buys 2 whole
        # shares -- a real, placeable order that is still not worth the
        # commission against a $500 minimum.
        result = plan(
            cash=d(1_000),
            holdings=(holding("AAPL", 50), holding("NVDA", 78)),
            targets=(Target("AAPL", 5000), Target("NVDA", 5000)),
            prices=prices,
            settings=RebalanceSettings(investment_amount=d(20_000), min_order_value=d(500)),
        )
        assert result.orders == ()
        assert any("below the 500" in s.reason for s in result.skipped)

    def test_orders_at_or_above_the_minimum_still_go_through(self, prices):
        result = plan(
            cash=d(1_000),
            holdings=(holding("AAPL", 50), holding("NVDA", 78)),
            targets=(Target("AAPL", 5000), Target("NVDA", 5000)),
            prices=prices,
            settings=RebalanceSettings(investment_amount=d(20_000), min_order_value=d(200)),
        )
        nvda = next(o for o in result.orders if o.symbol == "NVDA")
        assert nvda.quantity == d(2)
        assert nvda.estimated_value == d(250)


class TestCashConstraint:
    def test_buys_are_capped_by_the_cash_sells_actually_raise(self, prices):
        # A deliberately awkward case: the sell rounds down to whole shares, so
        # it raises less than the buys were sized against.
        result = plan(
            cash=d(0),
            holdings=(holding("AAPL", 100),),
            targets=(Target("AAPL", 5000), Target("NVDA", 5000)),
            prices=prices,
            settings=RebalanceSettings(investment_amount=d(20_000)),
        )
        raised = sum(o.estimated_value for o in result.orders if o.side is Side.SELL)
        spent = sum(o.estimated_value for o in result.orders if o.side is Side.BUY)
        assert spent <= raised, "spent more than the sells brought in"
        assert result.projected_cash_after >= 0

    def test_the_most_underweight_holding_is_funded_first(self, prices):
        result = plan(
            cash=d(300),
            targets=(Target("AAPL", 2000), Target("NVDA", 8000)),
            prices=prices,
            settings=RebalanceSettings(investment_amount=d(300)),
        )
        # NVDA is 8000 bps underweight vs AAPL's 2000: it eats the budget first.
        assert result.orders[0].symbol == "NVDA"

    def test_starved_buys_are_recorded_rather_than_silently_dropped(self, prices):
        result = plan(
            cash=d(130),
            targets=(Target("AAPL", 2000), Target("NVDA", 8000)),
            prices=prices,
            settings=RebalanceSettings(investment_amount=d(130)),
        )
        assert any(s.symbol == "AAPL" for s in result.skipped)


class TestCashBuffer:
    def test_the_buffer_is_held_back_from_deployment(self, basket, prices):
        result = plan(
            cash=d(100_000),
            targets=basket,
            prices=prices,
            settings=RebalanceSettings(investment_amount=d(100_000), cash_buffer_bps=1000),
        )
        assert result.deployable_value == d(90_000)
        assert sum(o.estimated_value for o in result.orders) <= d(90_000)

    def test_a_bot_at_rest_with_a_buffer_reports_no_drift(self, prices):
        # The regression this guards: measuring weights against the full sleeve
        # instead of the deployable part leaves a permanent phantom drift equal
        # to the buffer, and the bot rebalances forever.
        result = plan(
            cash=d(10_000),
            holdings=(holding("AAPL", 450),),
            targets=(Target("AAPL", 10000),),
            prices=prices,
            settings=RebalanceSettings(investment_amount=d(100_000), cash_buffer_bps=1000),
        )
        assert result.max_drift_bps == 0
        assert result.orders == ()


class TestInvestmentSleeve:
    def test_it_deploys_only_the_allocated_amount_not_the_whole_account(self, basket, prices):
        result = plan(
            cash=d(500_000),
            targets=basket,
            prices=prices,
            settings=RebalanceSettings(investment_amount=d(10_000)),
        )
        assert result.investable_value == d(10_000)
        assert sum(o.estimated_value for o in result.orders) <= d(10_000)

    def test_it_falls_back_to_the_account_when_that_is_smaller(self, basket, prices):
        result = plan(
            cash=d(5_000),
            targets=basket,
            prices=prices,
            settings=RebalanceSettings(investment_amount=d(100_000)),
        )
        assert result.investable_value == d(5_000)
        assert sum(o.estimated_value for o in result.orders) <= d(5_000)


class TestBlastRadius:
    def test_a_run_is_capped_at_max_orders(self, prices):
        symbols = [f"S{i}" for i in range(10)]
        result = plan(
            cash=d(100_000),
            targets=tuple(Target(s, 1000) for s in symbols),
            prices=dict.fromkeys(symbols, d(100)),
            settings=RebalanceSettings(investment_amount=d(100_000), max_orders=3),
        )
        assert len(result.orders) == 3
        assert any("capped at 3 orders" in s.reason for s in result.skipped)


class TestAuditTrail:
    def test_the_plan_serialises_for_storage(self, basket, prices, settings):
        result = plan(cash=d(100_000), targets=basket, prices=prices, settings=settings)
        blob = result.as_dict()
        assert {"orders", "skipped", "drifts", "max_drift_bps"} <= blob.keys()
        # Values are strings, not floats -- the audit record must survive a
        # JSON round trip without losing precision.
        assert isinstance(blob["orders"][0]["quantity"], str)
