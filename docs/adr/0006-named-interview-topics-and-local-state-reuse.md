# 0006. Named interview topics and local state reuse

- Status: accepted
- Date: 2026-11-01

## Context

Questions could refer to a topic by a generic description, while the persistent
progress summary did not identify the current topic. Dependency context supplied
IDs and content without titles. The
[interview loop](../../src/automagic/interview.py) also repeated invalidity queries
within unchanged state. [Graph invalidity checks](../../src/automagic/merkle.py)
performed a second dependency-order traversal after computing hashes.

## Decision

The [question prompt](../../src/automagic/interview.py) supplies dependency titles
and an ID-to-title catalogue, and instructs the provider to quote exact topic
titles in references. IDs remain available for internal graph operations.
The [progress reporter](../../src/automagic/progress.py) prints a title-only topic
label before each question and uses titles in spinners. Its `node_title` is
transient progress data, not checkpoint metadata.

Reuse freshly computed invalid-topic sets for immediately adjacent progress
calculations where no mutation or callback intervenes. Refresh after provider,
validation, persistence, and history transitions rather than maintaining a
long-lived cache. `Graph.invalid()` iterates the dependency-ordered hash map
instead of traversing the graph a second time.

Keep provider requests single-topic and sequential. Do not batch or prefetch
reviews, skip validation, or remove any durable save. Merkle hashes, graph and
checkpoint schemas, pending queues, events, and the resume contract in
[ADR 0005](0005-atomic-interview-checkpoints.md) remain unchanged.

## Consequences

The current topic is identifiable even when provider wording is vague. Naming
other topics is prompt guidance, not machine-enforced natural-language parsing.
Saved questions are not rewritten during resume.

Repeated local graph work is reduced without changing provider request counts.
Subprocess startup and model/network latency are not removed. Regression tests
cover [titles and callback freshness](../../tests/test_progress.py),
[graph work counts and ordering](../../tests/test_planning.py), and
[saved-question presentation](../../tests/test_interview_resume_cli.py).
