# Guardrails

## Context size

- Instruction and context files (`AGENTS.md`, `.github/copilot-instructions.md`, `.github/instructions/*`, `docs/*.md`) stay under 8000 bytes each.
- Work items fit one agent context window (about 40000 tokens) and one human review (about 400 changed lines).
- `automagic check` enforces the size limits and runs as a pre-commit hook.

## Grounding

- Statements about the codebase must be traceable to a file. Relative markdown links must resolve; `automagic check` verifies this.
- When information is missing, ask or record an open question; do not invent.
- Decisions are recorded as ADRs in `docs/adr/`.

## Review

- One work item per branch or worktree, reviewed by a human before merge.
