from conftest import make_bond

import bond_management.bond as bond_module
from bond_management.bond import Bond


class TestBond:
    def test_fields_are_stored(self):
        b = make_bond(
            isin="GB00B3KJDS62",
            name="Treasury 4.25% 2036",
            market="index-linked",
            indexation_lag=3,
            section="Long",
            amount_in_issue_m=12345.0,
            base_rpi=92.6,
            amount_incl_uplift_m=1050.0,
        )
        assert b.isin == "GB00B3KJDS62"
        assert b.name == "Treasury 4.25% 2036"
        assert b.market == "index-linked"
        assert b.indexation_lag == 3
        assert b.section == "Long"
        assert b.amount_in_issue_m == 12345.0
        assert b.base_rpi == 92.6
        assert b.amount_incl_uplift_m == 1050.0

    def test_to_dict_has_every_column(self):
        b = make_bond()
        assert list(b.to_dict()) == bond_module.COLUMNS
        assert b.to_dict()["isin"] == b.isin


class TestFrame:
    def test_columns_and_rows(self):
        bonds = [
            make_bond("GB00B3KJDS62"),
            make_bond("GB00BFMCN691", market="index-linked"),
        ]
        df = Bond.frame(bonds)
        assert list(df.columns) == bond_module.COLUMNS
        assert df["isin"].tolist() == ["GB00B3KJDS62", "GB00BFMCN691"]
        assert list(df["market"]) == ["conventional", "index-linked"]

    def test_empty(self):
        df = Bond.frame([])
        assert len(df) == 0
        assert list(df.columns) == bond_module.COLUMNS
