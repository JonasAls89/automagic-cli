---
name: automagic-review
description: Review a work item against its node, the guardrails and the initial scope
---

Run `automagic review <work item or node id> --verify` and read `.automagic/plan/reviews/<id>.md`; it lists the worktree, the base commit and deterministic findings (scope, size, guardrails, verification). Then judge the diff yourself against the node text in `.automagic/BRIEF.md` and `docs/GUARDRAILS.md`: is only the node's work done, is every claim grounded in files, is anything missing? The default `prek run --all-files` checks tracked/staged files; surface any warning about untracked files and leave staging decisions to the user.

If GitKraken MCP tools are available (`git_log_or_diff`, `git_commit_composer`, `git_status`, `gitlens_start_review`), use them to inspect status, changes from the report's base commit and the commit log in the worktree. Include staged, unstaged and untracked files; a branch-only diff omits uncommitted work. Fall back to `git diff <base>` for tracked changes, `git status --short` and reading untracked files, and `git log` for commits. If a pull request exists, use `gitlens_start_review` with its URL.

For commit messages: if the worktree has uncommitted changes, call `git_commit_composer` (direction `auto`, `directory` = worktree) to propose how to group and word the commits; otherwise improve the report's proposed message. Never commit, push or merge without the user's approval. Finish with the verdict, findings by severity with file paths, and the proposed commit message(s).
