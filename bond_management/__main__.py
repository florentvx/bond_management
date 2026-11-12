"""`python -m bond_management` from the repo root — the report scripts.

    python -m bond_management          load both sources and print their heads
    python -m bond_management table    recalculated yield table (copied to clipboard)
    python -m bond_management graph    par yield curve chart (screens/*.png)
    python -m bond_management zhistory pillar z-spreads per cached close (screens/*.csv)
    python -m bond_management alt-history  pillar z-spreads replayed over screens/history
                                           -> screens/zspread_history_alt.csv
    python -m bond_management history  fetch daily price/yield history for live conventional gilts
    python -m bond_management ibkr     IBKR Flex statement -> screens/ibkr_trades.csv
                                       plus the portfolio as zero-coupon bonds (clipboard)

The table/graph/zhistory/history/ibkr subcommands import the scripts in `bond_management.scripts`;
alt-history imports `bond_management.core.zspread_history_alt`.
"""

import argparse
import sys

from . import load_data
from .core.bond import Bond


def heads():
    prices, bonds = load_data()
    print(prices.head())
    print(Bond.frame(bonds).head())


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m bond_management",
        description="bond_management report scripts",
    )
    sub = parser.add_subparsers(dest="script")
    sub.add_parser("table", help="recalculated yield table, copied to the clipboard")
    sub.add_parser("graph", help="synthetic par yield curve chart (screens/*.png)")
    sub.add_parser(
        "zhistory", help="pillar z-spreads per cached close (screens/zspread_history.csv)"
    )
    sub.add_parser(
        "alt-history",
        help="pillar z-spreads replayed over screens/history (screens/zspread_history_alt.csv)",
    )
    parser_hist = sub.add_parser(
        "history",
        help="fetch historical prices for live conventional gilts (screens/history/*.csv)",
    )
    parser_hist.add_argument(
        "--start-date", "-s", default="2026-01-01", help="Start date e.g. 1Jan2026 or 2026-01-01"
    )
    parser_hist.add_argument("--end-date", "-e", default=None, help="End date e.g. 2026-11-01")
    parser_hist.add_argument(
        "--ticker",
        "-t",
        required=True,
        help="Gilt ticker symbol (e.g. T27A) or 'all' for all live conventional gilts",
    )
    parser_hist.add_argument(
        "--test", action="store_true", help="Run on 1 example gilt before full run"
    )
    parser_hist.add_argument(
        "--out-dir", "-o", default="screens/history", help="Output directory"
    )
    sub.add_parser(
        "ibkr",
        help="IBKR Flex statement: heads, reconcile screens/ibkr_trades.csv, zero-coupon table",
    )

    args = parser.parse_args(argv)
    if args.script == "table":
        from .scripts import yield_table

        yield_table.main()
    elif args.script == "graph":
        from .scripts import generate_graph

        generate_graph.main()
    elif args.script == "zhistory":
        from .scripts import zspread_history

        zspread_history.main()
    elif args.script == "alt-history":
        from .core import zspread_history_alt

        zspread_history_alt.main()
    elif args.script == "history":
        from .scripts import fetch_history

        sub_args = sys.argv[2:] if argv is None else argv[1:]
        fetch_history.main(sub_args)
    elif args.script == "ibkr":
        from .scripts import ibkr_api

        ibkr_api.main()
    else:
        heads()


if __name__ == "__main__":
    main()
