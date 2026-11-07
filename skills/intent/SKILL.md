---
name: intent
description: Capture or revise high-level project purpose, users, outcomes and scope in INTENT.md.
disable-model-invocation: true
---

1. Read `docs/CONTEXT.md` when present, then root `INTENT.md`, README, glossary
   and relevant decisions. Ask for facts not available in the repository.
2. Use the [intent template](assets/INTENT.md) as a capture outline. Ask focused
   questions about the problem, intended users, outcomes, observable success,
   scope/non-goals and constraints. Record unknowns explicitly. Keep implementation
   decisions, architecture design and work-item decomposition for external planning.
3. Present the proposed high-level intent (or a surgical revision of existing
   intent) for confirmation, then write root `INTENT.md`. Keep it below 8,000 bytes.
   Capture approval is not approval of a design or permission to execute.
4. Refresh/validate context using the [private helper](../../scripts/helper.py):
   resolve the plugin root two levels above this directory and run
   `uv run --project <plugin-root> --locked python <plugin-root>/scripts/helper.py context --path <repository>`,
   substituting and shell-quoting absolute paths.
5. Show this explicit user handoff:

   `/mattpocock-skills:grill-with-docs`

   Ask the user to give that skill `INTENT.md` and the relevant context/decisions
   as planning inputs, and preserve the settled design in repository documents.
   The external skill is user-invoked: do not silently invoke it or copy its
   instructions. If unavailable, report the missing dependency and direct the
   user to install their Matt Pocock plugin; do not claim planning is complete.
6. Explain that, after external planning and explicit design approval,
   `/automagic:worktrees` proposes the reviewed execution handoff, then
   `/automagic:run` executes it. Neither step starts automatically.
