# 0012. Bounded planning interview

- Status: accepted
- Date: 2026-11-02

## Context

The legacy [interviewer](../../src/automagic/interview.py) checks one planning
topic at a time, asks one question, and validates the topic when the provider
returns no question. Its pass limit bounds passes rather than user questions.
It reports unsettled plans and offers resume without final approval.
The [checkpoint model](../../src/automagic/merkle.py) holds one pending question
and answer, with [durability](../../src/automagic/durability.py) preserving
interrupted work.

A pass limit and provider validation alone do not establish that the user
understands the intended work. The design interview selected an explicit
readiness contract and a user-effort budget instead of relying solely on
stronger prompts or asking questions until the provider stops.

## Decision

The [bounded interviewer](../../src/automagic/bounded_interview.py) implements this
contract for the [plan command](../../src/automagic/cli.py), with versioned
[runtime state](../../src/automagic/planning_state.py) and a configurable
[question allowance](../../src/automagic/config.py). The topic-only Python API
remains available to existing callers that do not request an allowance.

This supersedes the automatic pass-extension and checkpoint-clearing rules of
[ADR 0005](0005-atomic-interview-checkpoints.md) for bounded interviews; atomic
snapshots, idempotent events, and graph-only publication remain unchanged.

### Purpose and readiness

Design for developers arriving with fuzzy goals. Clarify outcomes before
implementation choices, challenge consequential assumptions, and expose
tradeoffs. Investigate facts available in the repository rather than asking
users to supply them; label information that cannot be verified. Suggestions
are not agreed facts or decisions.

A ready plan has an agreed outcome, intended users, in/out of scope, relevant
constraints, and observable acceptance criteria. Each dimension is supported,
unresolved, or explicitly not applicable with a reason. Depth is proportional
to risk, not an exhaustive feature checklist.

Unknowns that could materially change direction, scope, user experience,
feasibility, or success block readiness. Minor reversible unknowns may remain
if disclosed. Suggested assumptions require agreement; final approval cannot
override a known blocking unknown.

### Questioning and progress

Prioritize direction-changing decisions before polish. Ask up to three
independent questions per round, each with a recommendation and its tradeoff.
Dependent questions wait until their prerequisites are settled. Recommendations
never imply consent.

Reopen an answered question only for a specific new contradiction, missing
detail, or changed assumption, explaining the gap. Do not merely rephrase it
on another pass. Park speculative extras.

Pause after two consecutive completed questioning rounds that neither settle
a decision, resolve a blocker, nor uncover a genuinely new material decision.
Explain what is stuck and suggest a concrete next step. Wording changes alone
are not progress. Two rounds is an initial design choice, not a measured optimum.

### Budget and continuation

Use a configurable allowance of 12 individual answered substantive questions
by default. Explicit skips and "I don't know" consume a slot and remain
unresolved. A fully answered three-question batch consumes three slots.
Persist each answer as it arrives.

Interruptions, internal provider checks, and redisplaying saved questions do
not consume slots. Show the remaining allowance and retain it across resume.
Twelve is a ceiling, not a target: a complete initial goal may proceed directly
to confirmation. A zero allowance cannot force an ambiguous plan to be ready.

At exhaustion, summarize agreements and blockers and offer save/stop or an
explicit six-question extension. Never silently replenish the allowance.
Retain a separate pass safeguard; extending it is explicit and does not
automatically extend the question allowance.

Budget exhaustion, pass exhaustion, no progress, and provider failures retain
unfinished state and report the actual stop reason, not success. Recovery
retains unused question allowance. The numerical defaults are starting points
to validate, not values established by existing usage.

### Confirmation

Before declaring readiness, show a concise plain-language summary of outcome,
scope, key tradeoffs, acceptance criteria, and remaining unknowns. Require
explicit user confirmation; do not quiz the user.

Confirmation is outside the substantive-question budget. Users may correct
the summary, reopening affected decisions. Any resulting substantive question
returns to the budgeted interview and requires an extension if exhausted.
Do not conceal extra questions inside confirmation.

Persist pending confirmation across interruptions. An interruption is never
approval.

### Compatibility and boundary

Preserve saved decisions. Explain legacy transitions and obtain consent before
starting a new-style interview or discarding an incompatible pending position.
Do not fabricate historical question counts or treat an old settled plan as
having received the new approval.

Limit implementation to the interview and directly necessary persistence,
configuration, and presentation. Preserve the existing roles and behavior of
[work-item sizing](../../src/automagic/sizing.py),
[plan publication](../../src/automagic/publication.py),
[worktrees](../../src/automagic/worktrees.py), and
[execution](../../src/automagic/runner.py).

## Consequences

This requires more than prompt changes: durable question batches, accounting,
progress evidence, readiness assessment, and pending confirmation are represented
consistently. Approval is retained against the graph hash in runtime state.
The existing graph remains the planning structure.

Explicit budgets can pause legitimate complex work before readiness. Deliberate
extensions allow deeper work without disguising an unbounded interview.
Recommendations reduce decision effort but must remain distinguishable from
user agreement.

[Engine tests](../../tests/test_bounded_interview.py),
[CLI tests](../../tests/test_bounded_plan_cli.py), and
[recovery tests](../../tests/test_interview_resume_cli.py) cover early completion,
dependency ordering, saved answers, deferrals, exhaustion, extensions, reopening,
no-progress stops, approval/correction recovery, blockers, and legacy transitions.
Semantic readiness and material progress still depend on provider judgment;
these deterministic tests do not measure live-model interview quality.
