import datetime as dt

import pytest
from conftest import make_bond

import bond_management.core.bond as bond_module
from bond_management.core.bond import Bond
from bond_management.core.pricing import coupon_schedule


class TestBond:
    def test_fields_are_stored(self):
        b = make_bond(
            isin="GB00B3KJDS62",
            name="Treasury 4.25% 2036",
            market="index-linked",
            indexation_lag=3,
            section="Long",
            amount_in_issue_m=12345.0,
            base_rpi=92.6,
            amount_incl_uplift_m=1050.0,
        )
        assert b.isin == "GB00B3KJDS62"
        assert b.name == "Treasury 4.25% 2036"
        assert b.market == "index-linked"
        assert b.indexation_lag == 3
        assert b.section == "Long"
        assert b.amount_in_issue_m == 12345.0
        assert b.base_rpi == 92.6
        assert b.amount_incl_uplift_m == 1050.0

    def test_to_dict_has_every_column(self):
        b = make_bond()
        assert list(b.to_dict()) == bond_module.COLUMNS
        assert b.to_dict()["isin"] == b.isin

    def test_schedule(self):
        b = make_bond(dividend_dates="7 Jun/Dec", redemption_date=dt.date(2027, 12, 7))
        assert b.schedule(dt.date(2026, 10, 12))[-3:] == [
            dt.date(2026, 12, 7),
            dt.date(2027, 6, 7),
            dt.date(2027, 12, 7),
        ]

    def test_established_gilt_schedule_is_the_plain_pattern(self):
        b = make_bond(
            dividend_dates="7 Jun/Dec",
            redemption_date=dt.date(2027, 12, 7),
            first_issue_date=dt.date(2021, 3, 3),
        )
        assert b.schedule(dt.date(2026, 10, 12)) == coupon_schedule(
            "7 Jun/Dec", dt.date(2027, 12, 7), dt.date(2026, 10, 12)
        )

    def test_new_issue_schedule_anchors_at_the_issue_date(self):
        # 4 5/8% Treasury Gilt 2030 (T30K): first issued 11/09/2026, so the
        # 22/05/2026 coupon never existed and the issue date anchors instead.
        b = make_bond(
            dividend_dates="22 May/Nov",
            redemption_date=dt.date(2030, 5, 22),
            first_issue_date=dt.date(2026, 9, 11),
        )
        assert b.schedule(dt.date(2026, 10, 19))[:2] == [
            dt.date(2026, 9, 11),
            dt.date(2026, 11, 22),
        ]

    def test_settlement_before_issue_keeps_the_pattern(self):
        b = make_bond(
            dividend_dates="22 May/Nov",
            redemption_date=dt.date(2030, 5, 22),
            first_issue_date=dt.date(2026, 10, 21),
        )
        assert dt.date(2026, 5, 22) in b.schedule(dt.date(2026, 10, 19))

    def test_schedule_without_a_redemption_date_raises(self):
        b = make_bond()
        with pytest.raises(ValueError, match="no redemption date"):
            b.schedule(dt.date(2026, 10, 19))


class TestFrame:
    def test_columns_and_rows(self):
        bonds = [
            make_bond("GB00B3KJDS62"),
            make_bond("GB00BFMCN691", market="index-linked"),
        ]
        df = Bond.frame(bonds)
        assert list(df.columns) == bond_module.COLUMNS
        assert df["isin"].tolist() == ["GB00B3KJDS62", "GB00BFMCN691"]
        assert list(df["market"]) == ["conventional", "index-linked"]

    def test_empty(self):
        df = Bond.frame([])
        assert len(df) == 0
        assert list(df.columns) == bond_module.COLUMNS
