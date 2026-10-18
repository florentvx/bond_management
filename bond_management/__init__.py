"""Gilt prices from giltsyield.com paired with the DMO gilts in issue.

    prices, bonds = load_data()

load_data() reads the cached price file when its feed timestamp has reached the
17:00 close (weekends and bank holidays read the previous trading day's file)
and refetches it otherwise, then pairs the prices with the newest DMO gilts in
issue as Bond objects, downloading a fresh inventory from DMO when a priced
gilt is missing from it or the cached one is over a month old.
force=True ignores both caches.
"""

import datetime as dt
import re
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from . import dmo
from .bond import Bond
from .dates import CLOSE, as_naive, feed_date

__all__ = [
    "Bond",
    "cached_prices",
    "feed_date",
    "load_data",
    "load_gilt_prices",
    "num",
    "scrape_gilts",
]

URL = "https://giltsyield.com/bond/"
HEADERS = {"User-Agent": "personal-gilt-tracker (contact: you@example.com)"}
OUT_DIR = Path("screens")
COLUMNS = [
    "name",
    "tidm",
    "isin",
    "coupon_pct",
    "matures",
    "clean_price",
    "accrued",
    "dirty_price",
    "mod_duration",
    "gross_yield_pct",
    "dmo_yield_pct",
]
PRICE_COLUMNS = [
    "Gilt",
    "Coupon",
    "Matures",
    "Clean price",
    "Accr. int.",
    "Dirty price",
    "Mod. duration",
    "Gross yield",
    "DMO yield",
]
VULGAR = str.maketrans(
    {
        "¼": " 1/4",
        "½": " 1/2",
        "¾": " 3/4",
        "⅓": " 1/3",
        "⅔": " 2/3",
        "⅛": " 1/8",
        "⅜": " 3/8",
        "⅝": " 5/8",
        "⅞": " 7/8",
    }
)


def num(s):
    """'£99.85 ▲0.03%' -> 99.85 ; '4¼%' -> 4.25 ; '3.659% ▼6.7bps' -> 3.659"""
    s = s.replace("£", "").translate(VULGAR)
    m = re.search(r"(-?\d+)\s+(\d+)/(\d+)", s)
    if m:
        whole, frac = int(m.group(1)), int(m.group(2)) / int(m.group(3))
        return -(abs(whole) + frac) if whole < 0 else whole + frac
    m = re.fullmatch(r"\s*(-?)(\d+)/(\d+)\s*%?", s)
    if m:
        frac = int(m.group(2)) / int(m.group(3))
        return -frac if m.group(1) else frac
    m = re.search(r"(-?)(\d+)/(\d+)(?=%)", s)
    if m:
        frac = int(m.group(2)) / int(m.group(3))
        return -frac if m.group(1) else frac
    m = re.search(r"-?[\d,]*\.?\d+", s)
    return float(m.group().replace(",", "")) if m else None


def scrape_gilts():
    r = requests.get(URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Data timestamp, e.g. "Market data as of 02 Oct 2026 17:00"
    text = soup.get_text(" ", strip=True)
    m = re.search(r"Market data as of\s+(\d{1,2} \w{3} \d{4} \d{2}:\d{2})", text)
    as_of = (
        dt.datetime.strptime(m.group(1), "%d %b %Y %H:%M").replace(tzinfo=dt.UTC)
        if m
        else None
    )

    # Pick the table that has a "Clean price" header
    table = next(
        (t for t in soup.find_all("table") if "Clean price" in t.get_text()), None
    )
    if table is None:
        raise RuntimeError(
            f"no gilt price table (with a 'Clean price' header) at {URL}"
        )
    headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
    idx = {h: i for i, h in enumerate(headers)}
    absent = [h for h in PRICE_COLUMNS if h not in idx]
    if absent:
        raise RuntimeError(f"price table is missing columns: {', '.join(absent)}")

    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < len(headers):
            continue
        cells = [td.get_text(" ", strip=True) for td in tds]

        gilt = cells[idx["Gilt"]]
        tidm_isin = re.search(r"\b([A-Z0-9]{2,5})\s*·\s*(GB[0-9A-Z]{10})", gilt)
        link = tds[idx["Gilt"]].find("a")

        rows.append(
            {
                "name": link.get_text(strip=True) if link else gilt,
                "tidm": tidm_isin.group(1) if tidm_isin else None,
                "isin": tidm_isin.group(2) if tidm_isin else None,
                "coupon_pct": num(cells[idx["Coupon"]]),
                "matures": cells[idx["Matures"]],
                "clean_price": num(cells[idx["Clean price"]]),
                "accrued": num(cells[idx["Accr. int."]]),
                "dirty_price": num(cells[idx["Dirty price"]]),
                "mod_duration": num(cells[idx["Mod. duration"]]),
                "gross_yield_pct": num(cells[idx["Gross yield"]]),
                "dmo_yield_pct": num(cells[idx["DMO yield"]]),
            }
        )

    df = pd.DataFrame(rows, columns=COLUMNS)
    df["as_of"] = as_of
    return df


def cached_prices(path, cutoff):
    """The cached price file when it already holds the cutoff close, else None."""
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError):
        print(f"{path.name} is unreadable, refetching")
        return None
    if "as_of" not in df.columns:
        print(f"{path.name} has no data timestamp, refetching")
        return None
    as_of = pd.to_datetime(df["as_of"], errors="coerce").max()
    if pd.isna(as_of):
        print(f"{path.name} has no data timestamp, refetching")
        return None
    as_of = as_naive(as_of)
    if as_of < cutoff:
        print(
            f"{path.name} only has {as_of:%d/%m/%Y %H:%M}, refetching for the 17:00 close"
        )
        return None
    print(f"{path.name} already has the {as_of:%d/%m/%Y %H:%M} close")
    df["as_of"] = pd.to_datetime(df["as_of"])
    return df


def newest_cache(out_dir):
    """Newest gilts_*.csv in out_dir (filenames sort by date), or None."""
    files = sorted(Path(out_dir).glob("gilts_*.csv"))
    return files[-1] if files else None


def scrape_problems(df, previous_rows):
    """Reasons a scraped price table must not be written to the cache."""
    if not len(df):
        return ["no rows"]
    problems = []
    if "as_of" not in df.columns or df["as_of"].isna().all():
        problems.append("no data timestamp")
    nulls = [
        c
        for c in ("isin", "clean_price", "matures")
        if c in df.columns and df[c].isna().any()
    ]
    if nulls:
        problems.append(f"null values in {', '.join(nulls)}")
    if previous_rows is not None and len(df) < previous_rows:
        problems.append(f"only {len(df)} of {previous_rows} rows")
    return problems


def load_gilt_prices(out_dir=OUT_DIR, force=False):
    """Gilt prices for the latest trading day.

    Weekend and bank-holiday runs point at the previous trading day's file, and
    a cached file is only reused once its feed timestamp reaches that day's
    17:00 close; otherwise the feed is refetched and saved under the same name.
    An empty, partial or corrupt scrape is never written: the newest cached
    file is kept and returned instead.
    force=True skips the cache and refetches the feed.
    """
    out_dir = Path(out_dir)
    day = feed_date()
    cutoff = dt.datetime.combine(day, CLOSE)
    path = out_dir / f"gilts_{day:%Y%m%d}.csv"
    if not force:
        cached = cached_prices(path, cutoff)
        if cached is not None:
            return cached

    df = scrape_gilts()
    out_dir.mkdir(parents=True, exist_ok=True)
    old, old_df = newest_cache(out_dir), None
    if old is not None:
        try:
            old_df = pd.read_csv(old)
        except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError):
            old_df = None
    problems = scrape_problems(df, len(old_df) if old_df is not None else None)
    if problems:
        print(f"{path.name} <- refusing to cache: {'; '.join(problems)}")
        if old_df is not None:
            print(f"keeping {old.name}")
            return old_df
        return df
    df.to_csv(path, index=False)
    as_of = (
        as_naive(df["as_of"].iloc[0])
        if len(df) and not pd.isna(df["as_of"].iloc[0])
        else None
    )
    if as_of is None:
        print(f"{path.name} <- {len(df)} gilts, no feed timestamp")
    elif as_of < cutoff:
        print(
            f"{path.name} <- {len(df)} gilts, "
            f"feed only shows {as_of:%d/%m/%Y %H:%M}, close is {CLOSE:%H:%M}"
        )
    else:
        print(f"{path.name} <- {len(df)} gilts, {as_of:%d/%m/%Y %H:%M} close")
    return df


def load_data(out_dir=OUT_DIR, force=False):
    """Gilt prices plus the DMO gilts in issue, refreshing whichever is stale.

    Returns (prices, bonds): the price DataFrame and the inventory as Bond
    objects. The inventory is the newest D1A workbook on disk, downloaded when
    there is none or when it is over dmo.MAX_AGE_DAYS old, and refreshed when
    a priced gilt is missing from it; a failed refresh falls back to the
    cached workbook (re-raising only when there is none). force=True skips
    both caches and hits the network again.
    """
    prices = load_gilt_prices(out_dir, force=force)
    cached = dmo.find_newest(out_dir)
    path = cached
    if path is not None and not force and dmo.is_stale(path):
        print(
            f"{path.name} is over {dmo.MAX_AGE_DAYS} days old, fetching the latest DMO inventory"
        )
        path = None
    if path is None or force:
        try:
            path = dmo.fetch(out_dir=out_dir, force=force)
        except dmo.FETCH_ERRORS as e:
            if cached is None:
                raise
            print(f"could not refresh the DMO inventory: {e}; using {cached.name}")
            path = cached
    bonds = dmo.read(path)

    if len(prices):
        priced = set(prices["isin"].dropna())
        missing = sorted(priced - {b.isin for b in bonds})
        if missing:
            print(
                f"{', '.join(missing)} not in {path.name}, fetching the latest DMO inventory"
            )
            try:
                fresh = dmo.fetch(out_dir=out_dir, force=True)
            except dmo.FETCH_ERRORS as e:
                print(f"could not refresh the DMO inventory: {e}; using {path.name}")
            else:
                if fresh != path:
                    path, bonds = fresh, dmo.read(fresh)
                    missing = sorted(priced - {b.isin for b in bonds})
                if missing:
                    print(f"still missing from the DMO inventory: {', '.join(missing)}")
        gone = sorted(
            b.isin
            for b in bonds
            if b.market == "conventional" and b.isin not in priced
        )
        if gone:
            print(f"feed may be missing {len(gone)} gilts: {', '.join(gone)}")
    return prices, bonds
