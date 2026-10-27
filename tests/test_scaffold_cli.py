import subprocess

import pytest
from typer.testing import CliRunner

from automagic import guardrails
from automagic.cli import app
from automagic.config import Config
from automagic.scaffold import scaffold

runner = CliRunner()


@pytest.mark.parametrize("language", ["python", "typescript"])
def test_init_creates_baseline_and_passes_own_check(tmp_path, language):
    result = runner.invoke(app, ["init", "--path", str(tmp_path), "--language", language])
    assert result.exit_code == 0, result.output
    for rel in (
        ".pre-commit-config.yaml",
        "AGENTS.md",
        ".github/copilot-instructions.md",
        "docs/adr/0001-record-architecture-decisions.md",
        ".github/prompts/automagic-plan.prompt.md",
        ".github/skills/automagic-plan/SKILL.md",
        ".github/workflows/ci.yml",
    ):
        assert (tmp_path / rel).exists(), rel
    assert guardrails.check(tmp_path, Config()) == []
    assert runner.invoke(app, ["check", "--path", str(tmp_path)]).exit_code == 0


def test_python_precommit_has_required_hooks(tmp_path):
    runner.invoke(app, ["init", "--path", str(tmp_path), "--language", "python"])
    text = (tmp_path / ".pre-commit-config.yaml").read_text()
    for hook in ("ruff-check", "ruff-format", "zizmor", "bandit"):
        assert hook in text
    assert (tmp_path / ".python-version").exists()


def test_scaffold_is_idempotent_and_never_overwrites(tmp_path):
    (tmp_path / "AGENTS.md").write_text("mine")
    scaffold(tmp_path, "python", "demo", Config())
    assert (tmp_path / "AGENTS.md").read_text() == "mine"
    again = scaffold(tmp_path, "python", "demo", Config())
    assert {a.status for a in again} == {"skipped"}
    forced = scaffold(tmp_path, "python", "demo", Config(), force=True)
    assert (tmp_path / "AGENTS.md").read_text() != "mine"
    assert {a.status for a in forced} == {"overwritten"}


def test_dry_run_writes_nothing(tmp_path):
    actions = scaffold(tmp_path, "python", "demo", Config(), dry_run=True)
    assert actions and all(a.status == "would-create" for a in actions)
    assert not any(tmp_path.iterdir())


def test_check_detects_size_broken_links_and_unindexed(tmp_path):
    runner.invoke(app, ["init", "--path", str(tmp_path), "--language", "python"])
    (tmp_path / "AGENTS.md").write_text("see [x](missing.md) and `[y](nope.md)`\n" + "z" * 9000)
    (tmp_path / "docs" / "extra.md").write_text("# Extra\n")
    problems = "\n".join(guardrails.check(tmp_path, Config()))
    assert "exceeds limit" in problems
    assert "broken link missing.md" in problems
    assert "nope.md" not in problems
    assert "extra.md: not listed" in problems
    runner.invoke(app, ["context", "--path", str(tmp_path)])
    assert "extra.md: not listed" not in "\n".join(guardrails.check(tmp_path, Config()))


def test_unsupported_language(tmp_path):
    result = runner.invoke(app, ["init", "--path", str(tmp_path), "--language", "cobol"])
    assert result.exit_code == 1


def test_worktrees_create_end_to_end(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "i"], cwd=repo, check=True)
    from automagic.merkle import Graph, Node

    g = Graph(goal="Demo goal")
    g.add(Node(id="a", title="A", content="do a", est_lines=10))
    g.add(Node(id="b", title="B", content="do b", depends_on=["a"], est_lines=500))
    for n in g.nodes:
        g.validate(n)
    g.save(repo / ".automagic" / "plan" / "graph.json")

    shown = runner.invoke(app, ["worktrees", "--path", str(repo)])
    assert shown.exit_code == 0 and "git worktree add" in shown.output
    assert not (tmp_path / "repo.worktrees").exists()

    done = runner.invoke(app, ["worktrees", "--path", str(repo), "--create", "--yes"])
    assert done.exit_code == 0, done.output
    briefs = sorted((tmp_path / "repo.worktrees").glob("*/.automagic/BRIEF.md"))
    assert len(briefs) == 2
    assert "do a" in briefs[0].read_text()
