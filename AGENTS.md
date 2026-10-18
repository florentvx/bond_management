# AGENTS.md

## Project goal

Load UK **gilt** (government bond) market data, modelize the market from it, and use that
to manage a gilt bond portfolio.

Concretely, the project today covers the data layer (joining a live price feed with the
official DMO inventory on ISIN, cached on disk) plus the first modeling piece: a gilt
cashflow/pricing engine (`pricing.py`, validated against the feed's own columns).
Still to come: yield-curve / portfolio layers (position tracking, P&L, trade decisions).

## Remediation plan (completed)

Every item of the former `FULL_REVIEW.md` has been fixed per the plan
`sniffy-honking-squid.md` (opencode plans dir: `~/.local/share/opencode/plans/`),
each with a regression test, and `FULL_REVIEW.md` has been deleted.
Both gates are green: `.venv/Scripts/python -m pytest -q` + `ruff check .`.

- [x] 1. `indexation_lag` field on `Bond` (3/8-month groups no longer conflated)
- [x] 2. Cache validation: refuse to cache empty/partial/corrupt scrapes, return last good file
- [x] 3. Import fallback: `if __package__` instead of `try/except ImportError` (no masked errors)
  — later superseded: executables moved to the repo root, package imports are plain relative
- [x] 4. Accurate post-fetch "close" message + no sleep before the final retry raise
- [x] 5. `num()` handles vulgar fractions (`4¼`, `1 1/8%`)
- [x] 6. Footer/blank D1A rows no longer corrupt `section`
- [x] 7. Graceful degradation when a DMO inventory refresh fails (use cached workbook)
- [x] 8. Remove dead `[tool.hatch]` config from `pyproject.toml`
- [x] 9. Remaining test gaps (`raise_for_status`, unresolvable ISINs)
- [x] 10. AGENTS.md accuracy pass (API name, dependency roles, cache-validation note)
- [x] 11. Delete `FULL_REVIEW.md` once everything is green

## Layout

Standard installable package layout (repo root ≠ package):

- `pyproject.toml` — package metadata (`bond-management`), runtime dependencies,
  setuptools package discovery, pytest `testpaths`. Install with `pip install -e .`
  (already done in `.venv`; a fresh clone needs it once).
- `__main__.py`, `dmo.py` — repo-root executables (the package itself is only ever
  imported, never run): `python .` loads both sources and prints heads;
  `python dmo.py [--date dd/mm/yyyy] [--out DIR] [--force]` fetches the DMO inventory.
- `bond_management/` — the importable package:
  - `__init__.py` — entry point and data-loading orchestration. Public API
    (`__all__`): `load_data(out_dir="screens") -> (prices_df, list[Bond])`,
    `load_gilt_prices`, `scrape_gilts`, `cached_prices`, `feed_date`, `num`, `Bond`.
  - `dmo.py` — UK DMO "Gilts in Issue" (report **D1A**) downloader + `.xls` parser.
    `read()` returns `list[Bond]`; a library (`fetch`, `download_xls`,
    `read`, `find_newest`, ...) — the CLI is the root `dmo.py` executable.
  - `bond.py` — the `Bond` class: every D1A field per gilt (isin, market,
    indexation_lag, section, dates, amounts, base RPI, ...). `Bond.frame(bonds)`
    builds the inventory DataFrame; `Bond.schedule(since)` derives coupon dates.
  - `pricing.py` — gilt cashflows and analytics (conventional gilts): coupon
    schedule from the D1A `dividend_dates` pattern, cashflows with ex-dividend
    handling, ICMA accrued, `price` / `yield_from_price` (bisection),
    `modified_duration`, `convexity`. Two yield conventions, see Conventions.
  - `dates.py` — all date logic: `CLOSE`, `easter`, `bank_holidays`, `feed_date`,
    `as_naive`, `parse_date`, `parse_utc_datetime`, `excel_date`.
- `screens/` — **gitignored** local data cache: `gilts_YYYYMMDD.csv` (price snapshots),
  `gilts_in_issue_YYYYMMDD.xls` (inventory). A fresh clone starts empty and downloads on
  first run. Do not delete casually; `load_data()` makes live network calls and writes here
  (path is relative to the current working directory — run from the repo root).
- `tests/` — offline pytest suite (network monkeypatched, temp dirs for caches). Run with
  `.venv/Scripts/python -m pytest -q` from the repo root. `tests/fixtures/` holds committed
  snapshots (a Friday-close feed CSV + the inventory fields extracted from a D1A workbook)
  that the pricing calibration tests replay.
- `.venv/` — Python 3.12 virtualenv (gitignored).

There is no `README`, no CI. Lint: `ruff` (installed in `.venv`, default rules — no
config file); `ruff format` would rewrap most files, but is not enforced.

## Data sources

1. **giltsyield.com** (`https://giltsyield.com/bond/`) — scraped HTML table of
   conventional gilt prices: clean/dirty price, accrued interest, modified duration,
   gross yield, DMO yield, coupon, maturity, TIDM, ISIN, and a "Market data as of"
   timestamp (treated as UTC). Feed carries conventional gilts only (71 rows currently).
2. **DMO** (`dmo.gov.uk`, report `D1A`) — official inventory, all gilts in issue
   (conventional + index-linked, ~105): amount in issue, redemption/ex-dividend dates,
   base RPI, maturity buckets (Ultra-Short / Short / Medium / Long), indexation lag.
   Downloaded as legacy OLE2 `.xls`.

The join key between the two is **ISIN** (`GB[0-9A-Z]{10}`); TIDM is kept alongside for
the price feed.

## Running

```bash
# activate venv (Windows)
.venv/Scripts/activate

# once after a fresh clone (editable install makes `import bond_management` resolve)
pip install -e .

# load both sources (cached; refreshes stale data) and print heads
python .

# DMO inventory only (today's COB, or latest published)
python dmo.py
python dmo.py --date 02/10/2026 --out screens
python dmo.py --force   # ignore the cache, hit DMO again

# tests (offline)
python -m pytest -q

# lint
python -m ruff check .
```

Debug via VS Code "Python Debugger: Current File" (`.vscode/launch.json`).

## Conventions

- **Cache freshness:** price CSVs are reused only once their `as_of` reaches that day's
  **17:00 UTC close** (`CLOSE`); weekends *and England & Wales bank holidays* (computed
  in `bank_holidays()`, one-off extras excluded) fall back via `feed_date`. Empty, partial
  or corrupt scrapes are never cached — the newest good `gilts_*.csv` is kept and returned
  instead. The newest `gilts_in_issue_*.xls` by filename is reused, re-downloaded when
  older than `dmo.MAX_AGE_DAYS` (31) or when a priced ISIN is missing; `force=True` /
  `--force` skips both caches.
- **Bot protection:** DMO requires `curl_cffi` Chrome impersonation — plain `requests`
  gets a ShieldSquare CAPTCHA. Retries with linear backoff (`15 * attempt` s) on 403/429
  or captcha pages; fresh session per retry.
- **Errors:** expected conditions (COB not yet published, weekend, missing ISIN) print a
  message and degrade gracefully — a failed DMO refresh falls back to the newest cached
  workbook; `RuntimeError` / `ValueError` only for real failures (blocked network, date
  out of DMO's range).
- **Style:** module docstring = user documentation; `UPPER_CASE` module constants;
  small helper functions with one-line docstrings; `print()` for user-facing messages
  (no logging); plain stdlib + pandas, no type hints, no dataclasses/config files.
- **Pricing conventions:** yields are decimals, prices per £100 nominal. `compound="semi"`
  (the default) is ICMA actual/actual semi-annual — the UK market/DMO standard, matches the
  feed's `dmo_yield_pct` (median ~0.4bp on the calibrated snapshot); `compound="annual"`
  (annual, actual/365.25) matches the feed's `gross_yield_pct` (its own convention, median
  ~0.3bp). Settlement is the caller's choice — the feed's `as_of` implies T+1 (business day).
  From a gilt's D1A `ex_dividend_date`, settling on or after it drops the next coupon and
  zeroes accrued (the redemption amount is still received, at its own date when the dropped
  coupon is the final one). A gilt issued after the last scheduled coupon — e.g. T30K, first
  issued 11/09/2026 — is anchored at its D1A `first_issue_date` via `Bond.schedule`: accrual
  and the current-period fraction run from the issue date and the first coupon is the full
  coupon (DMO's convention, matched within ~2bp on every calibrated feed row). The feed's
  accrued/dirty/duration for such new issues follow a pro-rated-stub model its DMO-yield
  column does not, so calibration solves engine yields from clean + the engine's accrued
  rather than from the feed's dirty.
- Imports: the package (`bond_management/`) is only ever imported, never run — its
  modules use plain relative imports (`from . import dmo`). The executables live at
  the repo root and import it absolutely (`from bond_management import ...`), resolved
  by the editable install or by the root being on `sys.path` when run from there, so a
  missing third-party dependency surfaces with its own ImportError instead of a masked one.

## Dependencies (declared in `pyproject.toml`)

Runtime: `pandas`, `requests` (price feed only), `beautifulsoup4` (price feed only),
`curl_cffi` (DMO only — required for the package to import at all), `xlrd` (DMO only,
legacy `.xls`). Optional extras: `test` = `pytest`, `lint` = `ruff`.

Installed in `.venv` but not declared — likely intended for the modeling/portfolio
layer: `yfinance`, `peewee` (SQLite ORM), `websockets`, `numpy`.

## Gotchas

- `screens/` is gitignored — only `.gitignore`, `AGENTS.md`, `pyproject.toml`,
  `bond_management/`, `tests/` and the root executables (`__main__.py`, `dmo.py`)
  are tracked.
- Running `load_data()` hits the network and writes to `screens/`.
- Date-stamped filenames are the cache keys; freshness rules are filename- and
  clock-based, not content-based.
- Index-linked gilts exist in the DMO inventory but not in the price feed — expect
  unmatched ISINs on that side of the join.
