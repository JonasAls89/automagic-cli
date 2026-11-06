---
name: automagic-run
description: Run one agent per work item in its worktree, in dependency waves
---

Run `automagic run --dry-run` and show the waves, agent command and verify command. Only after the user confirms, run `automagic run --yes`. Use `--only <ids>` (work item or node ids; unfinished prerequisites are added) to run a subset, for example the command copied from `plan.html`. Agents leave changes uncommitted for human review and commit grouping, preserving existing staging. Dependents wait until prerequisite changes are manually committed; rerun to resume. Never commit, push or merge without the user's approval. Report each item's status and branch, and point to `.automagic/plan/runs/<id>.log` for failures.
