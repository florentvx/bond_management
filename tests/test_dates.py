import argparse
import datetime as dt

import pandas as pd
import pytest

from bond_management.dates import (
    CLOSE,
    as_naive,
    bank_holidays,
    easter,
    excel_date,
    feed_date,
    parse_date,
)


class TestAsNaive:
    def test_strips_timezone(self):
        ts = pd.Timestamp("2026-10-09 17:00", tz="UTC")
        assert as_naive(ts).tz is None

    def test_naive_stays_naive(self):
        ts = pd.Timestamp("2026-10-09 17:00")
        assert as_naive(ts) == ts


class TestClose:
    def test_is_1700(self):
        assert CLOSE == dt.time(17, 0)


class TestBankHolidays:
    def test_easter_algorithm(self):
        assert easter(2026) == dt.date(2026, 4, 5)
        assert easter(2025) == dt.date(2025, 4, 20)

    def test_2026(self):
        assert bank_holidays(2026) == {
            dt.date(2026, 1, 1),
            dt.date(2026, 4, 3),  # Good Friday
            dt.date(2026, 4, 6),  # Easter Monday
            dt.date(2026, 5, 4),  # early May
            dt.date(2026, 5, 25),  # spring
            dt.date(2026, 8, 31),  # summer
            dt.date(2026, 12, 25),
            dt.date(2026, 12, 26),  # Boxing Day (falls on Saturday)
            dt.date(2026, 12, 28),  # its weekday substitute
        }

    def test_2027_weekend_christmas_gets_two_substitutes(self):
        holidays = bank_holidays(2027)
        assert dt.date(2027, 12, 25) in holidays  # Saturday
        assert dt.date(2027, 12, 26) in holidays  # Sunday
        assert dt.date(2027, 12, 27) in holidays  # substitute for the 25th
        assert dt.date(2027, 12, 28) in holidays  # substitute for the 26th


class TestFeedDate:
    def test_plain_weekday(self):
        assert feed_date(dt.date(2026, 10, 9)) == dt.date(2026, 10, 9)  # Friday

    def test_saturday_falls_back_to_friday(self):
        assert feed_date(dt.date(2026, 10, 10)) == dt.date(2026, 10, 9)

    def test_sunday_falls_back_to_friday(self):
        assert feed_date(dt.date(2026, 10, 11)) == dt.date(2026, 10, 9)

    def test_easter_weekend_falls_back_to_thursday(self):
        # Easter Monday (6th) and Good Friday (3rd) are both holidays, weekend between
        assert feed_date(dt.date(2026, 4, 6)) == dt.date(2026, 4, 2)
        assert feed_date(dt.date(2026, 4, 3)) == dt.date(2026, 4, 2)

    def test_christmas_lull(self):
        # 25th (Fri) / 26th (Sat) / 27th (Sun) / 28th (Mon substitute) all off
        assert feed_date(dt.date(2026, 12, 28)) == dt.date(2026, 12, 24)

    def test_new_year_crosses_year_boundary(self):
        assert feed_date(dt.date(2027, 1, 1)) == dt.date(2026, 12, 31)


class TestParseDate:
    def test_slashes(self):
        assert parse_date("02/10/2026") == dt.date(2026, 10, 2)

    def test_dashes(self):
        assert parse_date("2026-10-02") == dt.date(2026, 10, 2)

    def test_invalid(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_date("October 2, 2026")


class TestExcelDate:
    def test_blank(self):
        assert excel_date("", 0) is None
        assert excel_date(None, 0) is None

    def test_zero_is_blank(self):
        assert excel_date(0, 0) is None
        assert excel_date(0.0, 0) is None

    def test_serial(self):
        assert excel_date(44927, 0) == dt.date(2023, 1, 1)
