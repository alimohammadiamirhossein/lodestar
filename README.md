# Lodestar

An MCP server that gives coding agents a cheap map of a huge file tree, so they ask the map first
and open only the files it names.

Status: alpha. It builds a structure index and answers `find`, `tree`, `info` and `stats`. It does
not read file contents and does not call any model.

See [docs/design.md](docs/design.md) for where it is going: code graph, experiment table,
nightly consolidation.
