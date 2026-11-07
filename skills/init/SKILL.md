---
name: init
description: Set up repository docs, GitHub security configuration and language/framework-aware pre-commit checks.
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
   Inspect manifests, lockfiles, existing hooks, formatter/linter configuration,
   framework configuration and documented verification commands. Account for
   each language/framework and package directory in a monorepo. Ask when the
   intended stack is absent or ambiguous; do not choose a framework from a language alone.
2. Apply by removing `--dry-run`. Existing files are skipped. Explain skipped or
   outdated baseline sections; propose surgical edits and obtain confirmation.
   Use `--force` only when the user explicitly approves overwriting generated files.
   If an existing context index lacks the managed markers, propose adding one
   managed section before retrying; preserve the rest.
3. Report created/skipped files and errors. The baseline includes Markdown/agent
   files, `.github/dependabot.yml` (weekly Actions updates, seven-day version cooldown),
   `.github/zizmor.yml` (default audits enabled), and `.pre-commit-config.yaml`
   (common file checks plus pinned zizmor). No application manifests, CI workflows,
   dependencies, intent skeleton or duplicate workspace skills are generated.
4. Propose language/framework-specific pre-commit additions using the project's
   existing tools and configuration. Prefer verified native hook IDs with pinned
   revisions. For project commands without a native hook, use a local
   `language: system` hook with the documented command and appropriate file
   selection; set `pass_filenames: false` for project-wide commands. Explain
   formatter, linter, framework-specific and test checks separately.
   Obtain confirmation before edits or introducing tools. Merge into existing
   configs rather than replacing custom hooks. Keep a pinned zizmor hook in the
   final configuration, even when the baseline config was skipped.
5. Propose weekly Dependabot entries for the actual package ecosystems and
   manifest/lockfile directories. Framework names are not package ecosystems:
   verify the package-manager mapping against
   [GitHub's supported ecosystems](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories).
   Confirm the proposed edits, keep the GitHub Actions entry and avoid duplicate
   ecosystem/directory entries. Use the baseline's seven-day version-update
   cooldown for each entry; security updates are not delayed by it.
   With no package manifests, retain only the
   Actions baseline and report package updates as not yet configured.
6. Validate the final hook configuration with the bundle's existing prek:

   ```sh
   uv run --project <plugin-root> --locked prek --cd <repository> validate-config .pre-commit-config.yaml
   ```

   Confirm before installing Git hooks (`prek install`) or executing them
   (`prek run --all-files`), using the same bundle-owned uv invocation.
   Installation requires a Git repository; do not initialize Git automatically.
   Report missing tools, failed checks and skipped/untracked files explicitly.
   Never stage or commit for the user. Keep zizmor's default audits enabled;
   exemptions require separate justification and approval
   ([configuration](https://docs.zizmor.sh/configuration/)).
7. Completion means the generated/merged configs are reviewed, hook syntax is
   valid, and pinned zizmor remains present. State whether hooks were installed
   and run; a declined setup step is not a completed step.
   Show `/automagic:intent` as the next user invocation. Do not start planning,
   create worktrees or launch agents automatically.
