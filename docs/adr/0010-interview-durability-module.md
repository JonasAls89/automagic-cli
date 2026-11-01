# 0010. Concentrate interview durability

- Status: accepted
- Date: 2026-11-01

## Context

Snapshot ordering lived in the [interview loop](../../src/automagic/interview.py),
while idempotent history delivery lived in the [CLI](../../src/automagic/cli.py).
Callers had to coordinate both to preserve the recovery guarantees of
[ADR 0005](0005-atomic-interview-checkpoints.md).

## Decision

The concrete [InterviewDurability module](../../src/automagic/durability.py)
owns graph/checkpoint snapshots and pending-event delivery. Saving records the
graph and event together before appending history; delivery clears the event
only after an idempotent, flushed history append. Resume can deliver an already
saved event without repeating a provider request.

The loop retains topic progression, provider interaction and progress reporting.
The CLI retains checkpoint-repair consent. Existing in-process persistence
callbacks remain available, but cannot be combined with InterviewDurability.
Graph.save remains the atomic snapshot implementation; no generic storage port
or automatic retry is introduced.

## Consequences

Production callers no longer supply coordinated save/history callbacks.
The [durability tests](../../tests/test_interview_checkpoint.py) exercise the
module with temporary files and filesystem failures; existing
[CLI recovery tests](../../tests/test_interview_resume_cli.py) and
[callback-freshness tests](../../tests/test_progress.py) remain.

Graph and history are not a single atomic transaction: the saved pending event
and its ID bridge interruptions between them. Sequential provider requests,
Merkle hashes, checkpoint schemas and every durable save retain the guarantees
of [ADR 0005](0005-atomic-interview-checkpoints.md) and
[ADR 0006](0006-named-interview-topics-and-local-state-reuse.md).
