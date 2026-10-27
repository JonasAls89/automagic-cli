---
description: Run one agent per work item in its worktree, in dependency waves
agent: agent
---

Run `automagic run --dry-run` and show the waves, agent command and verify command. Only after the user confirms, run `automagic run --yes`. Use `--only <ids>` (work item or node ids; unfinished prerequisites are added) to run a subset, for example the command copied from `plan.html`. Agents commit in their own worktree branch and never push or merge. Report each item's status and branch, and point to `.automagic/plan/runs/<id>.log` for failures.
