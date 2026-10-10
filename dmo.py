"""Download the DMO "Gilts in Issue" report (D1A) Excel export.

Needs curl_cffi: plain requests is served DMO's CAPTCHA page, an impersonated
Chrome session is not. DMO publishes the day's COB data during the working day;
before that the script falls back to the latest date that has data.

Usage:
    python dmo.py                 # today's COB, or the latest published one
    python dmo.py --date 02/10/2026
    python dmo.py --date 2026-10-02 --out screens
"""


import argparse
import datetime as dt
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import xlrd
from curl_cffi import requests as cr

REPORT_CODE = "D1A"
PAGE_URL = "https://www.dmo.gov.uk/data/pdfdatareport"
EXPORT_URL = "https://www.dmo.gov.uk/umbraco/surface/DataExport/GetDataExport"
REFERER = f"{PAGE_URL}?reportCode={REPORT_CODE}"

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
CAPTCHA = b"ShieldSquare Captcha"
NO_DATA = "No information".encode("utf-16-le")
FAILED = b"Unable to fulfil the report request"

OUT_DIR = Path("screens")
RETRIES = 3
ISIN = re.compile(r"GB[0-9A-Z]{10}")


def new_session():
    return cr.Session(impersonate="chrome", timeout=60)


def blocked(response):
    """True when DMO's bot protection (or its WAF) rejected us instead of serving the report."""
    return response.status_code in (403, 429) or CAPTCHA in response.content


def open_report():
    """Load the report page and return (session, date metadata).

    The page also seeds the cookies the export endpoint expects.
    """
    for attempt in range(1, RETRIES + 1):
        session = new_session()
        r = session.get(REFERER)
        if not blocked(r):
            m = re.search(r"var pdfreportdata = (\{.*?\n\});", r.text, re.DOTALL)
            if m:
                return session, date_metadata(json.loads(m.group(1)))
            raise RuntimeError("Could not find report metadata on the DMO page")
        time.sleep(15 * attempt)
    raise RuntimeError("DMO bot protection is blocking this network, wait a few minutes and run again")


def parse_utc_datetime(value, fmt):
    """Parse a datetime string and attach UTC so later date extraction is explicit."""
    return dt.datetime.strptime(value, fmt).replace(tzinfo=dt.timezone.utc)


def date_metadata(report_data):
    """Min/max COB date allowed by the picker plus the latest COB that actually has data."""
    param = report_data["ReportParameters"][0]
    limits = {i["key"]: parse_utc_datetime(i["value"], "%d/%m/%Y").date() for i in param["listItems"]}
    return {
        "min": limits["Min"],
        "max": limits["Max"],
        "latest": parse_utc_datetime(param["defaultValue1"], "%d/%m/%Y").date(),
    }


def download_xls(session, cob_date):
    """Return (session, XLS bytes) for a COB date, or (session, None) when the report has no data."""
    query = (
        f"reportCode={REPORT_CODE}"
        "&exportFormatValue=xls"
        f"&parameters={quote('&COBDate=' + cob_date.strftime('%d/%m/%Y'), safe='')}"
    )
    for attempt in range(1, RETRIES + 1):
        r = session.get(EXPORT_URL + "?" + query, headers={"Referer": REFERER})
        body = r.content
        if body.startswith(OLE2_MAGIC):
            return session, (None if NO_DATA in body else body)
        if blocked(r):
            time.sleep(15 * attempt)
            session = new_session()
        elif FAILED in body:
            time.sleep(3 * attempt)
        else:
            raise RuntimeError(f"Unexpected response ({r.status_code}): {body[:200]!r}")
    raise RuntimeError(f"DMO export kept failing for {cob_date:%d/%m/%Y}, try again later")


def parse_date(value):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(value, fmt).replace(tzinfo=dt.timezone.utc).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"not a date: {value!r} (use dd/mm/yyyy or yyyy-mm-dd)")


def find_newest(out_dir=OUT_DIR):
    """The newest D1A workbook cached in out_dir (files are named by COB date), or None."""
    files = sorted(Path(out_dir).glob("gilts_in_issue_*.xls"))
    return files[-1] if files else None


def find_existing(out_dir, cob_date=None):
    """A file in out_dir we can reuse instead of sending another request to DMO."""
    out_dir = Path(out_dir)
    if cob_date is not None:
        path = out_dir / f"gilts_in_issue_{cob_date:%Y%m%d}.xls"
        return path if path.exists() else None

    newest = find_newest(out_dir)
    if newest is None:
        return None
    today = dt.datetime.now(tz=dt.timezone.utc).date()
    # Reuse when it covers today, or was already fetched today (today's COB not published yet).
    cob = parse_utc_datetime(newest.stem.removeprefix("gilts_in_issue_"), "%Y%m%d").date()
    fetched_today = dt.datetime.fromtimestamp(newest.stat().st_mtime, tz=dt.timezone.utc).date()
    if cob == today or fetched_today == today:
        return newest
    return None


def fetch(cob_date=None, out_dir=OUT_DIR):
    cached = find_existing(out_dir, cob_date)
    if cached:
        print(f"{cached.name} already in {Path(out_dir)}, skipping DMO request")
        return cached

    session, meta = open_report()

    if cob_date is None:
        cob_date = dt.datetime.now(tz=dt.timezone.utc).date()
        if cob_date > meta["latest"]:
            print(f"{cob_date:%d/%m/%Y} not published yet, falling back to {meta['latest']:%d/%m/%Y}")
            cob_date = meta["latest"]
    if not meta["min"] <= cob_date <= meta["max"]:
        raise ValueError(f"date out of range, DMO only serves {meta['min']:%d/%m/%Y} to {meta['max']:%d/%m/%Y}")

    session, body = download_xls(session, cob_date)
    if body is None and cob_date != meta["latest"]:
        print(f"No D1A data for {cob_date:%d/%m/%Y}, falling back to {meta['latest']:%d/%m/%Y}")
        cob_date = meta["latest"]
        session, body = download_xls(session, cob_date)
    if body is None:
        raise RuntimeError(f"No gilts in issue data available (latest COB: {meta['latest']:%d/%m/%Y})")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"gilts_in_issue_{cob_date:%Y%m%d}.xls"
    path.write_bytes(body)
    print(f"{cob_date:%d/%m/%Y} -> {path} ({len(body):,} bytes)")
    return path


def excel_date(value, datemode):
    """An Excel serial date cell -> datetime.date (blank cells stay None)."""
    if value in ("", None):
        return None
    return xlrd.xldate_as_datetime(value, datemode).date()


def read(path):
    """Parse a D1A workbook into a DataFrame of the gilts in issue.

    Rows carry the conventional/index-linked split (market), the maturity bucket
    (section: Ultra-Short/Short/Medium/Long) and the report's COB date, so the
    inventory can be joined with a price feed on ISIN.
    """
    book = xlrd.open_workbook(path)
    sheet = book.sheet_by_index(0)
    m = re.search(r"GILTS IN ISSUE ON (\d{1,2} \w+ \d{4})", str(sheet.cell_value(0, 2)))
    cob_date = dt.datetime.strptime(m.group(1).title(), "%d %B %Y").replace(tzinfo=dt.timezone.utc).date() if m else None

    rows, market, section = [], None, None
    for r in range(sheet.nrows):
        cells = sheet.row_values(r) + [""] * (9 - sheet.ncols)
        name, code = str(cells[0]).strip(), str(cells[1]).strip()
        if code == "ISIN Code":
            market = "index-linked" if "Index-linked" in name else "conventional"
            section = None
        elif ISIN.fullmatch(code):
            rows.append({
                "name": name,
                "isin": code,
                "market": market,
                "section": section,
                "redemption_date": excel_date(cells[2], book.datemode),
                "first_issue_date": excel_date(cells[3], book.datemode),
                "dividend_dates": cells[4],
                "ex_dividend_date": excel_date(cells[5], book.datemode),
                "amount_in_issue_m": cells[6],
                "base_rpi": float(cells[7]) if market == "index-linked" and cells[7] else None,
                "amount_incl_uplift_m": cells[8] if market == "index-linked" else None,
                "cob_date": cob_date,
            })
        elif market and not code:
            section = name
    print(f"{Path(path).name}: {len(rows)} gilts in issue on {cob_date:%d/%m/%Y}")
    return pd.DataFrame(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download the DMO gilts in issue (D1A) Excel export")
    parser.add_argument("--date", type=parse_date, default=None, help="COB date, dd/mm/yyyy (default: today)")
    parser.add_argument("--out", default=str(OUT_DIR), help="output directory (default: screens)")
    args = parser.parse_args()
    fetch(args.date, args.out)
