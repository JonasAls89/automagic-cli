# 0016. Human-owned work-item commits

- Status: accepted
- Date: 2026-11-06

## Context

Automatic implementation and formatter-fix commits in the
[runner](../../src/automagic/runner.py) prevented users from reviewing work before
choosing semantic commit groups. The [reviewer](../../src/automagic/review.py)
compared committed branch history, so merely removing commits would leave new
work outside scope, size and AI review. Dependent worktrees inherit prerequisites
through Git history, not another worktree's uncommitted files.

## Decision

The runner leaves implementation and verification fixes uncommitted and preserves
the index. Its prompt reserves staging and commits for the user; the
[default command](../../src/automagic/config.py) also denies agent commits.
Verification retries once when a failing command changes the worktree.

`done` means the agent and verification finished. Dependent items remain `pending`
while any prerequisite has uncommitted changes, excluding its generated brief.
Independent work continues. This pause exits successfully; after review and manual
commits, another run resumes dependents without rerunning completed agents.
Clean prerequisites must also contain committed changes from their recorded base;
discarding or stashing the work does not unlock dependents.
Prerequisite branch preparation and recorded review bases remain unchanged.

Review compares the recorded base to the current working tree, including staged,
unstaged and untracked files, without staging them. Only an untracked generated
brief is excluded; a tracked brief remains a scope error. Verification runs before
collecting scope and size, so formatter fixes are reviewed. AI review uses the same
working-tree diff. The [skills](../../src/automagic/slashcmds.py) propose commit
groups and messages but leave committing to the user.
The default `prek run --all-files` skips untracked files; verification warns users
to stage new files themselves and rerun hooks, without requiring commits.

## Consequences

Users control commit boundaries and can review before committing. Dependency waves
can require multiple run/review/commit cycles. Existing commits remain reviewable;
run-state hashes and stale-plan handling retain
[ADR 0004](0004-run-state-tracks-work-item-hashes.md)'s guarantees.
[Runner tests](../../tests/test_runner.py) and
[review tests](../../tests/test_review.py) cover manual commit ownership,
dependency resumption, verification fixes and uncommitted review inputs.
