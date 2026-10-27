---
name: automagic-review
description: Review a work item against its node, the guardrails and the initial scope
---

Run `automagic review <work item or node id> --verify` and read `.automagic/plan/reviews/<id>.md`; it lists the worktree, the diff range and deterministic findings (scope, size, guardrails, verification). Then judge the diff yourself against the node text in `.automagic/BRIEF.md` and `docs/GUARDRAILS.md`: is only the node's work done, is every claim grounded in files, is anything missing?

If GitKraken MCP tools are available (`git_log_or_diff`, `git_commit_composer`, `git_status`, `gitlens_start_review`), prefer them: call `git_log_or_diff` with `action: diff` and the report's `base..branch` range, and `action: log` for the commits, using the worktree path as `directory`; if a pull request exists for the branch, use `gitlens_start_review` with its URL. Fall back to `git diff` and `git log` when GitKraken is unavailable.

For commit messages: if the worktree has uncommitted changes, call `git_commit_composer` (direction `auto`, `directory` = worktree) to propose how to group and word the commits; otherwise improve the report's proposed message. Never commit, push or merge without the user's approval. Finish with the verdict, findings by severity with file paths, and the proposed commit message(s).
