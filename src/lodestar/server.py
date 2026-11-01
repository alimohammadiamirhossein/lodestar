"""MCP server: the same queries as the CLI, as tools an agent can call."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import db, dream, index, query, savings

INSTRUCTIONS = """\
Lodestar is a map of the user's file trees. It never reads file contents.

Ask it before searching or listing folders yourself, then open only what it names:
- `find`: search names, paths and notes. Big folders are collapsed into one entry with counts,
  extensions and sample names.
- `tree`: list the indexed children of a path, biggest first.
- `info`: describe one path.
- `stats`: what is indexed and when.
- `whats_new`: what changed at the last nightly dream.
- `token_savings`: estimated tokens saved so far. Lines show `~N tok`, what reading a file costs.
- `reindex`: refresh the roots the user indexed before. It cannot add new roots.
A collapsed entry means its individual files are not indexed; list that folder to see them.
"""

mcp = FastMCP("lodestar", instructions=INSTRUCTIONS)


def _read_only(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, readOnlyHint=True, destructiveHint=False,
                           idempotentHint=True, openWorldHint=False)


@mcp.tool(annotations=_read_only("Find files and folders"))
def find(query_text: str, kind: str = "", limit: int = 20) -> str:
    """Search the index. query_text: words from a name or path, or an extension like jpg.
    kind: optional filter (repo, experiment, dataset, checkpoints, media, docs, dir, file).
    limit: at most 50 results."""
    with _conn() as conn:
        answer = query.find(conn, query_text, kind, limit)
        savings.record(conn, "find", answer)
        return answer


@mcp.tool(annotations=_read_only("List indexed children"))
def tree(path: str, limit: int = 40) -> str:
    """List the indexed children of a folder, biggest first."""
    with _conn() as conn:
        answer = query.tree(conn, path, limit)
        savings.record(conn, "tree", answer)
        return answer


@mcp.tool(annotations=_read_only("Describe a path"))
def info(path: str) -> str:
    """Describe one indexed path: kind, counts, size, extensions, sample names, last index time."""
    with _conn() as conn:
        answer = query.info(conn, path)
        savings.record(conn, "info", answer)
        return answer


@mcp.tool(annotations=_read_only("What is indexed"))
def stats() -> str:
    """Show the indexed roots, node counts and the last run for each."""
    with _conn() as conn:
        return query.stats(conn)


@mcp.tool(annotations=_read_only("What changed at the last dream"))
def whats_new() -> str:
    """What changed at the last nightly dream."""
    return dream.latest_brief(db.home())


@mcp.tool(annotations=_read_only("Estimated tokens saved"))
def token_savings() -> str:
    """Estimated tokens saved so far by lodestar answers and the read guard. Estimates only."""
    with _conn() as conn:
        return savings.report(conn)


@mcp.tool(annotations=ToolAnnotations(title="Refresh the index", readOnlyHint=False,
                                      destructiveHint=False, idempotentHint=True,
                                      openWorldHint=False))
def reindex(max_seconds: float = 120) -> str:
    """Re-index the roots the user indexed before (never new ones). Stops after max_seconds
    and keeps a partial index."""
    with _conn() as conn:
        roots = db.known_roots(conn)
        if not roots:
            return "Nothing to refresh: the user has not indexed any root yet."
        lines = []
        deadline = index.start_deadline(max_seconds)
        for target in roots:
            remaining = index.seconds_left(deadline)
            if remaining is not None and remaining <= 0:
                lines.append(f"{target['root']}: skipped (time budget spent)")
                continue
            try:
                s = index.index_root(conn, target["root"], max_seconds=remaining,
                                     allow_foreign=target["allow_foreign"])
            except (FileNotFoundError, index.ForeignRoot, index.NestedRoot) as exc:
                lines.append(f"{target['root']}: {exc}")
                continue
            lines.append(f"{s['root']}: {s['status']}, {s['files']:,} files, {s['nodes']:,} nodes, "
                         f"{s['dirs_reused']:,} dirs reused, {s['seconds']}s")
        return "\n".join(lines)


class _conn:
    """Open the index for one call and close it after."""

    def __enter__(self):
        self.conn = db.connect()
        return self.conn

    def __exit__(self, *exc):
        self.conn.close()


def run() -> None:
    mcp.run()
