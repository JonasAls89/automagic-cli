# 0002. Merkle assumption graph for planning

- Status: accepted
- Date: 2026-10-25

## Context

Plans made by AI agents are often accepted before assumptions are challenged. We want questioning to continue until the plan is demonstrably settled, and work items sized for human review and agent context windows.

## Decision

Model a plan as a graph of assumption/decision nodes. A node hash is `sha256(title, content, files, est_lines, sorted child hashes)`, so a change invalidates all ancestors. Each node records the hash at which it was last validated. The interview ends when no node is invalid. Work items are packed dependencies-first under configurable token (40000) and changed-line (400) budgets; oversized nodes are split.

## Consequences

Convergence depends on the provider eventually having no further questions, so rounds are capped and plans can be resumed. Token estimates are heuristic. The provider is pluggable via `Provider.complete`.
