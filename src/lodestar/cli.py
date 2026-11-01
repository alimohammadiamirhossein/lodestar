"""Command line: index a tree, query it, or serve it over MCP."""

from __future__ import annotations

import argparse
import sys

from . import __version__, db, index, query


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lodestar", description="A cheap map of huge file trees.")
    p.add_argument("--version", action="version", version=f"lodestar {__version__}")
    sub = p.add_subparsers(dest="cmd")

    i = sub.add_parser("index", help="index roots (default: the roots indexed before)")
    i.add_argument("roots", nargs="*")
    i.add_argument("--full", action="store_true", help="rescan collapsed directories too")
    i.add_argument("--max-seconds", type=float, help="stop early and keep a partial index")
    i.add_argument("--allow-foreign", action="store_true", help="allow roots owned by other users")

    f = sub.add_parser("find", help="search names, paths and notes")
    f.add_argument("query", nargs="+")
    f.add_argument("--kind", default="")
    f.add_argument("--limit", type=int, default=20)

    n = sub.add_parser("info", help="describe one indexed path")
    n.add_argument("path")

    t = sub.add_parser("tree", help="list the indexed children of a path")
    t.add_argument("path")
    t.add_argument("--limit", type=int, default=40)

    sub.add_parser("stats", help="what is indexed")
    sub.add_parser("serve", help="run the MCP server on stdio")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0
    if args.cmd == "serve":
        from .server import run
        run()
        return 0

    conn = db.connect()
    if args.cmd == "index":
        targets = ([{"root": r, "allow_foreign": args.allow_foreign} for r in args.roots]
                   or db.known_roots(conn))
        if not targets:
            print("Name a root to index, for example: lodestar index ~/projects", file=sys.stderr)
            return 2
        status = 0
        for target in targets:
            root = target["root"]
            try:
                s = index.index_root(conn, root, full=args.full, max_seconds=args.max_seconds,
                                     allow_foreign=target["allow_foreign"])
            except (FileNotFoundError, index.ForeignRoot) as exc:
                print(f"{root}: {exc}", file=sys.stderr)
                status = 1
                continue
            print(f"{s['root']}: {s['status']}, {s['files']:,} files, {s['nodes']:,} nodes, "
                  f"{s['dirs_scanned']:,} dirs scanned, {s['dirs_reused']:,} reused, {s['seconds']}s")
        return status
    if args.cmd == "find":
        print(query.find(conn, " ".join(args.query), args.kind, args.limit))
    elif args.cmd == "info":
        print(query.info(conn, args.path))
    elif args.cmd == "tree":
        print(query.tree(conn, args.path, args.limit))
    elif args.cmd == "stats":
        print(query.stats(conn))
    return 0
