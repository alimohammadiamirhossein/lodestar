# Changelog

## 0.1.0 (unreleased)

- Structure index with directory collapsing, kind detection, incremental refresh by mtime and a
  time budget.
- `find`, `info`, `tree` and `stats` over SQLite FTS5.
- `dream` and `brief`: nightly refresh and a brief of what changed, also as the `whats_new` tool
  and `/lodestar:brief`.
- `forget`; nested roots are refused.
- Token estimates (`~N tok`) in results, `lodestar savings` and the `token_savings` tool, and an
  opt-in read guard (`lodestar hooks`) that shows read costs and stops unchanged repeat reads.
- Search drops ancestor folders that matched only through a child and folds sibling files.
- CLI, MCP server over stdio, Claude Code plugin and a skill.
