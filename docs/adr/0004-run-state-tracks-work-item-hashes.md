# 0004. Run state tracks work item hashes

- Status: accepted
- Date: 2026-10-27

## Context

The run state in `.automagic/plan/run.json` was keyed by work item id and guarded only by the plan's root hash ([runner.py](../../src/automagic/runner.py)). After a plan amendment the only option was `--restart`, which redid every item, although the Merkle graph ([ADR 0002](0002-merkle-assumption-graph.md)) already says which nodes changed. Work item ids are positional and branches are named after them, so existing branches and worktrees are reused after a restart.

Three related gaps made reused worktrees unreliable:

- A worktree that already existed (from `automagic worktrees --create`, or after a failed merge) skipped the merge of its prerequisite branches.
- An agent that changed nothing was recorded as done, so dependents built on nothing.
- `--restart` re-recorded the review base at the current HEAD, hiding earlier agent commits from `automagic review`.

## Decision

Each item records `hash`, derived from its node ids and their Merkle hashes (`item_hash`). If the root hash changed, `automagic run` stops and lists the stale items, meaning items already run whose hash differs. `--update` resets only those to pending and keeps their branch, worktree and base, so the agent continues on the existing work. `--restart` still redoes everything but keeps the base of branches whose worktree exists.

`prepare_worktree` merges every prerequisite branch that is not an ancestor of the worktree, whether or not the worktree already existed, and `automagic worktrees --create` branches dependents off their first prerequisite like `run` does. `execute` fails an item whose branch has no diff against its base.

## Consequences

Untouched items stay done after an amendment. Because ids are positional, an amendment that repacks work items marks the shifted ones stale and their agents continue on a branch that held different nodes; read the diff before merging. A state file from before this change has no hashes, so every item run before is stale after a plan change. An item whose agent legitimately has nothing to do must be recorded by hand or its node removed.
