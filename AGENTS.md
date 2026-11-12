# AGENTS.md

## Project goal

Load UK **gilt** (government bond) market data, modelize the market from it, and use that
to manage a gilt bond portfolio.

Concretely, the project today covers the data layer (joining a live price feed with the
official DMO inventory on ISIN, cached on disk) plus the first modeling pieces: a gilt
cashflow/pricing engine (`pricing.py`, validated against the feed's own columns) and a
z-spread curve calibrated on the priced population, weighted toward par so the
tax-skewed low-coupon gilts don't drag it (`zspread.py`).
Still to come: the rest of the portfolio layer (position tracking, P&L, trade decisions).

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
- `bond_management/` — the importable package:
  - `__init__.py` — entry point and data-loading orchestration. Public API
    (`__all__`): `load_data(out_dir="screens") -> (prices_df, list[Bond])`,
    `load_gilt_prices`, `scrape_gilts`, `cached_prices`, `feed_date`, `num`, `Bond`.
  - `__main__.py` — CLI entry point: `python -m bond_management` prints the heads of
    both sources, `python -m bond_management table` runs the yield table,
    `python -m bond_management graph` the par-yield chart and
    `python -m bond_management zhistory` the pillar z-spread history,
    `python -m bond_management alt-history` the same replayed over `screens/history/*.csv`
    (`screens/zspread_history_alt.csv`),
    `python -m bond_management history` the historical price fetch,
    `python -m bond_management ibkr` the IBKR trade-history screen (argparse
    subcommands dispatching to `bond_management.scripts` — except `alt-history`,
    which dispatches to `bond_management.core.zspread_history_alt`; heads works anywhere).
  - `core/` — domain models, pricing analytics, curve calibration, date logic, and utilities:
    - `bond.py` — `Bond` class: every D1A field per gilt (isin, market, indexation_lag, section, dates, amounts, base RPI, ...).
    - `pricing.py` — gilt cashflows and analytics (conventional gilts): coupon schedule, ICMA accrued, price/yield, duration, convexity.
    - `zspread.py` — z-spread curve over benchmark tenors, Gauss-Newton par-weighted calibration.
    - `dates.py` — date utilities, bank holidays, close times, business day arithmetic.
    - `dmo.py` — UK DMO "Gilts in Issue" (report **D1A**) downloader + `.xls` parser (`python bond_management/core/dmo.py`).
    - `fetch_gilt_history.py`, `zspread_history_alt.py`, `plot_yield_curve_evolution.py`.
  - `scripts/` — executable report scripts run by `__main__.py`:
    - `yield_table.py` — recalculates feed yields against pricing engine (`python bond_management/scripts/yield_table.py`).
    - `generate_graph.py` — calibrates z-spread curve and writes `screens/par_yield_curve_YYYYMMDD.png` (`python bond_management/scripts/generate_graph.py`).
    - `zspread_history.py` — replays calibration over cached closes (`python bond_management/scripts/zspread_history.py`).
    - `fetch_history.py` — fetches historical prices for live conventional gilts.
    - `ibkr_api.py` — IBKR Flex statement (credentials from env or the gitignored
      `secrets/ibkr.env`), reconciles trades into `screens/ibkr_trades.csv` and
      decomposes the portfolio into zero-coupon flows — every coupon plus the
      redemption, one row per payment date (amount due, curve pv, market pv,
      merged z-spread), printed and copied to the clipboard, never written to screens/.
- `screens/` — **gitignored** local data cache: `gilts_YYYYMMDD.csv` (price snapshots),
  `gilts_in_issue_YYYYMMDD.xls` (inventory), plus generated charts
  (`par_yield_curve_YYYYMMDD.png`). A fresh clone starts empty and downloads on
  first run. Do not delete casually; `load_data()` makes live network calls and writes here
  (path is relative to the current working directory — run from the repo root).
- `tests/` — offline pytest suite (network monkeypatched, temp dirs for caches). Run with
  `.venv/Scripts/python -m pytest -q` from the repo root. `tests/fixtures/` holds committed
  snapshots (a Friday-close feed CSV + the inventory fields extracted from a D1A workbook)
  that the pricing and z-spread calibration tests replay.
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
python -m bond_management

# DMO inventory only (today's COB, or latest published)
python bond_management/core/dmo.py
python bond_management/core/dmo.py --date 02/10/2026 --out screens
python bond_management/core/dmo.py --force   # ignore the cache, hit DMO again

# recalc the yield table for every feed gilt vs the engine (needs repo-root cwd)
python -m bond_management table   # or: python bond_management/scripts/yield_table.py

# calibrate the z-spread on the latest data and draw the par-yield vs market chart
python -m bond_management graph   # or: python bond_management/scripts/generate_graph.py

# pillar z-spreads for every cached close in screens/ (offline)
python -m bond_management zhistory   # or: python bond_management/scripts/zspread_history.py

# refresh screens/history/*.csv from giltsyield.com (merges with existing rows)
python -m bond_management history --ticker all --start-date 1Jan2026

# pillar z-spreads replayed over screens/history/*.csv -> screens/zspread_history_alt.csv
python -m bond_management alt-history   # or: python bond_management/core/zspread_history_alt.py

# IBKR Flex statement: print heads, append/refresh screens/ibkr_trades.csv,
# then the portfolio as zero coupons (payment date, amount, curve pv, pv,
# z-spread; terminal + clipboard only)
python -m bond_management ibkr

# tests (offline)
python -m pytest -q

# lint
python -m ruff check .
```

Debug via VS Code "Python Debugger: Current File" (`.vscode/launch.json`).

## Conventions

- **Cache freshness:** price CSVs are reused only once their `as_of` reaches that day's
  **17:00 UTC close** (`CLOSE`); weekends *and England & Wales bank holidays* (computed
  in `bank_holidays()`, one-off extras excluded) fall back via `feed_date`. Empty, corrupt
  or missing-still-live-gilt scrapes are never cached — the newest good `gilts_*.csv` is
  kept and returned instead (a gilt that matured out of the feed since that close is
  expected, checked per ISIN in `scrape_problems`, not a partial scrape). The newest
  `gilts_in_issue_*.xls` by filename is reused, re-downloaded when
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
  from a gilt's D1A `ex_dividend_date`, settling inside `[ex_date, the coupon that ex-date
  detaches)` drops that coupon and zeroes accrued (the redemption amount is still received,
  at its own date when the dropped coupon is the final one); once that coupon is paid the
  gilt trades cum again — the D1A field is point-in-time as of the inventory, so an ex-date
  already in the past does not on its own mean ex-dividend. A gilt issued after the last
  scheduled coupon — e.g. T30K, first
  issued 11/09/2026 — is anchored at its D1A `first_issue_date` via `Bond.schedule`: accrual
  and the current-period fraction run from the issue date and the first coupon is the full
  coupon (DMO's convention, matched within ~2bp on every calibrated feed row). The feed's
  accrued/dirty/duration for such new issues follow a pro-rated-stub model its DMO-yield
  column does not, so calibration solves engine yields from clean + the engine's accrued
  rather than from the feed's dirty.
- **Z-spread curve:** eight benchmark nodes (`DEFAULT_TENORS` = 0.5/1/3/5/10/20/30/50y),
  interpolated linearly and flat outside them; cashflows are discounted continuously,
  `exp(-z(T) * (T - settlement))` over `(date - settlement).days / 365.25` years, so
  z-spreads and residuals are decimals (the `fit` report quotes residuals in bp) while
  prices stay per £100 nominal. `fit()` rolls the feed's `as_of` to T+1 (`next_business_day`)
  and targets clean + the engine's accrued — the pricing tests' `dirty_of` convention —
  minimising Σ (weight · residual)² with z-units throughout (a bp weighs the same for
  a 1y and a 45y gilt), each gilt's contribution scaled by `price_weight`: a normal
  law (σ = `WEIGHT_SIGMA` = 20 price points) around par, so deep-discount low-coupon
  gilts — TG61 at 22.7, TR73 at 28.3, rich on UK tax treatment rather than yield —
  carry weight ≈ 0 and cannot drift the curve (the report has the weight per gilt,
  `generate_graph.py` prints the lightest). Gilts maturing within `MIN_LIFE_DAYS`
  (30) of settlement are left out with a printed note — a bond days from redemption
  says nothing about the curve (T26A on both snapshots) — leaving residuals within
  ~30bp for everything the fit counts (median ~3bp); the ignored deep discounts sit
  up to ~30bp away by construction, which is the point.
- **Par curve (`generate_graph.py`):** one synthetic gilt per grid point — semi-annual
  coupon, settlement mid-period (a coupon `SETTLE_LAG` = 91 days back, so accrued is a
  quarter's coupon), redemption 0.25y…50.25y in six-month steps — and its coupon is the
  one pricing at clean 100 under the curve; clean price is affine in the coupon, so two
  `curve_price` evaluations (0% and 1%) solve it exactly. Gilts plot at their own
  semi-annual YTM (T+1 settlement, engine accrued), the sub-month ones left out as in
  the fit, colour = `fit`'s `residual_bp`
  (positive = cheap) — a price residual against the fitted curve on the gilt's own
  cashflows, so for low-coupon gilts it can read opposite to the point's side of the
  par line (par yield is coupon-weighted, their YTM is redemption-weighted); grid
  points outside 0.5–50y are drawn dashed over the flat part.
- Imports: the package's modules use plain relative imports (`from . import dmo`) and
  are library code — `bond_management/__main__.py` dispatches to scripts in
  `bond_management.scripts` via relative imports (`from .scripts import yield_table`).
  The scripts import the package absolutely (`from bond_management import ...`),
  resolved by the editable install or by the root being on `sys.path` when run from
  there, so a missing third-party dependency surfaces with its own ImportError instead
  of a masked one.

## Dependencies (declared in `pyproject.toml`)

Runtime: `pandas`, `requests` (price feed only), `beautifulsoup4` (price feed only),
`curl_cffi` (DMO only — required for the package to import at all), `xlrd` (DMO only,
legacy `.xls`), `numpy` (z-spread calibration only), `matplotlib` (`generate_graph.py`
only). Optional extras: `test` = `pytest`,
`lint` = `ruff`.

Installed in `.venv` but not declared — likely intended for the portfolio layer:
`yfinance`, `peewee` (SQLite ORM), `websockets`.

## Gotchas

- `screens/` is gitignored — only `.gitignore`, `AGENTS.md`, `LICENSE`, `pyproject.toml`,
  `bond_management/`, `tests/` are tracked.
- Running `load_data()` hits the network and writes to `screens/`.
- Date-stamped filenames are the cache keys; freshness rules are filename- and
  clock-based, not content-based.
- Index-linked gilts exist in the DMO inventory but not in the price feed — expect
  unmatched ISINs on that side of the join.
