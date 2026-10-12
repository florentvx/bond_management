"""A gilt from the DMO gilts in issue (D1A) report."""

import pandas as pd

COLUMNS = [
    "name",
    "isin",
    "market",
    "indexation_lag",
    "section",
    "redemption_date",
    "first_issue_date",
    "dividend_dates",
    "ex_dividend_date",
    "amount_in_issue_m",
    "base_rpi",
    "amount_incl_uplift_m",
    "cob_date",
]


class Bond:
    """One gilt in issue, with every field the D1A report provides.

    market is "conventional" or "index-linked", indexation_lag the months of
    lag (3 or 8) for index-linked gilts and None for conventional ones, section
    the maturity bucket (Ultra-Short/Short/Medium/Long) and cob_date the
    report's close-of-business date, so bonds can be joined with a price feed
    on isin.
    """

    def __init__(
        self,
        name,
        isin,
        market,
        indexation_lag,
        section,
        redemption_date,
        first_issue_date,
        dividend_dates,
        ex_dividend_date,
        amount_in_issue_m,
        base_rpi,
        amount_incl_uplift_m,
        cob_date,
    ):
        self.name = name
        self.isin = isin
        self.market = market
        self.indexation_lag = indexation_lag
        self.section = section
        self.redemption_date = redemption_date
        self.first_issue_date = first_issue_date
        self.dividend_dates = dividend_dates
        self.ex_dividend_date = ex_dividend_date
        self.amount_in_issue_m = amount_in_issue_m
        self.base_rpi = base_rpi
        self.amount_incl_uplift_m = amount_incl_uplift_m
        self.cob_date = cob_date

    def to_dict(self):
        return {c: getattr(self, c) for c in COLUMNS}

    @staticmethod
    def frame(bonds):
        """A DataFrame of the bonds, in the D1A column order."""
        return pd.DataFrame([b.to_dict() for b in bonds], columns=COLUMNS)
