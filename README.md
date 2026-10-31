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
| `automagic run [--dry-run] [--restart] [--update] [--yes] [--max-concurrency N]` | `/automagic-run` | Launches one Copilot CLI agent per work item in its worktree, in dependency waves. Agents commit on their own branch; nothing is pushed or merged. |
| `automagic review [target] [--verify] [--ai] [--base SHA]` | `/automagic-review` | Reviews one work item's branch against its nodes: scope (files vs the node's declared files; guardrail/CI files are errors), size budget, context-file guardrails, optional `verify_command` and AI review. Writes `.automagic/plan/reviews/<id>.md` with a verdict (`pass`/`concerns`/`fail`) and a proposed commit message. Exits 1 on `fail`. |
| `automagic worktrees [--create] [--yes]` | `/automagic-worktrees` | Shows or creates worktrees for the saved plan, each with a `.automagic/BRIEF.md`. |

Python projects get uv, ruff, bandit and zizmor in the pre-commit config; TypeScript projects get eslint, prettier and zizmor. Hooks are run with [prek](https://github.com/j178/prek) (`prek install`, `prek run --all-files`), which reads `.pre-commit-config.yaml`.
Slash commands are written as `.github/prompts/*.prompt.md` (VS Code) and `.github/skills/*/SKILL.md` (Copilot CLI).

## How planning works

1. A provider drafts planning topics (assumptions and decisions), stored as nodes in a Merkle graph. Each topic's hash covers its content and its dependencies' hashes.
2. For each unsettled topic (dependencies first) the interviewer reviews it, then asks a question or declares it settled. An answer revises the topic, which changes its hash and its dependents' hashes, re-opening their previous agreement.
3. Interview passes repeat until every topic is settled, or `max_rounds` is hit. Type `/done` to pause; `--resume` continues the saved pass and queue.
4. Oversized topics are split by the provider, then topics are packed dependencies-first into work items under the token and changed-line budgets.
5. Worktrees are proposed as `git worktree add` commands and created only on confirmation.

Planning topics are what the interview settles; work items are what agents implement. One work item can contain several topics, so 19 planning topics can become eight work items ([sizing.py](src/automagic/sizing.py)).

Pause/resume preserves the exact unanswered question without another question-generation call. Submitted answers are saved before the provider revises a topic, so `--resume` also recovers after Ctrl+C or a provider failure without asking for that answer again; failed calls are not retried automatically. A paused pass keeps its existing budget. After exhaustion, an explicit `--resume` adds another configured batch: with eight interview passes per batch, the next pass is `9/16`, not `1/8` ([interview.py](src/automagic/interview.py)).

Older plans without a checkpoint retain their decisions but start a fresh validation pass with a notice. If a checkpoint is invalid or no longer matches the graph, the CLI asks before discarding the saved position and any unapplied input; declining leaves the files unchanged. Invalid graph data remains an error ([cli.py](src/automagic/cli.py)).

Before each question the CLI prints where you are ([progress.py](src/automagic/progress.py)), and shows a spinner while the provider works:

```
Planning: [########------------] 4/10 topics settled · pass 2/8
5 topics pending this pass · 6 unsettled overall
```

- `settled/total topics` counts topics whose current hash equals their validated hash. Settled planning topics are not completed implementation work.
- `pass N/M` identifies the current interview pass and its limit, not the number of work items.
- `N topics pending this pass` counts unsettled topics in the saved pass's queue, including the current topic.
- `N unsettled overall` is the exact current number of unsettled topics across the plan, not a forecast of the questions remaining. A topic review can settle it without asking a question.
- Counts can move backwards. After an answer, `answer applied: +1 new topic, 2 settled topics re-opened` says why: the answer added topics or changed a topic that settled topics depend on. New or re-opened topics outside the current queue wait for another pass.

State is stored in `.automagic/plan/`; commit it so reviewers can inspect the plan:

- `graph.json`: the Merkle graph and versioned interview checkpoint, saved together atomically; the checkpoint does not affect Merkle hashes ([merkle.py](src/automagic/merkle.py)).
- `history.jsonl`: one event per validation, answer or amendment (question, answer, old/new hash, invalidated ancestors).
- `plan.md`: Mermaid graph (work items as subgraphs, oversized ones outlined red), work-item table, planning topic details with full hashes, and the interview history table.
- `plan.html`: a self-contained page (`automagic show --open`). Its heading distinguishes the two counts, for example `Work breakdown: 8 nodes covering 19 planning topics`. Each graph node represents a work item containing one or more planning topics. Hover over a node to inspect its topics. Tick a work item and its unfinished prerequisites are selected automatically (dashed); finished items are skipped. The page prints `automagic run --only <ids>` in dependency order; run it to create the worktrees and start the agents. The page itself launches nothing. Failed items are selectable again to retry them ([htmlview.py](src/automagic/htmlview.py)).

`docs/plan/` is meant to be committed: `<topic-id>.md` holds each topic's agreed scope, files, dependencies, hash chain and interview decisions, and `graph.json` lets a fresh clone continue the plan. That published graph omits the runtime interview checkpoint ([plandocs.py](src/automagic/plandocs.py)); preserve `.automagic/plan/graph.json` too for exact in-pass continuation. `automagic check` reports topic documents that are out of date with the graph; `automagic show` regenerates them. Internal names such as `node`, `round` and `max_rounds` in saved data and configuration are unchanged. See [ADR 0003](docs/adr/0003-committed-plan-documents.md) and [ADR 0005](docs/adr/0005-atomic-interview-checkpoints.md).

## Changing a finished plan

`automagic plan --amend "<change>"` edits or adds planning topics. Topics that depend on them get new hashes and are re-examined by the interview; the rest stay settled. The command lists edited, new and re-opened topics, and asks before applying a change that contradicts agreed intent (`--yes` accepts). The old run state is then stale: `automagic run` lists the stale work items, and `automagic run --update` redoes only those (see below).

## Running agents

`automagic run` executes the saved plan:

`automagic run --only wi-02,wi-04` (work item or planning topic ids) runs just those items plus any unfinished prerequisites; finished items are skipped.

1. Work items run in waves: every item whose prerequisites are done runs in parallel, up to `max_concurrency`.
2. A dependent worktree is branched off its first prerequisite branch, and every prerequisite branch not yet in its history is merged in, also when the worktree already exists (for example from `automagic worktrees --create`) or when an earlier attempt failed on a merge conflict.
3. Each agent runs `runner_command` in its worktree with `.automagic/BRIEF.md` as its brief. automagic commits the result (never the brief) and runs `verify_command`; if a formatter fixes files, the fixes are committed and the check repeats once.
4. A failed item blocks its dependents; siblings continue. An agent that exits cleanly but leaves the branch unchanged from its base also fails. Re-running skips finished items and retries the rest. State is in `.automagic/plan/run.json`, logs in `.automagic/plan/runs/<id>.log`.
5. Each item records a hash of its nodes (which cover their prerequisites). After a plan change, `automagic run` stops and lists the stale items: `--update` redoes only those, continuing on their existing branch with the prerequisites merged in again, and `--restart` redoes everything. Both keep each branch's recorded base, so reviews still see all of the item's work. See [ADR 0004](docs/adr/0004-run-state-tracks-work-item-hashes.md).
6. Nothing is pushed or merged. Review each branch and merge it yourself.

The default runner denies `git push` and `git merge` via `--deny-tool`, which is a best-effort guard rather than a sandbox: run agents only on repositories where you accept unattended shell access. Agents only see committed files, so commit `AGENTS.md` and the plan before running.

## Reviewing work

`automagic review wi-02` (a work item id, a planning topic id, or a branch; with no target it uses the current branch, so run it as `automagic review --path <project>` from inside a worktree) diffs the agent's own commits: the base is the commit recorded when `automagic run` prepared the worktree, so merged-in prerequisites are not counted. Findings:

- **scope**: changed files not declared by the item's planning topics are warnings; changes to guardrail, CI or config files outside the declared files are errors (`GUARDED` in [review.py](src/automagic/review.py): `AGENTS.md`, `docs/GUARDRAILS.md`, `.github/workflows|prompts|skills|instructions/*`, `.pre-commit-config.yaml`, `pyproject.toml`, `eslint.config.js`, `.automagic/*`); so is a committed brief. Topics that declare no files skip the warning but not the error, so list such a file in a planning topic to be allowed to change it.
- **size**: more changed lines than `max_changed_lines` is an error; far above the topic estimates is a warning.
- **guardrails**: `automagic check` problems in changed files are errors.
- **verify** (`--verify`) and **ai** (`--ai`, via the provider, truncated to half the context budget).

The deterministic checks cannot judge whether the code does what the planning topic asks. For that, use `/automagic-review`: it tells the agent to read the report, review the diff against the planning topic and guardrails, and use the GitKraken MCP tools (`git_log_or_diff` for the diff and log, `git_commit_composer` to propose commit grouping and messages for uncommitted work, `gitlens_start_review` for an existing pull request) when they are available, falling back to plain git. The CLI itself does not call MCP tools.

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

`uv run pytest` measures source coverage and requires at least 90%.

```sh
uv run pytest && uv run ruff check . && uv run bandit -c pyproject.toml -r src
```
