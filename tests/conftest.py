import sys
from pathlib import Path

# Prefer the repo checkout (the `bond_management/` folder lives right here) so the
# tests run even without `pip install -e .`; an installed copy would work too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bond_management.bond import Bond


class FakeResponse:
    """Stand-in for requests / curl_cffi responses in offline tests."""

    def __init__(self, text="", status_code=200, content=None):
        self.text = text
        self.status_code = status_code
        self.content = content if content is not None else text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def make_bond(isin="GB00B3KJDS62", **overrides):
    fields = {
        "name": "Test Gilt",
        "isin": isin,
        "market": "conventional",
        "section": "Long",
        "redemption_date": None,
        "first_issue_date": None,
        "dividend_dates": "",
        "ex_dividend_date": None,
        "amount_in_issue_m": 1000.0,
        "base_rpi": None,
        "amount_incl_uplift_m": None,
        "cob_date": None,
    }
    fields.update(overrides)
    return Bond(**fields)
