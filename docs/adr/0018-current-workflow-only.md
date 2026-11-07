# 0018. Support only the current workflow

- Status: accepted
- Date: 2026-11-07

## Context

The five-skill product established in
[ADR 0017](0017-skills-first-intent-workflow.md) no longer needs special handling
or preservation obligations for former configuration, planning or skill formats.
Those branches and tests add maintenance beyond the current workflow.

## Decision

Supersede ADR 0017's legacy-specific detection and preservation policy.
The [private helper](../../scripts/helper.py) exposes only the current workflows;
it does not inspect retired skill names or load repository configuration files.
The unused tracked configuration and state-directory constant are removed.

[Execution](../../src/automagic/execution.py) requires `docs/work/_index.md`
and typed Markdown work items. A missing handoff reports that requirement without
probing other formats. Design-source filenames receive no special classification;
regular committed files named `graph.json` are ordinary input documents.
The automation runtime namespace remains excluded from design inputs.

Remove compatibility-specific fixtures, tests and active workflow guidance.
There is no migration, conversion, cleanup or preservation feature for former
formats. Generic safety remains: [init](../../src/automagic/scaffold.py) skips
existing files unless overwrite is explicitly approved, and
[context](../../src/automagic/guardrails.py) retains text outside its managed
region. Current snapshot/worktree ownership, staging and human-owned commit
policies in the [runner](../../src/automagic/runner.py) remain unchanged.

## Consequences

Only the current handoff and runtime format are supported. Files outside the
managed workflow receive ordinary repository-file treatment; the helper does
not automatically delete target-repository data.

Accepted ADR 0017 remains an immutable decision record, not a live support
promise where this decision supersedes it.
