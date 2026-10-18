# NEXT_STEP: z-spread curve calibrated on the conventional gilt population

## Model

- Benchmark z-spreads `z_k` attached to tenors `T_k` (default `1, 3, 5, 10, 20, 30, 50`y).
- Curve `z(T)`: linear interpolation between nodes, flat extrapolation outside
  `[T_0, T_last]`.
- Discount factor: `df(t, T) = exp(-z(T) * (T - t))` with `T - t` in years
  (`(d - settlement).days / 365.25`, continuous compounding).
- Bond dirty price: `P = Σ_i cf_i * df(0, T_i)` — cashflows reused from
  `pricing.cashflows` (ex-dividend handling included).
- **Residual z-spread** of gilt *j* at curve `z`: the flat shift `r_j` added to
  the whole curve so that `Σ_i cf_ij * exp(-(z(T_ij) + r_j) * T_ij) = market
  dirty_j`. Solved by bisection, mirroring `yield_from_price`.
- **Calibration**: choose the node values `z_k` minimising `Σ_j r_j²` over all
  ~71 priced conventional gilts.
- Units: z-spreads and residuals as decimals (reported in bp), prices per £100
  nominal.
- Target dirty price = feed `clean_price` + engine `accrued` (the `dirty_of`
  convention of the pricing tests — not the feed's dirty, which embeds the
  pro-rated accrued of new issues).

## Design decisions (flagged, swappable)

1. **Interpolation: linear** (step / DMO-bucket is the alternative) — all
   interpolation lives in `z_at()`, so the shape can change without touching
   anything else.
2. **Node grid**: default above, configurable per call.
3. **Solver: numpy-only Gauss–Newton** — finite-difference Jacobian
   (h ≈ 1e-6) with Levenberg damping, residuals from an inner bisection per
   bond. No scipy; `numpy` is already in `.venv` → declare it in
   `pyproject.toml`.
4. **Settlement**: T+1 business day from the feed's `as_of` (same roll as the
   pricing test snapshot) → new `next_business_day()` helper in `dates.py`.

## Steps

1. `dates.py` — `next_business_day(day)`: roll over weekends and
   `bank_holidays()`; test in `tests/test_dates.py`.
2. New `bond_management/zspread.py`:
   - `DEFAULT_TENORS = (1.0, 3.0, 5.0, 10.0, 20.0, 30.0, 50.0)`
   - `z_at(tenors, values, t)` — linear interpolation, clamped at both ends.
   - `curve_price(coupon_pct, schedule, settlement, tenors, values,
     ex_dividend_date=None, residual=0.0)` — `Σ cf_i * exp(-(z(T_i) +
     residual) * T_i)`; reuses `pricing.cashflows` and `pricing.YEAR`.
   - `residual_zspread(coupon_pct, schedule, settlement, dirty, tenors, values,
     ex_dividend_date=None)` — bisection (monotone in the residual; same
     bracket/tolerance pattern as `yield_from_price`).
   - `calibrate(settlement, rows, tenors=DEFAULT_TENORS)` →
     `(values, residuals)`; Gauss–Newton started flat at
     `z0 = 2 * ln(1 + median dmo_yield / 2)`, FD Jacobian, damped steps, stop
     on `‖Δ‖ < 1e-10` or SSE stall.
   - `fit(prices_df, bonds, tenors=DEFAULT_TENORS)` → `(values, report)`;
     joins prices with conventional bonds on ISIN, builds schedules and
     ex-dividend dates, sets T+1 settlement, runs `calibrate`. Report columns:
     `isin, tidm, name, section, residual_bp, model_price, market_price`.
     (Callers keep the `tenors` they passed, so the tuple only carries
     `values`.)
3. `tests/test_zspread.py` (offline, committed fixtures):
   - unit: price falls as z/residual rises; flat curve ≈ `pricing.price` under
     `z = 2 * ln(1 + y/2)` within a couple of bp; residual round-trip ~1e-9.
   - synthetic: price fixture bonds from known nodes → `calibrate` recovers
     the nodes (~1e-7) with ~0 residuals.
   - snapshot: `fit` on the committed feed + inventory — house-style count
     assertions on `|residual|` (tolerances measured at implementation time),
     and optimality: nudging any node up *or* down raises `Σr²`.
4. Housekeeping: declare `numpy` in `pyproject.toml`; AGENTS.md entry (module
   line, units + T+1 convention); green `.venv/Scripts/python -m pytest -q`
   and `ruff check .`.

## Watch-outs

- `Σr²` is only locally smooth (residuals by bisection) — damping plus the
  flat start keeps Gauss–Newton stable; if it stalls, fall back to a
  coordinate scan.
- New issues (T30K, T32H): the feed's accrual basis differs from the engine's,
  so they will show it in their residuals — report them, don't special-case.
- Gilts maturing under the first node rely on flat extrapolation.
- Equal weights in z-units (as specified): a bp of residual weighs the same
  for a 1y and a 45y gilt — not price- or risk-weighted.
- Cross-check available per bond: its own flat z ≈ `2 * ln(1 + dmo_yield/2)`.
