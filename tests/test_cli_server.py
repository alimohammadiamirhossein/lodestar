import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

import lodestar
from lodestar import cli, db, index, server


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


def test_cli_forget_removes_root(home, capsys):
    assert cli.main(["index", str(home)]) == 0
    capsys.readouterr()
    assert cli.main(["find", "loader"]) == 0
    assert "train_loader.py" in capsys.readouterr().out
    root = index.norm(str(home))
    assert cli.main(["forget", str(home)]) == 0
    assert f"forgot {root} (" in capsys.readouterr().out
    assert cli.main(["find", "loader"]) == 0
    assert "No match" in capsys.readouterr().out
    assert server.stats().startswith("The index is empty")
    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM nodes WHERE root=?", (root,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM runs WHERE root=?", (root,)).fetchone()[0] == 0


def test_cli_forget_unknown_root_fails(home, capsys):
    assert cli.main(["forget", str(home)]) == 1
    assert "not an indexed root:" in capsys.readouterr().err


def test_cli_index_without_roots_after_forget(home, tmp_path, capsys):
    other = tmp_path / "other"
    (other / "keep").mkdir(parents=True)
    (other / "keep" / "file.txt").write_text("x")
    assert cli.main(["index", str(home), str(other)]) == 0
    shutil.rmtree(home)
    capsys.readouterr()
    assert cli.main(["index"]) == 1
    assert cli.main(["forget", str(home)]) == 0
    capsys.readouterr()
    assert cli.main(["index"]) == 0
    assert "ok" in capsys.readouterr().out


def test_cli_allow_foreign_applies_to_known_roots(home, monkeypatch, capsys):
    assert cli.main(["index", str(home)]) == 0
    monkeypatch.setattr(index.os, "getuid", lambda: os.stat(home).st_uid + 1, raising=False)
    capsys.readouterr()
    assert cli.main(["index"]) == 1
    assert "another user" in capsys.readouterr().err
    assert cli.main(["index", "--allow-foreign"]) == 0
    assert "ok" in capsys.readouterr().out
    assert cli.main(["index"]) == 0


def test_plugin_version_matches_package():
    path = Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json"
    assert json.loads(path.read_text())["version"] == lodestar.__version__


def test_cli_without_command_prints_help(capsys):
    assert cli.main([]) == 0
    assert "usage" in capsys.readouterr().out


def test_server_exposes_nine_tools():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names == {"find", "tree", "info", "stats", "whats_new", "reindex", "token_savings", "ideas", "idea"}


def test_server_tools_answer(home):
    assert "empty" in server.stats()
    assert "Nothing to refresh" in server.reindex()
    cli.main(["index", str(home)])
    assert "train_loader.py" in server.find("loader")
    assert "train_loader.py" in server.tree(str(home / "code"))
    assert server.info(str(home / "code")).startswith("dir:")
    assert "ok" in server.reindex()
