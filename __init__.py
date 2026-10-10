"""Gilt prices from giltsyield.com paired with the DMO gilts in issue.

    prices, in_issue = load_data()

load_data() reads the cached price file when its feed timestamp has reached the
17:00 close (Saturday/Sunday read Friday's file) and refetches it otherwise,
then pairs the prices with the newest DMO gilts in issue, downloading the latest
inventory from DMO when a priced gilt is missing from it.
"""

import datetime as dt
import re
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    from . import dmo
except ImportError:  # running this file directly from the package folder
    import dmo

URL = "https://giltsyield.com/bond/"
HEADERS = {"User-Agent": "personal-gilt-tracker (contact: you@example.com)"}
OUT_DIR = Path("screens")
CLOSE = dt.time(17, 0)

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
    as_of = dt.datetime.strptime(m.group(1), "%d %b %Y %H:%M").replace(tzinfo=dt.timezone.utc) if m else None

    # Pick the table that has a "Clean price" header
    table = next(t for t in soup.find_all("table") if "Clean price" in t.get_text())
    headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
    idx = {h: i for i, h in enumerate(headers)}

    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < len(headers):
            continue
        cells = [td.get_text(" ", strip=True) for td in tds]

        gilt = cells[idx["Gilt"]]
        tidm_isin = re.search(r"\b([A-Z0-9]{2,5})\s*·\s*(GB[0-9A-Z]{10})", gilt)
        link = tds[idx["Gilt"]].find("a")

        rows.append({
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
        })

    df = pd.DataFrame(rows)
    df["as_of"] = as_of
    return df


def as_naive(ts):
    """Drop the timezone so the feed's printed clock time compares with CLOSE."""
    ts = pd.Timestamp(ts)
    return ts.tz_localize(None) if ts.tz is not None else ts


def feed_date(day=None):
    """The date whose prices we want: Saturday and Sunday both fall back to Friday."""
    day = day or dt.datetime.now(tz=dt.timezone.utc).date()
    if day.weekday() == 5:
        return day - dt.timedelta(days=1)
    if day.weekday() == 6:
        return day - dt.timedelta(days=2)
    return day


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
        print(f"{path.name} only has {as_of:%d/%m/%Y %H:%M}, refetching for the 17:00 close")
        return None
    print(f"{path.name} already has the {as_of:%d/%m/%Y %H:%M} close")
    df["as_of"] = pd.to_datetime(df["as_of"])
    return df


def load_gilt_prices(out_dir=OUT_DIR):
    """Gilt prices for the latest trading day.

    Weekend runs point at Friday's file, and a cached file is only reused once
    its feed timestamp reaches that day's 17:00 close; otherwise the feed is
    refetched and saved under the same name.
    """
    out_dir = Path(out_dir)
    day = feed_date()
    cutoff = dt.datetime.combine(day, CLOSE)
    cached = cached_prices(out_dir / f"gilts_{day:%Y%m%d}.csv", cutoff)
    if cached is not None:
        return cached

    df = scrape_gilts()
    path = out_dir / f"gilts_{day:%Y%m%d}.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    as_of = as_naive(df["as_of"].iloc[0]) if len(df) and not pd.isna(df["as_of"].iloc[0]) else None
    if as_of is None:
        print(f"{path.name} <- {len(df)} gilts, no feed timestamp")
    elif as_of < cutoff:
        print(f"{path.name} <- {len(df)} gilts, feed only shows {as_of:%H:%M} (close is 17:00)")
    else:
        print(f"{path.name} <- {len(df)} gilts, {as_of:%d/%m/%Y %H:%M} close")
    return df


def load_data(out_dir=OUT_DIR):
    """Gilt prices plus the DMO gilts in issue, refreshing whichever is stale.

    Returns (prices, in_issue) DataFrames. The inventory is the newest D1A
    workbook on disk, downloaded when there is none; when a priced gilt is
    missing from it, the latest inventory is fetched from DMO instead.
    """
    prices = load_gilt_prices(out_dir)
    path = dmo.find_newest(out_dir) or dmo.fetch(out_dir=out_dir)
    in_issue = dmo.read(path)

    missing = sorted(set(prices["isin"].dropna()) - set(in_issue["isin"]))
    if missing:
        print(f"{', '.join(missing)} not in {path.name}, fetching the latest DMO inventory")
        fresh = dmo.fetch(out_dir=out_dir)
        if fresh != path:
            path, in_issue = fresh, dmo.read(fresh)
            missing = sorted(set(prices["isin"].dropna()) - set(in_issue["isin"]))
        if missing:
            print(f"still missing from the DMO inventory: {', '.join(missing)}")
    return prices, in_issue

if __name__ == "__main__":
    prices, in_issue = load_data()
    print(prices.head())
    print(in_issue.head())