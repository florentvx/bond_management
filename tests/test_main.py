import pandas as pd
import pytest

from bond_management import __main__ as cli


class TestDispatch:
    def test_table_runs_yield_table(self, monkeypatch):
        import yield_table

        called = []
        monkeypatch.setattr(yield_table, "main", lambda: called.append("table"))
        cli.main(["table"])
        assert called == ["table"]

    def test_graph_runs_generate_graph(self, monkeypatch):
        import generate_graph

        called = []
        monkeypatch.setattr(generate_graph, "main", lambda: called.append("graph"))
        cli.main(["graph"])
        assert called == ["graph"]

    def test_zhistory_runs_zspread_history(self, monkeypatch):
        import zspread_history

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
