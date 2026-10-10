import datetime as dt

import pandas as pd
import pytest
from conftest import FakeResponse, make_bond

import bond_management as bm


def make_page(rows_html, as_of="02 Oct 2026 17:00", headers=None):
    headers = headers or bm.PRICE_COLUMNS
    head = "".join(f"<th>{h}</th>" for h in headers)
    return (
        f"<html><body><p>Market data as of {as_of}</p>"
        f"<table><tr>{head}</tr>{rows_html}</table></body></html>"
    )


ROW_HTML = (
    "<tr><td><a href='/bond/t39'>Treasury 4.25% 2036</a> T39 · GB00B3KJDS62</td>"
    "<td>4.25%</td><td>22-Oct-2036</td><td>£99.85 ▲0.03%</td><td>£1.23</td>"
    "<td>£101.08</td><td>8.5</td><td>4.295% ▼6.7bps</td><td>4.301%</td></tr>"
)


def patch_scrape(monkeypatch, html, status=200):
    def fake_get(url, headers=None, timeout=None):
        return FakeResponse(text=html, status_code=status)

    monkeypatch.setattr(
        bm, "requests", type("m", (), {"get": staticmethod(fake_get)})()
    )


class TestNum:
    def test_price_with_move(self):
        assert bm.num("£99.85 ▲0.03%") == 99.85

    def test_yield_with_bps_move(self):
        assert bm.num("3.659% ▼6.7bps") == 3.659

    def test_thousands_separator(self):
        assert bm.num("1,234.56") == 1234.56

    def test_negative(self):
        assert bm.num("£-12.30") == -12.30

    def test_no_number(self):
        assert bm.num("N/A") is None


def write_cache(tmp_path, as_of, day="20261009"):
    df = pd.DataFrame(
        [{c: 1.0 for c in bm.COLUMNS} | {"isin": "GB00B3KJDS62", "as_of": as_of}]
    )
    path = tmp_path / f"gilts_{day}.csv"
    df.to_csv(path, index=False)
    return path


class TestCachedPrices:
    CUTOFF = dt.datetime(2026, 10, 9, 17, 0)

    def test_close_reached(self, tmp_path):
        path = write_cache(tmp_path, "2026-10-09 17:00:00+00:00")
        assert bm.cached_prices(path, self.CUTOFF) is not None

    def test_still_intraday(self, tmp_path):
        path = write_cache(tmp_path, "2026-10-09 16:00:00+00:00")
        assert bm.cached_prices(path, self.CUTOFF) is None

    def test_no_timestamp(self, tmp_path):
        path = write_cache(tmp_path, "")
        assert bm.cached_prices(path, self.CUTOFF) is None

    def test_missing_file(self, tmp_path):
        assert bm.cached_prices(tmp_path / "gilts_20261009.csv", self.CUTOFF) is None


class TestScrapeGilts:
    def test_happy_path(self, monkeypatch):
        patch_scrape(monkeypatch, make_page(ROW_HTML))
        df = bm.scrape_gilts()
        assert len(df) == 1
        row = df.iloc[0]
        assert row["name"] == "Treasury 4.25% 2036"
        assert row["tidm"] == "T39"
        assert row["isin"] == "GB00B3KJDS62"
        assert row["coupon_pct"] == 4.25
        assert row["clean_price"] == 99.85
        assert row["accrued"] == 1.23
        assert row["dirty_price"] == 101.08
        assert row["mod_duration"] == 8.5
        assert row["gross_yield_pct"] == 4.295
        assert row["dmo_yield_pct"] == 4.301
        assert row["as_of"] == dt.datetime(2026, 10, 2, 17, 0, tzinfo=dt.UTC)

    def test_no_price_table(self, monkeypatch):
        patch_scrape(
            monkeypatch,
            "<html><body><p>Market data as of 02 Oct 2026 17:00</p></body></html>",
        )
        with pytest.raises(RuntimeError, match="no gilt price table"):
            bm.scrape_gilts()

    def test_renamed_column(self, monkeypatch):
        headers = [h for h in bm.PRICE_COLUMNS if h != "DMO yield"]
        row = "<tr>" + "<td>x</td>" * len(headers) + "</tr>"
        patch_scrape(monkeypatch, make_page(row, headers=headers))
        with pytest.raises(RuntimeError, match="missing columns: DMO yield"):
            bm.scrape_gilts()

    def test_empty_rows_keep_schema(self, monkeypatch):
        patch_scrape(monkeypatch, make_page(""))
        df = bm.scrape_gilts()
        assert len(df) == 0
        assert list(df.columns) == bm.COLUMNS + ["as_of"]


class TestLoadGiltPrices:
    def setup_method(self):
        self.day = dt.date(2026, 10, 9)

    def patch(self, monkeypatch, scrape_calls):
        monkeypatch.setattr(bm, "feed_date", lambda day=None: self.day)

        def fake_scrape():
            scrape_calls.append(1)
            df = pd.DataFrame([{c: 1.0 for c in bm.COLUMNS}])
            df["as_of"] = dt.datetime(2026, 10, 9, 17, 0, tzinfo=dt.UTC)
            return df

        monkeypatch.setattr(bm, "scrape_gilts", fake_scrape)

    def test_cache_hit_skips_scrape(self, tmp_path, monkeypatch):
        write_cache(tmp_path, "2026-10-09 17:00:00+00:00", day="20261009")
        calls = []
        self.patch(monkeypatch, calls)
        df = bm.load_gilt_prices(tmp_path)
        assert calls == []
        assert len(df) == 1

    def test_force_refetches(self, tmp_path, monkeypatch):
        write_cache(tmp_path, "2026-10-09 17:00:00+00:00", day="20261009")
        calls = []
        self.patch(monkeypatch, calls)
        bm.load_gilt_prices(tmp_path, force=True)
        assert calls == [1]


PRICES = pd.DataFrame([{"isin": "GB00B3KJDS62"}])
BONDS = [make_bond("GB00B3KJDS62")]


class TestLoadData:
    def patch(self, monkeypatch, tmp_path, fetched, stale=False, prices=None):
        path = tmp_path / "gilts_in_issue_20261008.xls"
        path.write_bytes(b"x")
        monkeypatch.setattr(
            bm,
            "load_gilt_prices",
            lambda out_dir, force=False: PRICES if prices is None else prices,
        )
        monkeypatch.setattr(bm.dmo, "find_newest", lambda out_dir: path)
        monkeypatch.setattr(bm.dmo, "is_stale", lambda p, max_age_days=31: stale)

        def fake_fetch(cob_date=None, out_dir=None, force=False):
            fetched.append(force)
            return path

        monkeypatch.setattr(bm.dmo, "fetch", fake_fetch)
        monkeypatch.setattr(bm.dmo, "read", lambda p: BONDS)
        return path

    def test_fresh_inventory_never_downloads(self, tmp_path, monkeypatch):
        fetched = []
        self.patch(monkeypatch, tmp_path, fetched)
        prices, bonds = bm.load_data(tmp_path)
        assert fetched == []
        assert prices is PRICES
        assert bonds is BONDS

    def test_stale_inventory_is_refetched(self, tmp_path, monkeypatch):
        fetched = []
        self.patch(monkeypatch, tmp_path, fetched, stale=True)
        bm.load_data(tmp_path)
        assert fetched == [False]

    def test_force_skips_all_caches(self, tmp_path, monkeypatch):
        fetched = []
        self.patch(monkeypatch, tmp_path, fetched)
        bm.load_data(tmp_path, force=True)
        assert fetched == [True]

    def test_empty_prices_skip_missing_isin_check(self, tmp_path, monkeypatch):
        fetched = []
        empty = pd.DataFrame(columns=bm.COLUMNS)
        self.patch(monkeypatch, tmp_path, fetched, prices=empty)
        bm.load_data(tmp_path)
        assert fetched == []

    def test_missing_priced_isin_triggers_fetch(self, tmp_path, monkeypatch):
        fetched = []
        self.patch(monkeypatch, tmp_path, fetched)
        fresh = tmp_path / "gilts_in_issue_20261009.xls"

        def fake_fetch(cob_date=None, out_dir=None, force=False):
            fetched.append(force)
            fresh.write_bytes(b"x")
            return fresh

        monkeypatch.setattr(bm.dmo, "fetch", fake_fetch)
        reads = []

        def fake_read(p):
            reads.append(p.name)
            if p.name == "gilts_in_issue_20261008.xls":
                return [make_bond("GBOTHER0000")]
            return [make_bond("GB00B3KJDS62")]

        monkeypatch.setattr(bm.dmo, "read", fake_read)
        _prices, bonds = bm.load_data(tmp_path)
        assert fetched == [True]
        assert reads == ["gilts_in_issue_20261008.xls", "gilts_in_issue_20261009.xls"]
        assert [b.isin for b in bonds] == ["GB00B3KJDS62"]
