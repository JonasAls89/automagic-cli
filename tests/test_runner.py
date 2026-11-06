import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from automagic import sizing, worktrees
from automagic.cli import app
from automagic.config import Config
from automagic.merkle import Graph, Node
from automagic.runner import (
    ItemRun,
    RunError,
    RunState,
    agent_command,
    git,
    item_hash,
    prepare_run,
    prepare_worktree,
    waves,
)
from automagic.sizing import WorkItem

runner = CliRunner()

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def make_repo(tmp_path: Path, nodes: list[Node]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([*GIT, "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "README.md").write_text("base\n")
    subprocess.run([*GIT, "add", "."], cwd=repo, check=True)
    subprocess.run([*GIT, "commit", "-q", "-m", "init"], cwd=repo, check=True)
    g = Graph(goal="Demo")
    for n in nodes:
        g.add(n)
    for nid in g.nodes:
        g.validate(nid)
    g.save(repo / ".automagic" / "plan" / "graph.json")
    return repo


def configure(repo: Path, runner_cmd: str, verify: str = "") -> None:
    cfg = Config(runner_command=runner_cmd, verify_command=verify, max_concurrency=2)
    (repo / ".automagic" / "config.toml").write_text(cfg.to_toml())


# Each work item has est_lines 300 so no two share a work item under the 400 line budget.
CHAIN = [
    Node(id="a", title="A", content="do a", est_lines=300),
    Node(id="b", title="B", content="do b", depends_on=["a"], est_lines=300),
    Node(id="c", title="C", content="do c", depends_on=["a"], est_lines=300),
    Node(id="d", title="D", content="do d", depends_on=["b", "c"], est_lines=300),
]
# Writes a file named after the branch's work item so dependents can see upstream files.
WRITE = 'sh -c \'echo x > "$(basename "$PWD" | sed s/.*-wi-/wi-/).txt"\' {prompt}'


def test_waves_group_by_dependency():
    items = [
        WorkItem(id="wi-01"),
        WorkItem(id="wi-02", depends_on=["wi-01"]),
        WorkItem(id="wi-03", depends_on=["wi-01"]),
        WorkItem(id="wi-04", depends_on=["wi-02", "wi-03"]),
    ]
    assert waves(items) == [["wi-01"], ["wi-02", "wi-03"], ["wi-04"]]
    with pytest.raises(RunError):
        waves([WorkItem(id="x", depends_on=["y"]), WorkItem(id="y", depends_on=["x"])])


@pytest.mark.parametrize(
    ("title", "content", "expected"),
    [
        (
            "Add session export",
            "Export JSON data and document the supported formats.",
            "feature/wi-01_session-export",
        ),
        (
            "Refactor worktree branch naming",
            "Separate branch naming from worktree paths.",
            "refactor/wi-01_worktree-branch-naming",
        ),
        (
            "Update dependency pins",
            "Keep development dependencies current.",
            "chore/wi-01_dependency-pins",
        ),
    ],
)
def test_propose_names_branches_by_category_and_primary_intent(tmp_path, title, content, expected):
    graph = Graph(nodes={"a": Node(id="a", title=title, content=content)})
    item = WorkItem(id="wi-01", node_ids=["a"])

    proposal = worktrees.propose(graph, [item], tmp_path)[0]

    assert proposal.branch == expected


def test_proposed_intent_slug_is_bounded_and_has_no_trailing_separator(tmp_path):
    graph = Graph(
        nodes={
            "a": Node(
                id="a",
                title="Implement a very long concise intent for this work item",
                content="A long intent.",
            )
        }
    )
    item = WorkItem(id="wi-01", node_ids=["a"])

    proposal = worktrees.propose(graph, [item], tmp_path)[0]
    intent = proposal.branch.split("_", maxsplit=1)[1]

    assert len(intent) <= 32
    assert not intent.endswith("-")


@pytest.mark.parametrize("requested", [["wi-04"], ["d"], ["b", "wi-04", "d"]])
def test_run_preparation_skips_completed_prerequisites_without_mutating_inputs(tmp_path, requested):
    graph = Graph(goal="Demo", nodes={node.id: node.model_copy(deep=True) for node in CHAIN})
    items = sizing.partition(graph, tmp_path, Config())
    state = RunState(
        root_hash=graph.root_hash(),
        items={"wi-01": ItemRun(status="done", hash=item_hash(graph, items[0]))},
    )
    path = tmp_path / "run.json"
    state.save(path)
    original_state = path.read_bytes()
    original_items = [item.depends_on.copy() for item in items]

    prepared = prepare_run(graph, items, path, only=requested)

    assert prepared.selected == {"wi-02", "wi-03", "wi-04"}
    assert prepared.waves == [["wi-02", "wi-03"], ["wi-04"]]
    assert [item.depends_on for item in items] == original_items
    assert path.read_bytes() == original_state


@pytest.mark.parametrize(
    ("only", "restart", "changed", "expected", "preview"),
    [
        (["wi-02"], False, False, set(), []),
        (["b"], False, True, {"wi-02"}, [["wi-02"]]),
        (["b"], True, False, {"wi-01", "wi-02"}, [["wi-01"], ["wi-02"]]),
        ([], False, False, set(), []),
        (None, False, False, None, [["wi-01"], ["wi-02", "wi-03"], ["wi-04"]]),
    ],
)
def test_run_preparation_preserves_completed_stale_restart_and_full_preview_rules(
    tmp_path, only, restart, changed, expected, preview
):
    graph = Graph(goal="Demo", nodes={node.id: node.model_copy(deep=True) for node in CHAIN})
    items = sizing.partition(graph, tmp_path, Config())
    state = RunState(
        root_hash=graph.root_hash(),
        items={item.id: ItemRun(status="done", hash=item_hash(graph, item)) for item in items[:2]},
    )
    path = tmp_path / "run.json"
    state.save(path)
    if changed:
        graph.nodes["b"].content = "Use different storage"

    prepared = prepare_run(graph, items, path, only=only, restart=restart)

    assert prepared.selected == expected
    assert prepared.waves == preview


@pytest.mark.parametrize(("only", "restart"), [(None, False), (["b"], True)])
def test_run_preparation_does_not_read_saved_state_when_unused(tmp_path, only, restart):
    graph = Graph(goal="Demo", nodes={node.id: node.model_copy(deep=True) for node in CHAIN[:2]})
    items = sizing.partition(graph, tmp_path, Config())
    path = tmp_path / "run.json"
    path.write_text("invalid saved state")

    prepared = prepare_run(graph, items, path, only=only, restart=restart)

    assert prepared.waves == [["wi-01"], ["wi-02"]]
    with pytest.raises(ValueError):
        prepare_run(graph, items, path, only=["b"])
    assert path.read_text() == "invalid saved state"


def test_run_preparation_reports_unknown_ids_and_dependency_cycles_without_writes(tmp_path):
    graph = Graph()
    path = tmp_path / "run.json"
    with pytest.raises(RunError, match="unknown work item or planning topic 'missing'"):
        prepare_run(graph, [WorkItem(id="a")], path, only=["missing"])
    with pytest.raises(RunError, match="dependencies contain a cycle"):
        prepare_run(
            graph,
            [WorkItem(id="a", depends_on=["b"]), WorkItem(id="b", depends_on=["a"])],
            path,
        )
    assert not path.exists()


def test_agent_command_substitutes_prompt_and_requires_placeholder():
    cmd = agent_command(Config(), "do it")
    assert cmd[:3] == ["copilot", "-p", "do it"]
    assert "--deny-tool=shell(git commit)" in cmd
    assert "--deny-tool=shell(git push)" in cmd
    with pytest.raises(RunError):
        agent_command(Config(runner_command="copilot -p"))


def test_config_roundtrip_with_quotes(tmp_path):
    cfg = Config()
    (tmp_path / ".automagic").mkdir()
    (tmp_path / ".automagic" / "config.toml").write_text(cfg.to_toml())
    from automagic.config import load_config

    assert load_config(tmp_path) == cfg


def test_dry_run_creates_nothing(tmp_path):
    repo = make_repo(tmp_path, CHAIN)
    out = runner.invoke(app, ["run", "--path", str(repo), "--dry-run"])
    assert out.exit_code == 0, out.output
    assert "wave 1: wi-01" in out.output and "wave 2: wi-02, wi-03" in out.output
    assert not (tmp_path / "repo.worktrees").exists()


@pytest.mark.parametrize("requested", [["--only", " b,wi-03 "], ["--only", "b", "--only", "c"]])
def test_selected_run_preview_parses_ids_and_skips_completed_prerequisites(tmp_path, requested):
    repo = make_repo(tmp_path, CHAIN)
    graph = Graph.load(repo / ".automagic" / "plan" / "graph.json")
    path = repo / ".automagic" / "plan" / "run.json"
    RunState(root_hash=graph.root_hash(), items={"wi-01": ItemRun(status="done")}).save(path)
    original = path.read_bytes()

    result = runner.invoke(app, ["run", "--path", str(repo), "--dry-run", *requested])

    assert result.exit_code == 0, result.output
    assert "wave 1: wi-02, wi-03" in result.output
    assert "wi-01" not in result.output and "wi-04" not in result.output
    assert path.read_bytes() == original
    assert not (tmp_path / "repo.worktrees").exists()


def test_run_rereads_execution_state_after_confirmation(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, CHAIN[:2])
    configure(repo, WRITE)
    graph = Graph.load(repo / ".automagic" / "plan" / "graph.json")
    path = repo / ".automagic" / "plan" / "run.json"
    state = RunState(
        root_hash=graph.root_hash(),
        items={"wi-01": ItemRun(status="done"), "wi-02": ItemRun()},
    )
    state.save(path)

    def confirm(prompt):
        state.items["wi-02"].status = "done"
        state.save(path)
        return True

    monkeypatch.setattr("automagic.cli.typer.confirm", confirm)
    result = runner.invoke(app, ["run", "--path", str(repo), "--only", "b"])

    assert result.exit_code == 0, result.output
    assert "wave 1: wi-02" in result.output and "wi-02 [done]" in result.output
    assert "wi-02: running" not in result.output
    assert not (tmp_path / "repo.worktrees").exists()


def test_selection_errors_precede_confirmation_but_stale_plan_errors_follow_it(tmp_path):
    repo = make_repo(tmp_path, CHAIN[:1])
    path = repo / ".automagic" / "plan" / "run.json"
    RunState(root_hash="old", items={"wi-01": ItemRun(status="done")}).save(path)

    unknown = runner.invoke(app, ["run", "--path", str(repo), "--only", "unknown"], input="n\n")
    assert unknown.exit_code == 1 and "unknown work item" in unknown.output
    assert "Launch agents unattended" not in unknown.output
    declined = runner.invoke(app, ["run", "--path", str(repo)], input="n\n")
    assert declined.exit_code == 0 and "plan changed" not in declined.output
    confirmed = runner.invoke(app, ["run", "--path", str(repo)], input="y\n")
    assert confirmed.exit_code == 1 and "plan changed since the last run" in confirmed.output
    assert not (tmp_path / "repo.worktrees").exists()


def test_run_executes_waves_and_branches_dependents_off_prerequisites(tmp_path):
    repo = make_repo(tmp_path, CHAIN)
    configure(repo, WRITE)
    run_all(repo)
    wt = tmp_path / "repo.worktrees"
    last = next(p for p in wt.iterdir() if p.name.endswith("wi-04"))
    # wi-04 depends on both branches, so it contains both upstream agents' files
    for name in ("wi-01.txt", "wi-02.txt", "wi-03.txt", "wi-04.txt"):
        assert (last / name).exists(), name
    log = subprocess.run(
        ["git", "log", "--format=%s", "feature/wi-04_d"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "manual wi-04" in log and "manual wi-01" in log
    # the brief is never committed and main is untouched
    assert (
        ".automagic/BRIEF.md"
        not in subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "feature/wi-04_d"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert not (repo / "wi-01.txt").exists()
    # a second run skips completed items
    again = runner.invoke(app, ["run", "--path", str(repo), "--yes"])
    assert again.exit_code == 0 and "running" not in again.output


def test_failure_blocks_dependents_but_not_siblings(tmp_path):
    repo = make_repo(tmp_path, CHAIN)
    fail_b = 'sh -c \'case "$PWD" in *wi-02) exit 3;; esac; echo "$PWD" > out.txt\' {prompt}'
    configure(repo, fail_b)
    first = runner.invoke(app, ["run", "--path", str(repo), "--yes", "--only", "a"])
    assert first.exit_code == 0, first.output
    commit_file(Path(saved_state(repo).items["wi-01"].path), "out.txt", "prerequisite\n")
    out = runner.invoke(app, ["run", "--path", str(repo), "--yes"])
    assert out.exit_code == 1
    assert "wi-02 [failed]" in out.output
    assert "wi-03 [done]" in out.output
    assert "wi-04 [blocked]" in out.output


def test_verify_failure_marks_item_failed(tmp_path):
    repo = make_repo(tmp_path, CHAIN[:1])
    configure(repo, WRITE, verify="false")
    out = runner.invoke(app, ["run", "--path", str(repo), "--yes"])
    assert out.exit_code == 1 and "verification failed" in out.output


def test_stale_plan_requires_restart(tmp_path):
    repo = make_repo(tmp_path, CHAIN[:1])
    configure(repo, WRITE)
    assert runner.invoke(app, ["run", "--path", str(repo), "--yes"]).exit_code == 0
    g = Graph.load(repo / ".automagic" / "plan" / "graph.json")
    g.nodes["a"].content = "changed"
    g.validate("a")
    g.save(repo / ".automagic" / "plan" / "graph.json")
    out = runner.invoke(app, ["run", "--path", str(repo), "--yes"])
    assert out.exit_code == 1 and "--restart" in out.output
    assert "stale work items: wi-01" in out.output and "--update" in out.output


def amend(repo: Path, node: str, content: str) -> Graph:
    path = repo / ".automagic" / "plan" / "graph.json"
    g = Graph.load(path)
    g.nodes[node].content = content
    for nid in g.nodes:
        g.validate(nid)
    g.save(path)
    return g


def saved_state(repo: Path) -> RunState:
    return RunState.load(repo / ".automagic" / "plan" / "run.json")


def run_all(repo: Path) -> None:
    graph = Graph.load(repo / ".automagic/plan/graph.json")
    for _ in range(len(graph.nodes) + 1):
        out = runner.invoke(app, ["run", "--path", str(repo), "--yes"])
        assert out.exit_code == 0, out.output
        state = saved_state(repo)
        for item_id, run in state.items.items():
            if run.status != "done":
                continue
            path = Path(run.path)
            assert (
                git(["add", "-A", "--", ".", ":(exclude).automagic/BRIEF.md"], path).returncode == 0
            )
            code = git(["diff", "--cached", "--quiet"], path).returncode
            assert code in (0, 1)
            if code == 1:
                cmd = [*GIT[1:], "commit", "-q", "-m", f"manual {item_id}"]
                assert git(cmd, path).returncode == 0
        if all(run.status == "done" for run in state.items.values()):
            return
    pytest.fail("Work items did not resume after manual commits")


def test_update_redoes_only_work_items_whose_nodes_changed(tmp_path):
    repo = make_repo(tmp_path, CHAIN)
    configure(repo, WRITE)
    run_all(repo)
    g = amend(repo, "b", "do b differently")
    blocked = runner.invoke(app, ["run", "--path", str(repo), "--yes"])
    assert blocked.exit_code == 1 and "stale work items: wi-02, wi-04" in blocked.output
    out = runner.invoke(app, ["run", "--path", str(repo), "--yes", "--update"])
    assert out.exit_code == 0, out.output
    assert "wi-02: running" in out.output and "wi-04: running" in out.output
    assert "wi-01: running" not in out.output and "wi-03: running" not in out.output
    state = saved_state(repo)
    assert state.root_hash == g.root_hash()
    assert {i.status for i in state.items.values()} == {"done"}


def test_restart_keeps_the_base_of_existing_worktrees(tmp_path):
    repo = make_repo(tmp_path, CHAIN[:1])
    configure(repo, WRITE)
    assert runner.invoke(app, ["run", "--path", str(repo), "--yes"]).exit_code == 0
    before = saved_state(repo).items["wi-01"].base
    assert before
    assert runner.invoke(app, ["run", "--path", str(repo), "--yes", "--restart"]).exit_code == 0
    assert saved_state(repo).items["wi-01"].base == before


def test_agent_that_changes_nothing_fails_the_item(tmp_path):
    repo = make_repo(tmp_path, CHAIN[:1])
    configure(repo, "sh -c true {prompt}")
    out = runner.invoke(app, ["run", "--path", str(repo), "--yes"])
    assert out.exit_code == 1 and "wi-01 [failed]" in out.output
    assert "agent made no changes" in out.output


def commit_file(path: Path, name: str, text: str) -> None:
    (path / name).write_text(text)
    ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
    assert git(["add", name], path).returncode == 0
    assert git([*ident, "commit", "-q", "-m", f"add {name}"], path).returncode == 0


def worktree_pair(tmp_path: Path):
    repo = make_repo(tmp_path, CHAIN[:2])
    graph = Graph.load(repo / ".automagic" / "plan" / "graph.json")
    items = sizing.partition(graph, repo, Config())
    props = {p.item.id: p for p in worktrees.propose(graph, items, repo)}
    prepare_worktree(graph, props["wi-01"], items, props, repo)
    # created the way `automagic worktrees --create` used to: off HEAD, prerequisites ignored
    late = props["wi-02"]
    assert git(["worktree", "add", "-b", late.branch, str(late.path), "main"], repo).returncode == 0
    return graph, items, props, repo


def test_prepare_worktree_merges_prerequisites_into_an_existing_worktree(tmp_path):
    graph, items, props, repo = worktree_pair(tmp_path)
    commit_file(props["wi-01"].path, "one.txt", "1\n")
    prepare_worktree(graph, props["wi-02"], items, props, repo)
    assert (props["wi-02"].path / "one.txt").exists()
    prepare_worktree(graph, props["wi-02"], items, props, repo)  # repeating is harmless


def test_prepare_worktree_keeps_failing_while_a_prerequisite_conflicts(tmp_path):
    graph, items, props, repo = worktree_pair(tmp_path)
    commit_file(props["wi-01"].path, "same.txt", "1\n")
    commit_file(props["wi-02"].path, "same.txt", "2\n")
    for _ in range(2):
        with pytest.raises(RunError, match="conflicts"):
            prepare_worktree(graph, props["wi-02"], items, props, repo)
    status = git(["status", "--porcelain", "--", ".", ":(exclude).automagic"], props["wi-02"].path)
    assert status.stdout.strip() == ""


def test_run_after_worktrees_create_still_stacks_prerequisites(tmp_path):
    repo = make_repo(tmp_path, CHAIN)
    configure(repo, WRITE)
    created = runner.invoke(app, ["worktrees", "--path", str(repo), "--create", "--yes"])
    assert created.exit_code == 0, created.output
    run_all(repo)
    last = next(p for p in (tmp_path / "repo.worktrees").iterdir() if p.name.endswith("wi-04"))
    for name in ("wi-01.txt", "wi-02.txt", "wi-03.txt", "wi-04.txt"):
        assert (last / name).exists(), name


def test_run_leaves_work_uncommitted_and_unstaged(tmp_path):
    repo = make_repo(tmp_path, CHAIN[:1])
    configure(repo, WRITE)
    base = git(["rev-parse", "HEAD"], repo).stdout

    out = runner.invoke(app, ["run", "--path", str(repo), "--yes"])

    assert out.exit_code == 0, out.output
    run = saved_state(repo).items["wi-01"]
    path = Path(run.path)
    assert run.status == "done"
    assert git(["rev-parse", "HEAD"], path).stdout == base
    assert git(["diff", "--cached", "--quiet"], path).returncode == 0
    assert "?? wi-01.txt" in git(["status", "--porcelain"], path).stdout


def test_dependents_wait_for_manual_prerequisite_commits_and_resume(tmp_path):
    repo = make_repo(tmp_path, [*CHAIN[:2], Node(id="c", title="C", content="x", est_lines=300)])
    configure(repo, WRITE)

    paused = runner.invoke(app, ["run", "--path", str(repo), "--yes"])

    assert paused.exit_code == 0, paused.output
    state = saved_state(repo)
    assert state.items["wi-01"].status == "done"
    assert state.items["wi-02"].status == "pending"
    assert state.items["wi-03"].status == "done"
    assert "manual commit" in state.items["wi-02"].detail
    assert not Path(state.items["wi-02"].path).exists()

    commit_file(Path(state.items["wi-01"].path), "wi-01.txt", "x\n")
    resumed = runner.invoke(app, ["run", "--path", str(repo), "--yes", "--only", "b"])

    assert resumed.exit_code == 0, resumed.output
    assert "wi-01: running" not in resumed.output
    state = saved_state(repo)
    path = Path(state.items["wi-02"].path)
    assert state.items["wi-02"].status == "done"
    assert (path / "wi-01.txt").exists() and (path / "wi-02.txt").exists()
    assert git(["rev-parse", "HEAD"], path).stdout.strip() == state.items["wi-02"].base


def test_verification_retries_formatter_changes_without_committing(tmp_path):
    repo = make_repo(tmp_path, CHAIN[:1])
    verify = "sh -c 'grep -q fixed wi-01.txt || { echo fixed > wi-01.txt; exit 1; }'"
    configure(repo, WRITE, verify)
    base = git(["rev-parse", "HEAD"], repo).stdout

    out = runner.invoke(app, ["run", "--path", str(repo), "--yes"])

    assert out.exit_code == 0, out.output
    run = saved_state(repo).items["wi-01"]
    path = Path(run.path)
    assert (path / "wi-01.txt").read_text() == "fixed\n"
    assert git(["rev-parse", "HEAD"], path).stdout == base
    assert git(["diff", "--cached", "--quiet"], path).returncode == 0
    assert Path(run.log).read_text().count("$ sh -c 'grep") == 2


def test_run_preserves_staging_left_by_a_custom_runner(tmp_path):
    repo = make_repo(tmp_path, CHAIN[:1])
    configure(
        repo, "sh -c 'echo staged > staged.txt; git add staged.txt; echo new > new.txt' {prompt}"
    )
    base = git(["rev-parse", "HEAD"], repo).stdout

    out = runner.invoke(app, ["run", "--path", str(repo), "--yes"])

    assert out.exit_code == 0, out.output
    path = Path(saved_state(repo).items["wi-01"].path)
    assert git(["rev-parse", "HEAD"], path).stdout == base
    assert git(["diff", "--cached", "--name-only"], path).stdout == "staged.txt\n"
    assert "?? new.txt" in git(["status", "--porcelain"], path).stdout


def test_discarding_work_does_not_unlock_dependents_without_a_commit(tmp_path):
    repo = make_repo(tmp_path, CHAIN[:2])
    configure(repo, WRITE)
    first = runner.invoke(app, ["run", "--path", str(repo), "--yes"])
    assert first.exit_code == 0, first.output
    state = saved_state(repo)
    (Path(state.items["wi-01"].path) / "wi-01.txt").unlink()

    resumed = runner.invoke(app, ["run", "--path", str(repo), "--yes", "--only", "b"])

    assert resumed.exit_code == 0, resumed.output
    assert saved_state(repo).items["wi-02"].status == "pending"
    assert "manual commits" in resumed.output
    assert not Path(state.items["wi-02"].path).exists()
