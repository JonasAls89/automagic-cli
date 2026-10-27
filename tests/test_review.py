import json
from pathlib import Path

import pytest
from test_runner import WRITE, configure, make_repo
from typer.testing import CliRunner

from automagic import review as rv
from automagic.cli import app
from automagic.config import Config
from automagic.merkle import Graph, Node
from automagic.providers import FakeProvider, ProviderError
from automagic.runner import RunState
from automagic.sizing import WorkItem

runner = CliRunner()


def nodes(**declared: list[str]) -> list[Node]:
    return [
        Node(id="a", title="Add A", content="do a", est_lines=300, files=declared.get("a", [])),
        Node(
            id="b",
            title="Add B",
            content="do b",
            depends_on=["a"],
            est_lines=300,
            files=declared.get("b", []),
        ),
    ]


def run_all(repo: Path) -> None:
    out = runner.invoke(app, ["run", "--path", str(repo), "--yes"])
    assert out.exit_code == 0, out.output


def review(repo: Path, *args: str):
    return runner.invoke(app, ["review", *args, "--path", str(repo)])


def test_in_scope_work_passes_and_diffs_only_the_agents_own_commits(tmp_path):
    repo = make_repo(tmp_path, nodes(a=["wi-01.txt"], b=["wi-02.txt"]))
    configure(repo, WRITE)
    run_all(repo)
    out = review(repo, "wi-02")
    assert out.exit_code == 0, out.output
    assert "Verdict: **pass**" in out.output
    # wi-02 is branched off wi-01, but only wi-02's own file is reviewed
    assert "1 files" in out.output and "`wi-01.txt`" not in out.output
    assert (repo / ".automagic" / "plan" / "reviews" / "wi-02.md").exists()
    state = RunState.load(repo / ".automagic" / "plan" / "run.json")
    assert state.items["wi-02"].review == "pass" and state.items["wi-02"].base


def test_node_id_target_and_branch_target_resolve_to_the_work_item(tmp_path):
    repo = make_repo(tmp_path, nodes(a=["wi-01.txt"], b=["wi-02.txt"]))
    configure(repo, WRITE)
    run_all(repo)
    assert "# Review: wi-02" in review(repo, "b").output
    assert "# Review: wi-01" in review(repo, "automagic/demo/wi-01").output
    bad = review(repo, "nope")
    assert bad.exit_code == 1 and "not a work item" in bad.output


def test_out_of_scope_file_is_a_warning_and_guarded_file_is_an_error(tmp_path):
    repo = make_repo(tmp_path, nodes(a=["other.txt"]))
    configure(repo, WRITE)
    run_all(repo)
    out = review(repo, "wi-01")
    assert out.exit_code == 0
    assert "Verdict: **concerns**" in out.output
    assert (
        "outside the declared scope" in out.output and "Declared file was not changed" in out.output
    )

    sneaky = "sh -c 'echo \"$PWD\" > AGENTS.md' {prompt}"
    (tmp_path / "two").mkdir()
    repo2 = make_repo(tmp_path / "two", nodes(a=["a.txt"]))
    configure(repo2, sneaky)
    run_all(repo2)
    out = review(repo2, "wi-01")
    assert out.exit_code == 1 and "Verdict: **fail**" in out.output
    assert "Guardrail/CI/config file changed" in out.output


@pytest.mark.parametrize(
    "name",
    ["AGENTS.md", "pyproject.toml", "docs/GUARDRAILS.md", ".github/prompts/x.prompt.md"],
)
def test_guarded_files_fail_even_when_nodes_declare_no_files(tmp_path, name):
    repo = make_repo(tmp_path, [Node(id="a", title="A", content="x", est_lines=10)])
    configure(repo, f"sh -c 'mkdir -p $(dirname {name}) && echo \"$PWD\" > {name}' {{prompt}}")
    run_all(repo)
    out = review(repo, "wi-01")
    assert out.exit_code == 1 and "Verdict: **fail**" in out.output
    assert "Guardrail/CI/config file changed" in out.output and name in out.output


def test_ordinary_files_pass_when_nodes_declare_no_files(tmp_path):
    repo = make_repo(tmp_path, [Node(id="a", title="A", content="x", est_lines=10)])
    configure(repo, WRITE)
    run_all(repo)
    out = review(repo, "wi-01")
    assert out.exit_code == 0 and "Verdict: **pass**" in out.output


def test_size_budget_is_enforced(tmp_path):
    repo = make_repo(tmp_path, [Node(id="a", title="A", content="x", est_lines=1, files=["f.txt"])])
    cfg = Config(
        runner_command="sh -c 'seq 1 10 > f.txt' {prompt}",
        verify_command="",
        max_changed_lines=3,
    )
    (repo / ".automagic" / "config.toml").write_text(cfg.to_toml())
    run_all(repo)
    out = review(repo, "wi-01")
    assert out.exit_code == 1 and "exceed the budget of 3" in out.output


def test_guardrail_violation_in_changed_context_file_fails(tmp_path):
    big = "sh -c 'yes x | head -c 9000 > AGENTS.md' {prompt}"
    repo = make_repo(
        tmp_path, [Node(id="a", title="A", content="x", est_lines=10, files=["AGENTS.md"])]
    )
    configure(repo, big)
    run_all(repo)
    out = review(repo, "wi-01")
    assert out.exit_code == 1 and "exceeds limit" in out.output


def test_verify_flag_and_fallback_base_without_run_state(tmp_path):
    repo = make_repo(tmp_path, nodes(a=["wi-01.txt"]))
    configure(repo, WRITE)
    run_all(repo)
    configure(repo, WRITE, verify="false")
    (repo / ".automagic" / "plan" / "run.json").unlink()
    out = review(repo, "wi-01", "--verify")
    assert out.exit_code == 1 and "failed" in out.output
    assert "Verdict: **fail**" in out.output
    assert review(repo, "wi-01").exit_code == 0


def test_missing_worktree_is_a_clear_error(tmp_path):
    repo = make_repo(tmp_path, nodes())
    out = review(repo, "wi-01")
    assert out.exit_code == 1 and "does not exist" in out.output


def _prop(tmp_path: Path) -> tuple[Graph, rv.Review]:
    graph = Graph(goal="g")
    graph.add(Node(id="a", title="Add A", content="do a"))
    item = WorkItem(id="wi-01", node_ids=["a"])
    return graph, rv.Review(item, "b", tmp_path, "base")


def test_ai_review_adds_findings_and_commit_message(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "GUARDRAILS.md").write_text("rules")
    graph, result = _prop(tmp_path)
    monkeypatch.setattr(rv, "git", lambda args, cwd: type("R", (), {"stdout": "diff text"})())
    reply = {
        "findings": [
            {"severity": "error", "file": "x.py", "message": "invented API"},
            {"severity": "weird", "message": "hmm"},
        ],
        "commit_message": "Add A\n\nbody",
    }
    rv.ai_review(graph, result, FakeProvider([json.dumps(reply)]), Config())
    assert [f.severity for f in result.findings] == ["error", "warn"]
    assert result.commit_message == "Add A\n\nbody" and result.verdict == "fail"


def test_ai_review_failure_is_a_warning_not_a_crash(tmp_path, monkeypatch):
    graph, result = _prop(tmp_path)
    monkeypatch.setattr(rv, "git", lambda args, cwd: type("R", (), {"stdout": "d"})())

    class Broken:
        def complete(self, prompt: str) -> str:
            raise ProviderError("down")

    rv.ai_review(graph, result, Broken(), Config())
    assert result.verdict == "concerns" and "unavailable" in result.findings[0].message


def test_propose_message_is_capped_and_lists_nodes():
    item = WorkItem(id="wi-01")
    long = [Node(id=f"n{i}", title="T" * 40, content="") for i in range(2)]
    message = rv.propose_message(item, long)
    assert len(message.splitlines()[0]) <= 72 and "(n1)" in message
