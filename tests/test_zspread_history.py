import datetime as dt
from pathlib import Path

import pandas as pd
import pytest
from conftest import make_bond

import zspread_history
from bond_management import dmo
from bond_management.zspread import DEFAULT_TENORS, fit
from zspread_history import history

FIXTURES = Path(__file__).parent / "fixtures"


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


def snapshots(tmp_path):
    """Copy the committed Friday-close feed into tmp_path under its cache name."""
    path = tmp_path / "gilts_20261009.csv"
    path.write_bytes((FIXTURES / "gilts_20261009.csv").read_bytes())
    return path


class TestHistory:
    def test_columns_and_dates(self, tmp_path, inventory):
        snapshots(tmp_path)
        df = history(tmp_path, bonds=inventory)
        assert list(df.columns) == ["feed", "settle"] + [
            f"{t:g}y" for t in DEFAULT_TENORS
        ]
        assert len(df) == 1
        assert df["feed"].iloc[0] == dt.date(2026, 10, 9)
        assert df["settle"].iloc[0] == dt.date(2026, 10, 12)  # Friday close, T+1

    def test_values_are_fit_on_the_same_snapshot(self, tmp_path, inventory):
        path = snapshots(tmp_path)
        df = history(tmp_path, bonds=inventory)
        values, _ = fit(pd.read_csv(path), inventory)
        got = [df.iloc[0][f"{t:g}y"] for t in DEFAULT_TENORS]
        assert got == pytest.approx([v * 10_000 for v in values], abs=0.005)

    def test_one_row_per_distinct_close(self, tmp_path, inventory):
        snapshots(tmp_path)
        df = history(tmp_path, bonds=inventory)
        again = tmp_path / "gilts_20261011.csv"  # a Sunday run rereads Friday
        again.write_bytes((FIXTURES / "gilts_20261009.csv").read_bytes())
        df2 = history(tmp_path, bonds=inventory)
        assert len(df) == len(df2) == 1

    def test_unreadable_snapshot_is_skipped(self, tmp_path, inventory):
        snapshots(tmp_path)
        (tmp_path / "gilts_20250101.csv").write_text("")
        df = history(tmp_path, bonds=inventory)
        assert len(df) == 1

    def test_no_snapshots_raises(self, tmp_path, inventory):
        with pytest.raises(RuntimeError, match="no gilts_\\*.csv"):
            history(tmp_path, bonds=inventory)

    def test_nothing_calibratable_raises(self, tmp_path, inventory):
        """A snapshot whose every gilt is missing from the inventory."""
        df = pd.read_csv(FIXTURES / "gilts_20261009.csv")
        df["isin"] = "GB00FAKEFAKE"
        df.to_csv(tmp_path / "gilts_20261009.csv", index=False)
        with pytest.raises(RuntimeError, match="could be calibrated"):
            history(tmp_path, bonds=inventory)


class TestDefaultInventory:
    def test_bonds_none_reads_the_newest_workbook(self, tmp_path, inventory, monkeypatch):
        """With no bonds given, history() reads the newest cached D1A workbook."""
        snapshots(tmp_path)
        (tmp_path / "gilts_in_issue_20261007.xls").write_bytes(b"old")
        newest = tmp_path / "gilts_in_issue_20261008.xls"
        newest.write_bytes(b"new")
        read = []
        monkeypatch.setattr(
            dmo, "read", lambda p: (read.append(p), inventory)[1]
        )
        df = history(tmp_path)
        assert read == [newest]
        assert len(df) == 1

    def test_no_workbook_raises(self, tmp_path, inventory):
        snapshots(tmp_path)  # prices but no inventory: run python dmo.py first
        with pytest.raises(RuntimeError, match="no cached DMO inventory"):
            history(tmp_path)


class TestMain:
    def test_writes_the_csv(self, tmp_path, inventory, monkeypatch, capsys):
        snapshots(tmp_path)
        (tmp_path / "gilts_in_issue_20261008.xls").write_bytes(b"x")
        monkeypatch.setattr(zspread_history, "OUT_DIR", tmp_path)
        monkeypatch.setattr(dmo, "read", lambda p: inventory)
        zspread_history.main()
        out = pd.read_csv(tmp_path / "zspread_history.csv")
        assert list(out.columns) == ["feed", "settle"] + [
            f"{t:g}y" for t in DEFAULT_TENORS
        ]
        assert len(out) == 1
        assert f"wrote {tmp_path / 'zspread_history.csv'}" in capsys.readouterr().out
