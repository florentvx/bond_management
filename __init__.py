import datetime as dt
import re

import pandas as pd
import requests
from bs4 import BeautifulSoup

URL = "https://giltsyield.com/bond/"
HEADERS = {"User-Agent": "personal-gilt-tracker (contact: you@example.com)"}

def num(s):
    """'£99.85 ▲0.03%' -> 99.85 ; '3.659% ▼6.7bps' -> 3.659"""
    m = re.search(r"-?[\d,]*\.?\d+", s.replace("£", ""))
    return float(m.group().replace(",", "")) if m else None

def scrape_gilts():
    r = requests.get(URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Data timestamp, e.g. "Market data as of 02 Oct 2026 17:00"
    text = soup.get_text(" ", strip=True)
    m = re.search(r"Market data as of\s+(\d{1,2} \w{3} \d{4} \d{2}:\d{2})", text)
    as_of = dt.datetime.strptime(m.group(1), "%d %b %Y %H:%M").replace(tzinfo=dt.timezone.utc) if m else None

    # Pick the table that has a "Clean price" header
    table = next(t for t in soup.find_all("table") if "Clean price" in t.get_text())
    headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
    idx = {h: i for i, h in enumerate(headers)}

    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < len(headers):
            continue
        cells = [td.get_text(" ", strip=True) for td in tds]

        gilt = cells[idx["Gilt"]]
        tidm_isin = re.search(r"\b([A-Z0-9]{2,5})\s*·\s*(GB[0-9A-Z]{10})", gilt)
        link = tds[idx["Gilt"]].find("a")

        rows.append({
            "name": link.get_text(strip=True) if link else gilt,
            "tidm": tidm_isin.group(1) if tidm_isin else None,
            "isin": tidm_isin.group(2) if tidm_isin else None,
            "coupon_pct": num(cells[idx["Coupon"]]),
            "matures": cells[idx["Matures"]],
            "clean_price": num(cells[idx["Clean price"]]),
            "accrued": num(cells[idx["Accr. int."]]),
            "dirty_price": num(cells[idx["Dirty price"]]),
            "mod_duration": num(cells[idx["Mod. duration"]]),
            "gross_yield_pct": num(cells[idx["Gross yield"]]),
            "dmo_yield_pct": num(cells[idx["DMO yield"]]),
        })

    df = pd.DataFrame(rows)
    df["as_of"] = as_of
    return df

if __name__ == "__main__":
    from pathlib import Path
    filename = Path(f"screens/gilts_{dt.datetime.now(tz=dt.timezone.utc):%Y%m%d}.csv")
    df = None
    if not filename.exists():
        df = scrape_gilts()   
        df.to_csv(filename, index=False)
    else:
        df = pd.read_csv(filename)
    print(df.head())

    print("END")