"""Dates and calendars for the gilt tracker.

The 17:00 market-close cutoff, UK trading-day arithmetic (weekends and
England & Wales bank holidays) and the date parsers/converters shared by the
price and DMO loaders.
"""

import argparse
import calendar
import datetime as dt

import pandas as pd
import xlrd

CLOSE = dt.time(17, 0)


def easter(year):
    """Easter Sunday for a year (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def _substitutes(dates):
    """Original dates plus, for any that fall on a weekend, the next free weekday off."""
    observed = set(dates)
    for d in sorted(dates):
        if d.weekday() >= 5:
            nxt = d + dt.timedelta(days=1)
            while nxt.weekday() >= 5 or nxt in observed:
                nxt += dt.timedelta(days=1)
            observed.add(nxt)
    return observed


def bank_holidays(year):
    """England & Wales bank holidays for a year (one-off extras like jubilees excluded)."""

    def nth_weekday(month, weekday, last=False):
        if last:
            day = dt.date(year, month, calendar.monthrange(year, month)[1])
            return day - dt.timedelta(days=(day.weekday() - weekday) % 7)
        day = dt.date(year, month, 1)
        return day + dt.timedelta(days=(weekday - day.weekday()) % 7)

    e = easter(year)
    return _substitutes(
        {
            dt.date(year, 1, 1),
            e - dt.timedelta(days=2),  # Good Friday
            e + dt.timedelta(days=1),  # Easter Monday
            nth_weekday(5, 0),  # early May bank holiday
            nth_weekday(5, 0, last=True),  # spring bank holiday
            nth_weekday(8, 0, last=True),  # summer bank holiday
            dt.date(year, 12, 25),
            dt.date(year, 12, 26),
        }
    )


def feed_date(day=None):
    """The date whose prices we want: weekends and bank holidays fall back to the previous trading day."""
    day = day or dt.datetime.now(tz=dt.UTC).date()
    while day.weekday() >= 5 or day in bank_holidays(day.year):
        day -= dt.timedelta(days=1)
    return day


def as_naive(ts):
    """Drop the timezone so the feed's printed clock time compares with CLOSE."""
    ts = pd.Timestamp(ts)
    return ts.tz_localize(None) if ts.tz is not None else ts


def parse_utc_datetime(value, fmt):
    """Parse a datetime string and attach UTC so later date extraction is explicit."""
    return dt.datetime.strptime(value, fmt).replace(tzinfo=dt.UTC)


def parse_date(value):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(value, fmt).replace(tzinfo=dt.UTC).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"not a date: {value!r} (use dd/mm/yyyy or yyyy-mm-dd)"
    )


def excel_date(value, datemode):
    """An Excel serial date cell -> datetime.date (blank or zero cells stay None)."""
    if value in ("", None) or value == 0:
        return None
    return xlrd.xldate_as_datetime(value, datemode).date()
