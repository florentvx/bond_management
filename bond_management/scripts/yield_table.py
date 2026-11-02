import datetime as dt

import pandas as pd

from bond_management import load_data
from bond_management.core.dates import as_naive, next_business_day
from bond_management.core.pricing import accrued, yield_from_price


def main():
    prices, bonds = load_data()
    inv = {b.isin: b for b in bonds if b.market == "conventional"}

    feed_day = as_naive(prices["as_of"].iloc[0]).date()
    settle = next_business_day(feed_day)
    print(f"feed {feed_day:%d/%m/%Y} close, settlement T+1 = {settle:%d/%m/%Y} ({settle:%A})\n")

    rows = []
    for _, r in prices.iterrows():
        b = inv[r["isin"]]
        sched = b.schedule(settle)
        ex = dt.date.fromisoformat(str(b.ex_dividend_date)) if b.ex_dividend_date else None
        dirty = r["clean_price"] + accrued(r["coupon_pct"], sched, settle, ex)
        y = yield_from_price(r["coupon_pct"], sched, settle, dirty, ex)
        rows.append(
            (
                r["name"],
                r["tidm"],
                r["matures"],
                r["coupon_pct"],
                r["clean_price"],
                r["dmo_yield_pct"],
                y * 100,
                (y - r["dmo_yield_pct"] / 100) * 10_000,
            )
        )

    df = pd.DataFrame(
        rows,
        columns=["name", "tidm", "matures", "coupon_%", "clean_price", "dmo_yld_%", "recalc_yld_%", "diff_bp"],
    ).sort_values("matures").reset_index(drop=True)

    print(df.to_string(index=False, float_format=lambda x: f"{x:7.3f}"))
    d = df["diff_bp"].abs()
    print(
        f"\n{len(df)} conventional gilts | |diff| median {d.median():.2f}bp, "
        f"max {d.max():.2f}bp, within 0.5bp {(d <= 0.5).sum()}, within 2bp {(d <= 2).sum()}"
    )
    print(
        "worst: "
        + ", ".join(
            f"{r.name} {r.matures} ({r.diff_bp:+.1f}bp)"
            for r in df.sort_values("diff_bp", key=abs, ascending=False).head(4).itertuples()
        )
    )
    df.to_clipboard(index=False, float_format=lambda x: f"{x:7.3f}")

    print("END")


if __name__ == "__main__":
    main()
