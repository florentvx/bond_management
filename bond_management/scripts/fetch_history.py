"""Fetch daily historical gilt prices from giltsyield.com for all live conventional gilts.

Existing CSVs are merged, not overwritten: rows absent from the new download are kept,
rows present in both are compared (a warning is printed for any that differ, and the
new download wins for those dates), and only then are new rows added.

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

DATA_COLS = ["clean_price", "yield_pct"]


def warn_on_changed_overlap(new_df: pd.DataFrame, old_df: pd.DataFrame, path: Path) -> None:
    """Print a warning for every date whose values differ between the old file and the new download."""
    old_by_date = old_df.set_index("date")
    new_by_date = new_df.set_index("date")
    common = old_by_date.index.intersection(new_by_date.index)
    if len(common) == 0:
        return

    changed = []
    for col in DATA_COLS:
        if col not in old_by_date.columns or col not in new_by_date.columns:
            continue
        old_vals = old_by_date.loc[common, col]
        new_vals = new_by_date.loc[common, col].reindex(old_vals.index)
        diff = ~((old_vals == new_vals) | (old_vals.isna() & new_vals.isna()))
        changed.extend((d, col, old_vals[d], new_vals[d]) for d in old_vals.index[diff])

    if not changed:
        return
    print(f"Warning: {path.name}: {len(changed)} value(s) differ between the existing file and the new download")
    for date, col, old_val, new_val in changed[:10]:
        print(f"    {date} {col}: {old_val} -> {new_val} (new download kept)")
    if len(changed) > 10:
        print(f"    ... and {len(changed) - 10} more")


def merge_with_existing(new_df: pd.DataFrame, existing_path: Path) -> pd.DataFrame:
    """Merge a fresh download into an existing per-gilt CSV.

    Keeps old rows whose dates are not in the download, warns about overlapping dates
    whose values changed (the new download wins there), and returns the combined frame.
    """
    if not existing_path.exists():
        return new_df
    try:
        old_df = pd.read_csv(existing_path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:  # missing/unreadable/corrupt CSV
        print(f"Warning: could not read {existing_path} ({exc}); keeping only the new download.")
        return new_df
    if "date" not in old_df.columns:
        print(f"Warning: {existing_path} has no 'date' column; keeping only the new download.")
        return new_df

    warn_on_changed_overlap(new_df, old_df, existing_path)

    new_dates = set(new_df["date"])
    old_only = old_df[~old_df["date"].isin(new_dates)]
    merged = pd.concat([new_df, old_only], ignore_index=True, sort=False)
    merged = merged.reindex(columns=new_df.columns)
    return merged.sort_values("date").reset_index(drop=True)


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

            # Merge with any existing file (keep old rows, warn on changed overlap) and save
            single_file = out_dir / f"{t}_history.csv"
            n_downloaded = len(df)
            df = merge_with_existing(df, single_file)
            df.to_csv(single_file, index=False)
            print(f"    Saved {len(df)} rows to {single_file} ({n_downloaded} from this download)")

            all_dfs.append(df)
        except (ValueError, RuntimeError, pd.errors.PyDataError) as e:
            print(f"    Error fetching {t}: {e}")

        # Small delay between requests to be polite
        if i < len(gilts):
            time.sleep(0.5)

    if not all_dfs:
        raise RuntimeError("Failed to fetch historical data for any gilt.")

    combined_df = pd.concat(all_dfs, ignore_index=True)
    new_cols = list(combined_df.columns)
    combined_file = out_dir / "all_gilts_history.csv"

    # Keep rows for gilts not fetched in this run (their data is already merged per-gilt above)
    if combined_file.exists():
        try:
            old_combined = pd.read_csv(combined_file)
            if "ticker" in old_combined.columns:
                fetched = set(combined_df["ticker"])
                kept = old_combined[~old_combined["ticker"].isin(fetched)]
                combined_df = pd.concat([kept, combined_df], ignore_index=True, sort=False)
            else:
                print(f"Warning: {combined_file} has no 'ticker' column; keeping only this download.")
        except (OSError, UnicodeDecodeError, ValueError) as exc:  # missing/unreadable/corrupt CSV
            print(f"Warning: could not read {combined_file} ({exc}); keeping only this download.")

    combined_df = combined_df.reindex(columns=new_cols)
    combined_df = combined_df.sort_values(["ticker", "date"]).reset_index(drop=True)
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
