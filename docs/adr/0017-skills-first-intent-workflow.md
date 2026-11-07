# 0017. Skills-first intent and execution workflow

- Status: accepted
- Date: 2026-11-07

## Context

The product needs a lightweight Markdown baseline and a natural connection to
external thorough planning, rather than another CLI-led interview system.
Installed skills must carry their helpers and assets without depending on the
target project's dependency environment.

## Decision

Distribute five skills through the [automagic plugin](../../.claude-plugin/plugin.json):
init, context, intent, worktrees and run. The public CLI and former planning,
publication and review surfaces are retired.

Keep high-level project scope in [INTENT.md](../../INTENT.md); the external
user-invoked `mattpocock-skills:grill-with-docs` owns design interviews. Automagic's
[worktrees skill](../../skills/worktrees/SKILL.md) proposes implementation-sized
work items without resolving new design decisions.

Use reviewed Markdown under `docs/work/`, with typed fenced TOML metadata.
[Execution helpers](../../src/automagic/execution.py) record approval fingerprints
covering committed input contents and the base commit. A changed snapshot needs
new explicit approval; no selective planning-graph invalidation is retained.
State and logs remain orchestrator-owned under `.automagic/execution/`.

[Initialization](../../src/automagic/scaffold.py) generates only Markdown/agent
assets, preserves existing content and does not install target dependencies.
[Context validation](../../src/automagic/guardrails.py) replaces the separate
check surface. Cookiecutter is deferred: it would add a distribution mechanism
without solving this recurring workflow or existing-repository adoption.

The [runner](../../src/automagic/runner.py) retains Copilot CLI dependency waves,
verified completion and human-owned commits. Verification must be documented in
committed repository evidence. No implementation changes are staged, committed
or pushed by the orchestrator. Dependents wait for genuine manual commits;
the explicitly confirmed orchestrator integrates committed prerequisite branches.
This is a best-effort unattended execution policy, not a sandbox.

The [private launcher](../../scripts/helper.py) is invoked with a bundle-owned
`uv run --project <plugin-root> --locked` environment and an explicit target path.
The whole plugin is installed; copying an individual skill directory is not a
supported distribution.

## Consequences

The seam is an approved execution handoff, not the external planner's internal
representation. Structured validation and fingerprints detect malformed/stale
inputs, not semantic completeness; the user still reviews design and work items.
Missing verification and oversize work items block execution.

Existing target plans, configurations and legacy workspace skills are never
silently imported, overwritten or removed. Snapshot-specific worktree names and
ownership records prevent repurposing old work. Mutating helpers share a lock;
abandoned locks need explicit removal after checking for active workers.

## Superseded history

This contract supersedes ADRs 0002-0016 as the active product contract, carrying
forward human-owned commits and categorized intent-based branches. Their
accepted text is preserved unchanged in the
[pre-migration ADR snapshot](https://github.com/JonasAls89/automagic-cli/tree/53b19f4a052e776187eb26430160126414eba22e/docs/adr).
Implementation-coupled historical documents are no longer active local context;
their source links resolve within that pinned historical revision. ADR 0001
and the decision template remain active.
