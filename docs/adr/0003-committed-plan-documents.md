# 0003. Committed plan documents and plan amendments

- Status: accepted
- Date: 2026-10-25

## Context

The agreed scope of each node lived only in `.automagic/plan/graph.json` and in a brief that is never committed (see [runner.py](../../src/automagic/runner.py)). Later work had nothing in the repository to be checked against, and a fresh clone could not continue the plan.

## Decision

`automagic plan` and `automagic show` write `docs/plan/<node>.md` for every node, a `docs/plan/_index.md` and a copy of the graph as `docs/plan/graph.json` ([plandocs.py](../../src/automagic/plandocs.py)). Each node document starts with a marker holding the node hash; `automagic check` reports documents whose hash no longer matches the committed graph. The graph is loaded from `docs/plan/graph.json` when `.automagic/plan/graph.json` is missing.

`automagic plan --amend "<change>"` applies a change to a stable graph ([interview.py](../../src/automagic/interview.py)). Hash invalidation marks the nodes that depend on edited or new nodes, only those are re-examined, and contradictions with agreed intent require confirmation (`--yes` accepts them). The amendment is recorded in `history.jsonl`.

`automagic show` also writes a static `plan.html` ([htmlview.py](../../src/automagic/htmlview.py)) and `automagic run --only` runs a subset of work items plus their unfinished prerequisites.

## Consequences

Node documents are generated, so edit the graph through `plan`, not by hand. Amendments rely on the provider to name edited nodes and contradictions; the hash propagation is deterministic, the contradiction report is not.
