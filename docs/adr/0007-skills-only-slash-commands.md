# 0007. Skills-only slash commands

- Status: accepted
- Date: 2026-11-01

## Context

Slash-command bodies are centralised in [slashcmds.py](../../src/automagic/slashcmds.py). Only the skill-file output is required; the duplicate prompt-file format is no longer needed.

## Decision

Generate slash commands only as `.github/skills/automagic-*/SKILL.md`, retaining their names, descriptions and bodies ([slashcmds.py](../../src/automagic/slashcmds.py)).

Remove prompt-file discovery from context checks ([guardrails.py](../../src/automagic/guardrails.py)) and prompt-specific protected paths from work-item reviews ([review.py](../../src/automagic/review.py)). Skills retain both checks.

## Consequences

Fresh init and dry-run output contain no prompt files. Existing prompt files are left untouched, including with `--force`; init manages only its generated paths ([scaffold.py](../../src/automagic/scaffold.py), [tests](../../tests/test_scaffold_cli.py)).

Custom prompt files are now ordinary repository files rather than guarded context ([tests](../../tests/test_review.py)).
