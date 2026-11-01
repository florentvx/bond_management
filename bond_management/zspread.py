"""z-spread curve calibrated on the conventional gilt population.

    values, report = fit(prices, bonds)

Benchmark z-spreads sit at DEFAULT_TENORS (0.5, 1, 3, 5, 10, 20, 30 and 50
years) and interpolate linearly between the nodes, flat outside the ends.
Gilts maturing within MIN_LIFE_DAYS of settlement are left out: a bond a few
days from redemption says nothing useful about the curve. Each cashflow is
discounted at the curve with continuous compounding,
exp(-z(T) * (T - settlement)) over (date - settlement).days / 365.25 years,
reusing pricing.cashflows (ex-dividend handling included), so the curve's
view of a gilt is a dirty price per 100 nominal.

The residual z-spread of a gilt is the flat shift added to the whole curve
that reprices it exactly; calibrate() picks the nodes minimising the weighted
sum of squared residuals over the priced population (Gauss-Newton with a
finite-difference Jacobian and Levenberg damping, numpy only). Each gilt's
weight is a normal law in its dirty price's distance from par (price_weight,
sigma WEIGHT_SIGMA), so deep-discount low-coupon gilts - which trade rich on
their tax treatment rather than their yield - barely move the curve. The
report quotes the raw residuals in bp next to the weight. Everything else is
in decimals and settlement is the caller's: fit() rolls the feed's date to
T+1. Conventional gilts only.
"""

import datetime as dt
from functools import lru_cache

import numpy as np
import pandas as pd

from .dates import as_naive, next_business_day
from .pricing import BRACKET, NOMINAL, TOL, YEAR, accrued, cashflows

__all__ = [
    "DEFAULT_TENORS",
    "MIN_LIFE_DAYS",
    "WEIGHT_SIGMA",
    "calibrate",
    "curve_price",
    "fit",
    "price_weight",
    "residual_zspread",
    "z_at",
]

DEFAULT_TENORS = (0.5, 1.0, 3.0, 5.0, 10.0, 20.0, 30.0, 50.0)
MIN_LIFE_DAYS = 30  # gilts maturing sooner are left out of the fit (and the graph)
MIN_LIFE = dt.timedelta(days=MIN_LIFE_DAYS)
WEIGHT_SIGMA = 20.0  # price points from par at which a gilt's weight falls to exp(-1/2)
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


def price_weight(dirty):
    """How much a gilt counts in the fit: a normal law around par (dirty = 100)."""
    return float(np.exp(-0.5 * ((dirty - NOMINAL) / WEIGHT_SIGMA) ** 2))


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
    """(values, residuals): the nodes minimising the weighted squared residuals.

    Each row is a dict with coupon_pct, schedule, dirty (the price to hit),
    ex_dividend_date and dmo_yield (decimal, whose median sets the flat start
    2 * ln(1 + y / 2)) plus an optional weight (default 1) saying how much its
    residual counts - fit() fills it with price_weight. Gauss-Newton from that
    flat curve, Levenberg-damped on the weighted sum of squares; it stops once
    no node wants a STEP_TOL move or the sum of squares stalls. The residuals
    handed back are the raw z-spreads the report quotes, not the weighted ones.
    """
    if not rows:
        raise ValueError("nothing to calibrate")
    weights = np.array([row.get("weight", 1.0) for row in rows])
    start = 2 * np.log(1 + np.median([row["dmo_yield"] for row in rows]) / 2)
    values = np.full(len(tenors), start)
    residuals = _residuals(settlement, rows, tenors, values)
    sse = float((weights * residuals) @ (weights * residuals))
    damping = DAMPING
    for _ in range(MAX_STEPS):
        jacobian = weights[:, None] * _jacobian(
            settlement, rows, tenors, values, residuals
        )
        lhs = jacobian.T @ jacobian + damping * np.eye(len(tenors))
        delta = np.linalg.solve(lhs, -jacobian.T @ (weights * residuals))
        if np.abs(delta).max() < STEP_TOL:
            break
        trial = _residuals(settlement, rows, tenors, values + delta)
        trial_sse = float((weights * trial) @ (weights * trial))
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
    convention of the pricing tests) and runs calibrate. Gilts maturing within
    MIN_LIFE_DAYS of settlement are left out; every fitted gilt is weighted by
    price_weight, so deep-discount gilts (tax-skewed, far from par) pull the
    curve little. The report has one row per fitted gilt: isin, tidm, name,
    section, weight, residual_bp, model_price (the curve's dirty price) and
    market_price.
    """
    conventional = {b.isin: b for b in bonds if b.market == "conventional"}
    settle = next_business_day(as_naive(prices_df["as_of"].iloc[0]).date())
    rows, missing, expiring = [], [], []
    for _, r in prices_df.iterrows():
        bond = conventional.get(r["isin"])
        if bond is None:
            missing.append(r["isin"])
            continue
        schedule = bond.schedule(settle)
        if not schedule or schedule[-1] - settle < MIN_LIFE:
            expiring.append(r["tidm"])
            continue
        ex = (
            dt.date.fromisoformat(str(bond.ex_dividend_date))
            if bond.ex_dividend_date
            else None
        )
        dirty = r["clean_price"] + accrued(r["coupon_pct"], schedule, settle, ex)
        rows.append(
            {
                "isin": r["isin"],
                "tidm": r["tidm"],
                "name": r["name"],
                "section": bond.section,
                "coupon_pct": r["coupon_pct"],
                "schedule": schedule,
                "ex_dividend_date": ex,
                "dirty": dirty,
                "dmo_yield": r["dmo_yield_pct"] / 100,
                "weight": price_weight(dirty),
            }
        )
    if missing:
        print(f"no conventional inventory match for {', '.join(sorted(set(missing)))}")
    if expiring:
        print(
            f"left out of the fit: {', '.join(expiring)} "
            f"(maturing within {MIN_LIFE_DAYS} days of settlement)"
        )
    if not rows:
        raise ValueError("no priced conventional gilts to fit")
    values, residuals = calibrate(settle, rows, tenors)
    report = pd.DataFrame(
        {
            "isin": [row["isin"] for row in rows],
            "tidm": [row["tidm"] for row in rows],
            "name": [row["name"] for row in rows],
            "section": [row["section"] for row in rows],
            "weight": [row["weight"] for row in rows],
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
