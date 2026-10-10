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

try:
    from . import dmo
    from .bond import Bond
    from .dates import CLOSE, as_naive, feed_date
except ImportError:  # running this file directly from the package folder
    import dmo
    from bond import Bond
    from dates import CLOSE, as_naive, feed_date

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


def num(s):
    """'£99.85 ▲0.03%' -> 99.85 ; '3.659% ▼6.7bps' -> 3.659"""
    m = re.search(r"-?[\d,]*\.?\d+", s.replace("£", ""))
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
    df = pd.read_csv(path)
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


def load_gilt_prices(out_dir=OUT_DIR, force=False):
    """Gilt prices for the latest trading day.

    Weekend and bank-holiday runs point at the previous trading day's file, and
    a cached file is only reused once its feed timestamp reaches that day's
    17:00 close; otherwise the feed is refetched and saved under the same name.
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
            f"{path.name} <- {len(df)} gilts, feed only shows {as_of:%H:%M} (close is 17:00)"
        )
    else:
        print(f"{path.name} <- {len(df)} gilts, {as_of:%d/%m/%Y %H:%M} close")
    return df


def load_data(out_dir=OUT_DIR, force=False):
    """Gilt prices plus the DMO gilts in issue, refreshing whichever is stale.

    Returns (prices, bonds): the price DataFrame and the inventory as Bond
    objects. The inventory is the newest D1A workbook on disk, downloaded when
    there is none or when it is over dmo.MAX_AGE_DAYS old, and refreshed when
    a priced gilt is missing from it. force=True skips both caches and hits
    the network again.
    """
    prices = load_gilt_prices(out_dir, force=force)
    path = dmo.find_newest(out_dir)
    if path is not None and not force and dmo.is_stale(path):
        print(
            f"{path.name} is over {dmo.MAX_AGE_DAYS} days old, fetching the latest DMO inventory"
        )
        path = None
    if path is None or force:
        path = dmo.fetch(out_dir=out_dir, force=force)
    bonds = dmo.read(path)

    if len(prices):
        missing = sorted(set(prices["isin"].dropna()) - {b.isin for b in bonds})
        if missing:
            print(
                f"{', '.join(missing)} not in {path.name}, fetching the latest DMO inventory"
            )
            fresh = dmo.fetch(out_dir=out_dir, force=True)
            if fresh != path:
                path, bonds = fresh, dmo.read(fresh)
                missing = sorted(set(prices["isin"].dropna()) - {b.isin for b in bonds})
            if missing:
                print(f"still missing from the DMO inventory: {', '.join(missing)}")
    return prices, bonds
