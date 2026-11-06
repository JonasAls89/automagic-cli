# automagic

CLI that sets up baseline repository files and plans work with AI agents.

## Install

```sh
uv tool install .        # or: uv run automagic ...
```

## Commands

| Command | Slash command | What it does |
|---|---|---|
| `automagic init [--language python\|typescript] [--dry-run] [--force]` | `/automagic-init` | Scaffolds development-tool configs, pre-commit, `GLOSSARY.md`, `docs/adr`, `AGENTS.md`, Copilot instructions, guardrails, CI and slash commands. Never overwrites without `--force`. |
| `automagic check` | `/automagic-check` | Enforces context-size limits and grounding (relative links resolve, docs are indexed). Runs as a pre-commit hook. |
| `automagic context` | | Regenerates the `docs/CONTEXT.md` index. |
| `automagic plan "<goal>" [--resume]` | `/automagic-plan` | Runs a bounded planning interview, requires approval, then proposes worktrees. |
| `automagic show [--node <id>] [--open]` | `/automagic-show` | Prints the Markdown plan (Mermaid Merkle graph, work items, interview history) and regenerates HTML and topic documents without AI calls; `--node` prints a node's hash chain. |
| `automagic run [--dry-run] [--restart] [--update] [--yes] [--max-concurrency N]` | `/automagic-run` | Launches one Copilot CLI agent per work item in its worktree, in dependency waves. Leaves changes uncommitted for human review and commit grouping; never pushes. |
| `automagic review [target] [--verify] [--ai] [--base SHA]` | `/automagic-review` | Reviews one work item's committed and uncommitted changes against its nodes: scope (files vs the node's declared files; guardrail/CI files are errors), size budget, context-file guardrails, optional `verify_command` and AI review. Writes `.automagic/plan/reviews/<id>.md` with a verdict (`pass`/`concerns`/`fail`) and a proposed commit message. Exits 1 on `fail`. |
| `automagic worktrees [--create] [--yes]` | `/automagic-worktrees` | Shows or creates worktrees for the saved plan, each with a `.automagic/BRIEF.md`. |

Python projects get uv, ruff, bandit and zizmor in the pre-commit config; TypeScript projects get eslint, prettier and zizmor. Hooks are run with [prek](https://github.com/j178/prek) (`prek install`, `prek run --all-files`), which reads `.pre-commit-config.yaml`.

Both languages get a root `GLOSSARY.md` with the project name and a section for agreed domain terms, linked from `docs/CONTEXT.md` ([glossary template](src/automagic/templates/common/GLOSSARY.md.j2), [context template](src/automagic/templates/common/docs/CONTEXT.md.j2)). Existing glossaries are skipped unless `--force` is used.

Slash commands are provided as skills in `.github/skills/*/SKILL.md` ([slashcmds.py](src/automagic/slashcmds.py)).

### Python initialization

The generated [pyproject.toml](src/automagic/templates/python/pyproject.toml.j2) keeps runtime dependencies empty and puts pytest, pytest-cov, Ruff, Bandit, prek and zizmor in the development dependency group. Ruff, Bandit and zizmor versions match the pinned [pre-commit hooks](src/automagic/templates/python/.pre-commit-config.yaml).

Pytest looks for tests in `tests/`, imports from `src/`, and measures coverage of the normalized project package name, with a 90% minimum ([Python template](src/automagic/templates/python/pyproject.toml.j2), [name normalization](src/automagic/scaffold.py)). Add your application package under `src/<package_name>/` and tests under `tests/`; init does not generate application code or choose a build backend.

When it creates the Python manifest, [init](src/automagic/cli.py) prints the setup commands but does not install dependencies or Git hooks:

```sh
uv sync
uv run prek install
automagic check
```

Run `uv run pytest` once you have tests. Commit the generated `uv.lock`: the [lockfile hook](src/automagic/templates/python/.pre-commit-config.yaml) rejects a missing or stale lockfile without changing it, and [CI](src/automagic/templates/python/.github/workflows/ci.yml) uses `uv sync --locked`, runs the development checks and scans workflows with zizmor.

Existing manifests and hook configurations are still skipped unless you explicitly use `--force` ([scaffold.py](src/automagic/scaffold.py)). See [ADR 0008](docs/adr/0008-python-development-baseline.md) for the baseline's boundaries.

## How planning works

1. A provider drafts planning topics (assumptions and decisions), stored as nodes in a Merkle graph. Each topic's hash covers its content and its dependencies' hashes.
2. The interviewer assesses outcome, intended users, scope boundaries, constraints, and observable acceptance criteria. It proposes up to three independent questions with recommendations and tradeoffs; dependent questions wait. Answers revise topics and can invalidate dependent topics.
3. The default allowance is 12 individual answers or explicit deferrals (`/skip` or "I don't know"), not 12 batches. Type `/done` to pause. Readiness requires supported criteria (or a reason for non-applicability), settled topics, no material blockers, and explicit `/approve` after reviewing the summary. Corrections return to the same budgeted interview; a complete goal needs no unnecessary questions.
4. Oversized topics are split by the provider, then topics are packed dependencies-first into work items under the token and changed-line budgets.
5. Worktrees are proposed as `git worktree add` commands and created only on confirmation.

Planning topics are what the interview settles; work items are what agents implement. One work item can contain several topics, so 19 planning topics can become eight work items ([sizing.py](src/automagic/sizing.py)).

The [bounded interview](src/automagic/bounded_interview.py) retains each submitted answer before revision, pending batches, question allowance, and pending confirmation across interruptions. `--resume` does not silently replenish allowance. Exhaustion offers save/stop or six more questions; the separate `max_rounds` safeguard requires separate consent to extend. Two completed questioning rounds without material progress pause for review. Invalid question batches receive dependency feedback and up to two corrective reassessments, without spending question allowance or changing saved decisions. Persistent invalid batches, malformed responses, and provider execution failures preserve the last durable step; execution failures are not automatically retried ([durability.py](src/automagic/durability.py)).

Older plans retain saved decisions but require consent before starting a bounded interview; historical counts and approval are unknown. Invalid checkpoints require consent before discarding position and pending input; declining leaves files unchanged. Invalid graph data remains an error ([cli.py](src/automagic/cli.py)).

### Pauses, approval and recovery

The [planning screens](src/automagic/planning_ui.py) show compact readiness tables,
outstanding topics, material blockers, tradeoff/unknown previews, and the remaining
allowance. Topic content and past answers are available through `/details`, rather
than printed on every pause. Material blockers are never abbreviated.

A progress pause names its cause: two consecutive rounds without material progress,
no next question despite an unfinished plan, or an answered question repeated without
a new gap. Older saved pauses are explained from their structured state; missing
evidence is identified rather than guessed ([planning_state.py](src/automagic/planning_state.py)).
“No material blockers reported” does not mean all topics are settled or the plan is
approved. For example, the status portion of a pause can show:

```text
Planning paused: no next question
Why: The plan is not ready, but the assessment supplied no next question.
Topics: 14/16 settled | Questions: 15/18 used; 3 remaining
Unsettled topics: Quality gates; Documentation
Material blockers: none reported
```

| Action | When available | Consequence |
|---|---|---|
| `/details` | Pause, approval or recovery | Shows full saved information or recovery details; no AI calls, changes or publication. Returns to the same menu. |
| `/correct` | Pause or approval | Saves a correction before provider work, then reassesses. Follow-up questions use the remaining allowance. |
| `/retry` | Progress pause | Reassesses unchanged; adds no questions or passes and may pause again. |
| `/extend` | An exhausted safeguard | Adds six questions **or** the configured pass increment, never both. The menu shows before/after counts. |
| `/approve` | Ready for confirmation | Explicitly approves the reviewed plan; it is never inferred from a recommendation. |
| `/done` or Enter | Any menu | Stops safely; an unfinished plan exits with code 2. |
| `/reset` | Invalid checkpoint only | Discards interview position and pending input; keeps agreed decisions and saved history. |
| `/start` | Legacy transition only | Starts new question accounting while keeping agreed decisions/history and discarding the old pending position/input. |

Save/stop is always the default. `/quit` remains an alias, and existing y/n answers
remain accepted for continuation and recovery consent. At approval, a direct text
correction is still accepted. Corrections do not replenish allowance or imply approval
([bounded_interview.py](src/automagic/bounded_interview.py)).

The stop footer prints a resume command targeting the same repository, including a
quoted `--path` when needed. Errors and interruptions still exit with code 1;
failure guidance explains that only successfully saved input is recoverable and
does not automatically retry. If nothing was saved, rerun with the original goal
([cli.py](src/automagic/cli.py)). These choices are recorded in
[ADR 0014](docs/adr/0014-explainable-planning-screens.md).

### Question progress

Before each question the CLI prints where you are and the current topic's title ([progress.py](src/automagic/progress.py)), and shows a spinner while the provider works:

```
Planning: [########------------] 4/10 topics settled · pass 2/8
2 topics pending this pass · 6 unsettled overall
Topic: "Sink handling"
? 1. "Sink handling": Does "Scope" apply to every sink?
Recommendation: Cover all supported sinks
Tradeoff: More implementation work
Answer question 1 (/skip to defer). Questions remaining: 8
```

- `Topic: "..."` identifies the current topic by title, not its internal ID. Reviewing, revising and settled spinners also use titles.
- `settled/total topics` counts topics whose current hash equals their validated hash. Settled planning topics are not completed implementation work.
- `pass N/M` identifies the current interview pass and its limit, not the number of work items.
- `N topics pending this pass` counts questions remaining in the current saved batch, including the current topic.
- `N unsettled overall` is the exact current number of unsettled topics across the plan, not a forecast of the questions remaining. A topic review can settle it without asking a question.
- Counts can move backwards. After an answer, `answer applied: +1 new topic, 2 settled topics re-opened` says why. New prerequisites are assessed in a subsequent round.

The [CLI](src/automagic/cli.py) supplies bounded excerpts of repository documents, manifests, and topic file paths, excluding hidden or outside-repository paths. The interviewer receives the graph and prior answers, is instructed to distinguish evidence from suggestions, and explains new gaps when reopening decisions. Repeated answered questions without a new reason pause rather than consume further slots ([bounded_interview.py](src/automagic/bounded_interview.py)).

Readiness evidence and whether an answer materially settles a decision remain provider judgments, not proofs of completeness. The application enforces budgets, dependency independence, durable accounting, and explicit approval. See [ADR 0012](docs/adr/0012-bounded-planning-interview.md).

State is stored in `.automagic/plan/`; commit it so reviewers can inspect the plan:

- `graph.json`: the Merkle graph and versioned interview checkpoint, saved together atomically; the checkpoint does not affect Merkle hashes ([merkle.py](src/automagic/merkle.py)).
- `history.jsonl`: one event per validation, answer or amendment (question, answer, old/new hash, invalidated ancestors).
- `plan.html`: a self-contained page (`automagic show --open`). Its heading distinguishes the two counts, for example `Work breakdown: 8 nodes covering 19 planning topics`. Each graph node represents a work item containing one or more planning topics. Hover over a node to inspect its topics. Tick a work item and its unfinished prerequisites are selected automatically (dashed); finished items are skipped. The page prints `automagic run --only <ids>` in dependency order; run it to create the worktrees and start the agents. The page itself launches nothing. Failed items are selectable again to retry them ([htmlview.py](src/automagic/htmlview.py)).

`automagic show` prints the Markdown overview directly to the terminal: the Mermaid graph (work items as subgraphs, oversized ones outlined red), work-item table, planning topic details with full hashes, and interview history. It does not save `.automagic/plan/plan.md`, and planning and worktree proposals do not create it either. Existing copies are left untouched and can be deleted manually ([cli.py](src/automagic/cli.py), [render.py](src/automagic/render.py), [ADR 0009](docs/adr/0009-stdout-plan-overview.md)).

`docs/plan/` is meant to be committed: `<topic-id>.md` holds each topic's agreed scope, files, dependencies, hash chain and interview decisions, and `graph.json` lets a fresh clone continue the plan. That published graph omits the runtime interview checkpoint ([plandocs.py](src/automagic/plandocs.py)); preserve `.automagic/plan/graph.json` too for exact in-pass continuation. `automagic check` reports topic documents that are out of date with the graph; `automagic show` regenerates them. Internal names such as `node`, `round` and `max_rounds` in saved data and configuration are unchanged. See [ADR 0003](docs/adr/0003-committed-plan-documents.md) and [ADR 0005](docs/adr/0005-atomic-interview-checkpoints.md).

## Changing a finished plan

`automagic plan --amend "<change>"` edits or adds planning topics. Topics that depend on them get new hashes and are re-examined by the interview; the rest stay settled. The command lists edited, new and re-opened topics, and asks before applying a change that contradicts agreed intent (`--yes` accepts). The old run state is then stale: `automagic run` lists the stale work items, and `automagic run --update` redoes only those (see below).

## Running agents

`automagic run` executes the saved plan ([runner.py](src/automagic/runner.py)):

`automagic run --only wi-02,wi-04` (work item or planning topic ids) runs just those items plus any unfinished prerequisites; finished items are skipped.

1. Work items run in waves: every item whose prerequisites are done runs in parallel, up to `max_concurrency`.
2. Branches use `<category>/<work-item-id>_<intent>` (for example, `feature/wi-01_session-export`); the category is inferred from the brief and defaults to `feature`. A dependent worktree is branched off its first prerequisite branch, and every prerequisite branch not yet in its history is merged in, also when the worktree already exists (for example from `automagic worktrees --create`) or when an earlier attempt failed on a merge conflict.
3. Each agent runs `runner_command` in its worktree with `.automagic/BRIEF.md` as its brief. automagic runs `verify_command` and leaves implementation changes uncommitted, preserving staging. If a failing check fixes files, the check repeats once without staging or committing the fixes.
4. A failed item blocks its dependents; siblings continue. An agent that exits cleanly but leaves no changes relative to its base also fails. `done` means implementation and verification finished, not that changes were committed. Dependents remain `pending` until prerequisite changes are manually committed; this is a successful pause, not a failure. Re-running skips finished items and resumes the rest. State is in `.automagic/plan/run.json`, logs in `.automagic/plan/runs/<id>.log`.
5. Each item records a hash of its nodes (which cover their prerequisites). After a plan change, `automagic run` stops and lists the stale items: `--update` redoes only those, continuing on their existing branch with the prerequisites merged in again, and `--restart` redoes everything. Both keep each branch's recorded base, so reviews still see all of the item's work. See [ADR 0004](docs/adr/0004-run-state-tracks-work-item-hashes.md).
6. Run `automagic review <id> --verify`, choose semantic commit groups, and commit and merge each work item yourself. After committing prerequisites, rerun `automagic run` to advance their dependents. No implementation commits or pushes are automatic. See [ADR 0016](docs/adr/0016-human-owned-work-item-commits.md).

The [default runner](src/automagic/config.py) denies `git commit`, `git push` and `git merge` via `--deny-tool`, which is a best-effort guard rather than a sandbox: run agents only on repositories where you accept unattended shell access. Agents only inherit committed files from other worktrees, so commit `AGENTS.md` and the plan before running.

## Reviewing work

`automagic review wi-02` (a work item id, a planning topic id, or a branch; with no target it uses the current branch, so run it as `automagic review --path <project>` from inside a worktree) compares the recorded base to the current working tree, including committed, staged, unstaged and untracked files without changing the index. The generated, untracked `.automagic/BRIEF.md` is excluded. The base is the commit recorded when `automagic run` prepared the worktree, so merged-in prerequisites are not counted ([review.py](src/automagic/review.py)). Findings:

- **scope**: changed files not declared by the item's planning topics are warnings; changes to guardrail, CI or config files outside the declared files are errors (`GUARDED` in [review.py](src/automagic/review.py): `AGENTS.md`, `docs/GUARDRAILS.md`, `.github/workflows|skills|instructions/*`, `.pre-commit-config.yaml`, `pyproject.toml`, `eslint.config.js`, `.automagic/*`); so is a committed brief. Topics that declare no files skip the warning but not the error, so list such a file in a planning topic to be allowed to change it.
- **size**: more changed lines than `max_changed_lines` is an error; far above the topic estimates is a warning.
- **guardrails**: `automagic check` problems in changed files are errors.
- **verify** (`--verify`) runs against working files without requiring commits; any automatic fixes are included in the resulting scope and size checks. **ai** (`--ai`, via the provider, truncated to half the context budget) also sees uncommitted changes.

The default `prek run --all-files` checks tracked/staged files, not new untracked files. Review warns when this leaves new files unchecked: stage them yourself and rerun verification; no commit is needed. Scope, size and AI review include them even before staging ([review.py](src/automagic/review.py)).

The deterministic checks cannot judge whether the code does what the planning topic asks. For that, use `/automagic-review`: it tells the agent to read the report, review the diff against the planning topic and guardrails, and use the GitKraken MCP tools (`git_log_or_diff` for the diff and log, `git_commit_composer` to propose commit grouping and messages for uncommitted work, `gitlens_start_review` for an existing pull request) when they are available, falling back to plain git. The CLI itself does not call MCP tools.

## Configuration

`.automagic/config.toml`:

| Key | Default |
|---|---|
| `max_context_tokens` | 40000 |
| `max_changed_lines` | 400 |
| `max_rounds` | 8 |
| `max_questions` | 12 (initial answered/explicitly deferred questions; zero allowed) |
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
