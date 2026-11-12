"""Fetch an IBKR Flex Query statement and maintain the trade history screen.

    python -m bond_management ibkr

Credentials come from the environment (`IBKR_TOKEN`, `IBKR_QUERY_ID`), falling back
to the gitignored `secrets/ibkr.env` key=value file at the repo root.

Every run reconciles the statement's trades into screens/ibkr_trades.csv: rows
already in the file are matched on the trade's identity fields (account,
date-time, symbol, side, quantity, price), disagreeing values are reported and
refreshed from the broker, trades the statement no longer carries (short query
window) are kept, and unseen trades are appended.

It then prints the portfolio decomposed into zero-coupon bonds and copies it
to the clipboard, never to screens/: every coupon and the redemption of every
holding becomes a zero coupon — one row per payment date with the amount due,
its pv on the fitted curve alone (z-spread 0), its market pv with the gilt's
z-spread, and that z-spread. Flows sharing a payment date are merged: amounts,
curve pvs and market pvs are summed, then a single z-spread is bisected so the
merged amount, discounted at the fitted curve, reproduces the summed pv.
"""

import datetime as dt
import os
import time
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from bond_management import load_data
from bond_management.core.dates import as_naive, next_business_day
from bond_management.core.pricing import BRACKET, TOL, YEAR, accrued, cashflows
from bond_management.core.zspread import DEFAULT_TENORS, fit, residual_zspread, z_at

BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
SECRETS_FILE = Path(__file__).resolve().parents[2] / "secrets" / "ibkr.env"
OUT_FILE = Path("screens") / "ibkr_trades.csv"
KEY_FIELDS = ["accountId", "dateTime", "symbol", "buySell", "quantity", "tradePrice"]
TABLE_COLUMNS = ["payment_date", "amount", "curve_pv", "pv", "zspread_bp"]
POLL_ATTEMPTS = 10
POLL_SECONDS = 5


def load_secrets():
    """Return (token, query_id) from the environment, else from SECRETS_FILE."""
    values = {}
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
    token = os.environ.get("IBKR_TOKEN") or values.get("IBKR_TOKEN")
    query_id = os.environ.get("IBKR_QUERY_ID") or values.get("IBKR_QUERY_ID")
    if not token or not query_id:
        raise RuntimeError(f"IBKR_TOKEN / IBKR_QUERY_ID not set (env or {SECRETS_FILE})")
    return token, query_id


def fetch_statement(token, query_id):
    """Request a Flex report by query id and poll until it is ready; return the XML."""
    r = requests.get(
        f"{BASE}/SendRequest", params={"t": token, "q": query_id, "v": 3}, timeout=30
    )
    r.raise_for_status()
    root = ET.fromstring(r.text)
    if root.findtext("Status") != "Success":
        raise RuntimeError(f"SendRequest failed: {r.text.strip()}")
    ref_code = root.findtext("ReferenceCode")

    # The report takes a few seconds to generate
    for _ in range(POLL_ATTEMPTS):
        time.sleep(POLL_SECONDS)
        r = requests.get(
            f"{BASE}/GetStatement", params={"t": token, "q": ref_code, "v": 3}, timeout=30
        )
        r.raise_for_status()
        if "<FlexStatements" in r.text or "," in r.text[:200]:
            return r.text
    raise RuntimeError(f"statement not ready after {POLL_ATTEMPTS} tries: {r.text.strip()}")


def trade_records(root):
    """The statement's Trade elements as dicts, checked for the identity fields."""
    records = [dict(t.attrib) for t in root.iter("Trade")]
    if not records:
        raise RuntimeError("statement contains no Trade elements")
    for r in records:
        missing = [f for f in KEY_FIELDS if f not in r]
        if missing:
            raise RuntimeError(f"trade {r.get('symbol', '?')} lacks identity fields {missing}")
    return records


def update_history(trades, path=OUT_FILE):
    """Merge `trades` into the CSV history at `path`, oldest first.

    Rows already there are matched on KEY_FIELDS: values that disagree with the
    broker are printed and replaced, trades missing from the statement are kept
    (it only covers its configured query window), unseen trades are appended.
    Columns absent from an existing file are left blank on its old rows.
    Returns (checked, refreshed, added).
    """
    path = Path(path)
    fresh = pd.DataFrame(trades).fillna("").astype(str)
    old = (
        pd.read_csv(path, dtype=str, keep_default_na=False)
        if path.exists()
        else pd.DataFrame(columns=KEY_FIELDS)
    )
    file_cols = list(old.columns)
    cols = KEY_FIELDS + sorted((set(fresh.columns) | set(old.columns)) - set(KEY_FIELDS))
    fresh = fresh.reindex(columns=cols).fillna("")
    old = old.reindex(columns=cols).fillna("")

    rows = old.to_dict("records")
    index = {tuple(r[f] for f in KEY_FIELDS): i for i, r in enumerate(rows)}
    comparable = [c for c in file_cols if c not in KEY_FIELDS]
    checked = refreshed = added = 0
    for rec in fresh.to_dict("records"):
        key = tuple(rec[f] for f in KEY_FIELDS)
        if key not in index:
            rows.append(rec)
            index[key] = len(rows) - 1
            added += 1
            continue
        checked += 1
        stored = rows[index[key]]
        diffs = [(c, stored[c], rec[c]) for c in comparable if stored[c] != rec[c]]
        if not diffs:
            continue
        label = f"{rec['symbol']} {rec['dateTime']}"
        print(f"  {label}: " + ", ".join(f"{c} {a!r} -> {b!r}" for c, a, b in diffs))
        rows[index[key]] = rec
        refreshed += 1

    df = pd.DataFrame(rows, columns=cols)
    sort_on = [c for c in ("tradeDate", "dateTime") if c in cols]
    if sort_on:
        df = df.sort_values(sort_on, kind="stable")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return checked, refreshed, added


def net_positions(root):
    """{symbol: nominal} the statement's trades leave net long: buys in, sells out."""
    nets = {}
    for t in root.iter("Trade"):
        symbol = t.attrib.get("symbol", "")
        side = -1 if t.attrib.get("buySell", "").upper().startswith("S") else 1
        nets[symbol] = nets.get(symbol, 0.0) + side * float(t.attrib.get("quantity", 0))
    return nets


def parse_symbol(symbol):
    """('UKT 4 1/2 06/07/28') -> (coupon_pct 4.5, date(2028, 6, 7)).

    IBKR writes the coupon as whole words and fractions between 'UKT' and the
    US MM/DD/YY redemption date ('4 1/2' is 4.5%); raises ValueError on
    anything else.
    """
    parts = symbol.split()
    month, day, year = (int(x) for x in parts[-1].split("/"))
    coupon = sum((Fraction(p) for p in parts[1:-1]), Fraction(0))
    return float(coupon), dt.date(2000 + year, month, day)


def merged_zspread(face, pv, payment_date, settle, tenors, values):
    """The flat shift z with `face` discounted at the curve for payment_date == pv.

    Same bisection as residual_zspread: the discount falls as z rises, so the
    root sits within [-BRACKET, BRACKET] of the curve.
    """
    t = (payment_date - settle).days / YEAR

    def model(z):
        return face * np.exp(-(z_at(tenors, values, t) + z) * t)

    lo, hi = -BRACKET, BRACKET
    if not model(hi) <= pv <= model(lo):
        raise ValueError(f"pv {pv} is outside the price range of {face} face")
    while hi - lo > TOL:
        mid = (lo + hi) / 2
        if model(mid) > pv:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def zero_coupon_table(root, prices, bonds, settle):
    """The portfolio decomposed into zero-coupon bonds, one row per payment date.

    Net-long holdings are matched to the priced feed on (redemption date,
    coupon) parsed from the IBKR symbol, then split into their cashflows —
    every coupon and the redemption, pricing.cashflows' ex-dividend handling
    included. Each flow is a zero coupon of `amount` GBP due on its date: it
    is worth `curve_pv` on the fitted curve alone (z-spread 0) and `pv` today
    — the gilt's residual z-spread against that curve applied to the flow.
    Flows sharing a payment date — coupons of different bonds, or a coupon on
    another bond's redemption — are merged: amounts, curve pvs and market pvs
    summed, then one z-spread bisected so the merged amount discounted at the
    curve reproduces the summed pv. Unmatched or unparsable symbols are
    printed and skipped.
    """
    values, _ = fit(prices, bonds)
    tenors = DEFAULT_TENORS
    inventory = {b.isin: b for b in bonds if b.market == "conventional"}
    feed = {}
    for _, r in prices.iterrows():
        bond = inventory.get(r["isin"])
        if bond is None:
            continue
        redeem = dt.date.fromisoformat(str(bond.redemption_date))
        feed[(redeem, round(float(r["coupon_pct"]), 6))] = (r, bond)

    nets = net_positions(root)
    flat = sorted(s for s, q in nets.items() if q <= 0)
    if flat:
        print("not net long, skipped: " + ", ".join(flat))

    grouped, skipped = {}, []
    for symbol, face in sorted(nets.items()):
        if face <= 0:
            continue
        try:
            coupon, redeem = parse_symbol(symbol)
        except (ValueError, IndexError, ZeroDivisionError):
            skipped.append(f"{symbol} (unparsable symbol)")
            continue
        hit = feed.get((redeem, round(coupon, 6)))
        if hit is None:
            skipped.append(f"{symbol} (no match in the price feed)")
            continue
        row, bond = hit
        if redeem <= settle:
            skipped.append(f"{symbol} (redeemed on or before {settle:%d/%m/%Y})")
            continue
        schedule = bond.schedule(settle)
        ex = (
            dt.date.fromisoformat(str(bond.ex_dividend_date))
            if bond.ex_dividend_date
            else None
        )
        dirty = row["clean_price"] + accrued(coupon, schedule, settle, ex)
        try:
            z = residual_zspread(coupon, schedule, settle, dirty, tenors, values, ex)
        except ValueError as e:
            skipped.append(f"{symbol} ({e})")
            continue
        for date, per_100 in cashflows(coupon, schedule, settle, ex):
            t = (date - settle).days / YEAR
            due = face * per_100 / 100
            curve = due * float(np.exp(-z_at(tenors, values, t) * t))
            pv = curve * float(np.exp(-z * t))
            amount, curve_total, pv_total = grouped.get(date, (0.0, 0.0, 0.0))
            grouped[date] = (amount + due, curve_total + curve, pv_total + pv)
    if skipped:
        print("skipped: " + "; ".join(skipped))

    rows = []
    for date in sorted(grouped):
        amount, curve_pv, pv = grouped[date]
        try:
            z = merged_zspread(amount, pv, date, settle, tenors, values)
        except ValueError as e:
            print(f"{date:%d/%m/%Y} payment skipped: {e}")
            continue
        rows.append(
            {
                "payment_date": date,
                "amount": amount,
                "curve_pv": curve_pv,
                "pv": pv,
                "zspread_bp": z * 10_000,
            }
        )
    return pd.DataFrame(rows, columns=TABLE_COLUMNS)


def show(table, feed_day, settle):
    """Print the zero-coupon table and copy it to the clipboard (never to screens/)."""
    if table.empty:
        print("\nno net-long holding matched the price feed")
        return
    print(
        f"\nportfolio as zero-coupon bonds (coupons + redemption) — "
        f"feed {feed_day:%d/%m/%Y} close, settlement {settle:%d/%m/%Y}:"
    )
    print(table.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    table.to_clipboard(index=False, float_format=lambda x: f"{x:,.2f}")
    print("copied to the clipboard")


def main():
    token, query_id = load_secrets()
    root = ET.fromstring(fetch_statement(token, query_id))

    for tag in ["OpenPosition", "Trade", "CashTransaction"]:
        if root.find(f".//{tag}") is None:
            print(f"Tag {tag} not found in XML response")
    positions = pd.DataFrame([p.attrib for p in root.iter("OpenPosition")])
    trades = pd.DataFrame([t.attrib for t in root.iter("Trade")])
    transactions = pd.DataFrame([t.attrib for t in root.iter("CashTransaction")])
    print(positions.head())
    print(trades.head())
    print(transactions.head())

    checked, refreshed, added = update_history(trade_records(root))
    print(
        f"\n{OUT_FILE}: {checked} trades checked, {refreshed} updated, "
        f"{added} new, {checked - refreshed} unchanged"
    )

    prices, bonds = load_data()
    feed_day = as_naive(prices["as_of"].iloc[0]).date()
    settle = next_business_day(feed_day)
    show(zero_coupon_table(root, prices, bonds, settle), feed_day, settle)


if __name__ == "__main__":
    main()
