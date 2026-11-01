"""Tests for the nightly dream refresh and the brief it writes."""

import os
import shutil
import subprocess
import sys
import time

import pytest

from lodestar import cli, db, dream, index, server


def touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LODESTAR_HOME", str(tmp_path / "home"))
    root = tmp_path / "lab"
    touch(root / "code" / ".git" / "HEAD")
    touch(root / "code" / "main.py")
    touch(root / "plain" / "note.txt")
    touch(root / "experiment" / "events.out.tfevents.1")
    conn = db.connect()
    index.index_root(conn, str(root))
    conn.close()
    return root


def test_dream_reports_new_changed_and_removed(home):
    conn = db.connect()
    dream.dream(conn, db.home())
    touch(home / "newrepo" / ".git" / "HEAD")
    for i in range(150):
        touch(home / "plain" / f"file_{i:03d}.txt")
    shutil.rmtree(home / "experiment")
    text = dream.dream(conn, db.home())
    assert f"- repo {index.norm(str(home / 'newrepo'))}" in text
    assert index.norm(str(home / "plain")) in text
    assert f"- experiment {index.norm(str(home / 'experiment'))}" in text
    assert (db.home() / "dreams" / "latest.md").read_text() == text


def test_first_dream_with_empty_snapshot_lists_new_repo(home):
    conn = db.connect()
    conn.execute("DELETE FROM nodes")
    conn.commit()
    text = dream.dream(conn, db.home())
    assert f"- repo {index.norm(str(home / 'code'))}" in text
    assert len(text.splitlines()) <= 80


def test_second_dream_with_no_changes(home):
    conn = db.connect()
    dream.dream(conn, db.home())
    text = dream.dream(conn, db.home())
    assert "Nothing changed." in text
    assert "## New" not in text
    assert "## Removed" not in text
    assert "## Changed" not in text


def test_live_lock_raises(home):
    conn = db.connect()
    lock = db.home() / "dream.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()))
    with pytest.raises(dream.AlreadyRunning):
        dream.dream(conn, db.home())
    assert lock.exists()


def test_dead_lock_is_taken_over_and_removed(home):
    conn = db.connect()
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    lock = db.home() / "dream.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(process.pid))
    dream.dream(conn, db.home())
    assert not lock.exists()


def test_partial_refresh_skips_diff(home, monkeypatch):
    conn = db.connect()
    dream.dream(conn, db.home())
    shutil.rmtree(home / "experiment")
    ticks = iter(range(100_000))
    monkeypatch.setattr(index.time, "monotonic", lambda: next(ticks))
    text = dream.dream(conn, db.home(), max_seconds=2)
    assert "## Problems" in text
    assert "partial" in text
    assert "## Removed" not in text


def test_missing_root_does_not_stop_other_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("LODESTAR_HOME", str(tmp_path / "home"))
    gone = tmp_path / "gone"
    touch(gone / "data" / "one.csv")
    other = tmp_path / "other"
    touch(other / "keep" / "file.txt")
    conn = db.connect()
    index.index_root(conn, str(gone))
    index.index_root(conn, str(other))
    dream.dream(conn, db.home())
    shutil.rmtree(gone)
    touch(other / "newrepo" / ".git" / "HEAD")
    text = dream.dream(conn, db.home())
    assert "## Problems" in text and str(gone) in text and "not a directory" in text
    assert f"- repo {index.norm(str(other / 'newrepo'))}" in text
    assert "1 roots refreshed" in text


def test_only_thirty_dated_briefs_are_kept(home):
    conn = db.connect()
    dreams = db.home() / "dreams"
    dreams.mkdir(parents=True, exist_ok=True)
    for day in range(1, 36):
        (dreams / f"2020-01-{day:02d}.md").write_text("old")
    now = time.time()
    dream.dream(conn, db.home(), now=now)
    dated = [p for p in dreams.iterdir() if p.name != "latest.md"]
    assert len(dated) == 30
    assert (dreams / time.strftime("%Y-%m-%d", time.localtime(now))).with_suffix(".md").exists()


def test_new_section_is_capped(home):
    conn = db.connect()
    dream.dream(conn, db.home())
    for i in range(25):
        touch(home / f"repo_{i:02d}" / ".git" / "HEAD")
    text = dream.dream(conn, db.home())
    lines = text.splitlines()
    start = lines.index("## New")
    section = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        section.append(line)
    assert len([line for line in section if line.startswith("- ")]) == 20
    assert "... and 5 more" in section


def test_latest_brief_missing_and_max_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("LODESTAR_HOME", str(tmp_path / "home"))
    assert dream.latest_brief(db.home()) == "No brief yet. Run: lodestar dream"
    dreams = db.home() / "dreams"
    dreams.mkdir(parents=True)
    (dreams / "latest.md").write_text("one\ntwo\nthree")
    assert dream.latest_brief(db.home(), max_lines=2) == "one\ntwo"


def test_cli_dream_and_brief(home, capsys):
    assert cli.main(["dream"]) == 0
    latest = (db.home() / "dreams" / "latest.md").read_text()
    assert capsys.readouterr().out == latest + "\n"
    assert cli.main(["brief"]) == 0
    assert capsys.readouterr().out == latest + "\n"


def test_cli_dream_already_running(home, capsys):
    lock = db.home() / "dream.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()))
    assert cli.main(["dream"]) == 1
    assert "dream is already running" in capsys.readouterr().err


def test_whats_new_returns_latest_brief(home):
    conn = db.connect()
    text = dream.dream(conn, db.home())
    assert server.whats_new() == text


def _record_calls(calls):
    def fake_index_root(conn, root, *, full=False, max_seconds=None, allow_foreign=False):
        calls.append(full)
        return {"root": index.norm(root), "status": "ok"}
    return fake_index_root


def test_full_is_forced_when_last_full_run_is_old(home, monkeypatch):
    conn = db.connect()
    now = time.time()
    root = index.norm(str(home))
    conn.execute(
        "INSERT INTO runs(root, started, finished, status, full) VALUES (?,?,?,'ok',1)",
        (root, now - 8 * 24 * 60 * 60, now - 8 * 24 * 60 * 60))
    conn.commit()
    calls = []
    monkeypatch.setattr(dream.index, "index_root", _record_calls(calls))
    dream.dream(conn, db.home(), now=now)
    assert calls == [True]


def test_full_is_not_forced_when_last_full_run_is_recent(home, monkeypatch):
    conn = db.connect()
    now = time.time()
    root = index.norm(str(home))
    conn.execute(
        "INSERT INTO runs(root, started, finished, status, full) VALUES (?,?,?,'ok',1)",
        (root, now - 24 * 60 * 60, now - 24 * 60 * 60))
    conn.commit()
    calls = []
    monkeypatch.setattr(dream.index, "index_root", _record_calls(calls))
    dream.dream(conn, db.home(), now=now)
    assert calls == [False]


def test_index_root_records_full_flag(home):
    conn = db.connect()
    index.index_root(conn, str(home), full=True)
    row = conn.execute("SELECT full FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["full"] == 1


def test_pid_alive_does_not_kill_the_process_it_checks():
    assert dream._pid_alive(os.getpid()) is True
    assert dream._pid_alive(None) is False
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    assert dream._pid_alive(process.pid) is False
