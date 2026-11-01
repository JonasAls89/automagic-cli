# 0009. Print the Markdown plan overview without saving it

- Status: accepted
- Date: 2026-11-01

## Context

The Markdown overview is rendered from the graph, derived work items, configuration and interview history ([render.py](../../src/automagic/render.py)). It remains useful as one readable report for terminal and AI workflows, but saving another derived artifact alongside the HTML view and published topic documents is unnecessary.

## Decision

The shared output helper returns the rendered Markdown in memory, and `automagic show` prints it directly. Neither showing a plan, planning nor proposing worktrees creates `.automagic/plan/plan.md`. Existing copies are not read, overwritten or deleted ([cli.py](../../src/automagic/cli.py)).

HTML generation, published topic documents, graph and history persistence, run state, node inspection and browser opening retain their existing behavior. The planning and show skills present command output instead of referring to a saved overview ([slashcmds.py](../../src/automagic/slashcmds.py)).

## Consequences

The terminal report still contains the Mermaid graph, work-item breakdown, topic details and interview history. Regression tests check the complete report and untouched graph/history files, absence of a newly saved overview and preservation of legacy overview contents ([test_render.py](../../tests/test_render.py)).

Existing overview files may become stale and can be deleted manually. No automatic cleanup or new export option is introduced. Other output paths are covered by the planning, amendment and worktree tests ([test_interview_resume_cli.py](../../tests/test_interview_resume_cli.py), [test_plan_views.py](../../tests/test_plan_views.py), [test_scaffold_cli.py](../../tests/test_scaffold_cli.py)).
