# Copilot instructions for automagic

Keep this file short. Detailed context lives in `docs/` and is indexed in [docs/CONTEXT.md](../docs/CONTEXT.md).

## Grounding

- Base every claim and change on files you have read in this repository. Cite paths.
- If a fact is not in the repo or the task, say so and ask; do not guess APIs, flags or file names.
- Read `docs/CONTEXT.md` first, then only the documents relevant to the task.
- Record significant decisions as an ADR in `docs/adr/`.

## Scope and context size

- Make small, reviewable changes: about 400 changed lines per pull request.
- Do not load or paste large files wholesale; read ranges.
- Do not expand scope beyond the work item you were given.

## Quality gates

- Use `uv run` for everything. Lint and format with ruff, scan with bandit, run `uv run pytest`.
- Run `prek run --all-files` before committing (prek reads `.pre-commit-config.yaml`; install hooks with `prek install`).
- GitHub workflows must pass zizmor: minimal `permissions`, pinned actions, `persist-credentials: false`.
