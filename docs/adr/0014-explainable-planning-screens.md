# 0014. Explainable planning screens and explicit recovery actions

- Status: accepted
- Date: 2026-11-02

## Context

The [bounded interview](../../src/automagic/bounded_interview.py) previously passed
complete topic contents, readiness evidence and historical answers to CLI
confirmation prompts. A large summary obscured the decision being requested.
Its `no_progress` stop reason covers several conditions, and "no blockers" does
not establish that every planning topic is settled.

The [readiness contract](0012-bounded-planning-interview.md) remains unchanged.
Users need the specific cause, recoverable state and action consequences, not
merely a yes/no question. Invoking [plan publication](0011-plan-publication-module.md)
to inspect a pause would also regenerate derived files.

## Decision

Use a small [planning UI](../../src/automagic/planning_ui.py) shared by pauses and
approval. Render concise readiness/evidence tables, topic and question counts,
outstanding topic previews, all material blockers, and disclosed tradeoff/unknown
previews. Text is literal, formatting uses existing Rich/Typer dependencies, and
shortening is deterministic rather than another provider call.

Details display the complete callback summary without provider calls, state
changes or publication. Recommendations never imply agreement. Save/stop is the
default; approval, allowance extensions and checkpoint reset require explicit
actions. The menus retain existing slash commands and unambiguous yes/no aliases.

Derive precise progress-pause explanations from existing
[runtime state](../../src/automagic/planning_state.py), not old prose. Separate
unproductive rounds, an unfinished assessment without a next question, and an
unexplained repeated question. If a saved state cannot establish the exact cause,
say so. Do not equate an empty blocker list with readiness.

Extend the [continuation callback](../../src/automagic/interview.py) with a typed
correction alongside existing boolean results. Explicitly dispatch that type;
an arbitrary truthy result cannot grant an extension. Both paused and confirmation
corrections use the same durable transition: save the input before provider work,
clear stale pause/batch state, and reassess without replenishing question or pass
limits. This reuses the existing correcting phase without a checkpoint schema
change. Follow-up substantive questions remain budgeted.

Preserve stop thresholds, separate question/pass extensions, explicit approval,
legacy/reset consent, and exit codes. Failures remain errors with recovery guidance,
not automatic retries or success-shaped fallback plans. Resume commands identify
the same target repository.

## Consequences

The default view no longer grows with every settled topic and historical answer.
Previews can omit detail, so their abbreviation and Details action are visible;
material blockers are never shortened. Existing Python callback callers retain
their boolean behavior, while interactive users can correct intent at any pause.

[UI tests](../../tests/test_planning_ui.py) check a synthetic 16-topic/15-answer
pause stays within 60 lines at 100 columns, full read-only details, narrow/non-TTY
output, literal markup, budget-preserving actions, explicit recovery consent, and
corrections interrupted by provider failure or cancellation.
[Engine tests](../../tests/test_bounded_interview.py) cover exact causes and typed
corrections at active safeguards.
[Recovery tests](../../tests/test_interview_resume_cli.py) preserve existing saved
decisions, input, history, allowance and exit behavior.
