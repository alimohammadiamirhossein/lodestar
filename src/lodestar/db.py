"""SQLite storage: one row per useful node, full-text search over names, paths and notes."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .index import norm

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    path TEXT PRIMARY KEY,
    parent TEXT NOT NULL,
    root TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    is_dir INTEGER NOT NULL,
    n_files INTEGER NOT NULL,
    n_dirs INTEGER NOT NULL,
    size INTEGER NOT NULL,
    size_approx INTEGER NOT NULL,
    exts TEXT NOT NULL,
    samples TEXT NOT NULL,
    mtime INTEGER NOT NULL,
    collapsed INTEGER NOT NULL,
    note TEXT NOT NULL,
    run INTEGER NOT NULL,
    tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS nodes_parent ON nodes(parent);
CREATE INDEX IF NOT EXISTS nodes_root_run ON nodes(root, run);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    root TEXT NOT NULL,
    started REAL NOT NULL,
    finished REAL,
    status TEXT NOT NULL,
    nodes INTEGER NOT NULL DEFAULT 0,
    allow_foreign INTEGER NOT NULL DEFAULT 0,
    full INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS savings (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    session TEXT NOT NULL,
    kind TEXT NOT NULL,
    delivered INTEGER NOT NULL,
    avoided INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    name, path, kind, note, content='nodes', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, name, path, kind, note)
    VALUES (new.rowid, new.name, new.path, new.kind, new.note);
END;
CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, name, path, kind, note)
    VALUES ('delete', old.rowid, old.name, old.path, old.kind, old.note);
END;
CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE OF name, path, kind, note ON nodes
WHEN old.name IS NOT new.name OR old.path IS NOT new.path
  OR old.kind IS NOT new.kind OR old.note IS NOT new.note BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, name, path, kind, note)
    VALUES ('delete', old.rowid, old.name, old.path, old.kind, old.note);
    INSERT INTO nodes_fts(rowid, name, path, kind, note)
    VALUES (new.rowid, new.name, new.path, new.kind, new.note);
END;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    author TEXT NOT NULL,
    project TEXT NOT NULL,
    topic TEXT NOT NULL,
    text TEXT NOT NULL,
    evidence TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    merged_into INTEGER,
    suspect INTEGER NOT NULL DEFAULT 0,
    uses INTEGER NOT NULL DEFAULT 0,
    last_used REAL
);

CREATE TABLE IF NOT EXISTS idea_votes (
    idea_id INTEGER NOT NULL,
    voter TEXT NOT NULL,
    vote INTEGER NOT NULL,
    reason TEXT NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (idea_id, voter)
);

CREATE VIRTUAL TABLE IF NOT EXISTS ideas_fts USING fts5(
    topic, text, content='ideas', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS ideas_ai AFTER INSERT ON ideas BEGIN
    INSERT INTO ideas_fts(rowid, topic, text) VALUES (new.id, new.topic, new.text);
END;
CREATE TRIGGER IF NOT EXISTS ideas_ad AFTER DELETE ON ideas BEGIN
    INSERT INTO ideas_fts(ideas_fts, rowid, topic, text) VALUES ('delete', old.id, old.topic, old.text);
END;
"""


def home() -> Path:
    return Path(os.environ.get("LODESTAR_HOME") or Path.home() / ".lodestar")


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else home() / "index.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower():
            raise RuntimeError("This Python's SQLite was built without FTS5, which Lodestar needs.") from exc
        raise
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
    if "allow_foreign" not in columns:
        conn.execute("ALTER TABLE runs ADD COLUMN allow_foreign INTEGER NOT NULL DEFAULT 0")
    if "full" not in columns:
        conn.execute("ALTER TABLE runs ADD COLUMN full INTEGER NOT NULL DEFAULT 0")
    node_columns = {r["name"] for r in conn.execute("PRAGMA table_info(nodes)")}
    if "tokens" not in node_columns:
        conn.execute("ALTER TABLE nodes ADD COLUMN tokens INTEGER NOT NULL DEFAULT 0")
        # Collapsed folders are reused by mtime, so force them to be scanned again for tokens.
        conn.execute("UPDATE nodes SET mtime=-1 WHERE collapsed=1")
        conn.commit()
    return conn


def known_roots(conn: sqlite3.Connection) -> list[dict]:
    """Roots indexed before, each with whether the user allowed it although another user owns it."""
    rows = conn.execute(
        "SELECT root, max(allow_foreign) AS allow_foreign FROM runs GROUP BY root ORDER BY root"
    ).fetchall()
    return [{"root": r["root"], "allow_foreign": bool(r["allow_foreign"])} for r in rows]


def forget_root(conn: sqlite3.Connection, root: str) -> int:
    """Delete a root's nodes and runs, returning how many nodes were removed."""
    key = norm(root)
    nodes = conn.execute("DELETE FROM nodes WHERE root=?", (key,)).rowcount
    conn.execute("DELETE FROM runs WHERE root=?", (key,))
    conn.commit()
    return nodes
