# 0011. Separate plan publication from terminal presentation

- Status: accepted
- Date: 2026-11-01

## Context

The [CLI's proposal helper](../../src/automagic/cli.py) combined terminal
messages with history/run-state reads, rendering, artifact writes and context
indexing. Exercising publication required command-level setup even though
publication needs neither Git nor a terminal.

## Decision

The [plan publication module](../../src/automagic/publication.py) owns history
and run-state reads, in-memory Markdown rendering, HTML generation, committed
topic documents and context-index updates. Its result contains the Markdown and
artifact locations.

The CLI retains graph loading, work-item partitioning, terminal messages,
browser opening and worktree coordination. Existing renderers remain internal
collaborators; no generic output port is introduced.

## Consequences

[Publication tests](../../tests/test_plan_views.py) use temporary files without
a CLI invocation. They verify readable artifacts, saved history/status,
checkpoint-free published graphs and unchanged runtime/legacy files. Existing
CLI outcome tests remain. Filesystem failure tests verify error propagation,
permitted partial publication and unchanged runtime files without retries.

Publication remains synchronous and non-atomic across files; failures propagate.
No export option, transaction or automatic legacy cleanup is added.
[ADR 0003](0003-committed-plan-documents.md),
[ADR 0005](0005-atomic-interview-checkpoints.md) and
[ADR 0009](0009-stdout-plan-overview.md) retain their guarantees.
