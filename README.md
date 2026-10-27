# automagic

CLI that sets up baseline repository files and plans work with AI agents.

## Install

```sh
uv tool install .        # or: uv run automagic ...
```

## Commands

| Command | Slash command | What it does |
|---|---|---|
| `automagic init [--language python\|typescript] [--dry-run] [--force]` | `/automagic-init` | Scaffolds pre-commit, `docs/adr`, `AGENTS.md`, Copilot instructions, guardrails, CI and slash commands. Never overwrites without `--force`. |
| `automagic check` | `/automagic-check` | Enforces context-size limits and grounding (relative links resolve, docs are indexed). Runs as a pre-commit hook. |
| `automagic context` | | Regenerates the `docs/CONTEXT.md` index. |
| `automagic plan "<goal>" [--resume]` | `/automagic-plan` | Interviews you until the Merkle assumption graph is stable, then proposes worktrees. |
| `automagic show [--node <id>]` | `/automagic-show` | Regenerates and prints `plan.md` (Mermaid Merkle graph, work items, interview history) without AI calls; `--node` prints a node's hash chain. |
| `automagic run [--dry-run] [--restart] [--yes] [--max-concurrency N]` | `/automagic-run` | Launches one Copilot CLI agent per work item in its worktree, in dependency waves. Agents commit on their own branch; nothing is pushed or merged. |
| `automagic review [target] [--verify] [--ai] [--base SHA]` | `/automagic-review` | Reviews one work item's branch against its nodes: scope (files vs the node's declared files; guardrail/CI files are errors), size budget, context-file guardrails, optional `verify_command` and AI review. Writes `.automagic/plan/reviews/<id>.md` with a verdict (`pass`/`concerns`/`fail`) and a proposed commit message. Exits 1 on `fail`. |
| `automagic worktrees [--create] [--yes]` | `/automagic-worktrees` | Shows or creates worktrees for the saved plan, each with a `.automagic/BRIEF.md`. |

Python projects get uv, ruff, bandit and zizmor in the pre-commit config; TypeScript projects get eslint, prettier and zizmor. Hooks are run with [prek](https://github.com/j178/prek) (`prek install`, `prek run --all-files`), which reads `.pre-commit-config.yaml`.
Slash commands are written as `.github/prompts/*.prompt.md` (VS Code) and `.github/skills/*/SKILL.md` (Copilot CLI).

## How planning works

1. A provider drafts a graph of assumptions and decisions. Each node's hash covers its content and its dependencies' hashes.
2. For each unvalidated node (dependencies first) the interviewer asks a question or declares it settled. An answer revises the node, which changes its hash and its ancestors', invalidating them.
3. Rounds repeat until a round leaves every node validated, or `max_rounds` is hit (resume with `--resume`). Type `/done` to stop early.
4. Oversized nodes are split by the provider, then nodes are packed dependencies-first into work items under the token and changed-line budgets.
5. Worktrees are proposed as `git worktree add` commands and created only on confirmation.

Before each question the CLI prints where you are ([progress.py](src/automagic/progress.py)), and shows a spinner while the provider works:

```
[########------------] 4/10 settled · round 2/8 · 5 left this round · at least 6 checks to go
```

- `settled/total` counts nodes whose current hash equals their validated hash.
- `left this round` is the nodes still to be checked in the current round.
- `at least N checks to go` is the number of unsettled nodes. It is a lower bound, not a forecast: the interviewer decides whether to ask follow-ups, so there is no percentage.
- Counts can move backwards. After an answer, `answer applied: +1 new node, 2 settled nodes re-opened` says why: the answer added nodes or changed a node that settled nodes depend on.

State is stored in `.automagic/plan/`; commit it so reviewers can inspect the plan:

- `graph.json`: the Merkle graph and resume state.
- `history.jsonl`: one event per validation, answer or amendment (question, answer, old/new hash, invalidated ancestors).
- `plan.md`: Mermaid graph (work items as subgraphs, oversized ones outlined red), work-item table, node details with full hashes, and the interview history table.
- `plan.html`: a self-contained page (`automagic show --open`). Tick a work item and its unfinished prerequisites are selected automatically (dashed); finished items are skipped. The page prints `automagic run --only <ids>` in dependency order; run it to create the worktrees and start the agents. The page itself launches nothing. Failed items are selectable again to retry them.

`docs/plan/` is meant to be committed: `<node>.md` holds each node's agreed scope, files, dependencies, hash chain and interview decisions, and `graph.json` lets a fresh clone continue the plan. `automagic check` reports node documents that are out of date with the graph; `automagic show` regenerates them. See [ADR 0003](docs/adr/0003-committed-plan-documents.md).

## Changing a finished plan

`automagic plan --amend "<change>"` edits or adds nodes. Nodes that depend on them get new hashes and are re-examined by the interview; the rest stay validated. The command lists edited, new and invalidated nodes, and asks before applying a change that contradicts agreed intent (`--yes` accepts). The old run state is then stale and needs `automagic run --restart`.

## Running agents

`automagic run` executes the saved plan:

`automagic run --only wi-02,wi-04` (work item or node ids) runs just those items plus any unfinished prerequisites; finished items are skipped.

1. Work items run in waves: every item whose prerequisites are done runs in parallel, up to `max_concurrency`.
2. A dependent worktree is branched off its first prerequisite branch, and the other prerequisite branches are merged in (a merge conflict fails the item).
3. Each agent runs `runner_command` in its worktree with `.automagic/BRIEF.md` as its brief. automagic commits the result (never the brief) and runs `verify_command`; if a formatter fixes files, the fixes are committed and the check repeats once.
4. A failed item blocks its dependents; siblings continue. Re-running skips finished items and retries the rest. State is in `.automagic/plan/run.json`, logs in `.automagic/plan/runs/<id>.log`. A changed plan requires `--restart`.
5. Nothing is pushed or merged. Review each branch and merge it yourself.

The default runner denies `git push` and `git merge` via `--deny-tool`, which is a best-effort guard rather than a sandbox: run agents only on repositories where you accept unattended shell access. Agents only see committed files, so commit `AGENTS.md` and the plan before running.

## Reviewing work

`automagic review wi-02` (a work item id, a node id, or a branch; with no target it uses the current branch, so run it as `automagic review --path <project>` from inside a worktree) diffs the agent's own commits: the base is the commit recorded when `automagic run` prepared the worktree, so merged-in prerequisites are not counted. Findings:

- **scope**: changed files not declared by the item's nodes are warnings; changes to guardrail, CI or config files (`AGENTS.md`, `.github/workflows/*`, `.pre-commit-config.yaml`, ...) outside the declared files are errors; so is a committed brief. Nodes that declare no files skip this check.
- **size**: more changed lines than `max_changed_lines` is an error; far above the node estimates is a warning.
- **guardrails**: `automagic check` problems in changed files are errors.
- **verify** (`--verify`) and **ai** (`--ai`, via the provider, truncated to half the context budget).

The deterministic checks cannot judge whether the code does what the node asks. For that, use `/automagic-review`: it tells the agent to read the report, review the diff against the node and guardrails, and use the GitKraken MCP tools (`git_log_or_diff` for the diff and log, `git_commit_composer` to propose commit grouping and messages for uncommitted work, `gitlens_start_review` for an existing pull request) when they are available, falling back to plain git. The CLI itself does not call MCP tools.

## Configuration

`.automagic/config.toml`:

| Key | Default |
|---|---|
| `max_context_tokens` | 40000 |
| `max_changed_lines` | 400 |
| `max_rounds` | 8 |
| `max_instruction_bytes` | 8000 |
| `provider` / `provider_command` | `copilot` / `copilot` |
| `runner_command` | Copilot CLI template with `{prompt}` (see `config.py`) |
| `verify_command` | `prek run --all-files` (empty disables) |
| `max_concurrency` / `agent_timeout` | 3 / 3600 seconds |

The default provider runs prompts through the GitHub Copilot CLI (`copilot -p`). Add providers by implementing `Provider.complete(prompt) -> str` in `providers.py`.
Token counts are estimated (about 4 characters per token).

## Development

```sh
uv run pytest && uv run ruff check . && uv run bandit -c pyproject.toml -r src
```
