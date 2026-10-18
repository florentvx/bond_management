"""`python .` from the repo root: load both sources and print their heads."""

from bond_management import load_data
from bond_management.bond import Bond

if __name__ == "__main__":
    prices, bonds = load_data()
    print(prices.head())
    print(Bond.frame(bonds).head())
