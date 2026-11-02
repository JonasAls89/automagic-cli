# 0013. Concentrate selected work-item run preparation

- Status: accepted
- Date: 2026-11-02

## Context

The [run command](../../src/automagic/cli.py) assembled a preparation recipe
from completed-item resolution, selection, dependency filtering and preview
ordering. Its caller knowledge had to stay consistent with the
[runner's execution rules](../../src/automagic/runner.py).

## Decision

The runner's `prepare_run` owns completed-item resolution, selection closure,
preview dependency scoping and ordering. Its result contains selected IDs and
preview waves. Preparation changes neither input work items nor saved state,
and creates no worktrees.

The CLI retains option parsing, graph loading, partitioning, confirmation and
presentation. Execution retains original dependencies and dynamic saved-status
checks; preview waves are not a guaranteed execution schedule.

Selection reads saved state before confirmation only when identifiers were
requested and restart is disabled. Execution independently reads state after
confirmation, without recomputing selection closure. An unrestricted preview
continues to include all work items, including completed ones.

## Consequences

Caller knowledge concentrates in the existing runner rather than another
orchestration module or adapter. The
[preparation and CLI tests](../../tests/test_runner.py) cover selection,
completed/stale prerequisites, restart, unchanged inputs, preview parsing,
post-confirmation state freshness and error timing. Existing Git execution
tests remain.

Item hashes, stale-plan/update handling, review bases, prerequisite merges and
restart behavior retain [ADR 0004](0004-run-state-tracks-work-item-hashes.md)'s
guarantees. Selection errors still precede confirmation; stale-plan execution
errors still follow it. No execution policy or retry behavior changes.
