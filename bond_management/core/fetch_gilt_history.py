"""Retrieve daily historical gilt prices and yields from giltsyield.com.

Usage as CLI:
    python bond_management/core/fetch_gilt_history.py --ticker T27A --start 2026-01-01 [--out T27A_history.csv]

Usage in Python:
    from bond_management.core.fetch_gilt_history import fetch_gilt_history
    df = fetch_gilt_history('T27A', start_date='2026-01-01')
"""

import argparse
import html
import json
import re
from datetime import datetime

import pandas as pd
import requests


def parse_flexible_date(date_str: str | None) -> str:
    """Parse various date string formats into YYYY-MM-DD."""
    if not date_str:
        return datetime.now().strftime("%Y-%m-%d")

    date_str = str(date_str).strip()
    formats = [
        "%Y-%m-%d",
        "%d%b%Y",
        "%d-%b-%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    raise ValueError(f"Could not parse date '{date_str}'. Expected formats like '2026-01-01', '1Jan2026', or '01/01/2026'.")


def fetch_gilt_history(ticker: str, start_date: str = "2026-01-01", end_date: str | None = None) -> pd.DataFrame:
    """Fetch daily historical price and yield data for a given gilt ticker from giltsyield.com.

    Args:
        ticker: Gilt ticker symbol (e.g. 'T27A', 'TG27', 'TR28').
        start_date: Start date for historical data (e.g. '2026-01-01' or '1Jan2026').
        end_date: End date for historical data (defaults to today's date).

    Returns:
        pd.DataFrame with columns ['date', 'clean_price', 'yield_pct'].
    """
    start_fmt = parse_flexible_date(start_date)
    end_fmt = parse_flexible_date(end_date)

    url = f"https://giltsyield.com/bond/historical/{ticker.strip().upper()}/"
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    })

    # Step 1: GET the historical page to extract session cookies and CSRF token
    resp_get = session.get(url)
    resp_get.raise_for_status()

    csrf_match = re.search(r'name=["\']csrf_token["\']\s+type=["\']hidden["\']\s+value=["\']([^"\']+)["\']', resp_get.text)
    if not csrf_match:
        csrf_match = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']csrf_token["\']', resp_get.text)

    csrf_token = csrf_match.group(1) if csrf_match else ""

    # Step 2: POST the form with custom start and end dates
    post_data = {
        "csrf_token": csrf_token,
        "start_date": start_fmt,
        "end_date": end_fmt,
    }

    resp_post = session.post(url, data=post_data)
    resp_post.raise_for_status()

    # Step 3: Extract chart data JSON payload from HTML canvas element
    chart_match = re.search(r'data-chart=(["\'])(.*?)\1', resp_post.text, re.DOTALL)
    if not chart_match:
        raise ValueError(f"No chart data found for ticker '{ticker}'. Please check the ticker symbol.")

    raw_json = html.unescape(chart_match.group(2))
    chart_data = json.loads(raw_json)

    labels = chart_data.get("data", {}).get("labels", [])
    datasets = chart_data.get("data", {}).get("datasets", [])

    yields = []
    prices = []
    for ds in datasets:
        label = ds.get("label", "")
        if "Yield" in label:
            yields = ds.get("data", [])
        elif "Price" in label or "Clean price" in label:
            prices = ds.get("data", [])

    dates = [l.split()[0] for l in labels]

    df = pd.DataFrame({
        "date": dates,
        "clean_price": prices,
        "yield_pct": yields
    })

    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch daily gilt price and yield history from giltsyield.com")
    parser.add_argument("--ticker", "-t", default="T27A", help="Gilt ticker symbol (default: T27A)")
    parser.add_argument("--start", "-s", default="2026-01-01", help="Start date e.g. '2026-01-01' or '1Jan2026'")
    parser.add_argument("--end", "-e", default=None, help="End date e.g. '2026-11-01'")
    parser.add_argument("--out", "-o", default=None, help="Output CSV file path (optional)")

    args = parser.parse_args()

    df_data = fetch_gilt_history(args.ticker, args.start, args.end)
    print(f"Fetched {len(df_data)} historical rows for {args.ticker.upper()} from {args.start} to {args.end or 'today'}:")
    print(df_data.head())
    print("...")
    print(df_data.tail())

    if args.out:
        df_data.to_csv(args.out, index=False)
        print(f"\nSaved data to {args.out}")
