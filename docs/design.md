> Roadmap for Lodestar. The advisor, DeepSeek routing and rollout material that this project came
> from is in [lab-plan.md](lab-plan.md). Lodestar has no dependency on Deputy or any model.

# Lodestar design

Status: the structure index, queries, MCP server and dream (section 6) are built. The code graph,
semantic cards and experiment registry below are plans.

## 4. Knowledge layers

How much loads, and when:

| Layer | What | Loaded | Budget |
|---|---|---|---|
| L0 | `~/.claude/CLAUDE.md` (routing rules, ~60 lines) + `MEMORY.md` index | every session | < 4k tokens total |
| L1 | Skills (descriptions always, bodies on use); path-scoped `.claude/rules/` per repo | on demand | ~60 tokens per skill description |
| L2 | **Lodestar**: structure index, code graph, semantic cards, experiment registry | via `lodestar` CLI | any answer ≤ ~1k tokens |
| L3 | Raw files | only after the lodestar has named them | read the named lines, not whole files |

The rule for agents: **ask the lodestar first, then open only what it names.**

### 4.1 Lodestar: four indexes, each built the cheapest way that works

**(a) Structure index (no LLM).** A pruned walk of the allowlisted roots into SQLite (WAL mode).
The key step is **collapsing**: any directory dominated by many files of one kind becomes a single
node with stats. Example: a folder of 2M JPEGs becomes
`dataset: imagenet_val/ 2.0M files, .jpg, 140 GB, samples: n01440764_10026.JPEG …`.
Checkpoint folders, wandb run directories, conda envs, `.git` internals and caches collapse or
get pruned the same way. That is how tens of millions of files become about 10^4 to 10^5 nodes.
Each node is classified as `repo` (has `.git`), `experiment` (config + logs or checkpoints),
`dataset`, `media`, `docs` or `other`, and records owner, size, mtime and a content hash for
small text files.

**(b) Code graph (no LLM).** For each repo, tree-sitter extracts symbols, imports and call edges.
The graph is rebuilt only for repos with new commits. Queries: `lodestar def <sym>`,
`lodestar refs <sym>`, `lodestar callers <fn>`, `lodestar impact <file>`. Start with an existing tool
(CodeGraph or codebase-memory-mcp, wrapped by the `lodestar` CLI) and replace it only if it fails
on your repos.

**(c) Semantic cards (DeepSeek, budget-capped).** Short markdown cards, Karpathy-wiki style,
**only** for high-value nodes:

- one card per repo (purpose, entry points, how to run, where configs live),
- one per *module directory* (not per file),
- one per paper or doc (from the first pages),
- one per experiment group.

Each card carries `[[links]]` to related cards, memories and skills, so cards, memory and skills
form one graph. Edges are labelled `extracted` (from code or config) or `inferred` (from the LLM).
Cards are rebuilt only when the node's content hash changes.

**(d) Experiment registry (parsers first).** Deterministic parsers read Hydra/YAML/JSON configs,
final metrics from logs (per-repo regex), checkpoint lists, and wandb/TensorBoard summaries.
They write one row per run: `run_id, repo, git_commit, config_diff_vs_parent, metrics, status,
started, owner, path`. DeepSeek adds only a one-line "what this run was testing". Queries:
`lodestar runs --repo X --best fid`, `lodestar diff-runs A B`, `lodestar runs --since 7d`.

**Search.** SQLite FTS5 (BM25) over paths, names and cards. Embeddings come later, and only if
measured recall needs them.

**Interface.** One `lodestar` CLI on the server plus one `lodestar` skill that documents it. Every
command prints a capped list of short lines. The MCP server is built; its tool listing is about 3 KB.

### 4.2 Skills

User-level skills for procedures you repeat; the dream proposes more as it sees them recur.
Server-specific starters:

- `lodestar`: how to query before reading
- `run-experiment`: launch through the scheduler, register the run, never poll
- `compare-runs`: registry diff, then open the configs
- `repo-onboard`: create a repo card and `.claude/rules/` the first time an agent works in a repo
- `handoff-to-deputy`: brief template with acceptance checks

Each skill links to its lodestar cards and to related skills. The dream proposes new skills (§6) and
you approve them.

## 6. Dreaming (built, v1)

`lodestar dream` is a nightly, model-free job. It never reads file contents and never calls a model.

1. Takes `dream.lock` (stale locks from dead processes are taken over) and lowers its priority.
2. Refreshes every known root under one time budget (`--max-seconds`). A root gets a full rescan
   when none succeeded in the last 7 days, which bounds the staleness described in the README.
3. Compares directories before and after and writes `dreams/YYYY-MM-DD.md` and `dreams/latest.md`
   (30 kept): new and removed repos, experiments, datasets and checkpoint folders; folders whose
   file count changed by 100 or 10%, or size by 1 GiB; and problems (missing, foreign, nested or
   partial roots). A partial refresh never reports removals.
4. Agents read it with the `whats_new` tool or `/lodestar:brief`; people with `lodestar brief`.

Schedule it with one line: `0 3 * * * ionice -c3 lodestar dream --max-seconds 1800 >> ~/.lodestar/dream.log 2>&1`.

Deferred, with the reason:

- **Transcript mining and automatic edits of CLAUDE.md and SKILL.md.** Transcripts carry other
  people's code and pasted secrets, and nothing yet measures whether an edit helped. Claude Code's
  Auto Dream already consolidates memory files.
- **Semantic cards.** Idea instead: `lodestar note PATH TEXT`, agent-written notes joined into the search, with no model call.
- **Dollar budgets.** Lodestar has no API client, so it cannot count spend.

## 7. Privacy and safety on a shared server

- **Allowlist roots** you own (your home, your project dirs). Never walk other users' homes,
  even if they are world-readable.
- **Before any DeepSeek call:** apply a denylist (`.env*`, `*key*`, `*secret*`, `.ssh`,
  credentials, tokens) and run a secret scanner. Matching files get metadata-only nodes.
- **Ask your PI or lab first:** is sending unpublished research code and papers to a third-party
  API (DeepInfra) allowed? If not, run the semantic layer with Claude only, or turn it off. The
  structure index, code graph and registry need no LLM, so they still work.
- Keep the DeepInfra key in your environment on the server, never in a repo or in the lodestar.

## 10. Open questions (the survey answers most)

- Host name and how you reach it (SSH alias)? Does Claude Code run there, or only locally?
- OS, filesystem (NFS / Lustre / local), and your quota?
- Scheduler (SLURM? none?) and the GPU etiquette rules?
- Is outbound HTTPS to api.deepinfra.com and api.anthropic.com allowed?
- Which roots are yours to index? Experiment tracking in use (wandb, TensorBoard, plain logs)?
- Lab policy on third-party APIs (§7)?

## Sources

- [Advisor tool, Claude Code docs](https://code.claude.com/docs/en/advisor)
- [The advisor strategy, Claude blog](https://claude.com/blog/the-advisor-strategy)
- [Effective context engineering for AI agents](https://anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Equipping agents with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [Manage costs, Claude Code docs](https://code.claude.com/docs/en/costs.md)
- [Memory, Claude Code docs](https://code.claude.com/docs/en/memory)
- [Subagents, Claude Code docs](https://code.claude.com/docs/en/sub-agents)
- [Auto Dream write-up (third-party)](https://claudefa.st/blog/guide/mechanics/auto-dream)
- [codebase-memory-mcp write-up](https://dev.to/deusdata/how-i-cut-my-ai-coding-agents-token-usage-by-120x-with-a-code-knowledge-graph-4a3d)
- [CodeGraph write-up](https://www.besthub.dev/articles/codegraph-slashes-token-usage-by-59-for-claude-code-and-codex-be9d7ca4361f)
- [graphify](https://claudelog.com/claude-code-mcps/graphify)
- [Karpathy LLM wiki guide](https://blog.starmorph.com/blog/karpathy-llm-wiki-knowledge-base-guide)
- [Deputy README and bench](https://github.com/alimohammadiamirhossein/deputy)
