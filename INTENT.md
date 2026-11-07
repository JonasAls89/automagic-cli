# Project intent

## Purpose and problem

Make grounded, reviewable agent work fit naturally into repositories without
owning another AI planning system or generating an application's technology stack.

## Intended users

Developers using Copilot who want a lightweight documentation baseline and a
clear connection between project intent, thoroughly reviewed design and
independently reviewable implementation work.

## Outcomes and observable success

A developer can initialize an existing or new repository without losing custom
files, capture its intent, plan externally, approve a bounded work breakdown,
and run verified work in separate worktrees. Prerequisites, failures and required
human actions remain visible. Developers control commit grouping and merging.

## Scope and non-goals

Provide five workflows: init, context, intent, worktrees and run.
Preserve Markdown/agent setup, grounding, small-context guardrails and
dependency-aware execution. Include GitHub security configuration and assisted
language/framework-specific pre-commit setup, not application scaffolding.

Do not provide an application generator, a planning interview engine, an
assumption graph, a separate review product, automatic commits or pushes,
or a migration/compatibility layer. Cookiecutter is not the distribution
mechanism for this scope.

## Constraints and principles

Support both new and existing repositories. Keep existing content intact unless
the user approves an edit. Delegate thorough design to the user's Matt Pocock
planning skills. Make approvals explicit and invalidate them when reviewed
inputs change. Require documented verification; unverified work is not complete.

## Assumptions and open questions

Users supply Copilot CLI, uv and the external planning plugin.
Future distribution or execution engines require a separate scope decision.
See the [current operating contract](docs/adr/0018-current-workflow-only.md) and
[repository setup scope](docs/adr/0019-repository-security-setup.md).
