# Lodestar

**A cheap map of huge file trees for coding agents.** Agents ask the map first and open only the
files it names, instead of grepping and listing folders.

Lodestar is an MCP server, a Claude Code plugin and a small CLI. It indexes the roots you name into
one SQLite file. It never reads file contents and never calls a model.

> Status: alpha. The structure index and the queries work. Not measured yet: how many tokens it
> saves an agent. The code graph, experiment table and nightly consolidation in
> [docs/design.md](docs/design.md) are not built.

## How it works

- **Collapsing.** A folder with 200 or more files, or 100 or more subfolders, becomes one entry
  with its file count, size estimate, extension counts and a few sample names. An image dataset
  with two million files is one line.
- **Kinds.** Folders are labelled `repo` (has `.git`), `experiment` (TensorBoard events, `.hydra`,
  `wandb`), `dataset`, `checkpoints`, `media`, `docs` or `dir`.
- **Search.** SQLite FTS5 over names, paths, kinds and notes, with prefix matching.
- **Incremental.** An unchanged collapsed folder (same mtime) is kept without rescanning. Deleted
  paths leave the index. `--full` rescans everything; `--max-seconds` stops early and keeps a
  partial index without deleting anything.
- **Bounded answers.** Every query returns a capped list of short lines.
- **Shared machines.** You name the roots. A root owned by another user is refused unless you
  pass `--allow-foreign`. Symlinks are not followed, other filesystems are skipped, and caches,
  `.git` and `node_modules` are pruned. File names are indexed, so a file called `.env.bak` shows
  up by name; its contents are not read.

One measurement: a research folder with 544,192 files indexed to 171,342 nodes in 27 seconds on a
Windows laptop. Most nodes are individual files in mid-sized folders, so on a tree like that the
saving is about 3x in rows, not 100x; the big gains come from huge flat folders.

## Install

```bash
pip install lodestar-mcp     # once published; until then: pip install -e .
lodestar index ~/projects ~/datasets
lodestar find train loader
```

As a Claude Code plugin:

```bash
claude plugin marketplace add alimohammadiamirhossein/lodestar
claude plugin install lodestar@lodestar
```

Or add the server by hand: `claude mcp add lodestar -- lodestar serve`.

The index lives in `~/.lodestar/index.db` (override with `LODESTAR_HOME`).

## Commands and tools

| CLI | MCP tool | Does |
|---|---|---|
| `lodestar index [roots]` | `reindex` | Build or refresh the index. The tool only refreshes roots you indexed before. |
| `lodestar find words` | `find` | Search; `--kind` filters |
| `lodestar tree PATH` | `tree` | Indexed children of a folder, biggest first |
| `lodestar info PATH` | `info` | Kind, counts, size, extensions, sample names |
| `lodestar stats` | `stats` | What is indexed and when |
| `lodestar serve` | | Run the MCP server on stdio |

`survey.sh` in [scripts/](scripts/survey.sh) is a read-only survey of a server: disk, tools,
repos, experiment markers and file counts per top-level folder.

## Develop

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

MIT licensed.
