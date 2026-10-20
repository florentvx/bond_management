"""z-spread curve calibrated on the conventional gilt population.

    values, report = fit(prices, bonds)

Benchmark z-spreads sit at DEFAULT_TENORS (1, 3, 5, 10, 20, 30 and 50
years) and interpolate linearly between the nodes, flat outside the ends.
Each cashflow is discounted at the curve with continuous compounding,
exp(-z(T) * (T - settlement)) over (date - settlement).days / 365.25 years,
reusing pricing.cashflows (ex-dividend handling included), so the curve's
view of a gilt is a dirty price per 100 nominal.

The residual z-spread of a gilt is the flat shift added to the whole curve
that reprices it exactly; calibrate() picks the nodes minimising the sum of
squared residuals over the priced population (Gauss-Newton with a
finite-difference Jacobian and Levenberg damping, numpy only). Everything is
in decimals - the report quotes residuals in bp - and settlement is the
caller's: fit() rolls the feed's date to T+1. Conventional gilts only.
"""

import datetime as dt
from functools import lru_cache

import numpy as np
import pandas as pd

from .dates import as_naive, next_business_day
from .pricing import BRACKET, TOL, YEAR, accrued, cashflows

__all__ = [
    "DEFAULT_TENORS",
    "calibrate",
    "curve_price",
    "fit",
    "residual_zspread",
    "z_at",
]

DEFAULT_TENORS = (1.0, 3.0, 5.0, 10.0, 20.0, 30.0, 50.0)
STEP = 1e-6  # finite-difference increment behind the Jacobian
STEP_TOL = 1e-10  # a node this settled wants to move no further
STALL_TOL = 1e-10  # relative gain in the sum of squares that still counts as progress
MAX_STEPS = 50
DAMPING = 1e-3  # Levenberg start: x10 on a rejected step, /10 on an accepted one
DAMPING_FLOOR = 1e-12
DAMPING_CEILING = 1e6


def z_at(tenors, values, t):
    """z at the year-fraction t: linear between the nodes, flat outside them."""
    return np.interp(t, tenors, values)


@lru_cache(maxsize=1024)
def _flows(coupon_pct, schedule, settlement, ex_dividend_date):
    """(times, amounts) of a gilt's cashflows, cached because bisection reprices them."""
    cash = cashflows(coupon_pct, schedule, settlement, ex_dividend_date)
    return (
        np.array([(d - settlement).days / YEAR for d, _ in cash]),
        np.array([a for _, a in cash]),
    )


def curve_price(
    coupon_pct, schedule, settlement, tenors, values, ex_dividend_date=None, residual=0.0
):
    """What the curve pays for the gilt: cashflows discounted at z plus a flat residual."""
    times, amounts = _flows(coupon_pct, tuple(schedule), settlement, ex_dividend_date)
    return float(
        np.sum(amounts * np.exp(-(z_at(tenors, values, times) + residual) * times))
    )


def residual_zspread(
    coupon_pct, schedule, settlement, dirty, tenors, values, ex_dividend_date=None
):
    """The flat shift r with curve_price(..., residual=r) == dirty, found by bisection.

    Price falls as r rises, so the same bracket and tolerance as
    yield_from_price bisect it.
    """

    def pv(r):
        return curve_price(
            coupon_pct, schedule, settlement, tenors, values, ex_dividend_date, r
        )

    lo, hi = -BRACKET, BRACKET
    if not pv(hi) <= dirty <= pv(lo):
        raise ValueError(f"dirty price {dirty} is outside this gilt's price range")
    while hi - lo > TOL:
        mid = (lo + hi) / 2
        if pv(mid) > dirty:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _residuals(settlement, rows, tenors, values):
    """One residual z-spread per row, at the curve `values`."""
    return np.array(
        [
            residual_zspread(
                row["coupon_pct"],
                row["schedule"],
                settlement,
                row["dirty"],
                tenors,
                values,
                row["ex_dividend_date"],
            )
            for row in rows
        ]
    )


def _jacobian(settlement, rows, tenors, values, residuals):
    """Forward differences of the residuals: d residual_j / d node_k."""
    jacobian = np.empty((len(rows), len(tenors)))
    for k in range(len(tenors)):
        bumped = values.copy()
        bumped[k] += STEP
        jacobian[:, k] = (_residuals(settlement, rows, tenors, bumped) - residuals) / STEP
    return jacobian


def calibrate(settlement, rows, tenors=DEFAULT_TENORS):
    """(values, residuals): the nodes minimising the squared residuals over the rows.

    Each row is a dict with coupon_pct, schedule, dirty (the price to hit),
    ex_dividend_date and dmo_yield (decimal, whose median sets the flat start
    2 * ln(1 + y / 2)). Gauss-Newton from that flat curve, Levenberg-damped;
    it stops once no node wants a STEP_TOL move or the sum of squares stalls.
    """
    if not rows:
        raise ValueError("nothing to calibrate")
    start = 2 * np.log(1 + np.median([row["dmo_yield"] for row in rows]) / 2)
    values = np.full(len(tenors), start)
    residuals = _residuals(settlement, rows, tenors, values)
    sse = float(residuals @ residuals)
    damping = DAMPING
    for _ in range(MAX_STEPS):
        jacobian = _jacobian(settlement, rows, tenors, values, residuals)
        lhs = jacobian.T @ jacobian + damping * np.eye(len(tenors))
        delta = np.linalg.solve(lhs, -jacobian.T @ residuals)
        if np.abs(delta).max() < STEP_TOL:
            break
        trial = _residuals(settlement, rows, tenors, values + delta)
        trial_sse = float(trial @ trial)
        if trial_sse >= sse:
            damping *= 10
            if damping > DAMPING_CEILING:
                break
            continue
        gain = sse - trial_sse
        values, residuals, sse = values + delta, trial, trial_sse
        damping = max(damping / 10, DAMPING_FLOOR)
        if gain <= STALL_TOL * sse:
            break
    return values, residuals


def fit(prices_df, bonds, tenors=DEFAULT_TENORS):
    """(values, report): the curve fitted to the priced conventional gilts.

    Joins the feed to `bonds` on isin, prices each match at T+1 settlement
    from the feed's timestamp (clean plus the engine's accrued, the dirty_of
    convention of the pricing tests) and runs calibrate. The report has one
    row per fitted gilt: isin, tidm, name, section, residual_bp,
    model_price (the curve's dirty price) and market_price.
    """
    conventional = {b.isin: b for b in bonds if b.market == "conventional"}
    settle = next_business_day(as_naive(prices_df["as_of"].iloc[0]).date())
    rows, missing = [], []
    for _, r in prices_df.iterrows():
        bond = conventional.get(r["isin"])
        if bond is None:
            missing.append(r["isin"])
            continue
        schedule = bond.schedule(settle)
        ex = (
            dt.date.fromisoformat(str(bond.ex_dividend_date))
            if bond.ex_dividend_date
            else None
        )
        rows.append(
            {
                "isin": r["isin"],
                "tidm": r["tidm"],
                "name": r["name"],
                "section": bond.section,
                "coupon_pct": r["coupon_pct"],
                "schedule": schedule,
                "ex_dividend_date": ex,
                "dirty": r["clean_price"]
                + accrued(r["coupon_pct"], schedule, settle, ex),
                "dmo_yield": r["dmo_yield_pct"] / 100,
            }
        )
    if missing:
        print(f"no conventional inventory match for {', '.join(sorted(set(missing)))}")
    if not rows:
        raise ValueError("no priced conventional gilts to fit")
    values, residuals = calibrate(settle, rows, tenors)
    report = pd.DataFrame(
        {
            "isin": [row["isin"] for row in rows],
            "tidm": [row["tidm"] for row in rows],
            "name": [row["name"] for row in rows],
            "section": [row["section"] for row in rows],
            "residual_bp": residuals * 10_000,
            "model_price": [
                curve_price(
                    row["coupon_pct"],
                    row["schedule"],
                    settle,
                    tenors,
                    values,
                    row["ex_dividend_date"],
                )
                for row in rows
            ],
            "market_price": [row["dirty"] for row in rows],
        }
    )
    return values, report
