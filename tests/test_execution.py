import json

import pytest

from automagic import worktrees
from automagic.config import Config
from automagic.execution import (
    INDEX,
    approve,
    atomic_write,
    load_plan,
    mutation_lock,
    repository_path,
    require_approval,
    require_committed,
    state_path,
)
from scripts.helper import main


def replace(root, name, before, after):
    path = root / name
    path.write_text(path.read_text().replace(before, after))


def test_snapshot_requires_helper_approval_and_committed_inputs(repo_factory, commit):
    root = repo_factory()
    plan = load_plan(root)
    require_committed(root, plan)
    with pytest.raises(ValueError, match="No reviewed"):
        require_approval(root, plan)
    approve(root, plan)
    require_approval(root, plan)
    (root / "unrelated.txt").write_text("uncommitted but unrelated")
    require_approval(root, plan)
    replace(root, "INTENT.md", "reviewed", "different")
    with pytest.raises(ValueError, match="Input changed"):
        require_committed(root, plan)
    changed = load_plan(root)
    with pytest.raises(ValueError, match="inputs changed"):
        require_approval(root, changed)
    with pytest.raises(ValueError, match="Commit all"):
        approve(root, changed)
    commit(root, "INTENT.md")
    new = load_plan(root)
    approve(root, new)
    require_approval(root, new)
    assert plan.digest != new.digest


@pytest.mark.parametrize(
    "name", ["INTENT.md", "README.md", INDEX, "docs/work/wi-01.md", "AGENTS.md"]
)
def test_every_reviewed_input_invalidates_approval(repo_factory, commit, name):
    root = repo_factory()
    if name == "AGENTS.md":
        (root / name).write_text("# Guidance\n")
        commit(root, name)
    plan = load_plan(root)
    approve(root, plan)
    path = root / name
    path.write_text(path.read_text() + "\nChanged reviewed input.\n")
    with pytest.raises(ValueError, match="inputs changed"):
        require_approval(root, load_plan(root))


@pytest.mark.parametrize(
    "before,after,message",
    [
        ("schema_version = 1", "schema_version = 2", "schema_version"),
        ("schema_version = 1", "schema_version = true", "schema_version"),
        ("blockers = []", 'blockers = ["unsettled scope"]', "external grilling"),
        (
            'sources = ["INTENT.md", "README.md"]',
            'sources = ["README.md", "README.md"]',
            "distinct",
        ),
        ('sources = ["INTENT.md", "README.md"]', 'sources = ["missing.md"]', "Missing execution"),
        (
            'sources = ["INTENT.md", "README.md"]',
            'sources = [".automagic/plan/graph.json"]',
            "Legacy/runtime",
        ),
        ('items = ["docs/work/wi-01.md"]', 'items = ["README.md"]', "directly in docs/work"),
        ('goal = "Demo"', 'goal = ""', "goal"),
        ('goal = "Demo"', 'goal = "Demo"\nunknown = 1', "unknown"),
    ],
)
def test_invalid_indexes_fail_before_state_writes(repo_factory, before, after, message):
    root = repo_factory()
    replace(root, INDEX, before, after)
    with pytest.raises(ValueError, match=message):
        load_plan(root)
    assert not (root / ".automagic").exists()


@pytest.mark.parametrize(
    "before,after,message",
    [
        ("depends_on = []", 'depends_on = ["missing"]', "Unknown prerequisite"),
        ("depends_on = []", 'depends_on = ["wi-01"]', "cycle"),
        ("est_lines = 100", "est_lines = 401", "oversized"),
        ("est_lines = 100", "est_lines = 0", "est_lines"),
        ('files = ["wi-01.txt"]', "files = []", "files"),
        ('files = ["wi-01.txt"]', 'files = ["../escape"]', "Invalid repository"),
        ('files = ["wi-01.txt"]', 'files = [".automagic/BRIEF.md"]', "non-runtime"),
        ('files = ["wi-01.txt"]', 'files = ["docs"]', "individual"),
        ('acceptance_criteria = ["wi-01.txt exists"]', 'acceptance_criteria = [" "]', "blank"),
        ('verify_sources = ["README.md"]', "verify_sources = []", "verify_sources"),
        (
            'title = "Add item wi-01"',
            'title = "Add item wi-01"\ncontent = "injected"',
            "outside the metadata",
        ),
    ],
)
def test_invalid_work_items(repo_factory, before, after, message):
    root = repo_factory()
    replace(root, "docs/work/wi-01.md", before, after)
    with pytest.raises(ValueError, match=message):
        load_plan(root)


def test_missing_verification_and_bad_commands(repo_factory):
    root = repo_factory()
    path = root / "docs/work/wi-01.md"
    text = path.read_text()
    line = next(line for line in text.splitlines() if line.startswith("verify_command ="))
    for value, message in [
        ("", "verify_command"),
        ("unknown-check", "not in verify_sources"),
        ("'", "quotation"),
    ]:
        path.write_text(text.replace(line, f"verify_command = {json.dumps(value)}"))
        with pytest.raises(ValueError, match=message):
            load_plan(root)


def test_duplicate_ids_bad_documents_and_missing_intent(repo_factory):
    root = repo_factory([[], []])
    replace(root, "docs/work/wi-02.md", 'id = "wi-02"', 'id = "wi-01"')
    with pytest.raises(ValueError, match="Duplicate work-item"):
        load_plan(root)
    (root / INDEX).write_text("# no metadata")
    with pytest.raises(ValueError, match="exactly one"):
        load_plan(root)
    root = repo_factory()
    (root / "INTENT.md").unlink()
    with pytest.raises(ValueError, match=r"INTENT\.md"):
        load_plan(root)


def test_size_limits_account_for_real_bytes_and_source_context(repo_factory):
    root = repo_factory()
    replace(root, "docs/work/wi-01.md", "est_lines = 100", "est_lines = 400")
    load_plan(root)
    with pytest.raises(ValueError, match="oversized"):
        load_plan(root, Config(max_context_tokens=2000))
    path = root / "INTENT.md"
    path.write_bytes(b"x" * 8000)
    load_plan(root)
    path.write_bytes(b"x" * 8001)
    with pytest.raises(ValueError, match="exceeds 8000"):
        load_plan(root)


@pytest.mark.parametrize("path", ["../escape", "/absolute", ".git/config", "x\\y", "\n", ""])
def test_invalid_paths(tmp_path, path):
    with pytest.raises(ValueError):
        repository_path(tmp_path, path)


def test_symlink_escape_state_lock_and_atomic_cleanup(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "escape").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        repository_path(root, "escape/file")
    with mutation_lock(root), pytest.raises(RuntimeError, match="locked"), mutation_lock(root):
        pytest.fail("acquired an existing lock")
    assert not state_path(root, "mutation.lock").exists()
    destination = root / "write/file"
    atomic_write(destination, "first")
    atomic_write(destination, "second")
    assert destination.read_text() == "second"
    assert list(destination.parent.iterdir()) == [destination]
    (root / ".automagic/execution/link").symlink_to(root / "write")
    with pytest.raises(ValueError, match="symlinks"):
        state_path(root, "link/file")


def test_legacy_graph_and_corrupt_approval_are_explicit(tmp_path, repo_factory, capsys):
    old = tmp_path / ".automagic/plan/graph.json"
    old.parent.mkdir(parents=True)
    old.write_text("legacy")
    with pytest.raises(ValueError, match="Legacy graphs"):
        load_plan(tmp_path)
    assert old.read_text() == "legacy"
    root = repo_factory()
    plan = load_plan(root)
    approve(root, plan)
    state_path(root, "approval.json").write_text("{}")
    assert main(["run", "--path", str(root), "--dry-run"]) == 1
    assert "error:" in capsys.readouterr().err


def test_base_change_and_untracked_documents_require_new_review(repo_factory, commit):
    root = repo_factory()
    plan = load_plan(root)
    (root / "new.txt").write_text("new")
    commit(root, "new.txt")
    with pytest.raises(ValueError, match="Base commit changed"):
        require_committed(root, plan)
    (root / "new-source.md").write_text("source")
    replace(
        root,
        INDEX,
        'sources = ["INTENT.md", "README.md"]',
        'sources = ["INTENT.md", "README.md", "new-source.md"]',
    )
    with pytest.raises(ValueError, match="Commit all"):
        require_committed(root, load_plan(root))


def test_committed_sources_with_spaces_and_brackets_are_literal(repo_factory, commit):
    root = repo_factory()
    relative = "notes [draft].md"
    (root / relative).write_text("# Approved design\n")
    replace(
        root,
        INDEX,
        'sources = ["INTENT.md", "README.md"]',
        'sources = ["INTENT.md", "README.md", "notes [draft].md"]',
    )
    commit(root, relative, INDEX)
    plan = load_plan(root)
    approve(root, plan)
    require_approval(root, plan)
    (root / relative).write_text("# Changed design\n")
    with pytest.raises(ValueError, match="Commit all"):
        require_committed(root, load_plan(root))


def test_sizing_and_briefs_use_only_each_items_verification_evidence(repo_factory):
    root = repo_factory([[] for _ in range(8)])
    for index in range(1, 9):
        name = f"checks-wi-{index:02}.md"
        (root / name).write_text((root / "README.md").read_text().ljust(8000, "x"))
        replace(
            root,
            f"docs/work/wi-{index:02}.md",
            'verify_sources = ["README.md"]',
            f'verify_sources = ["{name}"]',
        )
    plan = load_plan(root, Config(max_context_tokens=8000))
    assert all(f"checks-wi-{index:02}.md" in plan.inputs for index in range(1, 9))
    prop = worktrees.propose(plan, root)[0]
    text = worktrees.brief(plan, prop)
    assert "checks-wi-01.md" in text
    assert "checks-wi-02.md" not in text
    assert "checks-wi-08.md" not in text


def test_approval_rejects_symlink_inputs_with_uncommitted_targets(repo_factory, commit):
    root = repo_factory()
    (root / "design.md").write_text("# Committed design\n")
    (root / "alias.md").symlink_to("design.md")
    replace(
        root,
        INDEX,
        'sources = ["INTENT.md", "README.md"]',
        'sources = ["INTENT.md", "README.md", "alias.md"]',
    )
    commit(root, "design.md", "alias.md", INDEX)
    (root / "design.md").write_text("# Uncommitted design\n")
    with pytest.raises(ValueError, match="regular tracked"):
        approve(root, load_plan(root))


@pytest.mark.parametrize("version", [True, None])
def test_approval_state_requires_an_explicit_integer_version(repo_factory, version):
    root = repo_factory()
    plan = load_plan(root)
    approve(root, plan)
    path = state_path(root, "approval.json")
    data = json.loads(path.read_text())
    if version is None:
        del data["schema_version"]
    else:
        data["schema_version"] = version
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="schema_version"):
        require_approval(root, plan)
