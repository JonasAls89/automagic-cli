import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from automagic.cli import app
from automagic.config import Config
from automagic.merkle import Graph, Node
from automagic.runner import RunError, agent_command, waves
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
    fail_b = "sh -c 'case \"$PWD\" in *wi-02) exit 3;; esac; echo x > out.txt' {prompt}"
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
