"""Claude Code hook handlers that keep file reads cheap.

pre-read runs before the Read tool:
- it tells the agent roughly what reading the file will cost, and what has been saved so far;
- it stops a repeat read of a file that is already in the conversation and unchanged, once.
  If the agent asks again straight away it is let through and the saving is taken back.

session-start forgets what was read when the conversation is cleared or compacted, because the
file contents are no longer in context then.

Hooks must never break a read: every error ends in "do nothing".
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from pathlib import Path

from . import db, savings
from .tokens import file_tokens, human

LARGE = 6000        # tokens: also suggest a narrower read
MIN_BLOCK = 300     # tokens: smaller files are cheaper to re-read than to argue about
SHOW_FROM = int(os.environ.get("LODESTAR_SHOW_FROM", "500"))
KEEP_DAYS = 7
HOOKS = {
    "PreToolUse": [{"matcher": "Read",
                    "hooks": [{"type": "command", "command": "lodestar hook pre-read"}]}],
    "SessionStart": [{"hooks": [{"type": "command", "command": "lodestar hook session-start"}]}],
}


def _state_path(home: Path, session: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session)[:80] or "default"
    return home / "sessions" / f"{safe}.json"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"reads": {}}


def _save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))
    cutoff = time.time() - KEEP_DAYS * 86400
    for old in path.parent.glob("*.json"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def _context(text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def pre_read(payload: dict, conn=None, home: Path | None = None) -> dict | None:
    """Return the hook's JSON answer for a Read, or None to stay silent."""
    if payload.get("tool_name") != "Read":
        return None
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path")
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None

    home = Path(home) if home else db.home()
    own = conn is None
    conn = conn or db.connect()
    try:
        tokens = file_tokens(st.st_size, Path(path).suffix)
        session = str(payload.get("session_id") or "default")
        state_file = _state_path(home, session)
        state = _load(state_file)
        key = f"{os.path.abspath(path)}|{tool_input.get('offset', '')}|{tool_input.get('limit', '')}"
        seen = state["reads"].get(key)
        unchanged = bool(seen) and seen["mtime"] == st.st_mtime_ns and seen["size"] == st.st_size

        if unchanged and tokens >= MIN_BLOCK:
            if not seen.get("blocked"):
                seen["blocked"] = 1
                _save(state_file, state)
                savings.record(conn, "reread", avoided=tokens, session=session)
                return _deny(
                    f"Lodestar: {Path(path).name} (~{human(tokens)} tokens) is already in this "
                    "conversation and has not changed. Use what you have, or read a narrower "
                    "range with offset and limit. Ask again if you really need it re-read.")
            seen["blocked"] = 0
            _save(state_file, state)
            savings.record(conn, "reread_override", avoided=-tokens, session=session)
            return None

        state["reads"][key] = {"mtime": st.st_mtime_ns, "size": st.st_size, "blocked": 0}
        _save(state_file, state)
        if os.environ.get("LODESTAR_QUIET") or tokens < SHOW_FROM:
            return None
        text = f"Lodestar: reading {Path(path).name} costs about {human(tokens)} tokens."
        if tokens >= LARGE and not tool_input.get("limit"):
            text += " Consider offset and limit, or ask lodestar find/info first."
        saved = savings.total_avoided(conn)
        if saved > 0:
            text += f" Estimated saved so far: about {human(saved)} tokens."
        return _context(text)
    finally:
        if own:
            conn.close()


def session_start(payload: dict, home: Path | None = None) -> None:
    """Forget this session's reads when its context was cleared or compacted."""
    if payload.get("source") not in ("clear", "compact"):
        return
    home = Path(home) if home else db.home()
    try:
        _state_path(home, str(payload.get("session_id") or "default")).unlink(missing_ok=True)
    except OSError:
        pass


def hooks_snippet() -> str:
    return json.dumps({"hooks": HOOKS}, indent=2)
