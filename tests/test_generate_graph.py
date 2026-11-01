import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from conftest import make_bond

import generate_graph
from bond_management.dates import next_business_day
from bond_management.pricing import accrued
from bond_management.zspread import DEFAULT_TENORS, curve_price

FIXTURES = Path(__file__).parent / "fixtures"
SETTLE = next_business_day(dt.date(2026, 10, 9))  # the fixtures' Friday close, T+1
FLAT = np.full(len(DEFAULT_TENORS), 2 * np.log(1 + 0.05 / 2))  # flat at a 5% semi yield
TILTED = np.array([0.034, 0.036, 0.039, 0.042, 0.046, 0.050, 0.053, 0.056])


@pytest.fixture(scope="module")
def prices():
    """The committed Friday-close feed."""
    return pd.read_csv(FIXTURES / "gilts_20261009.csv")


@pytest.fixture(scope="module")
def inventory():
    """The committed inventory as conventional bonds (as in test_zspread)."""
    rows = pd.read_csv(FIXTURES / "inventory_20261008.csv")
    return [
        make_bond(
            isin=row.isin,
            name=row.name,
            dividend_dates=row.dividend_dates,
            redemption_date=dt.date.fromisoformat(row.redemption_date),
            first_issue_date=dt.date.fromisoformat(row.first_issue_date),
            ex_dividend_date=(
                dt.date.fromisoformat(row.ex_dividend_date)
                if row.ex_dividend_date
                else None
            ),
        )
        for row in rows.itertuples()
    ]


class TestSyntheticSchedule:
    def test_one_coupon_behind_and_k_ahead(self):
        sched = generate_graph.synthetic_schedule(4, SETTLE)
        assert len(sched) == 5
        assert sched[0] == SETTLE - dt.timedelta(days=generate_graph.SETTLE_LAG)
        for k in range(1, 5):
            assert sched[k] == SETTLE + dt.timedelta(
                days=round((k - 0.5) * generate_graph.YEAR / 2)
            )
        assert sched == tuple(sorted(sched))

    def test_settlement_accrues_a_quarter_of_the_coupon(self):
        """The footnote's claim: mid-period settlement means accrued = 1/4 coupon."""
        for coupons in (1, 5, 101):
            sched = generate_graph.synthetic_schedule(coupons, SETTLE)
            assert accrued(4.0, sched, SETTLE) == pytest.approx(1.0)


class TestParYield:
    @pytest.mark.parametrize("curve", [FLAT, TILTED], ids=["flat", "upward"])
    @pytest.mark.parametrize("coupons", [1, 10, 101])
    def test_reprices_the_synthetic_gilt_at_clean_100(self, coupons, curve):
        coupon, years = generate_graph.par_yield(coupons, SETTLE, DEFAULT_TENORS, curve)
        sched = generate_graph.synthetic_schedule(coupons, SETTLE)
        clean = curve_price(coupon, sched, SETTLE, DEFAULT_TENORS, curve) - accrued(
            coupon, sched, SETTLE
        )
        assert clean == pytest.approx(100.0, abs=1e-9)
        assert years == pytest.approx(
            (sched[-1] - SETTLE).days / generate_graph.YEAR, abs=1e-12
        )


class TestParCurve:
    def test_flat_curve_hovers_at_the_flat_yield(self):
        """A flat 5% curve prices par yields within ~1.5bp of 5% everywhere."""
        par = generate_graph.par_curve(SETTLE, DEFAULT_TENORS, FLAT)
        assert (par["yield_pct"] - 5.0).abs().max() < 0.05

    def test_grid_is_sorted_and_wider_than_the_pillars(self):
        par = generate_graph.par_curve(SETTLE, DEFAULT_TENORS, TILTED)
        assert list(par.columns) == ["yield_pct", "years"]
        assert len(par) == len(generate_graph.GRID)
        assert par["years"].is_monotonic_increasing
        assert par["years"].iloc[0] < min(DEFAULT_TENORS)  # 0.25y, before the 0.5y pillar
        assert par["years"].iloc[-1] > max(DEFAULT_TENORS)  # 50.25y, past the 50y pillar


class TestGiltYields:
    def test_columns_and_the_expiring_gilt_is_left_out(self, prices, inventory, capsys):
        df = generate_graph.gilt_yields(prices, inventory, SETTLE, {})
        assert list(df.columns) == ["tidm", "years", "yield_pct", "residual_bp"]
        assert len(df) == len(prices) - 1  # T26A matures 10 days after settlement
        assert "T26A" not in set(df["tidm"])
        assert "left out of the picture: T26A" in capsys.readouterr().out
        assert df["yield_pct"].between(0, 10).all()

    def test_residuals_are_joined_on_isin(self, prices, inventory):
        residuals = {isin: 7.5 for isin in prices["isin"]}
        joined = generate_graph.gilt_yields(prices, inventory, SETTLE, residuals)
        assert (joined["residual_bp"] == 7.5).all()

    def test_an_absent_residual_is_nan(self, prices, inventory):
        df = generate_graph.gilt_yields(prices, inventory, SETTLE, {})
        assert df["residual_bp"].isna().all()


class TestAxisRange:
    def test_contains_everything_and_ignores_the_short_end(self):
        par = pd.DataFrame({"yield_pct": [4.0, 6.0], "years": [1.0, 30.0]})
        gilts = pd.DataFrame(
            {
                "tidm": ["SHORT", "MID", "LONG"],
                "years": [0.3, 5.0, 40.0],
                "yield_pct": [0.5, 5.0, 7.0],  # SHORT must not drag ymin down to 0.5
                "residual_bp": [np.nan, np.nan, np.nan],
            }
        )
        ymin, ymax, xmax = generate_graph.axis_range(par, gilts)
        assert ymin == pytest.approx(3.75)  # min(4.0 par, 5.0 roomy gilt) - 0.25
        assert ymax == pytest.approx(7.25)  # max(6.0 par, 7.0 gilt) + 0.25
        assert xmax == pytest.approx(41.5)  # longest gilt 40y + 1.5
        assert ymin > 0.5  # the 0.3y gilt at 0.5% was left out of the floor
