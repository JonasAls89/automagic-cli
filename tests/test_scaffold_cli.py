import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from automagic import guardrails
from automagic.cli import app
from automagic.config import Config
from automagic.scaffold import scaffold
from automagic.slashcmds import COMMANDS, slash_files

runner = CliRunner()


@pytest.mark.parametrize("language", ["python", "typescript"])
def test_init_creates_baseline_and_passes_own_check(tmp_path, language):
    result = runner.invoke(
        app, ["init", "--path", str(tmp_path), "--language", language, "--name", "Demo Project"]
    )
    assert result.exit_code == 0, result.output
    next_steps = (
        "uv sync && uv run prek install && automagic check"
        if language == "python"
        else "prek install && automagic check"
    )
    assert f"Next: {next_steps}" in result.output
    assert not (tmp_path / ".venv").exists()
    assert not (tmp_path / ".git").exists()
    for rel in (
        ".pre-commit-config.yaml",
        "AGENTS.md",
        "GLOSSARY.md",
        ".github/copilot-instructions.md",
        "docs/adr/0001-record-architecture-decisions.md",
        ".github/skills/automagic-plan/SKILL.md",
        ".github/workflows/ci.yml",
    ):
        assert (tmp_path / rel).exists(), rel
    glossary = (tmp_path / "GLOSSARY.md").read_text()
    assert glossary.startswith("# Demo Project\n")
    assert "## Language\n" in glossary
    assert "created  GLOSSARY.md" in result.output
    assert "[Glossary](../GLOSSARY.md)" in (tmp_path / "docs/CONTEXT.md").read_text()
    assert not (tmp_path / ".github/prompts").exists()
    assert ".github/prompts/" not in result.output
    assert guardrails.check(tmp_path, Config()) == []
    assert runner.invoke(app, ["check", "--path", str(tmp_path)]).exit_code == 0


@pytest.mark.parametrize("language", ["python", "typescript"])
@pytest.mark.parametrize("force", [False, True])
def test_init_preserves_existing_glossary_unless_forced(tmp_path, language, force):
    glossary = tmp_path / "GLOSSARY.md"
    original = "# Existing domain\n\n## Language\n\n**Customer**:\nA buyer.\n"
    glossary.write_text(original)
    args = ["init", "--path", str(tmp_path), "--language", language, "--name", "Demo Project"]
    if force:
        args.append("--force")
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    status = "overwritten" if force else "skipped"
    assert f"{status}  GLOSSARY.md" in result.output
    if force:
        assert glossary.read_text().startswith("# Demo Project\n")
    else:
        assert glossary.read_text() == original


def test_slash_commands_only_generate_skills():
    assert set(slash_files()) == {f".github/skills/automagic-{name}/SKILL.md" for name in COMMANDS}


@pytest.mark.parametrize("command", ["plan", "show"])
def test_plan_skills_present_terminal_output_and_match_checked_in(command):
    relative = f".github/skills/automagic-{command}/SKILL.md"
    content = slash_files()[relative]
    assert "plan.md" not in content
    assert "`automagic show`" in content and "terminal output" in content
    assert (Path(__file__).resolve().parents[1] / relative).read_text() == content


@pytest.mark.parametrize("force", [False, True])
def test_scaffold_leaves_existing_prompts_untouched(tmp_path, force):
    path = tmp_path / ".github/prompts/automagic-plan.prompt.md"
    path.parent.mkdir(parents=True)
    path.write_text("mine")
    actions = scaffold(tmp_path, "python", "demo", Config(), force=force)
    assert path.read_text() == "mine"
    assert list(path.parent.iterdir()) == [path]
    assert all(not a.path.startswith(".github/prompts/") for a in actions)


def test_python_precommit_has_required_hooks(tmp_path):
    runner.invoke(app, ["init", "--path", str(tmp_path), "--language", "python"])
    text = (tmp_path / ".pre-commit-config.yaml").read_text()
    for hook in ("ruff-check", "ruff-format", "zizmor", "bandit", "uv-lock"):
        assert hook in text
    assert "entry: uv lock --check" in text
    assert "files: '^(pyproject\\.toml|uv\\.lock)$'" in text
    assert (tmp_path / ".python-version").exists()


def test_python_pyproject_has_development_and_coverage_baseline(tmp_path):
    result = runner.invoke(
        app, ["init", "--path", str(tmp_path), "--language", "python", "--name", "Demo Project"]
    )
    assert result.exit_code == 0, result.output
    project = tomllib.loads((tmp_path / "pyproject.toml").read_text())
    assert project["project"]["name"] == "demo_project"
    assert project["project"]["dependencies"] == []
    dev = project["dependency-groups"]["dev"]
    assert {requirement.partition("==")[0] for requirement in dev} == {
        "pytest",
        "pytest-cov",
        "ruff",
        "bandit[toml]",
        "prek",
        "zizmor",
    }
    hooks = (tmp_path / ".pre-commit-config.yaml").read_text()
    for package, repo, prefix in (
        ("ruff", "astral-sh/ruff-pre-commit", "v"),
        ("bandit[toml]", "PyCQA/bandit", ""),
        ("zizmor", "zizmorcore/zizmor-pre-commit", "v"),
    ):
        requirement = next((item for item in dev if item.startswith(f"{package}==")), None)
        assert requirement is not None
        version = requirement.partition("==")[2]
        assert f"repo: https://github.com/{repo}\n    rev: {prefix}{version}\n" in hooks
    assert project["tool"]["pytest"]["ini_options"] == {
        "testpaths": ["tests"],
        "pythonpath": ["src"],
        "addopts": ["--cov=demo_project", "--cov-report=term-missing"],
    }
    assert project["tool"]["coverage"]["report"]["fail_under"] == 90
    assert ".coverage*" in (tmp_path / ".gitignore").read_text()
    assert "uv run zizmor .github/workflows" in (tmp_path / ".github/workflows/ci.yml").read_text()


def test_python_coverage_baseline_enforces_threshold(tmp_path):
    scaffold(tmp_path, "python", "demo", Config())
    package = tmp_path / "src/demo"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "def choose(value):\n    if value:\n        return True\n    return False\n"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    test = tests / "test_demo.py"
    test.write_text("from demo import choose\n\n\ndef test_choose():\n    assert choose(True)\n")
    command = [sys.executable, "-m", "pytest", "-q"]
    failing = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert failing.returncode == 1, failing.stdout + failing.stderr
    assert "FAIL Required test coverage of 90" in failing.stdout
    test.write_text(
        "from demo import choose\n\n\ndef test_choose():\n"
        "    assert choose(True)\n    assert not choose(False)\n"
    )
    passing = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert passing.returncode == 0, passing.stdout + passing.stderr
    assert "100%" in passing.stdout


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("pyproject.toml", "[project]\nname = 'existing'\n"),
        (".pre-commit-config.yaml", "repos: []\n"),
    ],
)
def test_init_preserves_existing_python_tooling_files(tmp_path, rel, text):
    path = tmp_path / rel
    path.write_text(text)
    result = runner.invoke(app, ["init", "--path", str(tmp_path), "--language", "python"])
    assert result.exit_code == 0, result.output
    assert path.read_text() == text
    assert f"skipped  {rel}" in result.output
    if rel == "pyproject.toml":
        assert "Next: prek install && automagic check" in result.output


def test_scaffold_is_idempotent_and_never_overwrites(tmp_path):
    (tmp_path / "AGENTS.md").write_text("mine")
    scaffold(tmp_path, "python", "demo", Config())
    assert (tmp_path / "AGENTS.md").read_text() == "mine"
    again = scaffold(tmp_path, "python", "demo", Config())
    assert {a.status for a in again} == {"skipped"}
    forced = scaffold(tmp_path, "python", "demo", Config(), force=True)
    assert (tmp_path / "AGENTS.md").read_text() != "mine"
    assert {a.status for a in forced} == {"overwritten"}


@pytest.mark.parametrize("language", ["python", "typescript"])
def test_dry_run_writes_nothing(tmp_path, language):
    actions = scaffold(tmp_path, language, "demo", Config(), dry_run=True)
    assert actions and all(a.status == "would-create" for a in actions)
    assert all(not a.path.startswith(".github/prompts/") for a in actions)
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("language", ["python", "typescript"])
def test_init_dry_run_does_not_write_or_print_installation_steps(tmp_path, language):
    result = runner.invoke(
        app, ["init", "--path", str(tmp_path), "--language", language, "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "would-create" in result.output
    assert "would-create  GLOSSARY.md" in result.output
    assert "Next:" not in result.output
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("rel", "checked"),
    [
        (".github/prompts/custom.prompt.md", False),
        (".github/skills/custom/SKILL.md", True),
    ],
)
def test_only_skills_are_checked_as_slash_command_context(tmp_path, rel, checked):
    path = tmp_path / rel
    path.parent.mkdir(parents=True)
    path.write_text("[missing](missing.md)\n" + "z" * 9000)
    problems = "\n".join(guardrails.check(tmp_path, Config()))
    assert ("exceeds limit" in problems) is checked
    assert ("broken link missing.md" in problems) is checked


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


@pytest.mark.parametrize("legacy_content", [None, b"Legacy overview with manual edits\n"])
def test_worktrees_create_end_to_end(tmp_path, legacy_content):
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
    overview = repo / ".automagic" / "plan" / "plan.md"
    if legacy_content is not None:
        overview.write_bytes(legacy_content)

    shown = runner.invoke(app, ["worktrees", "--path", str(repo)])
    assert shown.exit_code == 0 and "git worktree add" in shown.output
    assert not (tmp_path / "repo.worktrees").exists()
    assert (overview.read_bytes() if overview.exists() else None) == legacy_content

    done = runner.invoke(app, ["worktrees", "--path", str(repo), "--create", "--yes"])
    assert done.exit_code == 0, done.output
    briefs = sorted((tmp_path / "repo.worktrees").glob("*/.automagic/BRIEF.md"))
    assert len(briefs) == 2
    assert "do a" in briefs[0].read_text()
    assert (overview.read_bytes() if overview.exists() else None) == legacy_content
