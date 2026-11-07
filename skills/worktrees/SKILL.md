---
name: worktrees
description: Turn an externally approved design into reviewed Markdown work items and Git worktrees.
disable-model-invocation: true
---

1. Read root `INTENT.md`, context/guardrails and the user's selected settled
   planning documents. Obtain confirmation that the design is externally approved.
   ADRs, an intent document or a quiet interviewer alone do not prove readiness.
   If scope, acceptance, constraints or blocking decisions remain unresolved,
   show the gap and send the user back to `/mattpocock-skills:grill-with-docs`.
2. Propose execution-sized work items, not new design decisions. Each should fit
   one context window (about 40,000 tokens) and a review (about 400 changed lines).
   Show each item's deliverable, acceptance criteria, exact allowed files,
   genuine prerequisites, and verification command with its repository evidence.
   Ask for a documented command when missing; never assume a successful default.
3. After confirming the breakdown, write `docs/work/_index.md` and one document
   per stable work-item ID using the [index](assets/_index.md) and
   [work-item](assets/work-item.md) templates. Fill all placeholders. Use literal
   repository-relative file paths, not globs or directories. Keep each document
   below 8,000 bytes. Clear `blockers` only after design gaps are settled.
   Handoff inputs must be regular tracked files. For symlinked inputs, propose
   confirmed regular-document snapshots; never replace user-owned links silently.
4. Use the [helper](../../scripts/helper.py) to refresh context, then preview
   the exact snapshot, commands, prerequisites, branches and base commit:

   ```sh
   uv run --project <plugin-root> --locked python <plugin-root>/scripts/helper.py context --path <repository>
   uv run --project <plugin-root> --locked python <plugin-root>/scripts/helper.py worktrees --path <repository>
   ```

   Resolve plugin root two levels above this directory; substitute and shell-quote
   absolute paths. The helper validates typed metadata, dependencies, evidence,
   size and paths, but cannot prove semantic completeness.
5. Ask the user to review and commit the intent, selected design documents,
   instructions, verification evidence and execution handoff. Never stage or
   commit for them. Account explicitly for uncommitted application sources read
   during normalization: worktrees inherit only the recorded committed base.
   Ask whether intended source inputs should be committed or deliberately excluded.
   Once committed, obtain explicit approval of the exact preview
   and run the worktrees helper with `--approve --yes`. This records helper-owned
   approval; a Markdown "approved" label is insufficient.
6. Separately obtain confirmation to create the shown worktrees, then use
   `--create --yes`. Creation does not launch agents. Existing owned worktrees
   are reused; collisions require explicit resolution, never reset or deletion.
7. Show `/automagic:run`. Changed inputs/base commits require a new reviewed
   snapshot. Do not silently replan, start agents, commit or push.
