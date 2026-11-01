# 0008. Python development baseline

- Status: accepted
- Date: 2026-11-01

## Context

Python initialization should configure development checks without choosing application dependencies, a packaging model, or installing tools on the user's behalf. The [scaffolder](../../src/automagic/scaffold.py) already distinguishes generated files from existing files and supports a non-writing preview.

## Decision

Extend the [Python project template](../../src/automagic/templates/python/pyproject.toml.j2) with pytest-cov, pytest discovery under `tests/`, imports from `src/`, and package-scoped coverage. Use the same 90% coverage minimum as this repository's [project configuration](../../pyproject.toml).

Keep runtime dependencies empty. Include pytest, pytest-cov, Ruff, Bandit, prek and zizmor in the development group. Pin Ruff, Bandit and zizmor to the versions used by the [hook template](../../src/automagic/templates/python/.pre-commit-config.yaml).

Validate the lockfile with a local `uv lock --check` hook when the manifest or lockfile changes. Keep [CI](../../src/automagic/templates/python/.github/workflows/ci.yml) on `uv sync --locked` and include the workflow scanner in CI.

When the Python manifest is created or overwritten, [init](../../src/automagic/cli.py) prints `uv sync` and `uv run prek install` before the existing guardrail check. It does not execute those commands. Preserve existing-file skipping, explicit `--force`, dry-run behavior and the TypeScript baseline.

## Consequences

New Python projects have configured checks, but must add application code and tests themselves. Users run the printed setup commands and commit the resulting lockfile before CI. A missing or stale lockfile is an explicit hook failure; init does not generate one.

Hook and development-tool version updates must stay aligned. Existing projects do not gain this configuration automatically, and application-specific dependencies and build backends remain project decisions.
