import json
import shlex
import subprocess
import sys

import pytest

from automagic.execution import git


@pytest.fixture
def command():
    def build(code):
        return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"

    return build


@pytest.fixture
def worker(command):
    return (
        command(
            "from pathlib import Path; "
            "item = Path('.automagic/BRIEF.md').read_text().splitlines()[0].split()[-1]; "
            "Path(item + '.txt').write_text('x\\n')"
        )
        + " {prompt}"
    )


@pytest.fixture
def commit():
    def perform(root, *paths):
        assert git(["--literal-pathspecs", "add", "--", *paths], root).returncode == 0
        result = git(
            [
                "commit",
                "-q",
                "-m",
                "fixture\n\nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
            ],
            root,
        )
        assert result.returncode == 0, result.stderr

    return perform


@pytest.fixture
def repo_factory(tmp_path, command, commit):
    counter = 0

    def create(dependencies=None, verify=None, extra_files=None):
        nonlocal counter
        root = tmp_path / f"repo {counter}"
        counter += 1
        root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
        assert git(["config", "user.name", "Fixture"], root).returncode == 0
        assert git(["config", "user.email", "fixture@example.invalid"], root).returncode == 0
        dependencies = dependencies if dependencies is not None else [[]]
        verify = verify or command(
            "from pathlib import Path; "
            "item = Path('.automagic/BRIEF.md').read_text().splitlines()[0].split()[-1]; "
            "assert Path(item + '.txt').is_file()"
        )
        (root / "README.md").write_text(f"# Demo\n\nVerification: `{verify}`\n")
        (root / "INTENT.md").write_text("# Intent\n\nProduce reviewed files.\n")
        work = root / "docs/work"
        work.mkdir(parents=True)
        item_paths = []
        for i, prerequisites in enumerate(dependencies, 1):
            item_id = f"wi-{i:02}"
            metadata = {
                "id": item_id,
                "title": f"Add item {item_id}",
                "depends_on": prerequisites,
                "files": [f"{item_id}.txt", *(extra_files or [])],
                "est_lines": 100,
                "acceptance_criteria": [f"{item_id}.txt exists"],
                "verify_command": verify,
                "verify_sources": ["README.md"],
            }
            text = "\n".join(f"{key} = {json.dumps(value)}" for key, value in metadata.items())
            relative = f"docs/work/{item_id}.md"
            (root / relative).write_text(
                f"# {item_id}\n\n```toml\n{text}\n```\n\nBuild only {item_id}.\n"
            )
            item_paths.append(relative)
        metadata = {
            "schema_version": 1,
            "goal": "Demo",
            "sources": ["INTENT.md", "README.md"],
            "items": item_paths,
            "blockers": [],
        }
        text = "\n".join(f"{key} = {json.dumps(value)}" for key, value in metadata.items())
        (work / "_index.md").write_text(f"# Handoff\n\n```toml\n{text}\n```\n\nReviewed design.\n")
        commit(root, ".")
        return root

    return create
