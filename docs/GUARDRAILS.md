# Guardrails

## Context size

- Instruction and context files (`AGENTS.md`, `.github/copilot-instructions.md`, `.github/instructions/*`, `docs/*.md`) stay under 8000 bytes each.
- Work items fit one agent context window (about 40000 tokens) and one human review (about 400 changed lines).
- `/automagic:context` validates active context documents. This repository's pre-commit hook uses the private read-only helper (`uv run python scripts/helper.py context --check`).

## Grounding

- Statements about the codebase must be traceable to a file. Relative Markdown links must resolve; [the validator](../src/automagic/guardrails.py) verifies this.
- When information is missing, ask or record an open question; do not invent.
- Decisions are recorded as ADRs in `docs/adr/`.

## Review

- One work item per branch or worktree, reviewed by a human before merge.
- The [runner](../src/automagic/runner.py) verifies approved file scope and changed-line size, preserves staging, and leaves implementation commits to the user.
- Missing verification and changed execution inputs block execution; approval is recorded by [the private helper](../src/automagic/execution.py), not inferred from Markdown.
