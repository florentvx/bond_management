"""Generate yield curve evolution plot and return analytics (volatility & correlation) from zspread_history_alt.csv.

Usage:
    python bond_management/core/plot_yield_curve_evolution.py
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SCREENS_DIR = Path("screens")
ALT_HISTORY_FILE = SCREENS_DIR / "zspread_history_alt.csv"
OUT_PLOT_FILE = SCREENS_DIR / "yield_curve_evolution.png"

TENORS = [0.5, 1.0, 3.0, 5.0, 10.0, 20.0, 30.0, 50.0]
TENOR_COLS = ["0.5y", "1y", "3y", "5y", "10y", "20y", "30y", "50y"]


def generate_analytics():
    if not ALT_HISTORY_FILE.exists():
        raise FileNotFoundError(f"{ALT_HISTORY_FILE} not found.")

    raw_df = pd.read_csv(ALT_HISTORY_FILE)

    # 1. Delete all data before March 2026 (keep feed >= '2026-03-01')
    df = raw_df[raw_df["feed"] >= "2026-03-01"].copy().reset_index(drop=True)
    df.to_csv(ALT_HISTORY_FILE, index=False)
    print(f"Updated {ALT_HISTORY_FILE}: {len(df)} trading days from {df['feed'].min()} to {df['feed'].max()}")

    # 2. Select representative dates for yield curve evolution plot (March - October 2026)
    sample_dates = [
        "2026-03-02",
        "2026-04-15",
        "2026-06-01",
        "2026-07-15",
        "2026-09-01",
        "2026-10-30",
    ]

    # Filter available dates
    available_dates = df["feed"].tolist()
    chosen_dates = [d for d in sample_dates if d in available_dates]
    if len(chosen_dates) < 3:
        # Fallback to evenly spaced indices
        idx = np.linspace(0, len(df) - 1, 6, dtype=int)
        chosen_dates = df.iloc[idx]["feed"].tolist()

    # 3. Plot yield curve evolution
    fig, ax = plt.subplots(figsize=(12, 6.5))
    colors = plt.cm.viridis(np.linspace(0.15, 0.9, len(chosen_dates)))

    for d, color in zip(chosen_dates, colors):
        row = df[df["feed"] == d].iloc[0]
        yields_pct = [row[col] / 100.0 for col in TENOR_COLS]
        ax.plot(
            TENORS,
            yields_pct,
            marker="o",
            linewidth=2.0,
            markersize=5,
            color=color,
            label=f"Feed: {d}",
        )

    ax.set_xscale("log")
    ax.set_xticks(TENORS)
    ax.get_xaxis().set_major_formatter(plt.ScalarFormatter())
    ax.set_xlabel("Tenor / Maturity (Years, Log Scale)", fontsize=11)
    ax.set_ylabel("Zero Yield (%)", fontsize=11)
    ax.set_title("Evolution of the UK Government Zero Yield Curve (March – October 2026)", fontsize=13, fontweight="bold", pad=12)
    ax.grid(True, which="both", linestyle="--", alpha=0.5)
    ax.legend(title="Snapshot Dates", loc="best", fontsize=9)

    fig.tight_layout()
    OUT_PLOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PLOT_FILE, dpi=150)
    print(f"Saved yield curve evolution plot to {OUT_PLOT_FILE}")
    plt.close(fig)

    # 4. Calculate Normal Returns Volatility Vector and Correlation Matrix (March – October 2026)
    # Normal returns: absolute daily yield changes delta_y = y_t - y_{t-1}
    normal_returns_bp = df[TENOR_COLS].diff().dropna()
    normal_returns_pct = (df[TENOR_COLS] / 100.0).diff().dropna()

    daily_vol_bp = normal_returns_bp.std()
    ann_vol_bp = daily_vol_bp * np.sqrt(252)
    ann_vol_pct = normal_returns_pct.std() * np.sqrt(252)

    corr_matrix = normal_returns_bp.corr()

    print("\n" + "=" * 60)
    print("  VOLATILITY VECTOR OF NORMAL RETURNS (MARCH - OCTOBER 2026)")
    print("=" * 60)
    vol_df = pd.DataFrame({
        "Daily Vol (bp/day)": daily_vol_bp.round(2),
        "Ann. Vol (bp/yr)": ann_vol_bp.round(2),
        "Ann. Vol (%/yr)": ann_vol_pct.round(3),
    })
    print(vol_df.to_string())

    print("\n" + "=" * 60)
    print("   CORRELATION MATRIX OF NORMAL RETURNS (MARCH - OCTOBER 2026)")
    print("=" * 60)
    print(corr_matrix.round(3).to_string())


if __name__ == "__main__":
    generate_analytics()
