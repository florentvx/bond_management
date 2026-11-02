"""Fetch daily historical gilt prices from giltsyield.com for all live conventional gilts.

Usage as CLI via script:
    python bond_management/scripts/fetch_history.py --start-date 1Jan2026 --test
    python bond_management/scripts/fetch_history.py --start-date 1Jan2026 --ticker T27A
    python bond_management/scripts/fetch_history.py --start-date 1Jan2026

Usage via package CLI:
    python -m bond_management history --start-date 1Jan2026 --test
    python -m bond_management history --start-date 1Jan2026
"""

import argparse
import datetime as dt
import time
from pathlib import Path

import pandas as pd

from bond_management import load_data
from bond_management.core.fetch_gilt_history import fetch_gilt_history

OUT_DIR = Path("screens") / "history"


def get_live_conventional_gilts():
    """Retrieve non-expired conventional gilts from DMO paired with price feed TIDMs."""
    prices, bonds = load_data()
    today = dt.date.today()

    isin_to_tidm = dict(zip(prices["isin"].dropna(), prices["tidm"].dropna()))
    isin_to_name = dict(zip(prices["isin"].dropna(), prices["name"].dropna()))

    conv_bonds = []
    for b in bonds:
        if b.market == "conventional" and b.redemption_date and b.redemption_date >= today:
            tidm = isin_to_tidm.get(b.isin)
            name = isin_to_name.get(b.isin, b.name)
            if tidm:
                conv_bonds.append({
                    "isin": b.isin,
                    "ticker": tidm,
                    "name": name,
                    "redemption_date": b.redemption_date,
                })
            else:
                print(f"Warning: No ticker (TIDM) found for live conventional gilt {b.isin} ({b.name})")

    return conv_bonds


def fetch_all_historical_prices(
    ticker: str,
    start_date: str = "2026-01-01",
    end_date: str | None = None,
    test: bool = False,
    out_dir: str | Path = OUT_DIR,
) -> pd.DataFrame:
    """Fetch historical daily prices and yields for live conventional gilts.

    Args:
        ticker: Gilt ticker symbol (e.g. 'T27A') or 'all' for all live conventional gilts.
        start_date: Historical start date (e.g. '1Jan2026' or '2026-01-01').
        end_date: Historical end date (defaults to today).
        test: If True, runs on 1 example gilt only.
        out_dir: Directory where results will be saved.

    Returns:
        pd.DataFrame containing historical prices.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    gilts = get_live_conventional_gilts()
    if not gilts:
        raise RuntimeError("No live conventional gilts found in DMO inventory.")

    if ticker and ticker.strip().lower() != "all":
        ticker_upper = ticker.strip().upper()
        gilts = [g for g in gilts if g["ticker"].upper() == ticker_upper]
        if not gilts:
            raise ValueError(f"Ticker '{ticker}' not found among live conventional gilts.")

    if test:
        print("Test mode enabled: running on 1 example gilt.")
        gilts = gilts[:1]

    print(f"Starting fetch for {len(gilts)} gilt(s) from {start_date} to {end_date or 'today'}...")

    all_dfs = []
    for i, g in enumerate(gilts, start=1):
        t = g["ticker"]
        name = g["name"]
        isin = g["isin"]
        print(f"[{i}/{len(gilts)}] Fetching {t} ({name}, ISIN: {isin})...")

        try:
            df = fetch_gilt_history(ticker=t, start_date=start_date, end_date=end_date)
            df.insert(0, "isin", isin)
            df.insert(1, "ticker", t)
            df.insert(2, "name", name)

            # Save individual gilt CSV
            single_file = out_dir / f"{t}_history.csv"
            df.to_csv(single_file, index=False)
            print(f"    Saved {len(df)} rows to {single_file}")

            all_dfs.append(df)
        except (ValueError, RuntimeError, pd.errors.PyDataError) as e:
            print(f"    Error fetching {t}: {e}")

        # Small delay between requests to be polite
        if i < len(gilts):
            time.sleep(0.5)

    if not all_dfs:
        raise RuntimeError("Failed to fetch historical data for any gilt.")

    combined_df = pd.concat(all_dfs, ignore_index=True)
    combined_file = out_dir / "all_gilts_history.csv"
    combined_df.to_csv(combined_file, index=False)
    print(f"\nDone! Combined data ({len(combined_df)} rows across {len(all_dfs)} gilts) written to {combined_file}")

    return combined_df


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fetch daily price and yield history for live conventional gilts."
    )
    parser.add_argument(
        "--ticker",
        "-t",
        required=True,
        help="Gilt ticker symbol (e.g. T27A) or 'all' for all live conventional gilts",
    )
    parser.add_argument(
        "--start-date", "-s", default="2026-01-01", help="Start date e.g. '1Jan2026' or '2026-01-01'"
    )
    parser.add_argument("--end-date", "-e", default=None, help="End date e.g. '2026-11-01'")
    parser.add_argument(
        "--test", action="store_true", help="Test mode: run on 1 example gilt before full run"
    )
    parser.add_argument(
        "--out-dir", "-o", default=OUT_DIR, help="Output directory (default: screens/history)"
    )

    args = parser.parse_args(argv)

    fetch_all_historical_prices(
        ticker=args.ticker,
        start_date=args.start_date,
        end_date=args.end_date,
        test=args.test,
        out_dir=args.out_dir,
    )


if __name__ == "__main__":
    main()
