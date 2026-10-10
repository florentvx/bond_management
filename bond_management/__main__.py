"""`python -m bond_management`: load both sources and print their heads."""

from . import load_data
from .bond import Bond

if __name__ == "__main__":
    prices, bonds = load_data()
    print(prices.head())
    print(Bond.frame(bonds).head())
