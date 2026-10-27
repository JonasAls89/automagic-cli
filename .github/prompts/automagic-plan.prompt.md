---
description: Interview me until the plan is stable, then propose reviewable worktrees
agent: agent
---

Ask the user for the goal if it is not provided. Run `automagic plan "<goal>"` in an interactive terminal and relay each question to the user verbatim, passing their answers through. The command builds a Merkle graph of assumptions and keeps asking until no node hash is invalidated. Afterwards show `.automagic/plan/plan.md` and ask before creating worktrees with `automagic worktrees --create`. To change a finished plan, run `automagic plan --amend "<change>"`: it lists the agreed nodes the change invalidates and asks before accepting contradictions with agreed intent. Before implementing new work in a repository that has `docs/plan/`, read the node documents there and say which nodes the work touches; if it contradicts one, propose an amend instead of proceeding.
