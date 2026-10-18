import datetime as dt
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakeResponse

from bond_management import dmo
from bond_management.dates import excel_date


class FakeSheet:
    def __init__(self, rows):
        self.rows = [list(r) for r in rows]
        self.nrows = len(self.rows)
        self.ncols = max(len(r) for r in self.rows)

    def row_values(self, r):
        return self.rows[r] + [""] * (self.ncols - len(self.rows[r]))

    def cell_value(self, r, c):
        return self.row_values(r)[c]


def fake_book(rows, datemode=0):
    return SimpleNamespace(sheet_by_index=lambda i: FakeSheet(rows), datemode=datemode)


D1A_ROWS = [
    ["", "", "GILTS IN ISSUE ON 08 OCTOBER 2026"],
    ["Conventional Gilts", "ISIN Code", ""],
    ["Ultra-Short", "", ""],
    [
        "Treasury 0.50% 2025",
        "GB00B3KJDS62",
        45955.0,
        44562.0,
        "12/06, 12/12",
        45940.0,
        12345.0,
        "",
        "",
    ],
    [
        "Treasury 4.25% 2036",
        "GB2026X3KJ45",
        46320.0,
        45000.0,
        "07/06, 07/12",
        46305.0,
        9876.0,
        "",
        "",
    ],
    ["Index-linked Gilts (3-month lag)", "ISIN Code", ""],
    [
        "Treasury 0.125% 2050",
        "GB00BFMCN691",
        60218.0,
        43831.0,
        "07/06, 07/12",
        60203.0,
        890.0,
        92.6,
        1050.0,
    ],
    ["Index-linked Gilts (8-month lag)", "ISIN Code", ""],
    [
        "Treasury 0.125% 2066",
        "GB00BZ1GK55X",
        65000.0,
        46000.0,
        "07/06, 07/12",
        64985.0,
        500.0,
        110.0,
        700.0,
    ],
]


class TestBlocked:
    def test_status_codes(self):
        assert dmo.blocked(FakeResponse(status_code=403))
        assert dmo.blocked(FakeResponse(status_code=429))

    def test_captcha_body(self):
        assert dmo.blocked(FakeResponse(content=b"prefix ShieldSquare Captcha suffix"))

    def test_clean(self):
        assert not dmo.blocked(FakeResponse(status_code=200, content=b"xls bytes"))


class TestNames:
    def test_cob_from_name(self):
        assert dmo.cob_from_name("gilts_in_issue_20261008.xls") == dt.date(2026, 10, 8)

    def test_cob_from_name_invalid(self):
        assert dmo.cob_from_name("gilts_in_issue_notes.xls") is None

    def test_find_newest(self, tmp_path):
        (tmp_path / "gilts_in_issue_20261007.xls").write_bytes(b"a")
        (tmp_path / "gilts_in_issue_20261008.xls").write_bytes(b"b")
        assert dmo.find_newest(tmp_path).name == "gilts_in_issue_20261008.xls"

    def test_find_newest_empty(self, tmp_path):
        assert dmo.find_newest(tmp_path) is None


class TestIsStale:
    def file_for(self, tmp_path, cob):
        path = tmp_path / f"gilts_in_issue_{cob:%Y%m%d}.xls"
        path.write_bytes(b"x")
        return path

    def test_fresh(self, tmp_path):
        today = dt.datetime.now(tz=dt.UTC).date()
        assert not dmo.is_stale(self.file_for(tmp_path, today))

    def test_exactly_max_age_is_fresh(self, tmp_path):
        today = dt.datetime.now(tz=dt.UTC).date()
        assert not dmo.is_stale(
            self.file_for(tmp_path, today - dt.timedelta(days=dmo.MAX_AGE_DAYS))
        )

    def test_older_than_max_age(self, tmp_path):
        today = dt.datetime.now(tz=dt.UTC).date()
        assert dmo.is_stale(
            self.file_for(tmp_path, today - dt.timedelta(days=dmo.MAX_AGE_DAYS + 1))
        )

    def test_unparseable_name_is_stale(self, tmp_path):
        path = tmp_path / "gilts_in_issue_notes.xls"
        path.write_bytes(b"x")
        assert dmo.is_stale(path)


class TestFindExisting:
    def file_for(self, tmp_path, cob, mtime=None):
        path = tmp_path / f"gilts_in_issue_{cob:%Y%m%d}.xls"
        path.write_bytes(b"x")
        if mtime is not None:
            os.utime(path, (mtime.timestamp(), mtime.timestamp()))
        return path

    def test_by_cob_date(self, tmp_path):
        path = self.file_for(tmp_path, dt.date(2026, 10, 8))
        assert dmo.find_existing(tmp_path, dt.date(2026, 10, 8)) == path

    def test_by_cob_date_missing(self, tmp_path):
        assert dmo.find_existing(tmp_path, dt.date(2026, 10, 8)) is None

    def test_reuse_when_cob_is_today(self, tmp_path):
        today = dt.datetime.now(tz=dt.UTC).date()
        path = self.file_for(tmp_path, today)
        assert dmo.find_existing(tmp_path) == path

    def test_reuse_when_fetched_today(self, tmp_path):
        old = dt.date(2000, 1, 1)
        now = dt.datetime.now(tz=dt.UTC)
        path = self.file_for(tmp_path, old, mtime=now)
        assert dmo.find_existing(tmp_path) == path

    def test_reject_when_old_and_fetched_long_ago(self, tmp_path):
        old = dt.date(2000, 1, 1)
        long_ago = dt.datetime(2000, 1, 2, tzinfo=dt.UTC)
        self.file_for(tmp_path, old, mtime=long_ago)
        assert dmo.find_existing(tmp_path) is None


class TestDateMetadata:
    def test_min_max_latest(self):
        report_data = {
            "ReportParameters": [
                {
                    "listItems": [
                        {"key": "Min", "value": "06/04/2004"},
                        {"key": "Max", "value": "09/10/2026"},
                    ],
                    "defaultValue1": "08/10/2026",
                }
            ],
        }
        meta = dmo.date_metadata(report_data)
        assert meta == {
            "min": dt.date(2004, 4, 6),
            "max": dt.date(2026, 10, 9),
            "latest": dt.date(2026, 10, 8),
        }


REPORT_PAGE = (
    "var pdfreportdata = {\n"
    '"ReportParameters": [{"listItems": '
    '[{"key": "Min", "value": "06/04/2004"}, {"key": "Max", "value": "09/10/2026"}], '
    '"defaultValue1": "08/10/2026"}]\n'
    "};"
)


def patch_sessions(monkeypatch, responses):
    """Serve responses in order from fake sessions; slept seconds are recorded."""
    calls = {"n": 0, "sleeps": []}

    class FakeSession:
        def get(self, url, headers=None):
            r = responses[min(calls["n"], len(responses) - 1)]
            calls["n"] += 1
            return r

    monkeypatch.setattr(dmo, "new_session", FakeSession)
    monkeypatch.setattr(dmo, "time", SimpleNamespace(sleep=calls["sleeps"].append))
    return calls


class TestOpenReport:
    def test_success(self, monkeypatch):
        calls = patch_sessions(monkeypatch, [FakeResponse(text=REPORT_PAGE)])
        _session, meta = dmo.open_report()
        assert meta["latest"] == dt.date(2026, 10, 8)
        assert calls["n"] == 1
        assert calls["sleeps"] == []

    def test_retries_when_metadata_missing(self, monkeypatch):
        calls = patch_sessions(monkeypatch, [FakeResponse(text="<html>nope</html>")])
        with pytest.raises(RuntimeError, match="kept failing"):
            dmo.open_report()
        assert calls["n"] == dmo.RETRIES

    def test_no_sleep_before_final_raise(self, monkeypatch):
        calls = patch_sessions(monkeypatch, [FakeResponse(text="<html>nope</html>")])
        with pytest.raises(RuntimeError, match="kept failing"):
            dmo.open_report()
        assert calls["sleeps"] == [15, 30]

    def test_recovers_on_retry(self, monkeypatch):
        calls = patch_sessions(
            monkeypatch,
            [FakeResponse(text="<html>nope</html>"), FakeResponse(text=REPORT_PAGE)],
        )
        _, meta = dmo.open_report()
        assert meta["latest"] == dt.date(2026, 10, 8)
        assert calls["n"] == 2
        assert calls["sleeps"] == [15]


class TestDownloadXls:
    COB = dt.date(2026, 10, 8)
    GOOD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"gilt-data"

    def test_blocked_retries_without_final_sleep(self, monkeypatch):
        calls = patch_sessions(monkeypatch, [FakeResponse(text="nope", status_code=403)])
        with pytest.raises(RuntimeError, match="kept failing"):
            dmo.download_xls(dmo.new_session(), self.COB)
        assert calls["n"] == dmo.RETRIES
        assert calls["sleeps"] == [15, 30]

    def test_recovers_on_retry(self, monkeypatch):
        calls = patch_sessions(
            monkeypatch,
            [FakeResponse(text="nope", status_code=403), FakeResponse(content=self.GOOD)],
        )
        _session, body = dmo.download_xls(dmo.new_session(), self.COB)
        assert body == self.GOOD
        assert calls["sleeps"] == [15]


class TestRead:
    def test_parses_sections(self, monkeypatch, tmp_path):
        path = tmp_path / "gilts_in_issue_20261008.xls"
        path.write_bytes(b"x")
        monkeypatch.setattr(dmo.xlrd, "open_workbook", lambda p: fake_book(D1A_ROWS))
        bonds = dmo.read(path)
        assert len(bonds) == 4
        assert [b.market for b in bonds] == [
            "conventional",
            "conventional",
            "index-linked",
            "index-linked",
        ]
        assert [b.section for b in bonds] == [
            "Ultra-Short",
            "Ultra-Short",
            None,
            None,
        ]
        assert [b.indexation_lag for b in bonds] == [None, None, 3, 8]
        assert bonds[0].cob_date == dt.date(2026, 10, 8)
        linked = bonds[2]
        assert linked.base_rpi == 92.6
        assert linked.amount_incl_uplift_m == 1050.0
        conventional = bonds[0]
        assert conventional.indexation_lag is None
        assert conventional.base_rpi is None
        assert conventional.amount_incl_uplift_m is None
        assert conventional.redemption_date == excel_date(45955.0, 0)

    def test_missing_cob_header_raises(self, monkeypatch, tmp_path):
        path = tmp_path / "gilts_in_issue_20261008.xls"
        path.write_bytes(b"x")
        monkeypatch.setattr(
            dmo.xlrd, "open_workbook", lambda p: fake_book([["no", "header", "here"]])
        )
        with pytest.raises(RuntimeError, match="could not find the COB date header"):
            dmo.read(path)

    def test_blank_and_footer_rows_keep_the_section(self, monkeypatch, tmp_path):
        rows = [
            ["", "", "GILTS IN ISSUE ON 08 OCTOBER 2026"],
            ["Conventional Gilts", "ISIN Code", ""],
            ["Ultra-Short", "", ""],
            ["", "", ""],
            [
                "Treasury 0.50% 2025",
                "GB00B3KJDS62",
                45955.0,
                44562.0,
                "12/06, 12/12",
                45940.0,
                12345.0,
                "",
                "",
            ],
            ["", "", ""],
            ["Note: amounts in millions", "", ""],
            ["Page -1 of 1", "", ""],
            ["Index-linked Gilts (3-month lag)", "ISIN Code", ""],
            [
                "Treasury 0.125% 2050",
                "GB00BFMCN691",
                60218.0,
                43831.0,
                "07/06, 07/12",
                60203.0,
                890.0,
                92.6,
                1050.0,
            ],
        ]
        path = tmp_path / "gilts_in_issue_20261008.xls"
        path.write_bytes(b"x")
        monkeypatch.setattr(dmo.xlrd, "open_workbook", lambda p: fake_book(rows))
        bonds = dmo.read(path)
        assert [b.section for b in bonds] == ["Ultra-Short", None]
        assert [b.market for b in bonds] == ["conventional", "index-linked"]


FETCH_META = {
    "min": dt.date(2000, 1, 1),
    "max": dt.date(2100, 1, 1),
    "latest": dt.date(2026, 10, 8),
}


class TestFetch:
    def test_reuses_cache(self, tmp_path, monkeypatch):
        cob = dt.date(2026, 10, 8)
        cache = tmp_path / f"gilts_in_issue_{cob:%Y%m%d}.xls"
        cache.write_bytes(b"old")
        monkeypatch.setattr(
            dmo,
            "open_report",
            lambda: (_ for _ in ()).throw(AssertionError("network must not be hit")),
        )
        assert dmo.fetch(cob, tmp_path) == cache
        assert cache.read_bytes() == b"old"

    def test_force_downloads(self, tmp_path, monkeypatch):
        cob = dt.date(2026, 10, 8)
        cache = tmp_path / f"gilts_in_issue_{cob:%Y%m%d}.xls"
        cache.write_bytes(b"old")
        monkeypatch.setattr(dmo, "open_report", lambda: (None, FETCH_META))
        monkeypatch.setattr(
            dmo, "download_xls", lambda session, date: (None, b"fresh-bytes")
        )
        path = dmo.fetch(cob, tmp_path, force=True)
        assert path == cache
        assert path.read_bytes() == b"fresh-bytes"


class TestRootExecutable:
    def test_help_runs(self):
        """`python dmo.py --help` (repo-root executable) imports the package correctly."""
        root = Path(__file__).resolve().parent.parent
        done = subprocess.run(
            [sys.executable, "dmo.py", "--help"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        assert "D1A" in done.stdout
