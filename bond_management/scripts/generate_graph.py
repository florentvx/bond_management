"""Synthetic par yield curve from the calibrated z-spread, over the real gilt market.

    python bond_management/scripts/generate_graph.py

Loads the latest feed and DMO inventory (load_data), calibrates the z-spread
curve on it (zspread.fit, the eight DEFAULT_TENORS pillars — residuals weighted
by a normal law around par, price_weight, so deep-discount gilts such as TG61
trade on tax rather than yield and barely move the curve; gilts maturing
within MIN_LIFE_DAYS, a month, are left out), then prices a grid of synthetic
par gilts under that curve: semi-annual coupon, settlement mid-period (one
coupon behind, so accrued is a quarter's coupon), and the coupon solving clean
price = 100 — that coupon is the synthetic par yield. The grid runs 0.25y to
50.25y in six-month steps, wider than the pillars, so the stretch below 0.5y
and above 50y is the curve's flat extrapolation and is drawn dashed.

Every priced conventional gilt joins the same picture as its semi-annual yield
to maturity against years to redemption — again dropping the ones maturing
within a month — coloured by the fit's residual z-spread: green is cheap
(trading through the curve), red is rich, and points the axis cannot reach
become edge triangles. The colour is a price residual against the fitted curve
on the gilt's own cashflows, while the position is a yield against the par
curve, so for low-coupon gilts the two can read opposite ways: the par yield
is coupon-weighted, their YTM is redemption-weighted. Relative value still
reads off the chart: a gilt above the par curve yields more than a synthetic
par bond of that maturity, one below it yields less.

Writes screens/par_yield_curve_YYYYMMDD.png (feed date) and shows the plot.
"""

import datetime as dt
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from bond_management import load_data
from bond_management.core.dates import as_naive, next_business_day
from bond_management.core.pricing import accrued, yield_from_price
from bond_management.core.zspread import (
    DEFAULT_TENORS,
    MIN_LIFE_DAYS,
    curve_price,
    fit,
)

YEAR = 365.25
SETTLE_LAG = 91  # days from settlement to the synthetic gilt's first coupon
GRID = range(1, 102)  # future coupons per synthetic gilt: 0.25y to 50.25y
SHORT_END = 0.5  # gilts maturing sooner are left out of the axis range
COLOUR_SPAN = 25.0  # residual colour scale floor, bp (saturation beyond it)
OUT_DIR = Path("screens")
CMAP = "RdYlGn"  # positive residual (cheap) reads green, negative (rich) red
FOOTNOTE = (
    "synthetic par gilt: semi-annual coupon, settles mid-period (accrued = 1/4 coupon), "
    "coupon solving clean price 100 under the curve\n"
    "gilt yield: semi-annual YTM from the feed's clean price at T+1; gilts maturing "
    "within 30 days of settlement left out\n"
    "colour = residual z-spread vs the fitted curve, bp (green cheap / red rich): a price "
    "measure on the gilt's own cashflows — a low-coupon gilt can sit above the line and\n"
    "still read red (the par curve is coupon-weighted, its YTM is redemption-weighted)"
)


def synthetic_schedule(coupons, settle):
    """Mid-period schedule: one coupon behind settlement, `coupons` ahead of it."""
    return tuple(
        [settle - dt.timedelta(days=SETTLE_LAG)]
        + [
            settle + dt.timedelta(days=round((k - 0.5) * YEAR / 2))
            for k in range(1, coupons + 1)
        ]
    )


def par_yield(coupons, settle, tenors, values):
    """(coupon_pct, maturity_years) of the gilt pricing at par under the curve.

    Clean price is affine in the coupon, so the two evaluations at 0% and 1%
    bracket the coupon solving clean price = 100 outright.
    """
    schedule = synthetic_schedule(coupons, settle)
    at_zero = curve_price(0.0, schedule, settle, tenors, values) - accrued(
        0.0, schedule, settle
    )
    at_one = curve_price(1.0, schedule, settle, tenors, values) - accrued(
        1.0, schedule, settle
    )
    return (100.0 - at_zero) / (at_one - at_zero), (schedule[-1] - settle).days / YEAR


def par_curve(settle, tenors, values):
    """Synthetic par yields across the maturity grid, as (yield_pct, years)."""
    rows = [par_yield(k, settle, tenors, values) for k in GRID]
    return pd.DataFrame(rows, columns=["yield_pct", "years"]).sort_values("years")


def gilt_yields(prices, bonds, settle, residuals):
    """Priced conventional gilts: tidm, years to redemption, yield and residual.

    Gilts maturing within MIN_LIFE_DAYS of settlement are left out, exactly as
    they are from the calibration itself.
    """
    inventory = {b.isin: b for b in bonds if b.market == "conventional"}
    rows, skipped, expiring = [], [], []
    for _, r in prices.iterrows():
        bond = inventory.get(r["isin"])
        if bond is None:
            continue
        schedule = bond.schedule(settle)
        if not schedule or schedule[-1] - settle < dt.timedelta(days=MIN_LIFE_DAYS):
            expiring.append(r["tidm"])
            continue
        ex = (
            dt.date.fromisoformat(str(bond.ex_dividend_date))
            if bond.ex_dividend_date
            else None
        )
        dirty = r["clean_price"] + accrued(r["coupon_pct"], schedule, settle, ex)
        try:
            yld = yield_from_price(r["coupon_pct"], schedule, settle, dirty, ex)
        except ValueError as e:
            skipped.append(f"{r['tidm']} ({e})")
            continue
        redemption = dt.date.fromisoformat(str(bond.redemption_date))
        rows.append(
            {
                "tidm": r["tidm"],
                "years": (redemption - settle).days / YEAR,
                "yield_pct": yld * 100,
                "residual_bp": residuals.get(r["isin"], np.nan),
            }
        )
    if expiring:
        print(
            f"left out of the picture: {', '.join(expiring)} "
            f"(maturing within {MIN_LIFE_DAYS} days of settlement)"
        )
    if skipped:
        print("could not price: " + "; ".join(skipped))
    return pd.DataFrame(rows)


def axis_range(par, gilts):
    """(ymin, ymax, xmax): the par curve plus every gilt outside the short end."""
    roomy = gilts[gilts["years"] >= SHORT_END]
    ymin = min(par["yield_pct"].min(), roomy["yield_pct"].min()) - 0.25
    ymax = max(par["yield_pct"].max(), roomy["yield_pct"].max()) + 0.25
    xmax = max(par["years"].max(), gilts["years"].max()) + 1.5
    return ymin, ymax, xmax


def plot(par, gilts, settle, feed_day, path):
    """Draw the par curve, the pillars and the market, save the figure, show it."""
    ymin, ymax, xmax = axis_range(par, gilts)
    inside = par[par["years"].between(min(DEFAULT_TENORS), max(DEFAULT_TENORS))]
    short = par[par["years"] <= inside["years"].min()]
    long = par[par["years"] >= inside["years"].max()]
    live = gilts[gilts["yield_pct"].between(ymin, ymax)]
    off = gilts[~gilts.index.isin(live.index)]

    fig, ax = plt.subplots(figsize=(13, 7))
    ax.set_xlim(0, xmax)
    ax.set_ylim(ymin, ymax)
    ax.axvspan(0, min(DEFAULT_TENORS), color="0.92", label="beyond the pillars (flat curve)")
    if max(DEFAULT_TENORS) < xmax:
        ax.axvspan(max(DEFAULT_TENORS), xmax, color="0.92")
    for i, pillar in enumerate(DEFAULT_TENORS):
        ax.axvline(
            pillar,
            color="0.6",
            ls=":",
            lw=0.9,
            zorder=1,
            label="calibration pillar" if i == 0 else None,
        )
    ax.plot(
        inside["years"],
        inside["yield_pct"],
        color="C0",
        lw=2.2,
        zorder=4,
        label="synthetic par yield (z-spread curve)",
    )
    ax.plot(
        short["years"],
        short["yield_pct"],
        color="C0",
        lw=2.2,
        ls="--",
        zorder=4,
        label="par yield beyond the pillars",
    )
    ax.plot(long["years"], long["yield_pct"], color="C0", lw=2.2, ls="--", zorder=4)

    span = max(COLOUR_SPAN, float(np.nanpercentile(gilts["residual_bp"].abs(), 90)))
    sc = ax.scatter(
        live["years"],
        live["yield_pct"],
        c=np.clip(live["residual_bp"], -span, span),
        cmap=CMAP,
        vmin=-span,
        vmax=span,
        s=42,
        edgecolors="0.25",
        linewidths=0.4,
        zorder=3,
        label="gilt yield (semi-annual YTM)",
    )
    fig.colorbar(
        sc, ax=ax, label="residual z-spread vs the curve, bp  (+ = cheap, - = rich)"
    )
    for row in off.itertuples():
        top = row.yield_pct > ymax
        y = ymax - 0.07 if top else ymin + 0.07
        ax.scatter(row.years, y, marker="^" if top else "v", color="0.35", s=48, zorder=3)
        ax.annotate(
            f"{row.tidm} {row.yield_pct:.1f}%",
            (row.years, y),
            textcoords="offset points",
            xytext=(7, -12 if top else 10),
            ha="left",
            fontsize=8,
            color="0.35",
        )
    valued = live[live["years"] >= min(DEFAULT_TENORS)]
    worst = valued.assign(abs_resid=valued["residual_bp"].abs()).nlargest(4, "abs_resid")
    for i, row in enumerate(worst.itertuples()):
        dx, dy = ((8, 7), (8, -13))[i % 2]
        ax.annotate(
            row.tidm,
            (row.years, row.yield_pct),
            textcoords="offset points",
            xytext=(dx, dy),
            fontsize=8,
            color="0.25",
        )

    ax.set_xlabel("Years to redemption")
    ax.set_ylabel("Yield, % (semi-annual)")
    ax.set_title(
        f"UK gilts: synthetic par yield curve vs the market — feed {feed_day:%d/%m/%Y} close, "
        f"settlement {settle:%d/%m/%Y}"
    )
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right", fontsize=9)
    fig.tight_layout(rect=(0, 0.115, 1, 1))
    fig.text(
        0.99,
        0.012,
        FOOTNOTE,
        ha="right",
        va="bottom",
        fontsize=8,
        color="0.4",
        linespacing=1.5,
    )
    fig.savefig(path, dpi=150)
    print(f"wrote {path}")
    plt.show()


def main():
    prices, bonds = load_data()
    feed_day = as_naive(prices["as_of"].iloc[0]).date()
    settle = next_business_day(feed_day)
    values, report = fit(prices, bonds)
    residuals = report.set_index("isin")["residual_bp"]
    par = par_curve(settle, DEFAULT_TENORS, values)
    gilts = gilt_yields(prices, bonds, settle, residuals)

    print(f"feed {feed_day:%d/%m/%Y} close, settlement T+1 = {settle:%d/%m/%Y}")
    print(
        "z-spread pillars: "
        + ", ".join(f"{t:g}y {v * 100:.2f}%" for t, v in zip(DEFAULT_TENORS, values))
    )
    print(
        "lightest weights: "
        + ", ".join(f"{r.tidm} {r.weight:.3g}" for r in report.nsmallest(4, "weight").itertuples())
    )
    at_pillars = np.interp(DEFAULT_TENORS, par["years"], par["yield_pct"])
    print(
        "par curve: "
        + ", ".join(f"{t:g}y {y:.2f}%" for t, y in zip(DEFAULT_TENORS, at_pillars))
    )
    print(
        f"{len(gilts)} gilts plotted, residual |median| "
        f"{report['residual_bp'].abs().median():.1f}bp, |max| "
        f"{report['residual_bp'].abs().max():.0f}bp"
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"par_yield_curve_{feed_day:%Y%m%d}.png"
    plot(par, gilts, settle, feed_day, path)


if __name__ == "__main__":
    main()
