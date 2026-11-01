import json
import os

import pytest

from lodestar import db, index


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "home" / "index.db")


def touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "lab"
    touch(root / "code" / ".git" / "HEAD")
    touch(root / "code" / "train_loader.py")
    touch(root / "code" / "sub" / "model.py")
    touch(root / "code" / "node_modules" / "junk.js")
    for i in range(250):
        touch(root / "images" / f"img_{i:04d}.jpg")
    for c in range(120):
        for j in range(3):
            touch(root / "classes" / f"c{c:03d}" / f"f{j}.png")
    return root


def row(conn, path):
    return conn.execute("SELECT * FROM nodes WHERE path=?", (index.norm(str(path)),)).fetchone()


def test_repo_is_classified_and_pruned(conn, tree):
    index.index_root(conn, str(tree))
    assert row(conn, tree / "code")["kind"] == "repo"
    assert row(conn, tree / "code" / "train_loader.py")["kind"] == "file"
    assert row(conn, tree / "code" / "node_modules") is None
    assert row(conn, tree / "code" / ".git") is None


def test_flat_directory_collapses(conn, tree):
    index.index_root(conn, str(tree))
    images = row(conn, tree / "images")
    assert images["collapsed"] == 1 and images["n_files"] == 250 and images["kind"] == "media"
    assert json.loads(images["samples"]) == ["img_0000.jpg", "img_0001.jpg", "img_0002.jpg"]
    assert row(conn, tree / "images" / "img_0000.jpg") is None


def test_directory_with_many_subdirectories_collapses(conn, tree):
    index.index_root(conn, str(tree))
    classes = row(conn, tree / "classes")
    assert classes["collapsed"] == 1 and classes["n_files"] == 360 and classes["n_dirs"] == 120
    assert row(conn, tree / "classes" / "c000") is None


def test_root_totals(conn, tree):
    summary = index.index_root(conn, str(tree))
    assert summary["status"] == "ok"
    assert summary["files"] == 2 + 250 + 360


def test_unchanged_collapsed_directory_is_not_rescanned(conn, tree, monkeypatch):
    index.index_root(conn, str(tree))
    scanned = []
    real = os.scandir
    monkeypatch.setattr(index.os, "scandir", lambda p: (scanned.append(str(p)), real(p))[1])
    summary = index.index_root(conn, str(tree))
    assert summary["dirs_reused"] >= 2
    assert not any(p.endswith("images") or p.endswith("classes") for p in scanned)
    assert summary["files"] == 2 + 250 + 360
    assert row(conn, tree / "images")["run"] == row(conn, tree / "code")["run"]


def test_changed_collapsed_directory_is_rescanned(conn, tree):
    index.index_root(conn, str(tree))
    touch(tree / "images" / "img_9999.jpg")
    os.utime(tree / "images", ns=(1, 2_000_000_000_000_000_000))
    index.index_root(conn, str(tree))
    assert row(conn, tree / "images")["n_files"] == 251


def test_deleted_files_leave_the_index(conn, tree):
    index.index_root(conn, str(tree))
    (tree / "code" / "train_loader.py").unlink()
    index.index_root(conn, str(tree))
    assert row(conn, tree / "code" / "train_loader.py") is None
    assert row(conn, tree / "code" / "sub" / "model.py") is not None


def test_full_flag_ignores_shortcuts(conn, tree):
    index.index_root(conn, str(tree))
    summary = index.index_root(conn, str(tree), full=True)
    assert summary["dirs_reused"] == 0


def test_time_budget_leaves_a_partial_index_and_deletes_nothing(conn, tree, monkeypatch):
    index.index_root(conn, str(tree))
    (tree / "code" / "train_loader.py").unlink()
    ticks = iter(range(10_000))
    monkeypatch.setattr(index.time, "monotonic", lambda: next(ticks))
    summary = index.index_root(conn, str(tree), full=True, max_seconds=3)
    assert summary["status"] == "partial"
    assert row(conn, tree / "code" / "train_loader.py") is not None


def test_experiment_markers(conn, tmp_path):
    touch(tmp_path / "runs" / "a" / "events.out.tfevents.1")
    touch(tmp_path / "runs" / "b" / ".hydra" / "config.yaml")
    index.index_root(conn, str(tmp_path / "runs"))
    assert row(conn, tmp_path / "runs" / "a")["kind"] == "experiment"
    assert row(conn, tmp_path / "runs" / "b")["kind"] == "experiment"


def test_missing_root_is_an_error(conn, tmp_path):
    with pytest.raises(FileNotFoundError):
        index.index_root(conn, str(tmp_path / "nope"))
