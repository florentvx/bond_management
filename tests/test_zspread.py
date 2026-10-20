import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from conftest import make_bond

from bond_management.dates import as_naive, next_business_day
from bond_management.pricing import accrued, coupon_schedule, price
from bond_management.zspread import (
    DEFAULT_TENORS,
    calibrate,
    curve_price,
    fit,
    residual_zspread,
    z_at,
)

FIXTURES = Path(__file__).parent / "fixtures"
CPN = 4.0
SETTLE = next_business_day(dt.date(2026, 10, 9))  # the fixtures' Friday close, T+1
SCHED = coupon_schedule("7 Jun/Dec", dt.date(2040, 12, 7), SETTLE)
FLAT = np.full(len(DEFAULT_TENORS), 2 * np.log(1 + 0.05 / 2))  # flat at a 5% semi yield

# A curve to price synthetic gilts from, one node per benchmark tenor.
KNOWN = np.array([0.036, 0.039, 0.042, 0.046, 0.050, 0.053, 0.056])
SYNTHETIC = [  # (coupon_pct, redemption): the tenors, plus three to over-determine
    (1.5, dt.date(2027, 12, 7)),
    (3.25, dt.date(2029, 6, 7)),
    (4.5, dt.date(2031, 12, 7)),
    (2.75, dt.date(2033, 6, 7)),
    (5.0, dt.date(2036, 12, 7)),
    (6.0, dt.date(2043, 6, 7)),
    (4.25, dt.date(2046, 12, 7)),
    (3.5, dt.date(2056, 6, 7)),
    (4.75, dt.date(2066, 12, 7)),
    (5.25, dt.date(2076, 6, 7)),
]


@pytest.fixture(scope="module")
def prices():
    """The committed Friday-close feed."""
    return pd.read_csv(FIXTURES / "gilts_20261009.csv")


@pytest.fixture(scope="module")
def inventory():
    """The committed inventory as conventional bonds.

    The fixture carries no market column, and fit() only ever looks up the
    feed's conventional isins, so every row is built conventional.
    """
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


@pytest.fixture(scope="module")
def fitted_rows(prices, inventory):
    """(settle, rows): what fit() calibrates on, rebuilt from the fixtures."""
    settle = next_business_day(as_naive(prices["as_of"].iloc[0]).date())
    conventional = {b.isin: b for b in inventory if b.market == "conventional"}
    rows = []
    for _, r in prices.iterrows():
        bond = conventional[r["isin"]]
        schedule = bond.schedule(settle)
        ex = (
            dt.date.fromisoformat(str(bond.ex_dividend_date))
            if bond.ex_dividend_date
            else None
        )
        rows.append(
            {
                "coupon_pct": r["coupon_pct"],
                "schedule": schedule,
                "ex_dividend_date": ex,
                "dirty": r["clean_price"]
                + accrued(r["coupon_pct"], schedule, settle, ex),
                "dmo_yield": r["dmo_yield_pct"] / 100,
            }
        )
    return settle, rows


@pytest.fixture(scope="module")
def fitted(prices, inventory):
    """(values, report): the curve fitted to the committed snapshot."""
    return fit(prices, inventory)


class TestZAt:
    def test_linear_between_the_nodes(self):
        assert z_at(DEFAULT_TENORS, KNOWN, 2.0) == pytest.approx((KNOWN[0] + KNOWN[1]) / 2)
        assert z_at(DEFAULT_TENORS, KNOWN, 15.0) == pytest.approx((KNOWN[3] + KNOWN[4]) / 2)

    def test_flat_outside_the_nodes(self):
        assert z_at(DEFAULT_TENORS, KNOWN, 0.0) == KNOWN[0]
        assert z_at(DEFAULT_TENORS, KNOWN, 60.0) == KNOWN[-1]

    def test_vectorises_over_cashflow_times(self):
        at = z_at(DEFAULT_TENORS, KNOWN, [1.0, 10.0, 50.0])
        assert list(at) == pytest.approx([KNOWN[0], KNOWN[3], KNOWN[-1]])


class TestCurvePrice:
    def test_rising_curve_or_residual_lowers_the_price(self):
        base = curve_price(CPN, SCHED, SETTLE, DEFAULT_TENORS, FLAT)
        assert base > curve_price(CPN, SCHED, SETTLE, DEFAULT_TENORS, FLAT + 0.01)
        assert base < curve_price(CPN, SCHED, SETTLE, DEFAULT_TENORS, FLAT - 0.01)
        assert base > curve_price(CPN, SCHED, SETTLE, DEFAULT_TENORS, FLAT, residual=0.01)

    def test_a_flat_shift_is_the_same_as_raising_the_whole_curve(self):
        bumped = curve_price(CPN, SCHED, SETTLE, DEFAULT_TENORS, FLAT + 0.01)
        shifted = curve_price(CPN, SCHED, SETTLE, DEFAULT_TENORS, FLAT, residual=0.01)
        assert bumped == pytest.approx(shifted)

    @pytest.mark.parametrize(
        "redemption, coupon",
        [
            (dt.date(2027, 6, 7), 4.0),
            (dt.date(2031, 12, 7), 1.5),
            (dt.date(2040, 12, 7), 6.0),
            (dt.date(2056, 6, 7), 4.0),
            (dt.date(2076, 6, 7), 5.25),
        ],
    )
    def test_flat_curve_matches_the_semi_annual_price(self, redemption, coupon):
        """z = 2 * ln(1 + y/2) discounts exactly like the semi-annual yield y."""
        sched = coupon_schedule("7 Jun/Dec", redemption, SETTLE)
        curve = curve_price(coupon, sched, SETTLE, DEFAULT_TENORS, FLAT)
        assert curve == pytest.approx(price(coupon, sched, SETTLE, 0.05), abs=0.02)

    def test_ex_dividend_drops_a_coupon(self):
        ex = dt.date(2026, 10, 1)  # settled 12/10, so the 07/12/2026 coupon is lost
        cum = curve_price(CPN, SCHED, SETTLE, DEFAULT_TENORS, FLAT)
        x = curve_price(CPN, SCHED, SETTLE, DEFAULT_TENORS, FLAT, ex)
        assert x < cum


class TestResidualZspread:
    def test_round_trips_the_price_it_was_given(self):
        dirty = curve_price(CPN, SCHED, SETTLE, DEFAULT_TENORS, FLAT, residual=0.0025)
        assert residual_zspread(CPN, SCHED, SETTLE, dirty, DEFAULT_TENORS, FLAT) == pytest.approx(
            0.0025, abs=1e-9
        )

    def test_a_rarer_priced_gilt_needs_a_higher_residual(self):
        cheap = curve_price(CPN, SCHED, SETTLE, DEFAULT_TENORS, FLAT) - 1.0
        dear = curve_price(CPN, SCHED, SETTLE, DEFAULT_TENORS, FLAT) + 1.0
        assert residual_zspread(CPN, SCHED, SETTLE, cheap, DEFAULT_TENORS, FLAT) > (
            residual_zspread(CPN, SCHED, SETTLE, dear, DEFAULT_TENORS, FLAT)
        )

    def test_dirty_outside_the_price_range_raises(self):
        short = coupon_schedule("7 Jun/Dec", dt.date(2027, 6, 7), SETTLE)
        with pytest.raises(ValueError, match="outside this gilt's price range"):
            residual_zspread(CPN, short, SETTLE, 1000.0, DEFAULT_TENORS, FLAT)


def synthetic_rows():
    """The synthetic gilts priced off KNOWN, so the truth is recoverable."""
    rows = []
    for coupon, redemption in SYNTHETIC:
        schedule = coupon_schedule("7 Jun/Dec", redemption, SETTLE)
        rows.append(
            {
                "coupon_pct": coupon,
                "schedule": schedule,
                "ex_dividend_date": None,
                "dirty": curve_price(coupon, schedule, SETTLE, DEFAULT_TENORS, KNOWN),
                "dmo_yield": 0.045,
            }
        )
    return rows


class TestCalibrate:
    def test_recovers_the_curve_the_prices_were_built_from(self):
        values, residuals = calibrate(SETTLE, synthetic_rows())
        assert list(values) == pytest.approx(KNOWN, abs=1e-7)
        assert max(abs(r) for r in residuals) < 1e-6

    def test_needs_something_to_calibrate_on(self):
        with pytest.raises(ValueError, match="nothing to calibrate"):
            calibrate(SETTLE, [])


class TestFit:
    """fit() on the committed Friday-close feed and inventory.

    The report is a house-style snapshot: counts wherever the market is free
    to disagree (the seven nodes cannot follow 71 gilts), plus the optimality
    of the sum of squares the calibration minimised.
    """

    def test_report_shape(self, prices, fitted):
        values, report = fitted
        assert len(values) == len(DEFAULT_TENORS)
        assert len(report) == len(prices)
        assert list(report.columns) == [
            "isin",
            "tidm",
            "name",
            "section",
            "residual_bp",
            "model_price",
            "market_price",
        ]

    def test_market_price_is_clean_plus_the_engine_accrued(self, fitted, fitted_rows):
        _, report = fitted
        settle, rows = fitted_rows
        assert settle == dt.date(2026, 10, 12)  # Friday 09/10 close, T+1 over the weekend
        assert list(report["market_price"]) == pytest.approx(
            [row["dirty"] for row in rows], abs=1e-9
        )

    def test_residuals_are_small(self, fitted):
        _, report = fitted
        d = report["residual_bp"].abs()
        # Out of 71 rows: median ~4.6bp, all within 60bp, all but one within
        # 30bp, all but six within 20bp — the shortest gilt is repriced off
        # the flat extrapolation below the 1y node.
        assert d.median() <= 6
        assert (d <= 20).sum() >= 63
        assert (d <= 30).sum() >= 68
        assert d.max() <= 60

    def test_nudging_any_node_up_or_down_raises_the_sse(self, fitted, fitted_rows):
        values, _ = fitted
        settle, rows = fitted_rows

        def sse(curve):
            return sum(
                residual_zspread(
                    row["coupon_pct"],
                    row["schedule"],
                    settle,
                    row["dirty"],
                    DEFAULT_TENORS,
                    curve,
                    row["ex_dividend_date"],
                )
                ** 2
                for row in rows
            )

        base = sse(values)
        for k in range(len(values)):
            for bump in (-1e-4, 1e-4):
                nudged = values.copy()
                nudged[k] += bump
                assert sse(nudged) > base
