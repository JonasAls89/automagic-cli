---
name: context
description: Refresh the repository context index and validate Markdown grounding and size.
disable-model-invocation: true
---

Use the [private helper](../../scripts/helper.py). Resolve the plugin root two
levels above this directory; substitute and shell-quote absolute paths:

```sh
uv run --project <plugin-root> --locked python <plugin-root>/scripts/helper.py context --path <repository>
```

The helper preserves text outside the managed context markers, indexes intent,
the glossary, ADR setup and execution documents, and validates active Markdown.
For read-only validation, add `--check`.

Read each reported problem and propose fixes grounded in the relevant files.
An absent, duplicate or misordered marker pair needs a confirmed surgical edit;
do not replace the user's context document. Refresh again after approved edits.
Completion means the index is current and validation passes, not that planning
or execution is approved. Legacy plan graphs are not consulted or modified.
