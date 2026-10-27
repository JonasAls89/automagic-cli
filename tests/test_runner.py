import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from automagic import sizing, worktrees
from automagic.cli import app
from automagic.config import Config
from automagic.merkle import Graph, Node
from automagic.runner import RunError, RunState, agent_command, git, prepare_worktree, waves
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


def test_agent_command_substitutes_prompt_and_requires_placeholder():
    cmd = agent_command(Config(), "do it")
    assert cmd[:3] == ["copilot", "-p", "do it"]
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


def test_run_executes_waves_and_branches_dependents_off_prerequisites(tmp_path):
    repo = make_repo(tmp_path, CHAIN)
    configure(repo, WRITE)
    out = runner.invoke(app, ["run", "--path", str(repo), "--yes"])
    assert out.exit_code == 0, out.output
    wt = tmp_path / "repo.worktrees"
    last = next(p for p in wt.iterdir() if p.name.endswith("wi-04"))
    # wi-04 depends on both branches, so it contains both upstream agents' files
    for name in ("wi-01.txt", "wi-02.txt", "wi-03.txt", "wi-04.txt"):
        assert (last / name).exists(), name
    log = subprocess.run(
        ["git", "log", "--format=%s", "automagic/demo/wi-04"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "wi-04: implement work item" in log and "wi-01: implement work item" in log
    # the brief is never committed and main is untouched
    assert (
        ".automagic/BRIEF.md"
        not in subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "automagic/demo/wi-04"],
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


def test_update_redoes_only_work_items_whose_nodes_changed(tmp_path):
    repo = make_repo(tmp_path, CHAIN)
    configure(repo, WRITE)
    assert runner.invoke(app, ["run", "--path", str(repo), "--yes"]).exit_code == 0
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
    out = runner.invoke(app, ["run", "--path", str(repo), "--yes"])
    assert out.exit_code == 0, out.output
    last = next(p for p in (tmp_path / "repo.worktrees").iterdir() if p.name.endswith("wi-04"))
    for name in ("wi-01.txt", "wi-02.txt", "wi-03.txt", "wi-04.txt"):
        assert (last / name).exists(), name
