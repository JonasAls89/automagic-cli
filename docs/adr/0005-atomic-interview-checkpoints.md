# 0005. Atomic interview checkpoints

- Status: accepted
- Date: 2026-10-30

## Context

The interview persisted revised nodes and validation hashes but rebuilt its
pending queue and started round 1 on every invocation. Pausing with ten checks
left could therefore resume with seventeen, although prior answers were saved.
The resume contract needs to preserve both agreed decisions and interview
position without changing the Merkle validation rules in
[ADR 0002](0002-merkle-assumption-graph.md).

## Decision

The saved graph has optional, versioned `interview` metadata containing the
current round and limit, remaining queue, execution phase, question, and any
submitted answer awaiting revision. Its fingerprint includes the goal, node
identities and validation hashes, not just the Merkle root. Graph and checkpoint
are saved in one temporary-file/atomic-replacement operation
([merkle.py](../../src/automagic/merkle.py)).

The [interview loop](../../src/automagic/interview.py) saves before prompting and
before requesting a revision. Resume reuses the saved question or answer rather
than regenerating a question or asking for an answer twice. Provider failures
and interruptions leave the last durable step available for explicit resume;
there are no automatic provider retries.

A completed transition stores its history event in the same graph snapshot
before appending it to history. Event IDs make the
[CLI history writer](../../src/automagic/cli.py) idempotent if an interruption
occurs between the append and clearing the pending event. Existing history
event types remain unchanged.

Pausing preserves the current round limit. Explicit resume after exhaustion
adds the configured `max_rounds` batch to that limit and continues cumulative
round numbers. New plans and amendments start fresh interviews; convergence
clears the active checkpoint.

Legacy graphs without a cursor start a fresh pass with a notice. For stale,
malformed or unsupported checkpoint metadata, the
[CLI](../../src/automagic/cli.py) validates the graph independently and asks before
discarding the cursor and pending input. Declining changes no saved file.
Unreadable or invalid graph data remains an error.

## Consequences

Answering a question still does not settle a node: only a question-free check
validates its current hash. Added or reopened nodes retain next-pass scheduling.
Progress now names checks explicitly and reports pauses during a round rather
than claiming the round completed
([progress.py](../../src/automagic/progress.py)).

Runtime metadata does not affect node/root hashes and is omitted from published
plan graphs ([plandocs.py](../../src/automagic/plandocs.py)). Graph-only CLI
consumers ignore the cursor, so viewing a plan neither alters the active
interview nor requires repairing its metadata. Exact continuation requires the
saved state graph; a published graph alone retains decisions but not the cursor.

The checkpoint cannot reconstruct a position that an older version never saved.
Atomic snapshots avoid partially written graphs; write failures are reported
without replacing the previous snapshot, and temporary files are cleaned up.
