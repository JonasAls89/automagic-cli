# 0019. Repository security setup during init

- Status: accepted
- Date: 2026-11-07

## Context

The Markdown-only initialization scope in
[ADR 0017](0017-skills-first-intent-workflow.md) omitted useful repository
security configuration. Users also need help matching pre-commit checks to
their programming language and framework, without restoring application
scaffolding or a hardcoded framework matrix.

## Decision

Extend [init](../../skills/init/SKILL.md) with three bundled setup files:

- [Dependabot](../../skills/init/assets/.github/dependabot.yml), initially weekly
  GitHub Actions updates at the repository root with a seven-day version cooldown.
- [zizmor configuration](../../skills/init/assets/.github/zizmor.yml), leaving
  default audits enabled, not a generated CI workflow.
- [Pre-commit configuration](../../skills/init/assets/.pre-commit-config.yaml),
  with common file checks and the pinned official zizmor hook.

The [scaffolder](../../src/automagic/scaffold.py) permits these YAML paths in
addition to Markdown. It retains dry-run and explicit overwrite behavior.
The init skill inspects actual manifests, lockfiles, frameworks and existing
tool configuration, then proposes confirmed hook additions and weekly Dependabot
package-ecosystem/directory entries. Existing configs are merged after review;
a skipped pre-commit file still needs a pinned zizmor hook.
Use the seven-day version-update cooldown in added entries so generated
configuration passes zizmor's default cooldown audit. Security updates are
unaffected by this version-update setting.

Prefer existing project tools and verified hook IDs/revisions. Unknown stacks
require questions, not guessed commands. Validate hook syntax with the bundle's
prek; hook installation and execution require confirmation. Do not install target
dependencies or initialize Git automatically, and report skipped or failed steps.

Keep common/zizmor template pins aligned with this repository's hooks, enforced
by [baseline tests](../../tests/test_baseline.py). The
[installed-bundle tests](../../tests/test_plugin.py) cover generated YAML without
using or modifying the target dependency environment.

Sources:
[Dependabot options](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference),
[supported ecosystems](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories),
[zizmor configuration](https://docs.zizmor.sh/configuration/), and the
[pinned hook manifest](https://github.com/zizmorcore/zizmor-pre-commit/blob/v1.31.1/.pre-commit-hooks.yaml).

## Consequences

This supersedes ADR 0017's Markdown-only/no-hook-generation restriction.
The five-skill interface and [current-only support policy](0018-current-workflow-only.md)
remain unchanged. The helper creates the common baseline; stack-specific setup is
an explicitly reviewed skill step, not automatic application/toolchain generation.
