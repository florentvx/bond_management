"""Historical pillar z-spreads calculated from historical daily price feed (screens/history/all_gilts_history.csv).

Replays z-spread calibration (zspread.fit) across all daily closes from 1Jan2026 to today.
Saves results to screens/zspread_history_alt.csv without overwriting screens/zspread_history.csv.

Usage as CLI via package:
    python -m bond_management alt-history

Usage as CLI via script:
    python bond_management/core/zspread_history_alt.py
"""

import datetime as dt
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from bond_management import load_data
from bond_management.core.dates import next_business_day
from bond_management.core.zspread import DEFAULT_TENORS, fit

SCREENS_DIR = Path("screens")
HISTORY_FILE = SCREENS_DIR / "history" / "all_gilts_history.csv"
OUT_FILE = SCREENS_DIR / "zspread_history_alt.csv"
ORIG_FILE = SCREENS_DIR / "zspread_history.csv"


def process_single_date(args):
    """Calibrate a single date."""
    d_str, sub_dict, bonds_data = args
    sub = pd.DataFrame(sub_dict)

    feed_date_obj = dt.date.fromisoformat(d_str)
    settle_date_obj = next_business_day(feed_date_obj)

    sub["as_of"] = pd.to_datetime(d_str + " 17:00:00")
    sub["tidm"] = sub["ticker"]

    try:
        values, _ = fit(sub, bonds_data)
    except ValueError as e:
        print(f"Skipping {d_str}: calibration error {e}")
        return None

    row = {"feed": d_str, "settle": settle_date_obj.isoformat()}
    for t, v in zip(DEFAULT_TENORS, values):
        row[f"{t:g}y"] = v * 10_000
    return row


def calibrate_history(
    history_path: Path = HISTORY_FILE,
    out_file: Path = OUT_FILE,
) -> pd.DataFrame:
    """Calibrate pillar z-spreads for every date in history_path using parallel processing."""
    if not history_path.exists():
        raise FileNotFoundError(
            f"{history_path} not found. Please run 'python -m bond_management history --ticker all' first."
        )

    history_df = pd.read_csv(history_path)
    prices_ref, bonds = load_data()

    isin_to_coupon = dict(zip(prices_ref["isin"].dropna(), prices_ref["coupon_pct"].dropna()))
    history_df["coupon_pct"] = history_df["isin"].map(isin_to_coupon)
    history_df["dmo_yield_pct"] = history_df["yield_pct"]

    dates = sorted(history_df["date"].unique())
    print(f"Calibrating z-spreads over {len(dates)} trading days ({dates[0]} to {dates[-1]})...")

    tasks = []
    for d_str in dates:
        sub = history_df[history_df["date"] == d_str].to_dict(orient="list")
        tasks.append((d_str, sub, bonds))

    rows = []
    with ProcessPoolExecutor() as executor:
        results = executor.map(process_single_date, tasks)
        for r in results:
            if r is not None:
                rows.append(r)

    rows.sort(key=lambda x: x["feed"])
    res_df = pd.DataFrame(rows)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    res_df.to_csv(out_file, index=False, float_format="%.2f")
    print(f"Wrote full history calibration ({len(res_df)} dates) to {out_file}")

    return res_df


def compare_with_original(
    orig_file: Path = ORIG_FILE,
    alt_df: pd.DataFrame = None,
) -> pd.DataFrame:
    """Compare alternative z-spread calibration results with original screens/zspread_history.csv."""
    if not orig_file.exists():
        print(f"Original file {orig_file} does not exist for comparison.")
        return None

    orig_df = pd.read_csv(orig_file)
    if alt_df is None:
        if not OUT_FILE.exists():
            print(f"{OUT_FILE} does not exist.")
            return None
        alt_df = pd.read_csv(OUT_FILE)

    common_dates = orig_df["feed"].tolist()
    alt_matched = alt_df[alt_df["feed"].isin(common_dates)].copy()

    tenor_cols = [f"{t:g}y" for t in DEFAULT_TENORS]
    diff_df = orig_df[["feed", "settle"]].copy()

    for col in tenor_cols:
        diff_df[col] = alt_matched[col].values - orig_df[col].values

    return diff_df


def main():
    alt_df = calibrate_history()

    if ORIG_FILE.exists():
        diff_df = compare_with_original(ORIG_FILE, alt_df)
        print("\n=== Comparison on Common Snapshot Dates ===")
        print("Original (screens/zspread_history.csv):")
        orig_df = pd.read_csv(ORIG_FILE)
        print(orig_df.to_string(index=False, float_format=lambda x: f"{x:7.2f}"))

        print("\nAlternative (screens/zspread_history_alt.csv):")
        common_dates = orig_df["feed"].tolist()
        alt_matched = alt_df[alt_df["feed"].isin(common_dates)]
        print(alt_matched.to_string(index=False, float_format=lambda x: f"{x:7.2f}"))

        print("\nDifference (Alternative - Original in bp):")
        print(diff_df.to_string(index=False, float_format=lambda x: f"{x:+7.2f}"))


if __name__ == "__main__":
    main()
