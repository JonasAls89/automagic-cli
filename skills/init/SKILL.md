---
name: init
description: Set up a Markdown-only repository baseline, preserving existing files.
disable-model-invocation: true
---

Initialize an existing or new repository using the [baseline assets](assets/)
and the [private helper](../../scripts/helper.py).

Resolve the plugin root two levels above this skill directory. Substitute and
shell-quote absolute plugin and target repository paths in these commands:

```sh
uv run --project <plugin-root> --locked python <plugin-root>/scripts/helper.py init --path <repository> --dry-run
```

1. Confirm the target repository and project name. Preview the files with the
   command above; add `--name` when the directory name is not the project name.
2. Apply by removing `--dry-run`. Existing files are skipped. Explain skipped or
   outdated baseline sections; propose surgical edits and obtain confirmation.
   Use `--force` only when the user explicitly approves overwriting generated files.
   If an existing context index lacks the managed markers, propose adding one
   managed section before retrying; preserve the rest.
3. Report created/skipped files and any errors. Init adds no language tooling,
   hooks, CI, dependencies, intent skeleton, or duplicate workspace skills.
   Legacy skills and plan files remain untouched; explain that they were not upgraded.
4. Show `/automagic:intent` as the next user invocation. Do not start planning,
   create worktrees or launch agents automatically.
