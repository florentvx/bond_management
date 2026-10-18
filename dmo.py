"""Download the DMO "Gilts in Issue" report (D1A) Excel export.

Needs curl_cffi: plain requests is served DMO's CAPTCHA page, an impersonated
Chrome session is not. DMO publishes the day's COB data during the working day;
before that it falls back to the latest date that has data.

Usage (from the repo root):
    python dmo.py            # today's COB, or the latest published one
    python dmo.py --date 02/10/2026
    python dmo.py --date 2026-10-02 --out screens
    python dmo.py --force    # ignore the cache, hit DMO again
"""

import argparse

from bond_management.dates import parse_date
from bond_management.dmo import OUT_DIR, fetch

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download the DMO gilts in issue (D1A) Excel export"
    )
    parser.add_argument(
        "--date",
        type=parse_date,
        default=None,
        help="COB date, dd/mm/yyyy (default: today)",
    )
    parser.add_argument(
        "--out", default=str(OUT_DIR), help="output directory (default: screens)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download even when a cached file exists",
    )
    args = parser.parse_args()
    fetch(args.date, args.out, force=args.force)
