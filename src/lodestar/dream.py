"""Nightly refresh: reindex every known root, then write a short brief of what changed."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from . import db, ideas, index, query

WEEK = 7 * 24 * 60 * 60
GIB = 1024 ** 3
SPECIAL_KINDS = frozenset({"repo", "experiment", "dataset", "checkpoints"})
SECTION_LIMIT = 20
BRIEF_LIMIT = 80
KEEP_BRIEFS = 30
DATED = re.compile(r"\d{4}-\d{2}-\d{2}\.md")


class AlreadyRunning(Exception):
    """Another dream holds the lock."""


def _lock_pid(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _pid_alive_windows(pid: int) -> bool:
    # os.kill(pid, 0) terminates the process on Windows, so ask the OS about it instead.
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # access denied: the process exists
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _acquire_lock(path: Path) -> None:
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _pid_alive(_lock_pid(path)):
                raise AlreadyRunning("dream is already running")
            path.unlink(missing_ok=True)
            continue
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        return


def _snapshot(conn) -> dict:
    rows = conn.execute(
        "SELECT path, kind, n_files, size, root FROM nodes WHERE is_dir=1").fetchall()
    return {r["path"]: {"kind": r["kind"], "n_files": r["n_files"], "size": r["size"],
                        "root": r["root"]} for r in rows}


def _needs_full(conn, root: str, now: float) -> bool:
    last = conn.execute(
        "SELECT max(started) FROM runs WHERE root=? AND full=1 AND status='ok'", (root,)
    ).fetchone()[0]
    return last is None or now - last > WEEK


def _changed(old: dict, new: dict) -> bool:
    delta = new["n_files"] - old["n_files"]
    if abs(delta) >= 100:
        return True
    if old["n_files"] and abs(delta) / old["n_files"] >= 0.10:
        return True
    return abs(new["size"] - old["size"]) >= GIB


def _diff(before: dict, after: dict, roots: list[str]) -> tuple[list[str], list[str], list[str]]:
    new_lines: list[str] = []
    removed_lines: list[str] = []
    changed_lines: list[str] = []
    for root in roots:
        old_root = {p: r for p, r in before.items() if r["root"] == root}
        new_root = {p: r for p, r in after.items() if r["root"] == root}
        for path, row in new_root.items():
            if path not in old_root and row["kind"] in SPECIAL_KINDS:
                new_lines.append(f"- {row['kind']} {path}")
        for path, row in old_root.items():
            if path not in new_root and row["kind"] in SPECIAL_KINDS:
                removed_lines.append(f"- {row['kind']} {path}")
        for path, row in new_root.items():
            old = old_root.get(path)
            if old and _changed(old, row):
                changed_lines.append(
                    f"- {path}: {old['n_files']:,} -> {row['n_files']:,} files, "
                    f"{query.human_size(old['size'])} -> {query.human_size(row['size'])}")
    return new_lines, removed_lines, changed_lines


def _capped(lines: list[str]) -> list[str]:
    if len(lines) <= SECTION_LIMIT:
        return lines
    return lines[:SECTION_LIMIT] + [f"... and {len(lines) - SECTION_LIMIT} more"]


def _brief(now: float, refreshed: int, problems: list[str],
           changes: tuple[list[str], list[str], list[str]], idea_lines: list[str] = ()) -> str:
    new_lines, removed_lines, changed_lines = changes
    lines = [
        "# Lodestar brief " + time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
        f"{refreshed} roots refreshed, {len(problems)} problems",
    ]
    if not (new_lines or removed_lines or changed_lines):
        lines.append("Nothing changed.")
    for title, items in (("New", new_lines), ("Removed", removed_lines),
                         ("Changed", changed_lines), ("Ideas", list(idea_lines)),
                         ("Problems", problems)):
        if items:
            lines.append(f"## {title}")
            lines.extend(_capped(items))
    return "\n".join(lines[:BRIEF_LIMIT])


def _write(home: Path, now: float, text: str) -> None:
    dreams = home / "dreams"
    dreams.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d", time.localtime(now))
    (dreams / f"{stamp}.md").write_text(text)
    (dreams / "latest.md").write_text(text)
    dated = sorted(p for p in dreams.iterdir() if DATED.fullmatch(p.name))
    for old in dated[:-KEEP_BRIEFS]:
        old.unlink()


def dream(conn, home: Path, *, max_seconds: float | None = None, full: bool = False,
          now: float | None = None) -> str:
    """Refresh every known root and write a brief of what changed."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    lock = home / "dream.lock"
    _acquire_lock(lock)
    if hasattr(os, "nice"):
        try:
            os.nice(19)
        except OSError:
            pass
    try:
        current = time.time() if now is None else now
        roots = db.known_roots(conn)
        before = _snapshot(conn)
        deadline = index.start_deadline(max_seconds)
        statuses: dict[str, str] = {}
        problems: list[str] = []
        refreshed = 0
        for target in roots:
            root = target["root"]
            remaining = index.seconds_left(deadline)
            if remaining is not None and remaining <= 0:
                statuses[root] = "skipped"
                problems.append(f"{root}: skipped (time budget spent)")
                continue
            force_full = full or _needs_full(conn, root, current)
            try:
                result = index.index_root(
                    conn, root, full=force_full, max_seconds=remaining,
                    allow_foreign=target["allow_foreign"])
            except (FileNotFoundError, index.ForeignRoot, index.NestedRoot) as exc:
                statuses[root] = "failed"
                problems.append(f"{root}: {exc}")
                continue
            statuses[root] = result["status"]
            refreshed += 1
        for target in roots:
            if statuses.get(target["root"]) == "partial":
                problems.append(f"{target['root']}: partial refresh, changes not compared")
        after = _snapshot(conn)
        diff_roots = [t["root"] for t in roots if statuses.get(t["root"]) == "ok"]
        idea_lines = ideas.consolidate(conn, home, now=current)
        text = _brief(current, refreshed, problems, _diff(before, after, diff_roots), idea_lines)
        _write(home, current, text)
        return text
    finally:
        lock.unlink(missing_ok=True)


def latest_brief(home: Path, max_lines: int = 40) -> str:
    """Return the first max_lines of the latest brief, or a hint when there is none."""
    try:
        text = (Path(home) / "dreams" / "latest.md").read_text()
    except FileNotFoundError:
        return "No brief yet. Run: lodestar dream"
    return "\n".join(text.splitlines()[:max_lines])
