import pandas as pd
import pytest

from bond_management import __main__ as cli


class TestDispatch:
    def test_table_runs_yield_table(self, monkeypatch):
        from bond_management.scripts import yield_table

        called = []
        monkeypatch.setattr(yield_table, "main", lambda: called.append("table"))
        cli.main(["table"])
        assert called == ["table"]

    def test_graph_runs_generate_graph(self, monkeypatch):
        from bond_management.scripts import generate_graph

        called = []
        monkeypatch.setattr(generate_graph, "main", lambda: called.append("graph"))
        cli.main(["graph"])
        assert called == ["graph"]

    def test_zhistory_runs_zspread_history(self, monkeypatch):
        from bond_management.scripts import zspread_history

        called = []
        monkeypatch.setattr(zspread_history, "main", lambda: called.append("zhistory"))
        cli.main(["zhistory"])
        assert called == ["zhistory"]

    def test_no_subcommand_prints_the_heads(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli, "load_data", lambda: (pd.DataFrame({"tidm": ["T26A"]}), [])
        )
        cli.main([])
        assert "T26A" in capsys.readouterr().out

    def test_unknown_subcommand_exits(self):
        with pytest.raises(SystemExit) as exc:
            cli.main(["nope"])
        assert exc.value.code == 2

    def test_yield_table_skips_missing_bonds(self, monkeypatch, capsys):
        from bond_management.scripts import yield_table

        prices = pd.DataFrame(
            [
                {
                    "name": "Missing Gilt",
                    "tidm": "TMISS",
                    "isin": "GBMISS000001",
                    "coupon_pct": 4.0,
                    "matures": "2030-01-01",
                    "clean_price": 100.0,
                    "dmo_yield_pct": 4.0,
                    "as_of": "2026-10-09 17:00",
                }
            ]
        )
        monkeypatch.setattr(yield_table, "load_data", lambda: (prices, []))
        monkeypatch.setattr(pd.DataFrame, "to_clipboard", lambda self, **kwargs: None)

        yield_table.main()
        out = capsys.readouterr().out
        assert "skipping GBMISS000001 (Missing Gilt): missing from DMO inventory" in out
