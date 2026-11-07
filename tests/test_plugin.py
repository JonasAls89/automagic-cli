import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

from automagic.config import Config
from automagic.guardrails import _links, check
from scripts.helper import BUNDLE, main


def test_exactly_five_consistent_plugin_skills_and_resolvable_resources():
    manifest = json.loads((BUNDLE / ".claude-plugin/plugin.json").read_text())
    assert manifest["name"] == "automagic"
    names = {"init", "context", "intent", "worktrees", "run"}
    assert set(manifest["skills"]) == {f"./skills/{name}" for name in names}
    assert {path.name for path in (BUNDLE / "skills").iterdir() if path.is_dir()} == names
    for name in names:
        path = BUNDLE / "skills" / name / "SKILL.md"
        text = path.read_text()
        assert f"name: {name}\n" in text
        assert "disable-model-invocation: true" in text
        for link in _links(text):
            assert (path.parent / link).resolve().is_relative_to(BUNDLE)
            assert (path.parent / link).exists()
    project = tomllib.loads((BUNDLE / "pyproject.toml").read_text())
    assert "scripts" not in project["project"]
    assert check(BUNDLE, Config()) == []


def test_installed_bundle_uses_own_environment_and_assets(tmp_path, repo_factory, commit, worker):
    bundle = tmp_path / "installed plugin"
    bundle.mkdir()
    for name in ("pyproject.toml", "uv.lock", "README.md"):
        shutil.copy2(BUNDLE / name, bundle / name)
    for name in ("src", "scripts", "skills", ".claude-plugin"):
        shutil.copytree(BUNDLE / name, bundle / name, ignore=shutil.ignore_patterns("__pycache__"))
    target = tmp_path / "unrelated target"
    target.mkdir()
    manifest = (
        b'[project]\nname = "target"\nversion = "0.0.1"\n'
        b'dependencies = ["never-install-this-target"]\n'
    )
    (target / "pyproject.toml").write_bytes(manifest)
    (target / "uv.lock").write_bytes(b"user lock")
    shadow = target / "shadow/automagic"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("raise RuntimeError('foreign runtime was imported')\n")
    environment = {**os.environ, "PYTHONPATH": str(shadow.parent)}
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(bundle),
            "--locked",
            "python",
            str(bundle / "scripts/helper.py"),
            "init",
            "--path",
            str(target),
        ],
        cwd=target,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "AGENTS.md").exists()
    assert (target / "pyproject.toml").read_bytes() == manifest
    assert (target / "uv.lock").read_bytes() == b"user lock"
    assert not (target / ".venv").exists()
    assert not (target / ".github/skills").exists()
    assert not (target / "INTENT.md").exists()
    repository = repo_factory([[], ["wi-01"]])

    def invoke(*arguments):
        outcome = subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(bundle),
                "--locked",
                "python",
                str(bundle / "scripts/helper.py"),
                *arguments,
                "--path",
                str(repository),
            ],
            cwd=target,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert outcome.returncode == 0, outcome.stdout + outcome.stderr
        return outcome.stdout

    invoke("init")
    commit(repository, ".")
    invoke("worktrees", "--approve", "--yes")
    invoke("worktrees", "--create", "--yes")
    invoke("run", "--dry-run", "--runner-command", worker)
    output = invoke("run", "--yes", "--runner-command", worker)
    assert "waiting for manual commits" in output
    state_file = next((repository / ".automagic/execution").glob("*/run.json"))
    state = json.loads(state_file.read_text())
    assert state["items"]["wi-01"]["status"] == "done"
    assert state["items"]["wi-02"]["status"] == "pending"
    first = Path(state["items"]["wi-01"]["path"])
    commit(first, "wi-01.txt")
    invoke("run", "--yes", "--runner-command", worker)
    state = json.loads(state_file.read_text())
    second = Path(state["items"]["wi-02"]["path"])
    assert state["items"]["wi-02"]["status"] == "done"
    assert (second / "wi-01.txt").is_file()
    assert not (repository / "wi-01.txt").exists()
    assert not (repository / ".venv").exists()


def test_private_helper_confirmation_and_error_paths(repo_factory, worker, monkeypatch, capsys):
    root = repo_factory()
    assert main(["worktrees", "--path", str(root)]) == 0
    assert "preview only" in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert main(["worktrees", "--path", str(root), "--approve"]) == 0
    assert "declined" in capsys.readouterr().out
    assert main(["worktrees", "--path", str(root), "--approve", "--create", "--yes"]) == 1
    assert "separate" in capsys.readouterr().err
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert main(["worktrees", "--path", str(root), "--approve"]) == 0
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert main(["worktrees", "--path", str(root), "--create"]) == 0
    assert main(["run", "--path", str(root), "--runner-command", worker]) == 0
    assert "execution declined" in capsys.readouterr().out
    assert main(["run", "--path", str(root), "--max-concurrency", "0"]) == 1
    assert "max_concurrency" in capsys.readouterr().err
