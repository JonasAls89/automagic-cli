import json
import subprocess
from pathlib import Path

import pytest

from automagic import runner, worktrees
from automagic.config import Config
from automagic.execution import approve, git, load_plan
from scripts.helper import main


def launch(root, worker, *extra):
    return main(["run", "--path", str(root), "--yes", "--runner-command", worker, *extra])


def saved(root):
    plan = load_plan(root)
    return runner.saved_state(plan, plan.runtime_path(root, "run.json"))


def ready(root):
    plan = load_plan(root)
    approve(root, plan)
    return plan


def test_waves_selection_preview_and_unknown_ids(repo_factory, worker, capsys):
    root = repo_factory([[], ["wi-01"], ["wi-01"], ["wi-02", "wi-03"]])
    plan = ready(root)
    before = {
        str(path): path.read_bytes() for path in (root / ".automagic").rglob("*") if path.is_file()
    }
    assert launch(root, worker, "--dry-run", "--only", "wi-04") == 0
    output = capsys.readouterr().out
    assert "wave 1: wi-01" in output
    assert "wave 2: wi-02, wi-03" in output
    assert "wave 3: wi-04" in output
    assert not worktrees.propose(plan, root)[0].path.exists()
    assert before == {
        str(path): path.read_bytes() for path in (root / ".automagic").rglob("*") if path.is_file()
    }
    assert launch(root, worker, "--only", "missing") == 1
    assert "unknown work item" in capsys.readouterr().err
    item = plan.items[0].model_copy(update={"depends_on": ["wi-01"]})
    with pytest.raises(runner.RunError, match="cycle"):
        runner.waves([item])


def test_numstat_handles_git_no_index_path_pairs():
    output = "\0".join(["1\t2\ttracked.txt", "3\t0\t", "/dev/null", "new file.txt", ""])
    assert runner.changed_lines(output) == 6
    with pytest.raises(runner.RunError, match="binary"):
        runner.changed_lines("-\t-\tbinary.dat\0")
    with pytest.raises(runner.RunError, match="Invalid"):
        runner.changed_lines("not numstat\0")
    with pytest.raises(runner.RunError, match="Incomplete"):
        runner.changed_lines("1\t0\t\0")


@pytest.mark.parametrize(
    "title,prefix",
    [
        ("Add session export", "feature/wi-01_session-export-"),
        ("Refactor worktree branch naming", "refactor/wi-01_worktree-branch-naming-"),
        ("Update dependency pins", "chore/wi-01_dependency-pins-"),
    ],
)
def test_categorized_intent_branches_keep_snapshot_identity(repo_factory, title, prefix):
    root = repo_factory()
    path = root / "docs/work/wi-01.md"
    path.write_text(path.read_text().replace("Add item wi-01", title))
    plan = load_plan(root)
    prop = worktrees.propose(plan, root)[0]
    assert prop.branch == prefix + plan.digest[:12]
    assert prop.path.name.endswith(plan.digest[:12] + "-wi-01")


def test_binary_changes_fail_explicitly(repo_factory, command):
    root = repo_factory(verify="true")
    ready(root)
    binary = command("from pathlib import Path; Path('wi-01.txt').write_bytes(bytes([0]) * 32)")
    assert launch(root, binary + " {prompt}") == 1
    assert "binary changes" in saved(root).items["wi-01"].detail


def test_execution_never_imports_or_rewrites_legacy_files(repo_factory, worker):
    root = repo_factory()
    legacy = {
        ".automagic/config.toml": b"invalid legacy configuration",
        ".automagic/plan/graph.json": b"invalid legacy graph",
        ".automagic/plan/run.json": b"invalid legacy run state",
        "docs/plan/old.md": b"[historical](missing-source.md)",
    }
    for relative, content in legacy.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    ready(root)
    assert launch(root, worker) == 0
    assert all((root / relative).read_bytes() == content for relative, content in legacy.items())


def test_changed_worktree_inputs_block_the_worker_before_launch(repo_factory, worker):
    root = repo_factory()
    plan = ready(root)
    props = worktrees.propose(plan, root)
    worktrees.create(plan, props, root)
    (props[0].path / "INTENT.md").write_text("# Different intent\n")
    assert launch(root, worker) == 1
    assert not (props[0].path / "wi-01.txt").exists()
    assert "Input changed" in saved(root).items["wi-01"].detail


def test_precreated_worktrees_resume_manual_commit_waves(repo_factory, worker, commit, capsys):
    root = repo_factory([[], ["wi-01"], ["wi-01"], ["wi-02", "wi-03"]])
    ready(root)
    assert main(["worktrees", "--path", str(root), "--create", "--yes"]) == 0
    for _ in range(4):
        assert launch(root, worker) == 0
        state = saved(root)
        for item_id, run in state.items.items():
            if run.status == runner.DONE:
                path = Path(run.path)
                if git(["status", "--porcelain", "--", f"{item_id}.txt"], path).stdout:
                    commit(path, f"{item_id}.txt")
        if all(run.status == runner.DONE for run in state.items.values()):
            break
    assert {run.status for run in state.items.values()} == {runner.DONE}
    final = Path(state.items["wi-04"].path)
    assert all((final / f"wi-{i:02}.txt").exists() for i in range(1, 5))
    assert not (root / "wi-01.txt").exists()
    assert ".automagic/BRIEF.md" not in git(["ls-tree", "-r", "--name-only", "HEAD"], final).stdout
    assert launch(root, worker) == 0
    assert "nothing to run" in capsys.readouterr().out


def test_manual_commit_pause_preserves_staging_and_base(repo_factory, worker, commit, capsys):
    root = repo_factory([[], ["wi-01"], []])
    ready(root)
    base = git(["rev-parse", "HEAD"], root).stdout.strip()
    assert launch(root, worker) == 0
    state = saved(root)
    first = Path(state.items["wi-01"].path)
    assert state.items["wi-01"].status == runner.DONE
    assert state.items["wi-02"].status == runner.PENDING
    assert state.items["wi-03"].status == runner.DONE
    assert "manual commits" in state.items["wi-02"].detail
    assert git(["rev-parse", "HEAD"], first).stdout.strip() == base
    assert git(["diff", "--cached", "--quiet"], first).returncode == 0
    (first / "wi-01.txt").unlink()
    assert launch(root, worker, "--only", "wi-02") == 0
    assert saved(root).items["wi-02"].status == runner.PENDING
    (first / "wi-01.txt").write_text("x\n")
    commit(first, "wi-01.txt")
    capsys.readouterr()
    assert launch(root, worker, "--only", "wi-02") == 0
    state = saved(root)
    second = Path(state.items["wi-02"].path)
    assert state.items["wi-02"].status == runner.DONE
    assert git(["rev-parse", "HEAD"], second).stdout.strip() == state.items["wi-02"].base
    assert (second / "wi-01.txt").exists()
    assert "wi-01 [running]" not in capsys.readouterr().out


def test_failure_blocks_dependents_but_not_siblings(repo_factory, worker, command, commit, capsys):
    root = repo_factory([[], ["wi-01"], ["wi-01"], ["wi-02", "wi-03"]])
    ready(root)
    assert launch(root, worker, "--only", "wi-01") == 0
    commit(Path(saved(root).items["wi-01"].path), "wi-01.txt")
    failing = (
        command(
            "from pathlib import Path; "
            "item = Path('.automagic/BRIEF.md').read_text().splitlines()[0].split()[-1]; "
            "assert item != 'wi-02'; Path(item + '.txt').write_text('x\\n')"
        )
        + " {prompt}"
    )
    assert launch(root, failing) == 1
    state = saved(root)
    assert state.items["wi-02"].status == runner.FAILED
    assert state.items["wi-03"].status == runner.DONE
    assert state.items["wi-04"].status == runner.BLOCKED
    assert "log:" in capsys.readouterr().out


@pytest.mark.parametrize(
    "verify,message", [("false", "verification failed"), ("true", "agent made no changes")]
)
def test_failed_verification_and_no_change_are_not_done(
    repo_factory, worker, command, verify, message
):
    root = repo_factory(verify=verify)
    ready(root)
    agent = worker if verify == "false" else command("pass") + " {prompt}"
    assert launch(root, agent) == 1
    assert message in saved(root).items["wi-01"].detail


def test_verification_fix_retry_does_not_commit(repo_factory, worker, command):
    verify = command(
        "from pathlib import Path; p = Path('wi-01.txt'); fixed = p.read_text() == 'fixed\\n'; "
        "p.write_text('fixed\\n'); raise SystemExit(0 if fixed else 1)"
    )
    root = repo_factory(verify=verify)
    ready(root)
    base = git(["rev-parse", "HEAD"], root).stdout
    assert launch(root, worker) == 0
    run = saved(root).items["wi-01"]
    path = Path(run.path)
    assert (path / "wi-01.txt").read_text() == "fixed\n"
    assert git(["rev-parse", "HEAD"], path).stdout == base
    assert git(["diff", "--cached", "--quiet"], path).returncode == 0
    assert Path(run.log).read_text().count(verify.split(" -c ")[0]) == 3


def test_scope_actual_line_budget_and_staging(repo_factory, command):
    root = repo_factory(verify="true", extra_files=["new.txt"])
    ready(root)
    staged = (
        command(
            "from pathlib import Path; import subprocess; "
            "Path('wi-01.txt').write_text('staged\\n'); "
            "subprocess.run(['git', 'add', 'wi-01.txt'], check=True); "
            "Path('new.txt').write_text('new\\n')"
        )
        + " {prompt}"
    )
    assert launch(root, staged) == 0
    path = Path(saved(root).items["wi-01"].path)
    assert git(["diff", "--cached", "--name-only"], path).stdout == "wi-01.txt\n"
    assert "?? new.txt" in git(["status", "--porcelain"], path).stdout
    root = repo_factory(verify="true")
    ready(root)
    outside = (
        command("from pathlib import Path; Path('unexpected.txt').write_text('x')") + " {prompt}"
    )
    assert launch(root, outside) == 1
    assert "outside approved" in saved(root).items["wi-01"].detail
    root = repo_factory(verify="true")
    ready(root)
    large = (
        command("from pathlib import Path; Path('wi-01.txt').write_text('x\\n' * 401)")
        + " {prompt}"
    )
    assert launch(root, large) == 1
    assert "exceed 400" in saved(root).items["wi-01"].detail


def test_ownership_collision_corruption_and_changed_git_identity(repo_factory, capsys):
    root = repo_factory()
    plan = ready(root)
    prop = worktrees.propose(plan, root)[0]
    prop.path.mkdir(parents=True)
    (prop.path / "user.txt").write_text("keep")
    assert main(["worktrees", "--path", str(root), "--create", "--yes"]) == 1
    assert (prop.path / "user.txt").read_text() == "keep"
    assert "collision" in capsys.readouterr().err
    root = repo_factory()
    plan = ready(root)
    props = worktrees.propose(plan, root)
    worktrees.create(plan, props, root)
    assert worktrees.create(plan, props, root) == []
    assert git(["switch", "--detach"], props[0].path).returncode == 0
    with pytest.raises(RuntimeError, match="identity changed"):
        worktrees.create(plan, props, root)
    data = json.loads(plan.runtime_path(root, "worktrees.json").read_text())
    data["snapshot"] = "wrong"
    plan.runtime_path(root, "worktrees.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="ownership"):
        worktrees.create(plan, props, root)


def test_prerequisite_merge_conflicts_are_repeatable_and_preserve_user_merge(repo_factory, commit):
    root = repo_factory([[], ["wi-01"]])
    plan = ready(root)
    props = {prop.item.id: prop for prop in worktrees.propose(plan, root)}
    worktrees.create(plan, list(props.values()), root)
    commit_path = props["wi-01"].path
    (commit_path / "same.txt").write_text("first")
    commit(commit_path, "same.txt")
    second = props["wi-02"].path
    (second / "same.txt").write_text("second")
    commit(second, "same.txt")
    for _ in range(2):
        with pytest.raises(runner.RunError, match="conflicts"):
            runner.prepare_worktree(plan, props["wi-02"], props, root)
    assert git(["rev-parse", "--verify", "-q", "MERGE_HEAD"], second).returncode != 0
    assert git(["merge", "--no-edit", props["wi-01"].branch], second).returncode != 0
    with pytest.raises(runner.RunError, match="existing merge"):
        runner.prepare_worktree(plan, props["wi-02"], props, root)
    assert git(["rev-parse", "--verify", "-q", "MERGE_HEAD"], second).returncode == 0


def test_bad_state_agent_template_and_logged_process_errors(
    repo_factory, worker, tmp_path, monkeypatch
):
    root = repo_factory()
    plan = ready(root)
    path = plan.runtime_path(root, "run.json")
    runner.RunState(snapshot="0" * 64).save(path)
    assert launch(root, worker, "--dry-run") == 1
    path.write_text("{}")
    assert launch(root, worker) == 1
    with pytest.raises(runner.RunError, match="prompt"):
        runner.agent_command(Config(runner_command="missing"))
    assert "--deny-tool=shell(git commit)" in runner.agent_command(Config())
    log = tmp_path / "log"
    assert runner._run_logged(["/missing/executable"], tmp_path, log, 1) == 127

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("fake", 1)

    monkeypatch.setattr(runner.subprocess, "run", timeout)
    assert runner._run_logged(["fake"], tmp_path, log, 1) == 124
    assert "timed out" in log.read_text()
