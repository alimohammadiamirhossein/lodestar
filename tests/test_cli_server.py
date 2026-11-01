import asyncio

import pytest

from lodestar import cli, server


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LODESTAR_HOME", str(tmp_path / "home"))
    root = tmp_path / "lab"
    (root / "code").mkdir(parents=True)
    (root / "code" / "train_loader.py").write_text("x")
    return root


def test_cli_index_then_find(home, capsys):
    assert cli.main(["index", str(home)]) == 0
    assert "ok" in capsys.readouterr().out
    assert cli.main(["find", "loader"]) == 0
    assert "train_loader.py" in capsys.readouterr().out
    assert cli.main(["stats"]) == 0
    assert "files in" in capsys.readouterr().out


def test_cli_index_without_roots_reuses_known_roots(home, capsys):
    assert cli.main(["index"]) == 2
    cli.main(["index", str(home)])
    capsys.readouterr()
    assert cli.main(["index"]) == 0
    assert "reused" in capsys.readouterr().out


def test_cli_missing_root_fails_without_crashing(home, capsys):
    assert cli.main(["index", str(home / "nope")]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_cli_without_command_prints_help(capsys):
    assert cli.main([]) == 0
    assert "usage" in capsys.readouterr().out


def test_server_exposes_five_tools():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names == {"find", "tree", "info", "stats", "reindex"}


def test_server_tools_answer(home):
    assert "empty" in server.stats()
    assert "Nothing to refresh" in server.reindex()
    cli.main(["index", str(home)])
    assert "train_loader.py" in server.find("loader")
    assert "train_loader.py" in server.tree(str(home / "code"))
    assert server.info(str(home / "code")).startswith("dir:")
    assert "ok" in server.reindex()
