---
name: run
description: Run and verify an approved committed execution snapshot in dependency-ordered worktrees.
disable-model-invocation: true
---

1. Read intent, context/guardrails and `docs/work/_index.md`. Planning and the
   execution breakdown must be approved through `/automagic:worktrees`.
2. Resolve the plugin root two levels above this directory. Substitute and
   shell-quote absolute paths, and preview with the [private helper](../../scripts/helper.py):

   ```sh
   uv run --project <plugin-root> --locked python <plugin-root>/scripts/helper.py run --path <repository> --dry-run
   ```

   Use `--only wi-01,wi-03` for a subset (unfinished prerequisites are included)
   or an explicitly requested positive `--max-concurrency` override.
3. Present the snapshot, base, branches, dependency waves, agent and verification
   commands. Obtain confirmation for unattended shell access, then remove
   `--dry-run` and add `--yes`. The default engine is Copilot CLI; this is not a
   sandbox. Do not replace it with chat-host subagents.
4. The helper rechecks committed inputs and approval after confirmation, records
   state/logs outside worktrees, verifies changes and checks approved scope/limits.
   Missing verification, failed checks, changed input and no-change agent exits
   cannot count as completion. Report errors and failure logs explicitly.
5. Agents leave implementation and formatter fixes uncommitted, preserving
   staging. Dependents pause until prerequisite changes are manually reviewed
   and committed; independent work continues. Report pending commit requirements
   and rerun to resume. Committed prerequisites are integrated by the confirmed
   orchestrator, never by implementation agents.
6. Never stage, commit, push, merge or reset on the user's behalf. A changed
   execution snapshot requires `/automagic:worktrees` review/reapproval; do not
   silently reuse approval or overwrite existing worktrees.
   If a helper lock was abandoned, verify no helper/workers are active before
   asking for explicit removal of that specific lock; never remove a live lock.
