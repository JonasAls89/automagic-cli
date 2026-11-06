# 0015. Categorized intent-based worktree branches

- Status: accepted
- Date: 2026-11-06

## Context

Worktree branches used the overall goal and work-item ID, which made branches
long and did not reveal the individual item's intent. The
[worktree proposal](../../src/automagic/worktrees.py) already creates a brief
for every item and supplies branch names to execution and review.

## Decision

Name branches `<category>/<work-item-id>_<intent>`, preserving the work-item
identifier. Use the first planning topic title as the concise intent slug,
removing a leading action verb and limiting the slug to 32 characters. Infer
the category from planning topic titles: use `refactor` for refactoring language,
`chore` for maintenance, documentation, dependency, tooling, testing or release
language, and otherwise use `feature`.

Worktree directory names and prerequisite branch relationships do not change.
Execution and review continue to use the branch name in the worktree proposal.

## Consequences

Branch names are shorter and identify each item's primary intent while retaining
the stable work-item ID. Category inference is deliberately lexical and may
classify unusual descriptions as `feature`; it does not change plan content or
execution policy.
