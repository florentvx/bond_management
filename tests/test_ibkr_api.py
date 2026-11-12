import datetime as dt
import math
from pathlib import Path

import pandas as pd
import pytest
from conftest import make_bond

from bond_management.core.dates import as_naive, next_business_day
from bond_management.core.pricing import YEAR
from bond_management.core.zspread import DEFAULT_TENORS, fit, z_at
from bond_management.scripts import ibkr_api
from bond_management.scripts.ibkr_api import (
    KEY_FIELDS,
    TABLE_COLUMNS,
    net_positions,
    parse_symbol,
    show,
    trade_records,
    update_history,
    zero_coupon_table,
)

FIXTURES = Path(__file__).parent / "fixtures"
SETTLE = dt.date(2026, 11, 9)  # the synthetic market's T+1


def trade(**overrides):
    row = {
        "accountId": "U1",
        "dateTime": "2026-10-23;034123",
        "symbol": "UKT 1 1/4 07/31/51",
        "buySell": "BUY",
        "quantity": "3000",
        "tradePrice": "40.911",
        "tradeDate": "2026-10-23",
        "proceeds": "-1227.33",
        "fifoPnlRealized": "0",
    }
    row.update(overrides)
    return row


class TestUpdateHistory:
    def test_first_run_writes_every_trade_oldest_first(self, tmp_path):
        path = tmp_path / "screens" / "ibkr_trades.csv"
        checked, refreshed, added = update_history(
            [trade(dateTime="2026-11-01;090000"), trade()], path
        )
        assert (checked, refreshed, added) == (0, 0, 2)
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        assert list(df["dateTime"]) == ["2026-10-23;034123", "2026-11-01;090000"]
        assert set(KEY_FIELDS) <= set(df.columns)

    def test_rerun_of_the_same_statement_changes_nothing(self, tmp_path, capsys):
        path = tmp_path / "ibkr_trades.csv"
        update_history([trade()], path)
        before = path.read_text()
        checked, refreshed, added = update_history([trade()], path)
        assert (checked, refreshed, added) == (1, 0, 0)
        assert path.read_text() == before
        assert capsys.readouterr().out == ""

    def test_broker_corrections_are_reported_and_stored(self, tmp_path, capsys):
        path = tmp_path / "ibkr_trades.csv"
        update_history([trade()], path)
        checked, refreshed, added = update_history(
            [trade(fifoPnlRealized="12.5", proceeds="-1228.00")], path
        )
        assert (checked, refreshed, added) == (1, 1, 0)
        out = capsys.readouterr().out
        assert "fifoPnlRealized '0' -> '12.5'" in out
        assert "proceeds '-1227.33' -> '-1228.00'" in out
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        assert df["fifoPnlRealized"].iloc[0] == "12.5"

    def test_trades_the_statement_no_longer_carries_are_kept(self, tmp_path):
        path = tmp_path / "ibkr_trades.csv"
        update_history([trade(), trade(dateTime="2026-11-01;090000")], path)
        checked, refreshed, added = update_history([trade()], path)
        assert (checked, refreshed, added) == (1, 0, 0)
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        assert len(df) == 2

    def test_new_statement_column_leaves_old_rows_blank_silent(self, tmp_path, capsys):
        path = tmp_path / "ibkr_trades.csv"
        update_history([trade()], path)
        checked, refreshed, added = update_history([trade(tradeID="99")], path)
        assert (checked, refreshed, added) == (1, 0, 0)
        assert capsys.readouterr().out == ""
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        assert df["tradeID"].iloc[0] == ""

    def test_missing_identity_field_raises(self):
        with pytest.raises(RuntimeError, match="identity fields"):
            trade_records(_FakeRoot(trade={"symbol": "X"}))


class _FakeTrade:
    def __init__(self, attrib):
        self.attrib = attrib


class _FakeRoot:
    def __init__(self, **trades):
        self._trades = [_FakeTrade(a) for a in trades.values()]

    def iter(self, tag):
        return iter(self._trades if tag == "Trade" else [])


class TestLoadSecrets:
    def test_environment_wins_over_the_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ibkr_api, "SECRETS_FILE", tmp_path / "missing.env")
        monkeypatch.setenv("IBKR_TOKEN", "env-token")
        monkeypatch.setenv("IBKR_QUERY_ID", "env-query")
        assert ibkr_api.load_secrets() == ("env-token", "env-query")

    def test_falls_back_to_the_gitignored_file(self, monkeypatch, tmp_path):
        secrets = tmp_path / "ibkr.env"
        secrets.write_text("IBKR_TOKEN=file-token\nIBKR_QUERY_ID=file-query\n")
        monkeypatch.setattr(ibkr_api, "SECRETS_FILE", secrets)
        monkeypatch.delenv("IBKR_TOKEN", raising=False)
        monkeypatch.delenv("IBKR_QUERY_ID", raising=False)
        assert ibkr_api.load_secrets() == ("file-token", "file-query")

    def test_absent_everywhere_is_an_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ibkr_api, "SECRETS_FILE", tmp_path / "missing.env")
        monkeypatch.delenv("IBKR_TOKEN", raising=False)
        monkeypatch.delenv("IBKR_QUERY_ID", raising=False)
        with pytest.raises(RuntimeError, match="IBKR_TOKEN"):
            ibkr_api.load_secrets()


def synthetic_market():
    """(prices, bonds): three gilts, two of them redeeming on the same date."""
    prices = pd.DataFrame(
        [
            {
                "name": "0% Treasury Gilt 2030",
                "tidm": "ZC30",
                "isin": "GB00SYNTH001",
                "coupon_pct": 0.0,
                "matures": "2030-06-07 2-5Y",
                "clean_price": 80.0,
                "dmo_yield_pct": 4.0,
                "as_of": "2026-11-06 17:00",
            },
            {
                "name": "4% Treasury Gilt 2030",
                "tidm": "CB30",
                "isin": "GB00SYNTH002",
                "coupon_pct": 4.0,
                "matures": "2030-06-07 2-5Y",
                "clean_price": 110.0,
                "dmo_yield_pct": 4.0,
                "as_of": "2026-11-06 17:00",
            },
            {
                "name": "4% Treasury Gilt 2031",
                "tidm": "CB31",
                "isin": "GB00SYNTH003",
                "coupon_pct": 4.0,
                "matures": "2031-12-07 2-5Y",
                "clean_price": 105.0,
                "dmo_yield_pct": 4.0,
                "as_of": "2026-11-06 17:00",
            },
        ]
    )
    bonds = [
        make_bond(
            isin="GB00SYNTH001",
            name="0% Treasury Gilt 2030",
            dividend_dates="7 Jun/Dec",
            redemption_date=dt.date(2030, 6, 7),
        ),
        make_bond(
            isin="GB00SYNTH002",
            name="4% Treasury Gilt 2030",
            dividend_dates="7 Jun/Dec",
            redemption_date=dt.date(2030, 6, 7),
        ),
        make_bond(
            isin="GB00SYNTH003",
            name="4% Treasury Gilt 2031",
            dividend_dates="7 Jun/Dec",
            redemption_date=dt.date(2031, 12, 7),
        ),
    ]
    return prices, bonds


def buys(holdings):
    """A statement root of BUY trades: {symbol: face}."""
    return _FakeRoot(
        **{
            f"t{i}": {"symbol": s, "buySell": "BUY", "quantity": str(face)}
            for i, (s, face) in enumerate(holdings.items())
        }
    )


def z_reproduces_pv(row, prices, bonds, settle):
    """amount discounted at the fitted curve at the row's z-spread, vs its pv."""
    values, _ = fit(prices, bonds)
    t = (row["payment_date"] - settle).days / YEAR
    z = row["zspread_bp"] / 10_000
    return row["amount"] * math.exp(-(z_at(DEFAULT_TENORS, values, t) + z) * t)


def curve_reproduces_pv(row, prices, bonds, settle):
    """amount discounted at the fitted curve at a zero z-spread, vs curve_pv."""
    values, _ = fit(prices, bonds)
    t = (row["payment_date"] - settle).days / YEAR
    return row["amount"] * math.exp(-z_at(DEFAULT_TENORS, values, t) * t)


class TestParseSymbol:
    def test_mixed_fraction_and_us_date(self):
        assert parse_symbol("UKT 1 1/4 07/31/51") == (1.25, dt.date(2051, 7, 31))
        assert parse_symbol("UKT 4 1/2 06/07/28") == (4.5, dt.date(2028, 6, 7))

    def test_whole_and_zero_coupon(self):
        assert parse_symbol("UKT 4 06/07/30") == (4.0, dt.date(2030, 6, 7))
        assert parse_symbol("UKT 0 06/07/30") == (0.0, dt.date(2030, 6, 7))

    def test_not_a_gilt_symbol(self):
        with pytest.raises(ValueError):
            parse_symbol("CASH")


class TestNetPositions:
    def test_buys_net_against_sells(self):
        root = _FakeRoot(
            a={"symbol": "UKT 4 1/2 06/07/28", "buySell": "BUY", "quantity": "3000"},
            b={"symbol": "UKT 4 1/2 06/07/28", "buySell": "SELL", "quantity": "1000"},
            c={"symbol": "UKT 1 1/4 07/31/51", "buySell": "SELL", "quantity": "500"},
        )
        assert net_positions(root) == {
            "UKT 4 1/2 06/07/28": 2000.0,
            "UKT 1 1/4 07/31/51": -500.0,
        }


class TestZeroCouponTable:
    def test_every_coupon_and_redemption_becomes_a_row(self, capsys):
        prices, bonds = synthetic_market()
        root = buys({"UKT 0 06/07/30": 1000, "UKT 4 06/07/30": 500, "UKT 4 12/07/31": 700})
        table = zero_coupon_table(root, prices, bonds, SETTLE)
        amounts = dict(zip(table["payment_date"], table["amount"]))
        # B and C pay twice a year to 2031; A only redeems
        assert len(table) == 11
        assert amounts[dt.date(2026, 12, 7)] == 24.0  # B's and C's coupons together
        assert amounts[dt.date(2029, 12, 7)] == 24.0
        assert amounts[dt.date(2030, 6, 7)] == 1524.0  # A redemption + B final + C coupon
        assert amounts[dt.date(2031, 12, 7)] == 714.0  # C's final redemption
        for _, row in table.iterrows():
            assert z_reproduces_pv(row, prices, bonds, SETTLE) == pytest.approx(
                row["pv"], rel=1e-9
            )
            assert curve_reproduces_pv(row, prices, bonds, SETTLE) == pytest.approx(
                row["curve_pv"], rel=1e-9
            )
        assert "skipped" not in capsys.readouterr().out

    def test_merged_pv_is_the_sum_of_the_flow_pvs(self):
        prices, bonds = synthetic_market()
        date = dt.date(2030, 6, 7)

        def flow(holdings):
            table = zero_coupon_table(buys(holdings), prices, bonds, SETTLE)
            return table[table["payment_date"] == date].iloc[0]

        alone_a = flow({"UKT 0 06/07/30": 1000})
        alone_b = flow({"UKT 4 06/07/30": 500})
        merged = flow({"UKT 0 06/07/30": 1000, "UKT 4 06/07/30": 500})
        assert merged["pv"] == pytest.approx(alone_a["pv"] + alone_b["pv"])
        assert merged["curve_pv"] == pytest.approx(
            alone_a["curve_pv"] + alone_b["curve_pv"]
        )
        assert merged["amount"] == 1510.0  # A's 1000 redemption + B's 500 x 102%

    def test_flat_and_unmatched_symbols_are_skipped(self, capsys):
        prices, bonds = synthetic_market()
        root = _FakeRoot(
            short={"symbol": "UKT 4 06/07/30", "buySell": "SELL", "quantity": "500"},
            odd={"symbol": "UKT 9 1/2 01/01/40", "buySell": "BUY", "quantity": "100"},
        )
        table = zero_coupon_table(root, prices, bonds, SETTLE)
        assert table.empty
        out = capsys.readouterr().out
        assert "not net long, skipped: UKT 4 06/07/30" in out
        assert "UKT 9 1/2 01/01/40 (no match in the price feed)" in out

    def test_no_trades_gives_an_empty_table(self):
        prices, bonds = synthetic_market()
        table = zero_coupon_table(_FakeRoot(), prices, bonds, SETTLE)
        assert list(table.columns) == TABLE_COLUMNS
        assert table.empty

    def test_committed_fixture_market(self, capsys):
        prices = pd.read_csv(FIXTURES / "gilts_20261009.csv")
        rows = pd.read_csv(FIXTURES / "inventory_20261008.csv")
        bonds = [
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
        settle = next_business_day(as_naive(prices["as_of"].iloc[0]).date())
        root = buys({"UKT 4 1/2 06/07/28": 1000})
        table = zero_coupon_table(root, prices, bonds, settle)
        assert list(table["payment_date"]) == [
            dt.date(2026, 12, 7),
            dt.date(2027, 6, 7),
            dt.date(2027, 12, 7),
            dt.date(2028, 6, 7),
        ]
        assert list(table["amount"]) == [22.5, 22.5, 22.5, 1022.5]
        for _, row in table.iterrows():
            assert z_reproduces_pv(row, prices, bonds, settle) == pytest.approx(
                row["pv"], rel=1e-9
            )
            assert curve_reproduces_pv(row, prices, bonds, settle) == pytest.approx(
                row["curve_pv"], rel=1e-9
            )
        assert "skipped" not in capsys.readouterr().out


class TestShow:
    def test_prints_the_table_and_copies_it(self, monkeypatch, capsys):
        prices, bonds = synthetic_market()
        table = zero_coupon_table(buys({"UKT 0 06/07/30": 1000}), prices, bonds, SETTLE)
        copied = {}
        monkeypatch.setattr(
            pd.DataFrame, "to_clipboard", lambda self, **kw: copied.update(df=self)
        )
        show(table, dt.date(2026, 11, 6), SETTLE)
        out = capsys.readouterr().out
        assert "payment_date" in out and "zspread_bp" in out
        assert "copied to the clipboard" in out
        assert copied["df"].iloc[0]["pv"] == table.iloc[0]["pv"]
        assert copied["df"].iloc[0]["curve_pv"] == table.iloc[0]["curve_pv"]
        assert list(copied["df"].columns) == TABLE_COLUMNS

    def test_empty_table_is_printed_but_not_copied(self, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise AssertionError("empty table must not touch the clipboard")

        monkeypatch.setattr(pd.DataFrame, "to_clipboard", boom)
        show(pd.DataFrame(columns=TABLE_COLUMNS), dt.date(2026, 11, 6), SETTLE)
        assert "no net-long holding matched the price feed" in capsys.readouterr().out
