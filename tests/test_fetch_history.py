import datetime as dt

import pandas as pd
from conftest import make_bond

from bond_management.scripts import fetch_history

COLS = ["isin", "ticker", "name", "date", "clean_price", "yield_pct"]


def hist_rows(rows):
    """Build a history frame from (date, clean_price, yield_pct) tuples."""
    return pd.DataFrame(rows, columns=["date", "clean_price", "yield_pct"]).assign(
        isin="GB00B1", ticker="AAA", name="Test Gilt"
    )[COLS]


def write_csv(path, rows, columns=COLS):
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


class TestMergeWithExisting:
    def test_no_existing_file_returns_download_untouched(self, tmp_path):
        new = hist_rows([("2026-01-02", 100.1, 4.5)])
        merged = fetch_history.merge_with_existing(new, tmp_path / "AAA_history.csv")
        assert merged.equals(new)

    def test_keeps_old_rows_absent_from_download_and_adds_new(self, tmp_path):
        path = tmp_path / "AAA_history.csv"
        write_csv(
            path,
            [
                ("GB00B1", "AAA", "Test Gilt", "2025-12-31", 99.9, 4.6),
                ("GB00B1", "AAA", "Test Gilt", "2026-01-01", 100.0, 4.5),
            ],
        )
        new = hist_rows([("2026-01-02", 100.1, 4.5)])

        merged = fetch_history.merge_with_existing(new, path)

        assert list(merged["date"]) == ["2025-12-31", "2026-01-01", "2026-01-02"]
        assert list(merged["clean_price"]) == [99.9, 100.0, 100.1]

    def test_identical_overlap_produces_no_warning(self, tmp_path, capsys):
        path = tmp_path / "AAA_history.csv"
        write_csv(path, [("GB00B1", "AAA", "Test Gilt", "2026-01-02", 100.1, 4.5)])
        new = hist_rows([("2026-01-02", 100.1, 4.5), ("2026-01-05", 100.2, 4.4)])

        merged = fetch_history.merge_with_existing(new, path)

        assert "Warning" not in capsys.readouterr().out
        assert list(merged["date"]) == ["2026-01-02", "2026-01-05"]

    def test_changed_overlap_warns_and_new_download_wins(self, tmp_path, capsys):
        path = tmp_path / "AAA_history.csv"
        write_csv(path, [("GB00B1", "AAA", "Test Gilt", "2026-01-02", 100.1, 4.5)])
        new = hist_rows([("2026-01-02", 99.9, 4.7)])

        merged = fetch_history.merge_with_existing(new, path)

        out = capsys.readouterr().out
        assert "Warning" in out
        assert "AAA_history.csv" in out
        assert "2026-01-02 clean_price: 100.1 -> 99.9" in out
        assert list(merged["clean_price"]) == [99.9]

    def test_unreadable_file_falls_back_to_download(self, tmp_path, capsys):
        path = tmp_path / "AAA_history.csv"
        path.write_text("not,a,valid\nhistory file", encoding="utf-8")
        # 'date' column missing -> keep only the download
        new = hist_rows([("2026-01-02", 100.1, 4.5)])

        merged = fetch_history.merge_with_existing(new, path)

        assert "Warning" in capsys.readouterr().out
        assert merged.equals(new)


class TestFetchAllHistoricalPrices:
    def _setup(self, monkeypatch):
        prices = pd.DataFrame(
            {
                "isin": ["GB00A", "GB00B"],
                "tidm": ["AAA", "BBB"],
                "name": ["Gilt A", "Gilt B"],
            }
        )
        bonds = [
            make_bond(isin="GB00A", name="Gilt A", redemption_date=dt.date(2030, 1, 1)),
            make_bond(isin="GB00B", name="Gilt B", redemption_date=dt.date(2031, 1, 1)),
        ]
        monkeypatch.setattr(fetch_history, "load_data", lambda: (prices, bonds))
        monkeypatch.setattr(fetch_history.time, "sleep", lambda _s: None)

        def fake_fetch(ticker, start_date, end_date=None):
            return pd.DataFrame(
                {
                    "date": ["2026-01-02", "2026-01-05"],
                    "clean_price": [100.1, 100.2],
                    "yield_pct": [4.5, 4.4],
                }
            )

        monkeypatch.setattr(fetch_history, "fetch_gilt_history", fake_fetch)

    def test_single_ticker_run_keeps_other_gilts_in_combined(self, tmp_path, monkeypatch):
        self._setup(monkeypatch)
        write_csv(
            tmp_path / "AAA_history.csv",
            [("GB00A", "AAA", "Gilt A", "2025-12-31", 99.9, 4.6)],
        )
        write_csv(
            tmp_path / "BBB_history.csv",
            [("GB00B", "BBB", "Gilt B", "2025-12-30", 98.0, 5.0)],
        )
        write_csv(
            tmp_path / "all_gilts_history.csv",
            [
                ("GB00A", "AAA", "Gilt A", "2025-12-31", 99.9, 4.6),
                ("GB00B", "BBB", "Gilt B", "2025-12-30", 98.0, 5.0),
            ],
        )

        combined = fetch_history.fetch_all_historical_prices(ticker="AAA", out_dir=tmp_path)

        # BBB untouched in the combined file, AAA merged (old + new, no duplicates)
        assert sorted(combined["ticker"]) == ["AAA", "AAA", "AAA", "BBB"]
        aaa = combined[combined["ticker"] == "AAA"]
        assert list(aaa["date"]) == ["2025-12-31", "2026-01-02", "2026-01-05"]
        bbb = combined[combined["ticker"] == "BBB"]
        assert list(bbb["date"]) == ["2025-12-30"]
        assert list(bbb["clean_price"]) == [98.0]

        on_disk = pd.read_csv(tmp_path / "all_gilts_history.csv")
        assert len(on_disk) == len(combined)

    def test_per_gilt_file_is_merged_not_overwritten(self, tmp_path, monkeypatch):
        self._setup(monkeypatch)
        write_csv(
            tmp_path / "AAA_history.csv",
            [("GB00A", "AAA", "Gilt A", "2025-12-31", 99.9, 4.6)],
        )

        fetch_history.fetch_all_historical_prices(ticker="AAA", out_dir=tmp_path)

        got = pd.read_csv(tmp_path / "AAA_history.csv")
        assert list(got["date"]) == ["2025-12-31", "2026-01-02", "2026-01-05"]

    def test_combined_rows_are_sorted_by_ticker_then_date(self, tmp_path, monkeypatch):
        self._setup(monkeypatch)

        fetch_history.fetch_all_historical_prices(ticker="all", out_dir=tmp_path)

        got = pd.read_csv(tmp_path / "all_gilts_history.csv")
        assert list(got["ticker"]) == ["AAA", "AAA", "BBB", "BBB"]
        assert list(got["date"]) == ["2026-01-02", "2026-01-05", "2026-01-02", "2026-01-05"]
