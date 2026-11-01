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

    def test_mixed_fraction(self):
        assert bm.num("1 1/8%") == 1.125

    def test_vulgar_fraction_with_percent(self):
        assert bm.num("4¼%") == 4.25

    def test_negative_vulgar_fraction(self):
        assert bm.num("-1½%") == -1.5

    def test_bare_vulgar_fraction(self):
        assert bm.num("¼") == 0.25

    def test_dividend_dates_are_not_fractions(self):
        assert bm.num("12/06, 12/12") == 12.0


class TestMaturesDate:
    def test_iso(self):
        assert bm.matures_date("2026-10-22") == dt.date(2026, 10, 22)

    def test_site_format(self):
        assert bm.matures_date("22-Oct-2036") == dt.date(2036, 10, 22)

    def test_slashed(self):
        assert bm.matures_date("22/10/2036") == dt.date(2036, 10, 22)

    def test_unparsable(self):
        assert bm.matures_date("soon") is None


def write_cache(tmp_path, as_of, day="20261009", rows=1, isins=None, matures="2099-12-31"):
    """A cached gilts_*.csv: distinct fake ISINs, all maturing on `matures`."""
    if isins is None:
        isins = [f"GB{i:010d}" for i in range(rows)]
    df = pd.DataFrame(
        [
            {c: 1.0 for c in bm.COLUMNS}
            | {"isin": isin, "matures": matures, "as_of": as_of}
            for isin in isins
        ]
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

    def test_zero_byte_file(self, tmp_path):
        path = tmp_path / "gilts_20261009.csv"
        path.write_bytes(b"")
        assert bm.cached_prices(path, self.CUTOFF) is None

    def test_missing_as_of_column(self, tmp_path):
        path = tmp_path / "gilts_20261009.csv"
        pd.DataFrame({"isin": ["GB00B3KJDS62"]}).to_csv(path, index=False)
        assert bm.cached_prices(path, self.CUTOFF) is None


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

    def test_http_error_is_raised(self, monkeypatch):
        patch_scrape(monkeypatch, make_page(ROW_HTML), status=500)
        with pytest.raises(RuntimeError, match="HTTP 500"):
            bm.scrape_gilts()


class TestLoadGiltPrices:
    def setup_method(self):
        self.day = dt.date(2026, 10, 9)

    def patch(self, monkeypatch, scrape_calls, rows=1, drop=()):
        monkeypatch.setattr(bm, "feed_date", lambda day=None: self.day)

        def fake_scrape():
            scrape_calls.append(1)
            df = pd.DataFrame(
                [
                    {c: 1.0 for c in bm.COLUMNS}
                    | {"isin": f"GB{i:010d}", "matures": "2099-12-31"}
                    for i in range(rows)
                ],
                columns=bm.COLUMNS,
            )
            for col in drop:
                df[col] = None
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

    def test_partial_scrape_keeps_previous_file(self, tmp_path, monkeypatch, capsys):
        write_cache(tmp_path, "2026-10-08 17:00:00+00:00", day="20261008", rows=10)
        calls = []
        self.patch(monkeypatch, calls, rows=1)
        df = bm.load_gilt_prices(tmp_path)
        assert calls == [1]
        assert len(df) == 10
        assert not (tmp_path / "gilts_20261009.csv").exists()
        out = capsys.readouterr().out
        assert "gilts_20261009.csv <- refusing to cache: missing GB0000000001" in out
        assert "(still live on 09/10/2026)" in out
        assert "keeping gilts_20261008.csv" in out

    def test_matured_gilt_leaving_the_feed_is_cached(self, tmp_path, monkeypatch):
        write_cache(
            tmp_path,
            "2026-10-08 17:00:00+00:00",
            day="20261008",
            isins=["GB0000000001"],
            matures="2026-10-08",
        )
        calls = []
        self.patch(monkeypatch, calls, rows=1)
        df = bm.load_gilt_prices(tmp_path)
        assert calls == [1]
        assert len(df) == 1
        assert len(pd.read_csv(tmp_path / "gilts_20261009.csv")) == 1

    def test_unparsable_matures_is_treated_as_live(self, tmp_path, monkeypatch):
        write_cache(
            tmp_path,
            "2026-10-08 17:00:00+00:00",
            day="20261008",
            isins=["GB0000000001"],
            matures="soon",
        )
        calls = []
        self.patch(monkeypatch, calls, rows=1)
        df = bm.load_gilt_prices(tmp_path)
        assert calls == [1]
        assert len(df) == 1
        assert not (tmp_path / "gilts_20261009.csv").exists()

    def test_partial_scrape_forced_still_refused(self, tmp_path, monkeypatch):
        write_cache(tmp_path, "2026-10-09 17:00:00+00:00", day="20261009", rows=10)
        calls = []
        self.patch(monkeypatch, calls, rows=1)
        df = bm.load_gilt_prices(tmp_path, force=True)
        assert calls == [1]
        assert len(df) == 10
        assert len(pd.read_csv(tmp_path / "gilts_20261009.csv")) == 10

    def test_empty_scrape_keeps_previous_file(self, tmp_path, monkeypatch):
        write_cache(tmp_path, "2026-10-08 17:00:00+00:00", day="20261008", rows=10)
        calls = []
        self.patch(monkeypatch, calls, rows=0)
        df = bm.load_gilt_prices(tmp_path)
        assert len(df) == 10
        assert not (tmp_path / "gilts_20261009.csv").exists()

    def test_empty_scrape_without_previous_writes_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        calls = []
        self.patch(monkeypatch, calls, rows=0)
        df = bm.load_gilt_prices(tmp_path)
        assert len(df) == 0
        assert not (tmp_path / "gilts_20261009.csv").exists()
        assert "refusing to cache: no rows" in capsys.readouterr().out

    def test_null_isin_scrape_refused(self, tmp_path, monkeypatch, capsys):
        write_cache(tmp_path, "2026-10-08 17:00:00+00:00", day="20261008", rows=10)
        calls = []
        self.patch(monkeypatch, calls, drop=("isin",))
        df = bm.load_gilt_prices(tmp_path)
        assert len(df) == 10
        assert not (tmp_path / "gilts_20261009.csv").exists()
        assert "null values in isin" in capsys.readouterr().out

    def test_stale_message_shows_the_full_feed_timestamp(
        self, tmp_path, monkeypatch, capsys
    ):
        self.day = dt.date(2026, 10, 10)
        calls = []
        self.patch(monkeypatch, calls)
        bm.load_gilt_prices(tmp_path)
        assert calls == [1]
        out = capsys.readouterr().out
        assert "feed only shows 09/10/2026 17:00, close is 17:00" in out


PRICES = pd.DataFrame([{"isin": "GB00B3KJDS62"}])
BONDS = [make_bond("GB00B3KJDS62")]


class TestLoadData:
    def patch(self, monkeypatch, tmp_path, fetched, stale=False, prices=None, bonds=None):
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
        monkeypatch.setattr(bm.dmo, "read", lambda p: BONDS if bonds is None else bonds)
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

    def test_warns_when_feed_is_missing_gilts(self, tmp_path, monkeypatch, capsys):
        fetched = []
        bonds = [
            make_bond("GB00B3KJDS62"),
            make_bond("GBGONE00001"),
            make_bond("GBLINKER0001", market="index-linked"),
        ]
        self.patch(monkeypatch, tmp_path, fetched, bonds=bonds)
        bm.load_data(tmp_path)
        assert fetched == []
        assert "feed may be missing 1 gilts: GBGONE00001" in capsys.readouterr().out

    @staticmethod
    def boom(cob_date=None, out_dir=None, force=False):
        raise RuntimeError("DMO down")

    def test_fetch_that_resolves_nothing_still_returns(
        self, tmp_path, monkeypatch, capsys
    ):
        fetched = []
        self.patch(monkeypatch, tmp_path, fetched)
        monkeypatch.setattr(bm.dmo, "read", lambda p: [make_bond("GBOTHER0000")])
        _prices, bonds = bm.load_data(tmp_path)
        assert fetched == [True]
        assert [b.isin for b in bonds] == ["GBOTHER0000"]
        assert (
            "still missing from the DMO inventory: GB00B3KJDS62"
            in capsys.readouterr().out
        )

    def test_stale_refresh_failure_falls_back_to_cached_workbook(
        self, tmp_path, monkeypatch, capsys
    ):
        fetched = []
        self.patch(monkeypatch, tmp_path, fetched, stale=True)
        monkeypatch.setattr(bm.dmo, "fetch", self.boom)
        prices, bonds = bm.load_data(tmp_path)
        assert prices is PRICES
        assert bonds is BONDS
        assert (
            "could not refresh the DMO inventory: DMO down; "
            "using gilts_in_issue_20261008.xls" in capsys.readouterr().out
        )

    def test_fetch_failure_without_cache_propagates(self, tmp_path, monkeypatch):
        fetched = []
        self.patch(monkeypatch, tmp_path, fetched)
        monkeypatch.setattr(bm.dmo, "find_newest", lambda out_dir: None)
        monkeypatch.setattr(bm.dmo, "fetch", self.boom)
        with pytest.raises(RuntimeError, match="DMO down"):
            bm.load_data(tmp_path)

    def test_missing_isin_refresh_failure_keeps_current_bonds(
        self, tmp_path, monkeypatch, capsys
    ):
        fetched = []
        self.patch(monkeypatch, tmp_path, fetched)
        old = [make_bond("GBOTHER0000")]
        monkeypatch.setattr(bm.dmo, "read", lambda p: old)

        def boom(cob_date=None, out_dir=None, force=False):
            fetched.append(force)
            raise RuntimeError("DMO down")

        monkeypatch.setattr(bm.dmo, "fetch", boom)
        _prices, bonds = bm.load_data(tmp_path)
        assert bonds is old
        assert fetched == [True]
        assert (
            "could not refresh the DMO inventory: DMO down; "
            "using gilts_in_issue_20261008.xls" in capsys.readouterr().out
        )
