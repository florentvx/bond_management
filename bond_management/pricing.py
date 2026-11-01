"""Gilt cashflows and analytics: price, yield, accrued, duration, convexity.

    sched = bond.schedule(settlement)
    y = yield_from_price(coupon_pct, sched, settlement, dirty, bond.ex_dividend_date)

Yields are decimals (0.05 = 5%), prices per £100 nominal. Two compounding
conventions: "semi" (the default) is the UK market/DMO standard — ICMA
actual/actual with semi-annual coupons — while "annual" (annual compounding,
actual/365.25) reproduces the giltsyield.com "Gross yield" column; the site's
"DMO yield" column follows the semi convention. Settlement is supplied by the
caller (the price feed's timestamp implies T+1 settlement). Bond.schedule()
anchors a new issue (issued after the last scheduled coupon) at its first
issue date, which stands in for the pre-issue coupon dates. A gilt trades
ex-dividend between the D1A ex_dividend_date and the coupon that ex-date
detaches: settling inside that window drops that coupon and makes accrued
interest zero, but the redemption amount is still received — at its own date
when the dropped coupon is the final one. Once that coupon is paid the gilt
trades cum again, even though the stored ex-date — point-in-time as of the
inventory — sits in the past.

Conventional gilts only — index-linked uplift is not modelled.
"""

import calendar
import datetime as dt
import re

__all__ = [
    "accrued",
    "cashflows",
    "convexity",
    "coupon_schedule",
    "modified_duration",
    "parse_pattern",
    "price",
    "yield_from_price",
]

YEAR = 365.25
NOMINAL = 100.0
PATTERN = r"(\d{1,2}) (\w{3})/(\w{3})"
FIRST_PERIOD = 182  # fallback when no earlier coupon anchors the schedule
BRACKET = 0.9  # yields are bisected within [-BRACKET, BRACKET]
TOL = 1e-12


def parse_pattern(dividend_dates):
    """'22 Apr/Oct' -> (22, 4, 10): the coupon day and the two coupon months."""
    m = re.fullmatch(PATTERN, str(dividend_dates).strip())
    if not m:
        raise ValueError(f"unrecognised dividend dates: {dividend_dates!r}")
    day = int(m.group(1))
    try:
        months = [dt.datetime.strptime(x, "%b").month for x in m.groups()[1:]]
    except ValueError:  # e.g. a non-English month abbreviation
        raise ValueError(f"unrecognised dividend dates: {dividend_dates!r}") from None
    return day, months[0], months[1]


def coupon_schedule(dividend_dates, redemption_date, since):
    """Coupon dates up to and including redemption, going back from `since`.

    Clamps the coupon day to the month's length (a synthetic '31 Aug/Feb'
    pays 28/29 Feb); the early dates only exist so the coupon period around
    `since` is always anchored.
    """
    day, m1, m2 = parse_pattern(dividend_dates)
    dates = set()
    for year in range(since.year - 1, redemption_date.year + 1):
        for month in (m1, m2):
            d = dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))
            if d <= redemption_date:
                dates.add(d)
    return sorted(dates)


def _trades_ex(schedule, settlement, ex_dividend_date):
    """Whether the gilt trades ex-dividend at settlement.

    True from the ex-date up to (not including) the coupon that ex-date
    detaches: after that payment the gilt trades cum again, so a settlement
    past it ignores an ex-date already in the past.
    """
    if ex_dividend_date is None or settlement < ex_dividend_date:
        return False
    coupon = next((d for d in schedule if d > ex_dividend_date), None)
    return coupon is not None and settlement < coupon


def cashflows(coupon_pct, schedule, settlement, ex_dividend_date=None):
    """[(date, amount)] the holder receives per £100 nominal.

    Coupons dated after settlement, with the redemption amount merged into
    the final one; the coupon the ex-date detaches is dropped while the gilt
    trades ex-dividend (settlement between ex_dividend_date and that coupon).
    """
    flows = [(d, coupon_pct / 2) for d in schedule if d > settlement]
    if _trades_ex(schedule, settlement, ex_dividend_date) and flows:
        flows[0] = (flows[0][0], flows[0][1] - coupon_pct / 2)
    if schedule and settlement < schedule[-1]:
        if flows and flows[-1][0] == schedule[-1]:
            flows[-1] = (flows[-1][0], flows[-1][1] + NOMINAL)
        else:
            flows.append((schedule[-1], NOMINAL))
    return [(d, a) for d, a in flows if a]


def accrued(coupon_pct, schedule, settlement, ex_dividend_date=None):
    """Accrued interest per £100 nominal (ICMA actual/actual, 0 when ex-dividend)."""
    if _trades_ex(schedule, settlement, ex_dividend_date):
        return 0.0
    upcoming = [d for d in schedule if d > settlement]
    prev = [d for d in schedule if d <= settlement]
    if not upcoming or not prev:
        return 0.0
    period = (upcoming[0] - prev[-1]).days
    return coupon_pct / 2 * (1 - (upcoming[0] - settlement).days / period)


def _anchor(schedule, settlement, ex_dividend_date):
    """(t0, steps0): the current period's remaining fraction, plus one coupon step while ex-dividend.

    The extra step keeps the surviving cashflows on their original steps
    after the ex-dividend coupon is dropped — except in the final coupon
    period, where that coupon is the redemption's own and the redemption
    date stays where it is.
    """
    upcoming = [d for d in schedule if d > settlement]
    prev = [d for d in schedule if d <= settlement]
    if not upcoming:
        return 0.0, 0
    period = (upcoming[0] - prev[-1]).days if prev else FIRST_PERIOD
    t0 = (upcoming[0] - settlement).days / period
    steps0 = 1 if _trades_ex(schedule, settlement, ex_dividend_date) and len(upcoming) > 1 else 0
    return t0, steps0


def _discounted(coupon_pct, schedule, settlement, y, ex_dividend_date, compound):
    """(P, dP/dy, d²P/dy²) of the cashflows at yield y."""
    flows = cashflows(coupon_pct, schedule, settlement, ex_dividend_date)
    if not flows:
        return 0.0, 0.0, 0.0
    if compound == "annual":
        steps = [(d - settlement).days / YEAR for d, _ in flows]
        base, deriv = 1 + y, 1.0
    elif compound == "semi":
        t0, steps0 = _anchor(schedule, settlement, ex_dividend_date)
        steps = [t0 + steps0 + i for i, _ in enumerate(flows)]
        base, deriv = 1 + y / 2, 0.5
    else:
        raise ValueError(f"unknown compound: {compound!r}")
    p = d1 = d2 = 0.0
    for (_, amount), n in zip(flows, steps):
        p += amount * base**-n
        d1 += amount * (-n * deriv) * base ** -(n + 1)
        d2 += amount * (n * (n + 1) * deriv**2) * base ** -(n + 2)
    return p, d1, d2


def price(coupon_pct, schedule, settlement, y, ex_dividend_date=None, compound="semi"):
    """Dirty (clean + accrued) price per £100 nominal at yield y."""
    return _discounted(coupon_pct, schedule, settlement, y, ex_dividend_date, compound)[0]


def yield_from_price(
    coupon_pct, schedule, settlement, dirty, ex_dividend_date=None, compound="semi"
):
    """The yield (decimal) at which price() equals dirty, found by bisection."""
    lo, hi = -BRACKET, BRACKET

    def pv(y):
        return price(coupon_pct, schedule, settlement, y, ex_dividend_date, compound)

    if not pv(hi) <= dirty <= pv(lo):
        raise ValueError(f"dirty price {dirty} is outside this gilt's price range")
    while hi - lo > TOL:
        mid = (lo + hi) / 2
        if pv(mid) > dirty:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def modified_duration(
    coupon_pct, schedule, settlement, y, ex_dividend_date=None, compound="semi"
):
    """-(1/P) dP/dy, the price risk per unit of yield (semi: Macaulay/(1+y/2))."""
    p, d1, _ = _discounted(coupon_pct, schedule, settlement, y, ex_dividend_date, compound)
    if not p:
        raise ValueError("no cashflows to price")
    return -d1 / p


def convexity(coupon_pct, schedule, settlement, y, ex_dividend_date=None, compound="semi"):
    """(1/P) d²P/dy², the curvature of the price/yield relationship."""
    p, _, d2 = _discounted(coupon_pct, schedule, settlement, y, ex_dividend_date, compound)
    if not p:
        raise ValueError("no cashflows to price")
    return d2 / p
