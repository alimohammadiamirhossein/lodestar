"""Read side: short, capped answers an agent can afford to read."""

from __future__ import annotations

import json
import re
import sqlite3
import time

from .index import norm

MAX_LIMIT = 50


def human_size(n: int, approx: bool = False) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            text = f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            return ("~" if approx else "") + text
        value /= 1024
    return str(n)


def _line(r: sqlite3.Row) -> str:
    if not r["is_dir"]:
        return f"file     {r['path']}  {human_size(r['size'])}"
    tag = "collapsed" if r["collapsed"] else "dir"
    return (f"{r['kind']:<11}{r['path']}  [{tag}] {r['n_files']:,} files, "
            f"{human_size(r['size'], bool(r['size_approx']))}")


def fts_query(text: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", text)
    return " ".join(f'"{t}"*' for t in tokens)


def find(conn: sqlite3.Connection, query: str, kind: str = "", limit: int = 20) -> str:
    match = fts_query(query)
    if not match:
        return "Give at least one word or number to search for."
    limit = max(1, min(limit, MAX_LIMIT))
    sql = ("SELECT n.* FROM nodes_fts JOIN nodes n ON n.rowid = nodes_fts.rowid"
           " WHERE nodes_fts MATCH ?")
    args: list = [match]
    if kind:
        sql += " AND n.kind = ?"
        args.append(kind)
    sql += " ORDER BY bm25(nodes_fts, 5.0, 1.0, 1.0, 2.0) LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    if not rows:
        return f"No match for {query!r}. Run `lodestar index` if the tree changed."
    return "\n".join(_line(r) for r in rows)


def info(conn: sqlite3.Connection, path: str) -> str:
    key = norm(path)
    r = conn.execute("SELECT * FROM nodes WHERE path=?", (key,)).fetchone()
    if r is None:
        return f"{key} is not in the index. Use find to locate it, or index its root first."
    if not r["is_dir"]:
        return _line(r)
    exts = json.loads(r["exts"])
    top = ", ".join(f"{e or '(none)'} {n:,}" for e, n in list(exts.items())[:6])
    lines = [
        f"{r['kind']}: {r['path']}",
        f"{r['n_files']:,} files in {r['n_dirs']:,} subdirectories, "
        f"{human_size(r['size'], bool(r['size_approx']))}",
        f"extensions: {top or 'none'}",
    ]
    samples = json.loads(r["samples"])
    if samples:
        lines.append("samples: " + ", ".join(samples))
    if r["collapsed"]:
        lines.append("collapsed: individual entries are not indexed; list the folder to see more")
    run = conn.execute("SELECT finished FROM runs WHERE id=?", (r["run"],)).fetchone()
    if run and run["finished"]:
        lines.append("indexed: " + time.strftime("%Y-%m-%d %H:%M", time.localtime(run["finished"])))
    return "\n".join(lines)


def tree(conn: sqlite3.Connection, path: str, limit: int = 40) -> str:
    key = norm(path)
    limit = max(1, min(limit, MAX_LIMIT * 2))
    total = conn.execute("SELECT count(*) FROM nodes WHERE parent=?", (key,)).fetchone()[0]
    if total == 0:
        known = conn.execute("SELECT 1 FROM nodes WHERE path=?", (key,)).fetchone()
        return ("Nothing indexed below it." if known else
                f"{key} is not in the index. Use find to locate it, or index its root first.")
    rows = conn.execute(
        "SELECT * FROM nodes WHERE parent=? ORDER BY is_dir DESC, n_files DESC, name LIMIT ?",
        (key, limit)).fetchall()
    lines = [_line(r) for r in rows]
    if total > len(rows):
        lines.append(f"... {total - len(rows):,} more")
    return "\n".join(lines)


def stats(conn: sqlite3.Connection) -> str:
    roots = conn.execute(
        "SELECT root, count(*) AS nodes, sum(CASE WHEN collapsed=1 THEN 1 ELSE 0 END) AS collapsed,"
        " sum(CASE WHEN parent='' THEN n_files ELSE 0 END) AS files FROM nodes GROUP BY root"
    ).fetchall()
    if not roots:
        return "The index is empty. Run `lodestar index <root>`."
    lines = []
    for r in roots:
        last = conn.execute(
            "SELECT status, finished FROM runs WHERE root=? ORDER BY id DESC LIMIT 1",
            (r["root"],)).fetchone()
        when = (time.strftime("%Y-%m-%d %H:%M", time.localtime(last["finished"]))
                if last and last["finished"] else "unfinished")
        status = last["status"] if last else "?"
        lines.append(f"{r['root']}: {r['files']:,} files in {r['nodes']:,} nodes "
                     f"({r['collapsed']:,} collapsed), last run {when} ({status})")
    kinds = conn.execute(
        "SELECT kind, count(*) AS n FROM nodes WHERE is_dir=1 GROUP BY kind ORDER BY n DESC"
    ).fetchall()
    lines.append("directories by kind: " + ", ".join(f"{k['kind']} {k['n']:,}" for k in kinds))
    return "\n".join(lines)
