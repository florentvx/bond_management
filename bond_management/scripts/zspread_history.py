"""Historical pillar z-spreads, one row per cached close in screens/.

    python bond_management/scripts/zspread_history.py

Replays the z-spread calibration (zspread.fit) over every gilts_*.csv cached
in screens and tabulates the eight benchmark-node z-spreads per feed date, in
bp. Every snapshot is paired with the newest cached D1A workbook: coupon
schedules and redemption dates are fixed, so one inventory serves them all —
a gilt missing from it (matured, or issued after the workbook's COB) is left
out with the same note fit gives the live run. Nothing hits the network; a
fresh clone needs screens/ populated first (python bond_management/core/dmo.py, then a few days of
python -m bond_management runs).

Writes screens/zspread_history.csv next to the price caches.
"""

from pathlib import Path

import pandas as pd

from bond_management.core import dmo
from bond_management.core.dates import as_naive, next_business_day
from bond_management.core.zspread import DEFAULT_TENORS, fit

OUT_DIR = Path("screens")


def history(out_dir=OUT_DIR, bonds=None):
    """DataFrame of pillar z-spreads (bp) per cached close, oldest first.

    One row per distinct feed date among the gilts_*.csv caches: the close
    date, its T+1 settlement and the eight calibrated nodes. bonds defaults
    to the newest cached D1A inventory; unreadable or duplicate snapshots are
    skipped with a note.
    """
    out_dir = Path(out_dir)
    files = sorted(out_dir.glob("gilts_*.csv"))
    if not files:
        raise RuntimeError(f"no gilts_*.csv snapshots in {out_dir}")
    if bonds is None:
        newest = dmo.find_newest(out_dir)
        if newest is None:
            raise RuntimeError(
                f"no cached DMO inventory in {out_dir}, run python dmo.py first"
            )
        bonds = dmo.read(newest)

    rows, seen = [], set()
    for path in files:
        try:
            prices = pd.read_csv(path)
        except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError):
            print(f"{path.name} is unreadable, skipping")
            continue
        if "as_of" not in prices.columns or prices["as_of"].isna().all():
            print(f"{path.name} has no data timestamp, skipping")
            continue
        feed = as_naive(pd.to_datetime(prices["as_of"], errors="coerce").max()).date()
        if feed in seen:
            print(f"{path.name} repeats the {feed:%d/%m/%Y} close, skipping")
            continue
        seen.add(feed)
        settle = next_business_day(feed)
        print(f"{path.name}: {feed:%d/%m/%Y} close, settle {settle:%d/%m/%Y}")
        try:
            values, _ = fit(prices, bonds)
        except ValueError as e:
            print(f"could not calibrate {path.name}: {e}")
            continue
        row = {"feed": feed, "settle": settle}
        for t, v in zip(DEFAULT_TENORS, values):
            row[f"{t:g}y"] = v * 10_000
        rows.append(row)
    if not rows:
        raise RuntimeError(f"no snapshot in {out_dir} could be calibrated")
    return pd.DataFrame(rows)


def main():
    df = history(OUT_DIR)
    print()
    print(df.to_string(index=False, float_format=lambda x: f"{x:7.2f}"))
    path = OUT_DIR / "zspread_history.csv"
    df.to_csv(path, index=False, float_format="%.2f")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
