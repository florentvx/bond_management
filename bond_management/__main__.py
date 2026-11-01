"""`python -m bond_management` from the repo root — the report scripts.

    python -m bond_management          load both sources and print their heads
    python -m bond_management table    recalculated yield table (copied to clipboard)
    python -m bond_management graph    par yield curve chart (screens/*.png)
    python -m bond_management zhistory pillar z-spreads per cached close (screens/*.csv)

The table/graph/zhistory subcommands import the root-level reports, so they need the
repo root as the working directory (it lands on `sys.path` via `-m`); heads
works anywhere. Each report still runs standalone (`python yield_table.py`,
`python generate_graph.py`).
"""

import argparse

from . import load_data
from .bond import Bond


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
    args = parser.parse_args(argv)
    if args.script == "table":
        import yield_table

        yield_table.main()
    elif args.script == "graph":
        import generate_graph

        generate_graph.main()
    elif args.script == "zhistory":
        import zspread_history

        zspread_history.main()
    else:
        heads()


if __name__ == "__main__":
    main()
