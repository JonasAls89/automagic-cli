import pytest

from automagic import guardrails
from automagic.config import Config
from automagic.scaffold import scaffold
from scripts.helper import BUNDLE, main

ASSETS = BUNDLE / "skills/init/assets"


def test_init_is_markdown_only_and_idempotent(tmp_path, capsys):
    assert main(["init", "--path", str(tmp_path), "--name", "Demo"]) == 0
    assert "Next: /automagic:intent" in capsys.readouterr().out
    files = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert len(files) == 8
    assert all(name.endswith(".md") for name in files)
    assert not (tmp_path / "INTENT.md").exists()
    assert not (tmp_path / ".github/skills").exists()
    assert not (tmp_path / ".automagic").exists()
    assert not (tmp_path / "pyproject.toml").exists()
    assert guardrails.check(tmp_path, Config()) == []
    assert main(["init", "--path", str(tmp_path)]) == 0
    assert files == {name: (tmp_path / name).read_bytes() for name in files}
    assert "skipped" in capsys.readouterr().out


@pytest.mark.parametrize("force", [False, True])
def test_existing_files_and_tooling_are_preserved(tmp_path, force):
    (tmp_path / "GLOSSARY.md").write_text("# Custom\n")
    for name in ("pyproject.toml", "uv.lock", ".pre-commit-config.yaml"):
        (tmp_path / name).write_text("owned by the user")
    old = tmp_path / ".automagic/plan/graph.json"
    old.parent.mkdir(parents=True)
    old.write_text("legacy graph")
    args = ["init", "--path", str(tmp_path)]
    if force:
        args.append("--force")
    assert main(args) == 0
    assert (tmp_path / "GLOSSARY.md").read_text() == (
        (ASSETS / "GLOSSARY.md.j2").read_text().replace("{{ project_name }}", tmp_path.name)
        if force
        else "# Custom\n"
    )
    assert old.read_text() == "legacy graph"
    for name in ("pyproject.toml", "uv.lock", ".pre-commit-config.yaml"):
        assert (tmp_path / name).read_text() == "owned by the user"


def test_dry_run_and_legacy_notice(tmp_path, capsys):
    old = tmp_path / ".github/skills/automagic-plan/SKILL.md"
    old.parent.mkdir(parents=True)
    old.write_text("user-owned old skill")
    assert main(["init", "--path", str(tmp_path), "--dry-run"]) == 0
    assert not (tmp_path / "AGENTS.md").exists()
    assert old.read_text() == "user-owned old skill"
    assert "not upgraded" in capsys.readouterr().out
    actions = scaffold(tmp_path, "Demo", ASSETS, force=True, dry_run=True)
    assert all(action.status.startswith("would-") for action in actions)


def test_context_preserves_surrounding_text_and_indexes_intent(tmp_path):
    assert main(["init", "--path", str(tmp_path)]) == 0
    path = tmp_path / "docs/CONTEXT.md"
    text = f"custom\r\n{guardrails.START}\r\nold index\r\n{guardrails.END}\r\nkeep this\r\n"
    path.write_bytes(text.encode())
    (tmp_path / "INTENT.md").write_text("# Intent\n")
    (tmp_path / "docs/work").mkdir()
    (tmp_path / "docs/work/_index.md").write_text("# Execution\n")
    assert main(["context", "--path", str(tmp_path)]) == 0
    result = path.read_bytes()
    assert result.startswith(b"custom\r\n")
    assert result.endswith(b"\r\nkeep this\r\n")
    assert b"[INTENT.md](../INTENT.md)" in result
    assert b"[Execution work items](work/_index.md)" in result
    assert not guardrails.update_index(tmp_path)
    before = path.read_bytes()
    assert main(["context", "--path", str(tmp_path), "--check"]) == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "text",
    [
        "custom context",
        f"{guardrails.END}\n{guardrails.START}",
        f"{guardrails.START}{guardrails.START}{guardrails.END}",
    ],
)
def test_bad_markers_fail_without_overwriting_or_initializing(tmp_path, text, capsys):
    path = tmp_path / "docs/CONTEXT.md"
    path.parent.mkdir()
    path.write_text(text)
    assert main(["init", "--path", str(tmp_path)]) == 1
    assert main(["context", "--path", str(tmp_path)]) == 1
    assert path.read_text() == text
    assert not (tmp_path / "AGENTS.md").exists()
    assert "markers" in capsys.readouterr().err


@pytest.mark.parametrize(
    "size,expected", [(8000, []), (8001, ["INTENT.md: 8001 bytes exceeds limit 8000"])]
)
def test_exact_context_size_boundary(tmp_path, size, expected):
    (tmp_path / "INTENT.md").write_bytes(b"x" * size)
    assert guardrails.check(tmp_path, Config()) == expected


def test_readonly_validation_detects_unindexed_intent(tmp_path, capsys):
    assert main(["init", "--path", str(tmp_path)]) == 0
    (tmp_path / "INTENT.md").write_text("# Intent\n")
    assert main(["context", "--path", str(tmp_path), "--check"]) == 1
    assert "INTENT.md: not listed" in capsys.readouterr().err
    assert main(["context", "--path", str(tmp_path)]) == 0


def test_links_indexing_and_fenced_examples(tmp_path, capsys):
    assert main(["init", "--path", str(tmp_path)]) == 0
    doc = tmp_path / "docs/extra.md"
    doc.write_text(
        "# Extra\n[bad](missing.md)\n`[ignore](other.md)`\n```\n[ignore](more.md)\n```\n"
    )
    problems = guardrails.check(tmp_path, Config())
    assert any("broken link missing.md" in issue for issue in problems)
    assert any("not listed" in issue for issue in problems)
    assert len(problems) == 2
    assert main(["context", "--path", str(tmp_path)]) == 1
    assert "missing.md" in capsys.readouterr().err
    doc.write_text("[outside](../../outside.md)\n")
    (tmp_path.parent / "outside.md").write_text("outside")
    assert any("escapes repository" in issue for issue in guardrails.check(tmp_path, Config()))
    doc.write_text("[web](https://example.invalid)\n[anchor](#here)\n")
    assert main(["context", "--path", str(tmp_path)]) == 0


def test_scaffold_asset_and_path_errors(tmp_path):
    with pytest.raises(ValueError, match="Missing bundled"):
        scaffold(tmp_path, "Demo", tmp_path / "missing")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "code.py").write_text("pass")
    with pytest.raises(ValueError, match="must be Markdown"):
        scaffold(tmp_path / "target", "Demo", assets)
    (assets / "code.py").unlink()
    (assets / "nested").mkdir()
    (assets / "nested/file.md").write_text("text")
    target = tmp_path / "target"
    target.mkdir()
    (target / "nested").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(ValueError, match="outside project"):
        scaffold(target, "Demo", assets)


def test_readonly_context_without_index(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/headingless.md").write_text("a body")
    assert "headingless" in guardrails.build_index(tmp_path)
    assert main(["context", "--path", str(tmp_path), "--check"]) == 0
    assert not (tmp_path / "docs/CONTEXT.md").exists()
