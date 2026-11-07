# automagic

A five-skill Copilot plugin connecting project intent and external planning to
reviewed, verified worktree execution. It also supplies a Markdown-only
repository baseline. There is no public `automagic` CLI.

## Install

Install this repository as an agent plugin from its Git URL or a local checkout.
In VS Code, use **Chat: Install Plugin from Source** with
`https://github.com/JonasAls89/automagic-cli`, or the local repository path.
See [VS Code's plugin documentation](https://code.visualstudio.com/docs/agent-customization/agent-plugins)
for harness and policy requirements.

Install the complete bundle, not individual skill folders. It contains the
[plugin manifest](.claude-plugin/plugin.json), [skills](skills/), assets and
[private helpers](scripts/helper.py). The skill namespace is `automagic`.

Execution requires uv, Python 3.12+ and Git. Running agents additionally requires
Copilot CLI. Thorough planning uses your separately installed Matt Pocock plugin,
whose verified planning skill is `/mattpocock-skills:grill-with-docs`.
Automagic does not install or vendor it.

## Workflows

| Skill | What it does |
|---|---|
| [/automagic:init](skills/init/SKILL.md) | Previews/applies Markdown and agent files, preserving existing content. |
| [/automagic:context](skills/context/SKILL.md) | Refreshes the managed context index and validates active document size and relative links. |
| [/automagic:intent](skills/intent/SKILL.md) | Captures high-level project purpose, users, outcomes, scope and constraints in root `INTENT.md`. |
| [/automagic:worktrees](skills/worktrees/SKILL.md) | Proposes bounded work from approved design, then offers approval and creation separately. |
| [/automagic:run](skills/run/SKILL.md) | Previews/runs committed approved work with Copilot CLI, verification and dependencies. |

These are user-invoked workflows. Installing the plugin or capturing intent does
not start planning, create branches or launch agents.

### Intent to execution

1. Initialize the Markdown baseline. It includes `AGENTS.md`, Copilot/docs
   instructions, glossary, context index, guardrails and ADR setup
   ([assets](skills/init/assets/)). No manifests, language tooling, CI, hooks,
   dependencies or unfinished intent file are generated.
2. Capture intent with `/automagic:intent`. Confirm its proposed edits;
   intent approval is not design approval.
3. Invoke `/mattpocock-skills:grill-with-docs` yourself, supplying intent and
   relevant repository context. Preserve the approved design in repository
   documents; an ADR collection alone does not prove planning is complete.
4. Use `/automagic:worktrees`. Review the deliverables, acceptance criteria,
   exact allowed files, estimates, blocking edges and verification evidence.
   Unresolved design questions go back to external planning.
5. Review and commit the handoff and its inputs yourself. Approve the exact
   preview, then separately confirm worktree creation.
6. Preview `/automagic:run`, then confirm unattended execution. Review and
   manually commit prerequisite work before resuming dependent items.

### Markdown handoff

The [index template](skills/worktrees/assets/_index.md) becomes
`docs/work/_index.md`; the [work-item template](skills/worktrees/assets/work-item.md)
becomes one Markdown file per stable ID. Fill every placeholder. Metadata uses
one fenced TOML block:

- Index: `schema_version = 1`, goal, repository-relative sources, work-item paths,
  and an explicit blockers list (empty only once design blockers are resolved).
- Work item: ID/title, dependencies, individual allowed file paths, changed-line
  estimate, observable acceptance criteria, verification command and evidence paths.
- The exact verification command must occur in a declared evidence document.
  Unknown checks must be documented and committed before execution.
- Keep each context document below 8,000 bytes. Default sizing allows about
  40,000 estimated context tokens and 400 changed lines; oversized work needs a
  reviewed split ([validation](src/automagic/execution.py)).

The helper fingerprints intent, the index, briefs, selected design inputs,
existing agent/context guidance, verification evidence and the base commit.
All inputs must be committed. Editing a Markdown "approved" label cannot grant
approval. Changed contents or base commits require a new reviewed snapshot.
Human review, not validation, judges semantic completeness.
Worktrees inherit the recorded committed base, not uncommitted application
sources in the main checkout. Commit intended source inputs or deliberately
exclude those local changes when reviewing the execution handoff.

### Execution and human control

The [runner](src/automagic/runner.py) executes ready work items in waves, with
default concurrency 3. `--only` on the private launcher supports stable IDs and
includes unfinished prerequisites. Repeating the same approved snapshot resumes
unfinished work without rerunning completed agents.

Agents leave implementation and formatter fixes uncommitted and preserve staging.
They must pass documented verification and approved scope/changed-line checks.
No-change exits, failed checks and binary changes requiring human review fail
explicitly. Failed prerequisites block dependents; independent siblings continue.

Dependent work pauses until prerequisites are clean and contain genuinely
committed changes. The confirmed orchestrator integrates those prerequisite
branches; implementation agents never commit, push or merge. Guardrails are
best-effort, not a sandbox: unattended Copilot CLI has shell access.

State, approvals, worktree ownership and logs are local under
`.automagic/execution/`; keep them out of commits. Each worktree has an untracked
`.automagic/BRIEF.md`. Branches retain categorized intent-based names with a
snapshot suffix. Existing unowned branches/paths are collisions, not opportunities
for reset, deletion or automatic adoption ([worktree helpers](src/automagic/worktrees.py)).
Concurrent mutations are locked; check for active helpers/workers before
explicitly removing an abandoned lock. A failed or interrupted worker is retried
only after another confirmed run; logs and existing changes remain available.

### Existing repositories and legacy plans

Init skips existing files; a skip is not an upgrade. The skill proposes confirmed
edits for outdated sections, including adding managed context markers when absent.
Legacy workspace skills, configurations, `docs/plan/` and `.automagic/plan/` remain
untouched. They are not imported as execution state. There is no graph migration,
planning resume/amend, HTML/show surface or separate review command.

The current contract and immutable historical ADR references are in
[ADR 0017](docs/adr/0017-skills-first-intent-workflow.md).

### Why not Cookiecutter?

Cookiecutter generates skeletons, not this recurring workflow. The plugin's
single asset set serves new and existing repositories. Cookiecutter is deferred,
not a dependency or parallel template product.

## Development

The Python package is private implementation, not an installable workflow CLI.
The launcher explicitly loads its bundled sources, so cached/global installs
cannot override them; dependencies still come from the bundle's uv environment.
Skills invoke the installed bundle's environment with an explicit target path:

```sh
uv run --project "/absolute/plugin-root" --locked python "/absolute/plugin-root/scripts/helper.py" context --path "/absolute/target-repository"
```

From this checkout:

```sh
uv run python scripts/helper.py context
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run bandit -c pyproject.toml -r .
uv run prek run --all-files
```

Tests include disposable Git repositories and fake workers; they do not launch
real unattended agents. Coverage of helpers and the private launcher must remain
at least 90%. The repository's own Python tooling/CI is retained; it is not part
of the generated target baseline.
