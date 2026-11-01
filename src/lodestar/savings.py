"""A ledger of estimated tokens saved.

Two kinds of saving are counted, and only these:

- index answers: a collapsed folder shown as one line stands in for the listing an agent would
  otherwise have read, at PATH_TOKENS per entry, minus the tokens the answer itself cost;
- repeat reads: a file read that the guard hook stopped because the file was already in the
  conversation and unchanged, at the file's estimated tokens. If the agent insists and reads it
  anyway, the saving is taken back.

Everything is an estimate from tokens.py, not a tokenizer count or a bill.
"""

from __future__ import annotations

import sqlite3
import time

from .tokens import PATH_TOKENS, human, text_tokens

LISTING_NOTE = f"listing = {PATH_TOKENS} tokens per entry, text = 4 bytes per token"


def record(conn: sqlite3.Connection, kind: str, answer: str = "", *, avoided: int | None = None,
           session: str = "") -> None:
    """Log one event. For an index answer, avoided comes from the answer and is net of its cost."""
    delivered = text_tokens(answer) if answer else 0
    if avoided is None:
        avoided = max(getattr(answer, "avoided", 0) - delivered, 0)
    conn.execute("INSERT INTO savings(ts, session, kind, delivered, avoided) VALUES (?,?,?,?,?)",
                 (time.time(), session, kind, delivered, int(avoided)))
    conn.commit()


def totals(conn: sqlite3.Connection, since: float | None = None) -> dict:
    where, args = ("WHERE ts >= ?", (since,)) if since else ("", ())
    rows = conn.execute(
        f"SELECT kind, count(*) AS calls, sum(delivered) AS delivered, sum(avoided) AS avoided"
        f" FROM savings {where} GROUP BY kind", args).fetchall()
    by_kind = {r["kind"]: {"calls": r["calls"], "delivered": r["delivered"] or 0,
                           "avoided": r["avoided"] or 0} for r in rows}
    return {"by_kind": by_kind, "avoided": sum(v["avoided"] for v in by_kind.values())}


def total_avoided(conn: sqlite3.Connection) -> int:
    return totals(conn)["avoided"]


def report(conn: sqlite3.Connection, since: float | None = None) -> str:
    t = totals(conn, since)
    if not t["by_kind"]:
        return "No savings recorded yet. Ask lodestar find/tree/info, or enable the read guard."
    lines = [f"Estimated tokens saved: about {human(t['avoided'])}"]
    labels = {"find": "find", "tree": "tree", "info": "info",
              "reread": "repeat reads stopped", "reread_override": "stopped reads that were retried"}
    for kind, v in sorted(t["by_kind"].items()):
        name = labels.get(kind, kind)
        if v["avoided"] < 0:
            lines.append(f"  {name}: {v['calls']:,} calls, took back about {human(-v['avoided'])}")
        elif kind.startswith("reread"):
            lines.append(f"  {name}: {v['calls']:,} calls, saved about {human(v['avoided'])}")
        else:
            lines.append(f"  {name}: {v['calls']:,} calls, answers cost {human(v['delivered'])}, "
                         f"saved about {human(v['avoided'])}")
    lines.append(f"Estimates, not billing ({LISTING_NOTE}).")
    return "\n".join(lines)


def summary_line(conn: sqlite3.Connection) -> str:
    saved = total_avoided(conn)
    return f"estimated tokens saved so far: about {human(saved)}" if saved else ""
