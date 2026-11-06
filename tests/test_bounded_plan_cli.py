import json

import pytest
from test_bounded_interview import assessment, draft, question
from test_runner import make_repo
from typer.testing import CliRunner

from automagic.cli import app
from automagic.config import Config
from automagic.durability import InterviewDurability
from automagic.interview import run_interview
from automagic.merkle import Graph, InterviewCheckpoint, Node
from automagic.planning_state import PlanningState
from automagic.providers import FakeProvider

runner = CliRunner()


@pytest.mark.parametrize("resume", [False, True])
def test_plan_recovers_from_a_dependent_question_batch(tmp_path, monkeypatch, resume):
    repo = make_repo(tmp_path, [])
    graph = draft()
    graph.nodes["sharing"] = Node(
        id="sharing", title="Sharing", content="Undecided", depends_on=["scope"]
    )
    replies = [
        assessment(questions=[question(), question("sharing", "sharing")]),
        assessment(questions=[question()]),
    ]
    args = ["plan", "--no-create"]
    if resume:
        graph.interview = InterviewCheckpoint(
            round_limit=8,
            pending=[],
            graph_hash=graph.interview_hash(),
            planning=PlanningState(question_limit=12),
        )
        graph.save(repo / ".automagic/plan/graph.json")
        args.append("--resume")
    else:
        replies.insert(
            0, json.dumps({"nodes": [node.model_dump() for node in graph.nodes.values()]})
        )
        args.append(graph.goal)
    provider = FakeProvider(replies)
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, args, input="/done\n")

    assert result.exit_code == 2, result.output
    assert "Who may read notes?" in result.output
    assert "Decide sharing?" not in result.output
    assert "error: Question batch contains an unsettled prerequisite" not in result.output
    state = Graph.load(repo / ".automagic/plan/graph.json").interview.planning
    assert state.answered == 0 and state.remaining == 12
    assert [item.node for item in state.batch] == ["scope"]


def test_new_plan_requires_approval_before_publishing(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, list(draft().nodes.values()))
    provider = FakeProvider(
        [
            json.dumps({"nodes": [draft().nodes["scope"].model_dump()]}),
            assessment(settled=["scope"], ready=True),
        ]
    )
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    paused = runner.invoke(
        app,
        ["plan", "Private notes", "--path", str(repo), "--no-create"],
        input="/done\n",
    )
    assert paused.exit_code == 2, paused.output
    assert "Outcome:" in paused.output
    path = repo / ".automagic/plan/graph.json"
    assert Graph.load(path).interview.planning.phase == "confirming"
    assert not (repo / "docs/plan/graph.json").exists()
    approved = runner.invoke(
        app,
        ["plan", "--resume", "--path", str(repo), "--no-create"],
        input="/approve\n",
    )
    assert approved.exit_code == 0, approved.output
    assert "Planning settled" in approved.output
    assert Graph.load(path).interview.planning.phase == "approved"
    assert "interview" not in json.loads((repo / "docs/plan/graph.json").read_text())


def test_cli_budget_stop_and_resume_decline_do_not_replenish(tmp_path, monkeypatch):
    graph = draft()
    repo = make_repo(tmp_path, list(graph.nodes.values()))
    path = repo / ".automagic/plan/graph.json"
    run_interview(
        graph,
        FakeProvider([assessment(questions=[question()])]),
        lambda _: None,
        max_questions=0,
        durability=InterviewDurability(path),
    )
    config = repo / ".automagic/config.toml"
    config.write_text(Config(max_questions=99).to_toml())
    provider = FakeProvider([])
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = runner.invoke(
        app,
        ["plan", "--resume", "--path", str(repo), "--no-create"],
        input="n\n",
    )
    assert result.exit_code == 2, result.output
    assert "six" in result.output.lower()
    assert "Who may read notes?" in result.output
    assert Graph.load(path).interview.planning.question_limit == 0
    assert provider.prompts == []


def test_legacy_transition_requires_consent_and_preserves_decisions(tmp_path, monkeypatch):
    graph = draft()
    repo = make_repo(tmp_path, list(graph.nodes.values()))
    path = repo / ".automagic/plan/graph.json"
    graph.save(path)
    original = path.read_bytes()
    provider = FakeProvider([assessment(questions=[question()])])
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    declined = runner.invoke(
        app,
        ["plan", "--resume", "--path", str(repo), "--no-create"],
        input="n\n",
    )
    assert declined.exit_code == 2 and path.read_bytes() == original
    assert provider.prompts == []
    accepted = runner.invoke(
        app,
        ["plan", "--resume", "--path", str(repo), "--no-create"],
        input="y\n/done\n",
    )
    assert accepted.exit_code == 2, accepted.output
    assert Graph.load(path).root_hash() == graph.root_hash()
    assert Graph.load(path).interview.planning.answered == 0


def test_interviewer_gets_actual_topic_sources_but_not_hidden_or_outside_files(
    tmp_path, monkeypatch
):
    repo = make_repo(tmp_path, list(draft().nodes.values()))
    (repo / "app.py").write_text("FRAMEWORK = 'verified-framework'\n")
    (repo / ".env").write_text("hidden-content\n")
    (tmp_path / "outside.py").write_text("outside-content\n")
    node = draft().nodes["scope"].model_dump()
    node["files"] = ["app.py", ".env", "../outside.py"]

    def reply(prompt):
        if prompt.startswith("You are a planning agent"):
            return json.dumps({"nodes": [node]})
        assert "verified-framework" in prompt
        assert "hidden-content" not in prompt and "outside-content" not in prompt
        return assessment(settled=["scope"], ready=True)

    monkeypatch.setattr("automagic.cli.get_provider", lambda _: FakeProvider(reply))
    result = runner.invoke(
        app,
        ["plan", "Private notes", "--path", str(repo), "--no-create"],
        input="/done\n",
    )
    assert result.exit_code == 2, result.output


def test_an_unapproved_plan_cannot_be_amended_to_reset_its_allowance(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, list(draft().nodes.values()))
    path = repo / ".automagic/plan/graph.json"
    run_interview(
        draft(),
        FakeProvider([assessment(settled=["scope"], ready=True)]),
        lambda _: None,
        max_questions=0,
        durability=InterviewDurability(path),
    )
    original = path.read_bytes()
    provider = FakeProvider([])
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = runner.invoke(
        app,
        ["plan", "--amend", "Change it", "--path", str(repo), "--no-create"],
    )
    assert result.exit_code == 1 and "approve the open plan" in result.output
    assert path.read_bytes() == original and provider.prompts == []
