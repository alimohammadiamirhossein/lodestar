"""Token estimates, the savings ledger and the read guard."""

import io
import json
import os
import sys
import time

import pytest

from lodestar import cli, db, guard, index, query, savings, tokens


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LODESTAR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LODESTAR_QUIET", raising=False)
    return tmp_path / "home"


@pytest.fixture
def lab(tmp_path):
    root = tmp_path / "lab"
    (root / "code").mkdir(parents=True)
    (root / "code" / "model.py").write_bytes(b"x" * 400)
    for i in range(250):
        p = root / "images" / f"img_{i:04d}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 300)
    for i in range(250):
        p = root / "notes" / f"n_{i:04d}.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 400)
    return root


@pytest.fixture
def conn(home, lab):
    c = db.connect()
    index.index_root(c, str(lab))
    return c


def read(path, session="s1", **extra):
    return {"tool_name": "Read", "session_id": session,
            "tool_input": {"file_path": str(path), **extra}}


def big(tmp_path, name="big.py", size=4000):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def allowed(answer):
    return answer is None or "permissionDecision" not in answer["hookSpecificOutput"]


# -- estimates -----------------------------------------------------------------------------------

def test_file_tokens_and_binary_files():
    assert tokens.file_tokens(400, ".py") == 100
    assert tokens.file_tokens(401, ".PY") == 101
    assert tokens.file_tokens(10_000, ".jpg") == 0
    assert tokens.file_tokens(0, ".py") == 0


def test_human_tokens():
    assert [tokens.human(n) for n in (7, 999, 1000, 12_345, 2_500_000)] == [
        "7", "999", "1.0k", "12.3k", "2.5M"]


def test_index_stores_tokens_per_file_and_folder(conn, lab):
    def row(path):
        return conn.execute("SELECT tokens, collapsed FROM nodes WHERE path=?",
                            (index.norm(str(path)),)).fetchone()

    assert row(lab / "code" / "model.py")["tokens"] == 100
    assert row(lab / "images")["tokens"] == 0
    assert row(lab / "notes")["collapsed"] == 1 and row(lab / "notes")["tokens"] == 250 * 100


def test_lines_show_what_reading_costs(conn, lab):
    assert "~100 tok" in query.find(conn, "model")
    notes = query.info(conn, str(lab / "notes"))
    assert "reading all the text here would cost about 25.0k tokens" in notes
    assert "tok" not in query.info(conn, str(lab / "images"))


def test_old_database_gets_the_tokens_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(db.SCHEMA.replace(",\n    tokens INTEGER NOT NULL DEFAULT 0\n);", "\n);"))
    raw.commit()
    raw.close()
    c = db.connect(path)
    assert "tokens" in {r["name"] for r in c.execute("PRAGMA table_info(nodes)")}


# -- ledger --------------------------------------------------------------------------------------

def test_collapsed_folder_replaces_a_listing(conn, lab):
    assert query.find(conn, "images").avoided == 250 * tokens.PATH_TOKENS
    assert query.find(conn, "model").avoided == 0
    assert query.tree(conn, str(lab)).avoided == 2 * 250 * tokens.PATH_TOKENS


def test_record_is_net_of_the_answer_cost(conn):
    answer = query.find(conn, "images")
    savings.record(conn, "find", answer)
    expected = 250 * tokens.PATH_TOKENS - tokens.text_tokens(answer)
    assert savings.total_avoided(conn) == expected
    savings.record(conn, "find", query.find(conn, "model"))
    assert savings.total_avoided(conn) == expected


def test_report_and_summary_line(conn):
    assert "No savings recorded" in savings.report(conn)
    assert savings.summary_line(conn) == ""
    savings.record(conn, "reread", avoided=5000)
    out = savings.report(conn)
    assert "about 5.0k" in out and "repeat reads stopped" in out and "Estimates, not billing" in out
    assert "5.0k" in savings.summary_line(conn)
    assert "estimated tokens saved so far" in query.stats(conn)


def test_report_since_filters_old_events(conn):
    savings.record(conn, "reread", avoided=1000)
    conn.execute("UPDATE savings SET ts=?", (time.time() - 10 * 86400,))
    conn.commit()
    assert "No savings recorded" in savings.report(conn, since=time.time() - 86400)
    assert "1.0k" in savings.report(conn)


def test_cli_find_is_recorded_and_savings_command_reports_it(home, lab, capsys):
    cli.main(["index", str(lab)])
    capsys.readouterr()
    cli.main(["find", "images"])
    capsys.readouterr()
    assert cli.main(["savings"]) == 0
    out = capsys.readouterr().out
    assert "find: 1 calls" in out and "Estimated tokens saved" in out


# -- the read guard ------------------------------------------------------------------------------

def test_first_read_gets_a_cost_hint(home, tmp_path):
    text = guard.pre_read(read(big(tmp_path)))["hookSpecificOutput"]["additionalContext"]
    assert "costs about 1.0k tokens" in text and "saved so far" not in text


def test_small_files_get_no_hint(home, tmp_path):
    assert guard.pre_read(read(big(tmp_path, "tiny.py", 400))) is None


def test_large_files_suggest_a_narrower_read(home, tmp_path):
    out = guard.pre_read(read(big(tmp_path, "huge.py", 40_000)))
    assert "offset and limit" in out["hookSpecificOutput"]["additionalContext"]
    ranged = guard.pre_read(read(big(tmp_path, "huge2.py", 40_000), limit=50))
    assert "offset and limit" not in ranged["hookSpecificOutput"]["additionalContext"]


def test_hint_shows_savings_so_far(home, tmp_path, conn):
    savings.record(conn, "reread", avoided=12_345)
    text = guard.pre_read(read(big(tmp_path)))["hookSpecificOutput"]["additionalContext"]
    assert "Estimated saved so far: about 12.3k tokens" in text


def test_unchanged_repeat_read_is_stopped_once(home, tmp_path):
    path = big(tmp_path)
    assert guard.pre_read(read(path)) is not None
    stopped = guard.pre_read(read(path))["hookSpecificOutput"]
    assert stopped["permissionDecision"] == "deny"
    assert "already in this conversation" in stopped["permissionDecisionReason"]
    assert savings.total_avoided(db.connect()) == 1000


def test_insisting_gets_through_and_takes_the_saving_back(home, tmp_path):
    path = big(tmp_path)
    guard.pre_read(read(path))
    guard.pre_read(read(path))
    assert allowed(guard.pre_read(read(path)))
    assert savings.total_avoided(db.connect()) == 0
    assert guard.pre_read(read(path))["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_changed_file_and_other_sessions_are_not_blocked(home, tmp_path):
    path = big(tmp_path)
    guard.pre_read(read(path))
    assert allowed(guard.pre_read(read(path, session="s2")))
    path.write_bytes(b"y" * 4001)
    later = time.time_ns() + 10**9
    os.utime(path, ns=(later, later))
    assert allowed(guard.pre_read(read(path)))


def test_small_files_are_never_blocked(home, tmp_path):
    path = big(tmp_path, "tiny.py", 400)
    for _ in range(3):
        assert guard.pre_read(read(path)) is None


def test_ranges_are_tracked_separately(home, tmp_path):
    path = big(tmp_path)
    guard.pre_read(read(path, offset=1, limit=50))
    assert allowed(guard.pre_read(read(path, offset=60, limit=50)))
    assert allowed(guard.pre_read(read(path)))


def test_compact_forgets_reads_but_startup_does_not(home, tmp_path):
    path = big(tmp_path)
    guard.pre_read(read(path))
    guard.session_start({"session_id": "s1", "source": "startup"})
    assert guard.pre_read(read(path))["hookSpecificOutput"]["permissionDecision"] == "deny"
    guard.session_start({"session_id": "s1", "source": "compact"})
    assert allowed(guard.pre_read(read(path)))


def test_quiet_mode_still_stops_repeats_but_adds_no_hint(home, tmp_path, monkeypatch):
    monkeypatch.setenv("LODESTAR_QUIET", "1")
    path = big(tmp_path)
    assert guard.pre_read(read(path)) is None
    assert guard.pre_read(read(path))["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_other_tools_and_bad_paths_are_ignored(home, tmp_path):
    assert guard.pre_read({"tool_name": "Bash", "tool_input": {"command": "ls"}}) is None
    assert guard.pre_read(read(tmp_path / "missing.py")) is None
    assert guard.pre_read(read(tmp_path)) is None
    assert guard.pre_read({"tool_name": "Read", "tool_input": {}}) is None


def test_cli_hook_reads_stdin_and_prints_json(home, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(read(big(tmp_path)))))
    assert cli.main(["hook", "pre-read"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_cli_hook_never_fails_on_bad_input(home, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert cli.main(["hook", "pre-read"]) == 0
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"source": "compact"})))
    assert cli.main(["hook", "session-start"]) == 0


def test_hooks_command_prints_settings(capsys):
    assert cli.main(["hooks"]) == 0
    settings = json.loads(capsys.readouterr().out)
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Read"
    assert "SessionStart" in settings["hooks"]
