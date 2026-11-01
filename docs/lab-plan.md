# Lab plan: advisor, DeepSeek routing and rollout

> Context for the shared lab server this project came from. Not part of Lodestar itself: the
> package has no dependency on Deputy, DeepSeek or any model. The roadmap is in [design.md](design.md).

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
