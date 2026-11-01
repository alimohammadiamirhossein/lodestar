"""Structure index: walk the roots you name and keep one row per useful node.

Big directories collapse into a single node with counts, extension stats and a few sample names, so
a tree of tens of millions of files becomes tens of thousands of rows. File contents are never read.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

PRUNE = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "site-packages", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".cache", ".tox", ".ipynb_checkpoints", ".conda",
    "$RECYCLE.BIN",
})
COLLAPSE_FILES = 200   # a directory with this many direct files becomes one node
COLLAPSE_DIRS = 100    # a directory with this many subdirectories becomes one node
MAX_DEPTH = 16
SAMPLE_NAMES = 3
SIZE_SAMPLE = 20
FLUSH_ROWS = 20000

MEDIA = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".mp4",
                   ".avi", ".mov", ".mkv", ".webm", ".wav", ".mp3", ".flac"})
CHECKPOINTS = frozenset({".ckpt", ".pt", ".pth", ".safetensors", ".bin", ".onnx"})
DATA = frozenset({".npy", ".npz", ".h5", ".hdf5", ".parquet", ".csv", ".tsv", ".jsonl",
                  ".tfrecord", ".arrow", ".lmdb", ".mat", ".pkl"})
DOCS = frozenset({".md", ".rst", ".txt", ".pdf", ".tex", ".docx", ".pptx", ".bib"})
GROUPS = (("media", MEDIA), ("checkpoints", CHECKPOINTS), ("dataset", DATA), ("docs", DOCS))


class Timeout(Exception):
    """The time budget ran out; the index is left partial and nothing is deleted."""


class ForeignRoot(Exception):
    """The root belongs to another user."""


@dataclass
class Sub:
    """Totals for a subtree, excluding the directory itself."""

    files: int = 0
    dirs: int = 0
    size: int = 0
    approx: bool = False
    exts: Counter = field(default_factory=Counter)

    def add_child(self, other: Sub) -> None:
        self.files += other.files
        self.dirs += other.dirs + 1
        self.size += other.size
        self.approx = self.approx or other.approx
        self.exts.update(other.exts)


def norm(path: str) -> str:
    return os.path.abspath(path).replace("\\", "/")


def join(key: str, name: str) -> str:
    return key.rstrip("/") + "/" + name


def ext_of(name: str) -> str:
    i = name.rfind(".")
    return name[i:].lower() if i > 0 else ""


def classify(is_repo: bool, is_experiment: bool, sub: Sub) -> str:
    if is_repo:
        return "repo"
    if is_experiment:
        return "experiment"
    total = sum(sub.exts.values())
    if total >= 20:
        for kind, group in GROUPS:
            if sum(n for e, n in sub.exts.items() if e in group) / total >= 0.6:
                return kind
    return "dir"


class Indexer:
    def __init__(self, conn: sqlite3.Connection, root: str, run: int, *, full: bool = False,
                 deadline: float | None = None):
        self.conn = conn
        self.root = norm(root)
        self.run = run
        self.full = full
        self.deadline = deadline
        self.dev = os.stat(root).st_dev
        self.rows: list[tuple] = []
        self.scanned = 0
        self.reused = 0
        self.prev = {} if full else {
            r["path"]: r["mtime"] for r in conn.execute(
                "SELECT path, mtime FROM nodes WHERE root=? AND collapsed=1", (self.root,))}

    # -- plumbing ---------------------------------------------------------------------------

    def _tick(self) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise Timeout

    def flush(self) -> None:
        if not self.rows:
            return
        self.conn.executemany(
            "INSERT INTO nodes(path, parent, root, name, kind, is_dir, n_files, n_dirs, size,"
            " size_approx, exts, samples, mtime, collapsed, note, run)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(path) DO UPDATE SET parent=excluded.parent, root=excluded.root,"
            " name=excluded.name, kind=excluded.kind, is_dir=excluded.is_dir,"
            " n_files=excluded.n_files, n_dirs=excluded.n_dirs, size=excluded.size,"
            " size_approx=excluded.size_approx, exts=excluded.exts, samples=excluded.samples,"
            " mtime=excluded.mtime, collapsed=excluded.collapsed, note=excluded.note,"
            " run=excluded.run", self.rows)
        self.rows.clear()

    def _emit(self, *row) -> None:
        self.rows.append(row)
        if len(self.rows) >= FLUSH_ROWS:
            self.flush()

    def _other_device(self, entry: os.DirEntry) -> bool:
        # DirEntry.stat() reports st_dev 0 on Windows, where there is nothing to compare.
        dev = entry.stat().st_dev
        return dev != 0 and dev != self.dev

    # -- walking ----------------------------------------------------------------------------

    def run_walk(self) -> Sub:
        sub = self.walk(self.root if self.root != "/" else "/", "", 0)
        self.flush()
        return sub

    def _reuse(self, key: str) -> Sub:
        """Keep an unchanged collapsed node and everything below it without rescanning."""
        self.flush()
        row = self.conn.execute(
            "SELECT n_files, n_dirs, size, size_approx, exts FROM nodes WHERE path=?", (key,)
        ).fetchone()
        prefix = key.rstrip("/") + "/"
        self.conn.execute(
            "UPDATE nodes SET run=? WHERE root=? AND (path=? OR (path>=? AND path<?))",
            (self.run, self.root, key, prefix, prefix[:-1] + "0"))
        self.reused += 1
        return Sub(row["n_files"], row["n_dirs"], row["size"], bool(row["size_approx"]),
                   Counter(json.loads(row["exts"])))

    def walk(self, path: str, parent: str, depth: int) -> Sub:
        self._tick()
        key = norm(path)
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            return Sub()
        if self.prev.get(key) == mtime:
            return self._reuse(key)
        self.scanned += 1

        sub = Sub()
        subdirs: list[tuple[str, str]] = []
        kept: list[os.DirEntry] = []
        samples: list[str] = []
        sampled_sizes: list[int] = []
        n_files = 0
        has_git = has_experiment = False
        try:
            with os.scandir(path) as it:
                for e in it:
                    name = e.name
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if name == ".git":
                                has_git = True
                            elif name in (".hydra", "wandb"):
                                has_experiment = True
                            if name in PRUNE or self._other_device(e):
                                continue
                            subdirs.append((name, e.path))
                        elif e.is_file(follow_symlinks=False):
                            if name == ".git":
                                has_git = True
                            elif name.startswith("events.out.tfevents"):
                                has_experiment = True
                            n_files += 1
                            sub.exts[ext_of(name)] += 1
                            if len(samples) < SAMPLE_NAMES or name < samples[-1]:
                                samples.append(name)
                                samples.sort()
                                del samples[SAMPLE_NAMES:]
                            if n_files <= COLLAPSE_FILES:
                                kept.append(e)
                            if n_files == COLLAPSE_FILES:
                                sampled_sizes = [_size(x) for x in kept[:SIZE_SAMPLE]]
                    except OSError:
                        continue
        except OSError:
            return Sub()

        sub.files = n_files
        collapsed_files = n_files >= COLLAPSE_FILES
        if collapsed_files:
            sub.size = int(sum(sampled_sizes) / max(len(sampled_sizes), 1) * n_files)
            sub.approx = True
        file_rows = []
        if not collapsed_files:
            for e in kept:
                try:
                    st = e.stat()
                except OSError:
                    continue
                sub.size += st.st_size
                file_rows.append((join(key, e.name), key, self.root, e.name, "file", 0, 1, 0,
                                  st.st_size, 0, json.dumps({ext_of(e.name): 1}), "[]",
                                  st.st_mtime_ns, 0, "", self.run))
        kept.clear()

        collapse_dirs = len(subdirs) >= COLLAPSE_DIRS or depth >= MAX_DEPTH
        subdirs.sort()
        for name, sub_path in subdirs:
            if collapse_dirs:
                child = self.count(sub_path)
            else:
                child = self.walk(sub_path, key, depth + 1)
            sub.add_child(child)
        if collapse_dirs:
            samples = [n for n, _ in subdirs[:SAMPLE_NAMES]]

        for row in file_rows:
            self._emit(*row)
        collapsed = collapsed_files or collapse_dirs
        kind = classify(has_git, has_experiment, sub)
        top = [e or "(none)" for e, _ in sub.exts.most_common(5)]
        note = " ".join(top + samples) if collapsed else " ".join(top)
        self._emit(key, parent, self.root, key.rsplit("/", 1)[-1] or key, kind, 1, sub.files,
                   sub.dirs, sub.size, int(sub.approx), json.dumps(dict(sub.exts.most_common(30))),
                   json.dumps(samples), mtime, int(collapsed), note, self.run)
        return sub

    def count(self, path: str) -> Sub:
        """Count a subtree without storing anything below it; sizes are sampled."""
        sub = Sub(approx=True)
        stack = [path]
        while stack:
            self._tick()
            current = stack.pop()
            n = 0
            sizes: list[int] = []
            try:
                with os.scandir(current) as it:
                    for e in it:
                        try:
                            if e.is_symlink():
                                continue
                            if e.is_dir(follow_symlinks=False):
                                if e.name not in PRUNE and not self._other_device(e):
                                    stack.append(e.path)
                                    sub.dirs += 1
                            elif e.is_file(follow_symlinks=False):
                                n += 1
                                sub.exts[ext_of(e.name)] += 1
                                if len(sizes) < 5:
                                    sizes.append(_size(e))
                        except OSError:
                            continue
            except OSError:
                continue
            sub.files += n
            if sizes:
                sub.size += int(sum(sizes) / len(sizes) * n)
        return sub


def _size(entry: os.DirEntry) -> int:
    try:
        return entry.stat().st_size
    except OSError:
        return 0


def _check_owner(root: str) -> None:
    getuid = getattr(os, "getuid", None)
    if getuid is not None and os.stat(root).st_uid != getuid():
        raise ForeignRoot(f"{root} is owned by another user; pass allow_foreign to index it anyway")


def index_root(conn: sqlite3.Connection, root: str, *, full: bool = False,
               max_seconds: float | None = None, allow_foreign: bool = False) -> dict:
    """Index one root. Returns a summary; status is 'ok' or 'partial' (time budget ran out)."""
    root_path = str(Path(root).expanduser())
    if not os.path.isdir(root_path):
        raise FileNotFoundError(f"{root_path} is not a directory")
    if not allow_foreign:
        _check_owner(root_path)
    start = time.monotonic()
    deadline = start + max_seconds if max_seconds else None
    run = conn.execute(
        "INSERT INTO runs(root, started, status, allow_foreign) VALUES (?,?,'running',?)",
        (norm(root_path), time.time(), int(allow_foreign))).lastrowid
    indexer = Indexer(conn, root_path, run, full=full, deadline=deadline)
    status = "ok"
    try:
        sub = indexer.run_walk()
        conn.execute("DELETE FROM nodes WHERE root=? AND run<>?", (indexer.root, run))
    except Timeout:
        indexer.flush()
        sub = Sub()
        status = "partial"
    nodes = conn.execute("SELECT count(*) FROM nodes WHERE root=?", (indexer.root,)).fetchone()[0]
    conn.execute("UPDATE runs SET finished=?, status=?, nodes=? WHERE id=?",
                 (time.time(), status, nodes, run))
    conn.commit()
    return {"root": indexer.root, "status": status, "nodes": nodes, "files": sub.files,
            "dirs_scanned": indexer.scanned, "dirs_reused": indexer.reused,
            "seconds": round(time.monotonic() - start, 1)}
