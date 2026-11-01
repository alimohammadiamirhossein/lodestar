import pytest

from lodestar import db, index, query


@pytest.fixture
def lab(tmp_path):
    root = tmp_path / "lab"
    for rel in ("code/train_loader.py", "code/eval_metrics.py", "code/.git/HEAD", "notes/plan.md"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    for i in range(250):
        p = root / "images" / f"img_{i:04d}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    return root


@pytest.fixture
def conn(tmp_path, lab):
    c = db.connect(tmp_path / "home" / "index.db")
    index.index_root(c, str(lab))
    return c


def test_find_matches_names_and_prefixes(conn):
    out = query.find(conn, "loader")
    assert "train_loader.py" in out
    assert "train_loa" in query.find(conn, "train_loa")


def test_find_by_extension_hits_collapsed_notes(conn):
    out = query.find(conn, "jpg", kind="media")
    assert "images" in out and "[collapsed]" in out and "250 files" in out


def test_find_filters_by_kind(conn):
    assert "train_loader.py" not in query.find(conn, "loader", kind="repo")


def test_find_limit_and_empty(conn):
    assert len(query.find(conn, "py", limit=1).splitlines()) == 1
    assert query.find(conn, "zzzz").startswith("No match")
    assert query.find(conn, "!!!").startswith("Give at least")


def test_find_survives_fts_syntax_in_query(conn):
    assert isinstance(query.find(conn, 'loader" OR * NEAR('), str)


def test_info_on_collapsed_directory(conn, lab):
    out = query.info(conn, str(lab / "images"))
    assert "250 files" in out and ".jpg 250" in out and "img_0000.jpg" in out and "collapsed" in out


def test_info_on_missing_path(conn, lab):
    assert "not in the index" in query.info(conn, str(lab / "nope"))


def test_tree_lists_children_biggest_first_and_caps(conn, lab):
    out = query.tree(conn, str(lab))
    lines = out.splitlines()
    assert lines[0].split()[0] == "media"
    assert len(query.tree(conn, str(lab), limit=1).splitlines()) == 2
    assert "more" in query.tree(conn, str(lab), limit=1)


def test_tree_below_collapsed_node_is_empty(conn, lab):
    assert query.tree(conn, str(lab / "images")).startswith("Nothing indexed")


def test_stats(conn, lab):
    out = query.stats(conn)
    assert "files in" in out and "directories by kind" in out
    assert query.stats(db.connect(lab.parent / "other.db")).startswith("The index is empty")


@pytest.mark.parametrize("n,text", [(0, "0 B"), (1023, "1023 B"), (1536, "1.5 KB"), (5 * 1024**3, "5.0 GB")])
def test_human_size(n, text):
    assert query.human_size(n) == text
    assert query.human_size(n, approx=True) == "~" + text
