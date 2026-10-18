import datetime as dt
from pathlib import Path

import pandas as pd
import pytest
from conftest import make_bond

from bond_management.dates import as_naive, next_business_day
from bond_management.pricing import (
    accrued,
    cashflows,
    convexity,
    coupon_schedule,
    modified_duration,
    parse_pattern,
    price,
    yield_from_price,
)

FIXTURES = Path(__file__).parent / "fixtures"

# A 4% gilt paying 7 Jun/Dec, redeemed 2027-12-07.
SCHED = [
    dt.date(2026, 6, 7),
    dt.date(2026, 12, 7),
    dt.date(2027, 6, 7),
    dt.date(2027, 12, 7),
]
CPN = 4.0


@pytest.fixture(scope="module")
def snapshot():
    """The committed Friday-close feed joined to the inventory, with T+1 settlement."""
    prices = pd.read_csv(FIXTURES / "gilts_20261009.csv")
    inv = pd.read_csv(FIXTURES / "inventory_20261008.csv").set_index("isin")
    settle = next_business_day(as_naive(prices["as_of"].iloc[0]).date())  # T+1 rolls over weekends/bank holidays
    return prices, inv, settle


def row_setup(inv, isin, settle):
    """(coupon_pct is caller-owned) schedule + ex-dividend date for a fixture gilt."""
    row = inv.loc[isin]
    b = make_bond(
        isin=isin,
        dividend_dates=row.dividend_dates,
        redemption_date=dt.date.fromisoformat(row.redemption_date),
        first_issue_date=dt.date.fromisoformat(row.first_issue_date),
    )
    ex = dt.date.fromisoformat(row.ex_dividend_date) if row.ex_dividend_date else None
    return b.schedule(settle), ex


def dirty_of(r, sched, settle, ex):
    """Clean price plus the engine's accrued.

    The feed's own dirty embeds its accrued, which for a new issue follows a
    pro-rated-stub basis that disagrees with its DMO-yield column, so engine
    yields are solved against clean + the engine's accrued instead.
    """
    return r["clean_price"] + accrued(r["coupon_pct"], sched, settle, ex)


class TestParsePattern:
    def test_real_patterns(self):
        assert parse_pattern("22 Apr/Oct") == (22, 4, 10)
        assert parse_pattern("7 Jun/Dec") == (7, 6, 12)
        assert parse_pattern(" 31 Jan/Jul ") == (31, 1, 7)

    @pytest.mark.parametrize("bad", ["June/December", "22/10", "", "22 Apr-Oct", "22 Abr/Abr"])
    def test_rejects_anything_else(self, bad):
        with pytest.raises(ValueError, match="unrecognised dividend dates"):
            parse_pattern(bad)


class TestCouponSchedule:
    def test_through_redemption_only(self):
        sched = coupon_schedule("7 Jun/Dec", dt.date(2027, 12, 7), dt.date(2026, 10, 12))
        assert sched == [
            dt.date(2025, 6, 7),
            dt.date(2025, 12, 7),
            dt.date(2026, 6, 7),
            dt.date(2026, 12, 7),
            dt.date(2027, 6, 7),
            dt.date(2027, 12, 7),
        ]

    def test_clamps_to_month_end(self):
        sched = coupon_schedule("31 Aug/Feb", dt.date(2028, 12, 31), dt.date(2027, 1, 1))
        assert dt.date(2026, 2, 28) in sched  # 2026 is not a leap year
        assert dt.date(2028, 2, 29) in sched  # 2028 is
        assert sched[-1] == dt.date(2028, 8, 31)


class TestCashflows:
    def test_coupon_halves_and_redemption(self):
        flows = cashflows(CPN, SCHED, dt.date(2026, 10, 12))
        assert flows == [
            (dt.date(2026, 12, 7), 2.0),
            (dt.date(2027, 6, 7), 2.0),
            (dt.date(2027, 12, 7), 102.0),
        ]

    def test_settlement_on_a_coupon_date_leaves_it_out(self):
        flows = cashflows(CPN, SCHED, dt.date(2026, 12, 7))
        assert flows[0] == (dt.date(2027, 6, 7), 2.0)

    def test_ex_dividend_drops_the_next_coupon(self):
        settle, ex = dt.date(2026, 12, 1), dt.date(2026, 11, 30)
        flows = cashflows(CPN, SCHED, settle, ex)
        assert flows == [
            (dt.date(2027, 6, 7), 2.0),
            (dt.date(2027, 12, 7), 102.0),
        ]
        assert cashflows(CPN, SCHED, dt.date(2026, 11, 29), ex)[0] == (
            dt.date(2026, 12, 7),
            2.0,
        )

    def test_ex_dividend_still_pays_the_redemption(self):
        final = [dt.date(2026, 6, 7), dt.date(2026, 12, 7)]
        flows = cashflows(CPN, final, dt.date(2026, 12, 1), dt.date(2026, 11, 30))
        assert flows == [(dt.date(2026, 12, 7), 100.0)]

    def test_matured_gilt_has_no_flows(self):
        assert cashflows(CPN, SCHED, dt.date(2028, 1, 1)) == []


class TestAccrued:
    def test_mid_period(self):
        # 127 of the 183 days from 07/06 to 07/12 have elapsed.
        assert accrued(CPN, SCHED, dt.date(2026, 10, 12)) == pytest.approx(2 * 127 / 183)

    def test_on_a_coupon_date(self):
        assert accrued(CPN, SCHED, dt.date(2026, 12, 7)) == 0.0

    def test_zero_while_ex_dividend(self):
        settle, ex = dt.date(2026, 12, 1), dt.date(2026, 11, 30)
        assert accrued(CPN, SCHED, settle, ex) == 0.0


class TestPrice:
    def test_par_when_yield_equals_coupon_on_a_coupon_date(self):
        assert price(CPN, SCHED, dt.date(2026, 12, 7), 0.04) == pytest.approx(100.0)

    def test_discount_and_premium_move_with_yield(self):
        settle = dt.date(2026, 10, 12)
        assert (
            price(CPN, SCHED, settle, 0.03)
            > price(CPN, SCHED, settle, 0.05)
            > price(CPN, SCHED, settle, 0.07)
        )

    def test_round_trip_through_yield_from_price(self):
        settle = dt.date(2026, 10, 12)
        for compound in ("semi", "annual"):
            for y in (0.01, 0.04, 0.08):
                dirty = price(CPN, SCHED, settle, y, compound=compound)
                assert yield_from_price(
                    CPN, SCHED, settle, dirty, compound=compound
                ) == pytest.approx(y, abs=1e-9)

    def test_dirty_outside_the_bracket_raises(self):
        with pytest.raises(ValueError, match="outside this gilt's price range"):
            yield_from_price(CPN, SCHED, dt.date(2026, 10, 12), 1000.0)

    def test_unknown_compound_raises(self):
        with pytest.raises(ValueError, match="unknown compound"):
            price(CPN, SCHED, dt.date(2026, 10, 12), 0.05, compound="continuous")


class TestRisk:
    def test_duration_and_convexity_match_numerical_derivatives(self):
        settle = dt.date(2026, 10, 12)
        h = 1e-4

        def pv(y):
            return price(CPN, SCHED, settle, y)

        for y in (0.03, 0.05):
            d1 = (pv(y + h) - pv(y - h)) / (2 * h)
            d2 = (pv(y + h) - 2 * pv(y) + pv(y - h)) / h**2
            assert modified_duration(CPN, SCHED, settle, y) == pytest.approx(
                -d1 / pv(y), rel=1e-6
            )
            assert convexity(CPN, SCHED, settle, y) == pytest.approx(d2 / pv(y), rel=1e-5)

    def test_earlier_maturity_means_less_duration(self):
        settle = dt.date(2026, 10, 12)
        near = [dt.date(2026, 12, 7)]
        assert modified_duration(CPN, SCHED, settle, 0.05) > modified_duration(
            CPN, near, settle, 0.05
        )

    def test_convexity_is_positive(self):
        assert convexity(CPN, SCHED, dt.date(2026, 10, 12), 0.05) > 0


class TestFeedCalibration:
    """The engine reproduces the feed's own yield/duration/accrued columns.

    Uses the committed October snapshot. The feed's accrued, dirty and
    duration for its two new issues (T30K, T32H) follow a pro-rated-stub
    model that its DMO-yield column does not, so engine yields are solved
    from clean + the engine's accrued (see dirty_of) and the assertions are
    counts wherever the feed is free to disagree.
    """

    def test_feed_maturity_matches_inventory(self, snapshot):
        prices, inv, _ = snapshot
        assert all(
            dt.date.fromisoformat(r["matures"])
            == dt.date.fromisoformat(inv.loc[r["isin"]]["redemption_date"])
            for _, r in prices.iterrows()
        )

    def test_annual_yield_matches_feed_gross_yield(self, snapshot):
        prices, inv, settle = snapshot
        errs = []
        for _, r in prices.iterrows():
            sched, ex = row_setup(inv, r["isin"], settle)
            y = yield_from_price(
                r["coupon_pct"], sched, settle, r["dirty_price"], ex, compound="annual"
            )
            errs.append(abs((y - r["gross_yield_pct"] / 100) * 10_000))
        errs = pd.Series(errs)
        # Out of 71 rows: every gilt within 6bp, all but four within 2bp.
        assert (errs <= 2).sum() >= 64
        assert (errs <= 6).sum() >= 67

    def test_semi_yield_matches_feed_dmo_yield(self, snapshot):
        prices, inv, settle = snapshot
        errs = []
        for _, r in prices.iterrows():
            sched, ex = row_setup(inv, r["isin"], settle)
            y = yield_from_price(
                r["coupon_pct"], sched, settle, dirty_of(r, sched, settle, ex), ex, compound="semi"
            )
            errs.append(abs((y - r["dmo_yield_pct"] / 100) * 10_000))
        errs = pd.Series(errs)
        # Out of 71 rows: all within 2bp, all but one within 0.5bp.
        assert (errs <= 0.5).sum() >= 70
        assert (errs <= 2).all()

    def test_duration_matches_feed(self, snapshot):
        prices, inv, settle = snapshot
        errs = []
        for _, r in prices.iterrows():
            if r["mod_duration"] <= 1:
                continue
            sched, ex = row_setup(inv, r["isin"], settle)
            y = yield_from_price(
                r["coupon_pct"], sched, settle, dirty_of(r, sched, settle, ex), ex, compound="semi"
            )
            errs.append(
                abs(
                    modified_duration(r["coupon_pct"], sched, settle, y, ex)
                    - r["mod_duration"]
                )
            )
        errs = pd.Series(errs)
        # Out of the 67 rows with mod_duration > 1: all but one within 0.06 —
        # T30K's feed duration comes off the feed's pro-rated new-issue model.
        assert (errs <= 0.06).sum() >= 66

    def test_accrued_matches_feed(self, snapshot):
        prices, inv, settle = snapshot
        errs = {}
        for _, r in prices.iterrows():
            if r["mod_duration"] <= 1:
                continue
            sched, ex = row_setup(inv, r["isin"], settle)
            if ex is not None and settle >= ex:
                continue  # the feed quotes a quirky negative figure while ex-dividend
            errs[r["tidm"]] = abs(
                accrued(r["coupon_pct"], sched, settle, ex) - r["accrued"]
            )
        errs = pd.Series(errs)
        # T30K and T32H accrue on DMO's full-coupon-from-issue basis; the
        # feed's accrued for them follows its pro-rated-stub model.
        assert (errs.drop(["T30K", "T32H"]) <= 0.05).all()

    def test_yield_round_trips_on_every_feed_row(self, snapshot):
        prices, inv, settle = snapshot
        for _, r in prices.iterrows():
            sched, ex = row_setup(inv, r["isin"], settle)
            for compound in ("semi", "annual"):
                y = yield_from_price(
                    r["coupon_pct"], sched, settle, r["dirty_price"], ex, compound
                )
                dirty = price(r["coupon_pct"], sched, settle, y, ex, compound)
                assert dirty == pytest.approx(r["dirty_price"], abs=1e-6)


class TestNewIssue:
    """A gilt issued inside its first coupon period is anchored at its issue date.

    4 5/8% Treasury Gilt 2030 (T30K) was first issued 11/09/2026 and pays
    22 May/Nov, so there was no 22/05/2026 coupon: the first period runs
    from the issue date (72 days to the 22/11/2026 coupon) and accrual
    starts there too.
    """

    CPN = 4.625
    ISSUE = dt.date(2026, 9, 11)
    SETTLE = dt.date(2026, 10, 19)

    def bond(self):
        return make_bond(
            dividend_dates="22 May/Nov",
            redemption_date=dt.date(2030, 5, 22),
            first_issue_date=self.ISSUE,
        )

    def test_schedule_drops_pre_issue_coupons_and_anchors_at_issue(self):
        sched = self.bond().schedule(self.SETTLE)
        assert sched[:2] == [self.ISSUE, dt.date(2026, 11, 22)]
        assert sched[-1] == dt.date(2030, 5, 22)

    def test_accrues_from_the_issue_date(self):
        sched = self.bond().schedule(self.SETTLE)
        # 38 of the 72 days from 11/09 to 22/11 have elapsed.
        assert accrued(self.CPN, sched, self.SETTLE) == pytest.approx(2.3125 * 38 / 72)

    def test_yield_matches_the_dmo_convention(self):
        # Clean 99.08 on 16/10/2026, DMO yield 4.908%: full first coupon,
        # accrued from the issue date.
        sched = self.bond().schedule(self.SETTLE)
        dirty = 99.08 + accrued(self.CPN, sched, self.SETTLE)
        y = yield_from_price(self.CPN, sched, self.SETTLE, dirty)
        assert y * 100 == pytest.approx(4.895, abs=0.02)


class TestFinalPeriodExDividend:
    """Ex-dividend in the final coupon period: the redemption date does not shift.

    0 3/8% Treasury Gilt 2026 (T26A) matures 22/10/2026 and went ex-dividend
    13/10/2026: a holder settling 19/10 forfeits the last coupon but still
    receives 100 at maturity, three days out.
    """

    CPN = 0.375
    SCHED = (dt.date(2026, 4, 22), dt.date(2026, 10, 22))
    EX = dt.date(2026, 10, 13)
    SETTLE = dt.date(2026, 10, 19)

    def test_only_the_redemption_is_left(self):
        flows = cashflows(self.CPN, self.SCHED, self.SETTLE, self.EX)
        assert flows == [(dt.date(2026, 10, 22), 100.0)]

    def test_yield_is_not_collapsed_by_a_period_shift(self):
        y = yield_from_price(self.CPN, self.SCHED, self.SETTLE, 99.96, self.EX)
        # Three days to redemption at 99.96, semi-annually compounded.
        assert y * 100 == pytest.approx(4.941, abs=0.02)

    def test_mid_life_ex_dividend_still_shifts_a_period(self):
        # A 4% gilt with two periods left: dropping the December coupon keeps
        # the later flows on their original steps, i.e. the price falls by
        # the discounted coupon alone.
        sched = [
            dt.date(2026, 6, 7),
            dt.date(2026, 12, 7),
            dt.date(2027, 6, 7),
            dt.date(2027, 12, 7),
        ]
        settle, ex = dt.date(2026, 12, 1), dt.date(2026, 11, 30)
        cum = price(4.0, sched, settle, 0.05)
        x = price(4.0, sched, settle, 0.05, ex)
        assert cum - x == pytest.approx(2.0 * 1.025 ** -(6 / 183))
