> Originally drafted as "atlas" for a shared lab server. Lodestar is the standalone project that
> implements the atlas layers; it has no dependency on Deputy. Section 3.2 (Deputy changes) belongs to
> the Deputy repo and is kept here only as context.

# Agent system design: shared lab server

Status: draft v1, 2026-11-01. Nothing built yet. Server-specific numbers are placeholders until
`survey.sh` has run on the server.

## 1. Goal and constraints

Make Claude Code agents on a shared lab server smarter and cheaper:

- they find what they need without reading many files,
- they remember what was learned across sessions,
- each model does only the work it is best value for.

The server, as you described it:

| Property | Implication for the design |
|---|---|
| Tens of millions of files (datasets, checkpoints, many repos) | Never summarize per file. Index structure for free, summarize only a few high-value nodes. |
| Large codebases | Code navigation through a symbol graph (tree-sitter), not grep + reading. |
| Many experiments | A structured experiment registry, filled by parsers, not by an LLM reading logs. |
| Several users and agents at once | Worktree per task, locks, a shared task board, careful CPU/IO use. |
| Shared with other people | Index only your own allowlisted folders. Nothing of other users goes to DeepSeek. |

## 2. What the research says (and what it rules out)

1. **The advisor pattern is now built into Claude Code.** Sonnet 5.5 as the main model with
   Fable or Opus 5.5 as advisor is an accepted pairing. You turn it on with `advisorModel` (or
   `/advisor`, or `claude --advisor`). The main model decides when to consult: before committing
   to a plan, when an error keeps coming back, and before saying it is done. Toggling it keeps
   the prompt cache. Each call re-reads the whole transcript uncached, so a lean main context makes
   the advisor cheaper. Anthropic reports Sonnet + Opus advisor at +2.7 points on SWE-bench
   Multilingual and 11.9% lower cost per task than Sonnet alone.
   → **Don't build a custom advisor. Use the native one.**
2. **Multi-agent setups burn tokens.** Anthropic measured about 15x the tokens of a chat for its
   multi-agent research system. Claude Code's docs give about 7x for agent teams in plan mode.
   → **Keep the hierarchy thin:** one Claude main per task, plus cheap helpers. Don't add
   Claude subagents by habit.
3. **Your own Deputy benchmark:** delegating *small* tasks saved nothing, because Claude spent as
   much reading and checking as it would have spent writing. → **DeepSeek wins on read-heavy
   and bulk work, and on large, well-specified changes whose tests do the checking.** It does not
   win on small edits.
4. **Context engineering** (Anthropic): the smallest set of high-signal tokens, loaded just in
   time. Skills load progressively (name and description always, body on use, files on demand).
   CLAUDE.md should stay under 200 lines. Hooks can shrink tool output before Claude sees it.
   CLIs cost less context than MCP tool listings.
5. **Code knowledge graphs** (CodeGraph, codebase-memory-mcp, code-review-graph) report 8x to
   120x fewer tokens on code questions. They use tree-sitter, so building the graph costs no LLM
   tokens. The figures are the authors' own benchmarks.
6. **graphify** builds a graph of mixed folders (code, papers, images). By default it sends docs
   to Claude for concept extraction. At tens of millions of files that is the opposite of saving
   tokens. Its own docs say small corpora gain little. → Borrow the idea of labelling edges
   EXTRACTED / INFERRED / AMBIGUOUS, not the tool.
7. **Karpathy's LLM wiki:** the agent compiles raw sources into interlinked markdown pages once,
   so a later question becomes a page read. → The semantic layer of the lodestar works this way,
   but only for high-value nodes.
8. **Dreaming:** Claude Code has a background memory consolidation feature called Auto Dream
   (gradual rollout, `/memory` shows the toggle if you have it). There is also a local
   `consolidate-memory` skill. Both tidy *memory files*. Neither indexes files, tracks
   experiments or proposes skills. → **Our dream does those parts. Auto Dream (if on) keeps
   handling auto-memory.**

## 3. Roles

```
                 you
                  │
          ┌───────▼────────┐  consults at decision points    ┌──────────────────────┐
          │  Sonnet 5.5    │ ───────────────────────────────▶│ Advisor              │
          │  main agent    │◀─── plan / correction / stop ───│ Opus 5.5 (default)   │
          │  plans, routes,│                                 │ Fable 5.1 (high      │
          │  judges, checks│                                 │ stakes, --advisor)   │
          └──┬─────┬────┬──┘                                 └──────────────────────┘
             │     │    │
   lodestar CLI │     │    │ Deputy (MCP)
  (no LLM)   │     │    └──────────────▶ DeepSeek V4.1 Flash: search, logs, papers,
             ▼     │                     checked coding, eval, plots, web triage, nightly jobs
   ┌──────────────┐│ Haiku 5.5 subagent
   │ Lodestar index  ││ (only to isolate verbose output: long test runs, web reading)
   │ + registry   │▼
   └──────────────┘
```

| Who | Does | Doesn't |
|---|---|---|
| **Sonnet 5.5** (main, effort medium) | Talks to you, plans, picks the route, writes judgment-heavy code, checks every result | Read many files before asking the lodestar; babysit long runs |
| **Advisor: Opus 5.5** | Plan check on non-trivial tasks, unstick after 2 failed attempts, sign-off before "done" on risky changes | Run on trivial tasks; call tools |
| **Advisor: Fable 5.1** | Same role for high-stakes sessions only (`claude --advisor fable`). On some plans Fable bills to usage credits, so it is not the default | Be on all the time |
| **DeepSeek via Deputy** | Most of the reading and writing: code search, logs, papers, nightly cards; coding behind a check; eval grading; plotting code; web triage; cross-review of its own diffs (§3.1) | Small unchecked edits; high-risk code; final decisions. Never sees secrets or other users' data |
| **Haiku 5.5 subagent** | Isolates verbose work and returns a short summary | Make decisions |

Deputy stays in **partner** mode (your current setting). Delegation needs an acceptance check
(tests or a script). Sonnet reads `git diff --stat`, the test output and the risky hunks. It does
not re-derive the whole change.

### 3.1 DeepSeek playbook

DeepSeek is capable. What decides whether it saves tokens is how much Claude spends checking its
work. **Give DeepSeek work whose output is checked by tests, scripts or another DeepSeek, so Claude
reads only a short summary.** At about $0.10 per million input tokens and $0.29 per million output
(Deputy's DeepInfra price table), reading a 10k-token page costs about a tenth of a cent.

**A. Work nobody reads line by line** (errors are cheap, because they are hints that get checked
when used)

| Job | DeepSeek returns | Claude reads |
|---|---|---|
| Code search, tracing a flow, reading other repos | `file:line` leads | only the cited lines |
| Training logs, crash traces, multi-run output | a short diagnosis or a registry row | the diagnosis |
| Papers and docs | summary, hyperparameters, claims with quotes | the summary |
| Nightly lodestar cards, run one-liners, session lessons | cards and candidates | the dream report |
| Second opinions (`consult`) | ideas from 2–3 angles | ideas, adopted only after its own check |

**B. Coding behind a check.** Small tasks don't pay off. Large tasks with automatic checks do.

1. Sonnet writes a brief and a check: a test, a script, or "this command must print X".
2. DeepSeek writes the code, runs the check and keeps fixing until it passes.
3. Sonnet reads the changed-file list, the check's result and only the risky hunks.

Fits: data loaders, train/eval scripts, config sweeps, tests, multi-file renames and refactors,
framework ports, CLIs, docs.

**C. Evaluation**

- **DeepSeek as grader** for large batches: rubric grading, A/B comparison, failure clustering.
  Calibrate first: Claude grades about 20 of the same items. If DeepSeek's grades mostly match,
  it grades the rest. If they often differ, fix the rubric before trusting it.
- Writing eval harnesses and metric code, behind a check (B).
- Turning big result tables into "what improved, what regressed, where".
- DeepSeek never grades code that DeepSeek wrote. Use tests or a separate reviewer lens.

**D. Visualization**

- DeepSeek writes plotting code from the data plus a written spec: matplotlib in your paper style,
  or HTML dashboards built from the registry.
- Deputy sends text only, so DeepSeek cannot see the figure it made. Checking is two steps:
  1. A script asserts the plot ran, the file exists, labels and legend are set, and there are no
     empty or NaN series.
  2. Claude looks at the final PNG once.
- For paper figures, the final visual judgment stays with you or Claude.

**E. Web triage: DeepSeek reads, Claude decides**

Claude Code's WebFetch already returns a small-model answer rather than the raw page, so one page
is not the problem. The gain comes with many pages:

1. A plain script, not a model, fetches the 10–30 search results and extracts clean text
   (e.g. trafilatura).
2. DeepSeek returns one row per page: a 3-line summary, relevance 0–5, and key facts with
   verbatim quotes.
3. Claude reads the table and opens only the top 1–2 pages. The quotes let it verify claims
   without reading everything.

Pages can carry hidden instructions aimed at AI models (prompt injection). The web-reading
DeepSeek is read-only, with no file edits and no commands, and its output is treated as data,
never as instructions.

**F. Cheap redundancy instead of Claude checking**

- **Best of N:** run 3 attempts and keep the one that passes the check.
- **Cross-review:** a second DeepSeek reviews the first one's diff. Claude sees only flagged
  issues.
- **Parallel workers:** independent pieces each get a worktree and their own DeepSeek. Sonnet
  briefs them and merges the results.

**Escalation ladder.** DeepSeek tries first. If the check fails twice, Sonnet takes over. If
Sonnet is stuck too, the advisor steps in.

**Who checks, by risk**

| Risk | Examples | Checked by |
|---|---|---|
| Low | Plots, one-off scripts, docs | The check passing (+ one look at the image for figures) |
| Medium | Data pipelines, refactors, eval code | The check + DeepSeek cross-review |
| High | Core training logic, security, anything that can lose data | Sonnet reads it, or writes it |

### 3.2 Deputy changes this needs

Today `delegate` takes a task and context, has no machine check or retries, and holds a global
lock, so only one coding task runs at a time (`deputy/src/deputy/server.py:320`). Deputy has no
web or image input.

| Change | What it does |
|---|---|
| `delegate(check=...)` | A command that must pass before Deputy reports back; the report includes its output |
| `delegate(attempts=N)` | Retry or best-of-N against the check; keep the passing attempt |
| `delegate(cross_review=true)` | A second DeepSeek reviews the diff and only flagged issues are returned |
| Worktree per task | Replace the global lock with one worktree per delegated task, so several run in parallel |
| `triage_web(urls, question)` | Code fetches and cleans pages; read-only DeepSeek returns summary, relevance and quotes per page |
| `grade(items, rubric)` | Batch grading with a calibration sample and an agreement score |
| `batch(job, inputs, budget)` | Nightly jobs (cards, run notes, lessons) with a hard dollar cap |

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
command prints at most ~20 lines and supports `--json`. A CLI costs no tool-listing context;
add an MCP wrapper only if agents running off the server need it.

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

## 5. Several agents at once

- **One git worktree per task** (`claude --worktree` / `isolation: worktree`), so agents never
  edit the same checkout.
- **Task board:** `~/.lodestar/board.sqlite`. Agents claim a task, take a lock per repo path and
  release it when done. A small `lodestar board` CLI does this. Agent teams (experimental, ~7x
  tokens) only if the board proves too weak.
- **Compute:** agents launch training through the lab's scheduler (SLURM or whatever the survey
  finds), never directly. Completion arrives as a notification or the next dream. No polling
  loops (they resend the full context every tick).
- **Good neighbour:** the indexer runs at night under `nice -n 19 ionice -c3`, with a wall-clock
  cap and an incremental walk (mtime pruning). No full rescans and no `du` over the whole
  filesystem.

## 6. Dreaming (nightly, per user)

A cron job on the server (or a desktop scheduled task over SSH) runs four stages:

1. **Index (no LLM).** Incremental walk, registry update, code-graph refresh for repos with new
   commits.
2. **Extract (DeepSeek, hard budget, e.g. $0.50 a night).**
   - Rebuild cards whose hash changed.
   - Read yesterday's Claude Code transcripts (`~/.claude/projects/*/*.jsonl`) and pull out
     candidate lessons: your corrections, approaches that failed, procedures repeated across
     sessions, experiment conclusions.
3. **Consolidate (one headless Sonnet run, `claude -p`, small).**
   - Merge candidates into memory: dedupe, fix contradictions, delete stale entries.
   - Lint links and cards.
   - When a procedure has appeared in 3 or more sessions, write a draft skill to
     `~/.lodestar/proposed-skills/`.
   - This run may write only to memory, lodestar, proposal folders and the instruction files listed
     in §6.1 (enforced with `--allowedTools` and permission rules).
4. **Refine the instruction files (applied automatically, see §6.1).**
5. **Report.** A morning brief: new results, changed repos, what was refined and why, proposals
   waiting for you, stale items found.

### 6.1 Refining the main .md files

The dream improves the files agents load and **applies the changes itself**. Every change is
evidence-backed, versioned and undoable.

**Files it may edit**

- `~/.claude/CLAUDE.md`
- your own project `CLAUDE.md` and `.claude/rules/*.md` files
- `SKILL.md` files of your skills
- `MEMORY.md` and memory files
- lodestar cards

**What triggers a refinement.** Each one comes from yesterday's transcripts or from a lint check:

| Signal | Refinement |
|---|---|
| You corrected the same thing in 2+ sessions | Add or sharpen the rule where it belongs (CLAUDE.md, a path rule, or the skill) |
| A rule exists but agents keep breaking it | Reword it: shorter, concrete, with the reason |
| A skill fired when it shouldn't have, or didn't fire when it should | Tune its `description` |
| A path, command or flag in a file no longer exists | Fix or remove the reference |
| Two instructions contradict each other | Keep the newer one you stated; remove the other |
| CLAUDE.md is over ~200 lines, or `/usage` says to trim it | Move workflow text into skills or path-scoped rules; compress wording |
| A procedure repeats and its skill is missing steps | Add the steps to the skill |

**Three tiers**

| Tier | Examples | What happens |
|---|---|---|
| Auto-apply | Broken references, duplicates, contradictions, compression, moving text to skills or rules, skill description tuning | Applied and listed in the report |
| Auto-apply and highlight | A new rule from repeated corrections; rewording an often-broken rule; new steps in a skill | Applied, shown at the top of the report with a one-command undo |
| Propose only | Deleting or weakening anything you wrote yourself or marked `<!-- keep -->`; new skills; anything about permissions, safety or `settings.json`; `CLAUDE.md` files committed to shared lab repos | Written to `~/.lodestar/proposals/`; shared-repo changes go on a local branch and are never pushed |

**Guardrails**

- **Versioned:** the instruction files live in a local git repo (`~/.claude` plus your project rule
  files). Each dream makes one commit with a message per change citing its evidence (session
  and line). `lodestar dream undo [change-id]` reverts one change or the whole night.
- **Validated before commit:** frontmatter parses, `[[links]]` resolve, size budgets hold
  (CLAUDE.md < 200 lines, MEMORY.md < 200 lines, skill descriptions < ~60 tokens), and a secret
  scan passes. A DeepSeek reviewer reads the diff against the evidence and flags edits the
  evidence doesn't support. Flagged edits drop to propose-only.
- **Limited drift:** at most ~5 edits per file per night and ~30 changed lines in total. Larger
  restructures become proposals.
- **Protected text:** your own feedback rules (for example the commit-style and push rules) are
  never removed automatically, only reworded with the meaning kept.
- **Timing:** runs only when no Claude session is active on that project, because CLAUDE.md edits
  don't reach a running session and they change its cached prompt prefix. New sessions pick up
  the refined files.
- **Harm check:** weekly, compare corrections per session and the benchmark score before and
  after the refinements. If a refined file made things worse, revert it automatically and note
  it in the report.

## 7. Privacy and safety on a shared server

- **Allowlist roots** you own (your home, your project dirs). Never walk other users' homes,
  even if they are world-readable.
- **Before any DeepSeek call:** apply a denylist (`.env*`, `*key*`, `*secret*`, `.ssh`,
  credentials, tokens) and run a secret scanner. Matching files get metadata-only nodes.
- **Ask your PI or lab first:** is sending unpublished research code and papers to a third-party
  API (DeepInfra) allowed? If not, run the semantic layer with Claude only, or turn it off. The
  structure index, code graph and registry need no LLM, so they still work.
- Keep the DeepInfra key in your environment on the server, never in a repo or in the lodestar.

## 8. Measure before trusting it

A 20-task benchmark on the server, in the style of Deputy's bench:

- 6 navigation ("where is the data loader for X")
- 5 experiment questions ("best run on Y and what changed")
- 5 code edits with hidden tests
- 4 cross-repo questions
- for the DeepSeek jobs in §3.1: grader agreement with Claude on a labelled sample, and web
  triage recall (does the top-2 contain the page Claude would have picked?)

Record tokens, dollars, wall time and success for:

- (A) plain Sonnet
- (B) Sonnet + advisor
- (C) B + lodestar
- (D) C + Deputy routing

Ship each layer only if it pays for itself, in line with your "push only when sure" rule.

Targets to check, not promises:

- L0 under 4k tokens
- navigation answers under 1k tokens
- at least 3x fewer tokens than (A) on navigation and experiment questions

## 9. Rollout

| Phase | Work | Cost to try |
|---|---|---|
| P0 | Run `survey.sh`. Settings: main `sonnet`, `advisorModel: "opus"`, effort medium. Routing rules in `~/.claude/CLAUDE.md`. Install Deputy on the server | ~1 hour |
| P0.5 | Deputy: `delegate(check, attempts, cross_review)`, worktree per task, then `triage_web`, `grade`, `batch` (§3.2). Benchmarked like Deputy's existing bench | 2–3 days |
| P1 | Structure index + collapse + FTS + `lodestar find`. Benchmark tasks A/B/C on navigation | 1–2 days |
| P2 | Experiment registry parsers for your 2–3 main repos + `lodestar runs` | 1–2 days |
| P3 | Code graph wrap + `repo-onboard` skill | 1 day |
| P4 | Semantic cards (after the lab OK) + nightly dream | 2 days |
| P5 | Task board + worktree flow for parallel agents | 1 day |

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
