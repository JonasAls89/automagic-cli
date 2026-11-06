import json
import shlex

import pytest
from test_bounded_interview import assessment, question
from typer.testing import CliRunner

from automagic import planning_ui
from automagic.cli import app
from automagic.durability import InterviewDurability
from automagic.merkle import Graph, GraphError, InterviewCheckpoint, Node
from automagic.planning_state import Assessment, Decision, PlanningQuestion, PlanningState
from automagic.providers import FakeProvider, ProviderError

runner = CliRunner()


def saved_pause(root, reason="no_progress", *, no_progress=0):
    nodes = {
        f"topic-{i:02}": Node(
            id=f"topic-{i:02}",
            title=f"Agreed topic {i}"
            if i < 14
            else ("Quality gates" if i == 14 else "Documentation"),
            content="\n".join(f"Implementation detail {j} for topic {i}." for j in range(6)),
        )
        for i in range(16)
    }
    graph = Graph(goal="Build a private notes app", nodes=nodes)
    for nid in list(nodes)[:14]:
        graph.validate(nid)
    readiness = Assessment.model_validate_json(assessment(settled=list(nodes)[:14], ready=True))
    readiness.unknowns = [f"Unknown {i}: deferred reversible detail." for i in range(5)]
    graph.interview = InterviewCheckpoint(
        round_limit=8,
        pending=[],
        graph_hash=graph.interview_hash(),
        planning=PlanningState(
            question_limit=15 if reason == "question_budget" else 18,
            answered=15,
            phase="asking",
            assessment=readiness,
            history=[
                Decision(
                    question=PlanningQuestion(**question("topic-00", f"decision-{i}")),
                    answer=f"Previous answer {i}",
                    resolved=True,
                )
                for i in range(15)
            ],
            no_progress=no_progress,
            completed_rounds=8 if reason == "pass_limit" else 0,
            stop_reason=reason,
            stop_detail="No decisions settled. Clarify the blockers or narrow scope.",
        ),
    )
    path = root / ".automagic/plan/graph.json"
    InterviewDurability(path).save(graph)
    return path


def resume(root, input, width=100):
    return runner.invoke(
        app,
        ["plan", "--resume", "--path", str(root), "--no-create"],
        input=input,
        color=False,
        terminal_width=width,
        env={"COLUMNS": str(width), "NO_COLOR": "1", "TERM": "dumb"},
    )


def ready_confirmation(root):
    path = saved_pause(root)
    graph = Graph.load(path)
    for nid in graph.nodes:
        graph.validate(nid)
    state = graph.interview.planning
    state.phase, state.stop_reason, state.stop_detail = "confirming", None, ""
    InterviewDurability(path).save(graph)
    return path


@pytest.mark.parametrize("no_progress", [0, 2])
def test_pause_is_compact_and_explains_why_no_blockers_is_not_approval(
    tmp_path, monkeypatch, no_progress
):
    path = saved_pause(tmp_path, no_progress=no_progress)
    original = path.read_bytes()
    provider = FakeProvider([])
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = resume(tmp_path, "n\n")

    assert result.exit_code == 2, result.output
    assert len(result.output.splitlines()) <= 60, result.output
    expected = "no next question" if no_progress == 0 else "Two consecutive"
    assert expected in result.output
    assert "no_progress" not in result.output
    assert "14/16 settled" in result.output and "15/18" in result.output
    assert "3 remaining" in result.output
    assert "Quality gates" in result.output and "Documentation" in result.output
    assert "none reported" in result.output and "still prevent approval" in result.output
    assert all(action in result.output for action in ("/details", "/correct", "/retry", "/done"))
    assert "Implementation detail" not in result.output
    assert "Previous answer" not in result.output
    assert "more" in result.output
    assert path.read_bytes() == original and provider.prompts == []


def test_details_and_invalid_choices_are_read_only(tmp_path, monkeypatch):
    path = saved_pause(tmp_path)
    original = path.read_bytes()
    history = path.with_name("history.jsonl")
    history.write_text('{"event":"existing"}\n')
    provider = FakeProvider([])
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = resume(tmp_path, "/not-an-action\n/details\n/done\n")

    assert result.exit_code == 2, result.output
    assert "Unknown action" in result.output
    assert "Implementation detail 5 for topic 15." in result.output
    assert "Previous answer 14" in result.output and "Unknown 4" in result.output
    assert path.read_bytes() == original and history.read_text() == '{"event":"existing"}\n'
    assert provider.prompts == []
    assert not (tmp_path / "docs/plan").exists()
    assert not path.with_name("plan.html").exists()


@pytest.mark.parametrize("action", ["/retry", "y"])
def test_explicit_retry_reassesses_without_replenishing(tmp_path, monkeypatch, action):
    path = saved_pause(tmp_path, no_progress=2)
    provider = FakeProvider([assessment(questions=[question("topic-14", "next")])])
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = resume(tmp_path, f"{action}\n/done\n")

    assert result.exit_code == 2, result.output
    state = Graph.load(path).interview.planning
    assert (state.question_limit, state.answered, state.remaining) == (18, 15, 3)
    assert state.no_progress == 0 and len(provider.prompts) == 1
    assert "may pause again" in result.output


@pytest.mark.parametrize("reason", ["question_budget", "pass_limit"])
def test_extensions_explain_and_change_only_the_matching_limit(tmp_path, monkeypatch, reason):
    path = saved_pause(tmp_path, reason)
    replies = (
        [assessment(questions=[question("topic-14", "next")])]
        if reason == "question_budget"
        else []
    )
    provider = FakeProvider(replies)
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = resume(tmp_path, "/extend\n/done\n")

    assert result.exit_code == 2, result.output
    checkpoint = Graph.load(path).interview
    assert checkpoint.round_limit == (16 if reason == "pass_limit" else 8)
    assert checkpoint.planning.question_limit == (21 if reason == "question_budget" else 18)
    assert checkpoint.planning.answered == 15
    assert (
        "15 -> 21" in result.output if reason == "question_budget" else "8 -> 16" in result.output
    )


@pytest.mark.parametrize("width", [40, 80, 100])
def test_terminal_output_preserves_literal_markup_and_target_repository(
    tmp_path, monkeypatch, width
):
    root = tmp_path / "repo with spaces"
    path = saved_pause(root)
    graph = Graph.load(path)
    graph.interview.planning.assessment.criteria.outcome.evidence = "[red]Literal evidence[/red]"
    InterviewDurability(path).save(graph)
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: FakeProvider([]))
    result = resume(root, "\n", width=width)

    assert result.exit_code == 2, result.output
    flattened = " ".join(result.output.split())
    assert "[red]Literal evidence[/red]" in flattened
    assert "\x1b[" not in result.output
    assert f"--path {shlex.quote(str(root))}" in result.output


@pytest.mark.parametrize("reason", ["question_budget", "pass_limit", "no_progress", None])
@pytest.mark.parametrize("failure", [ProviderError, KeyboardInterrupt])
def test_correction_is_saved_before_provider_work_and_recovers_once(
    tmp_path, monkeypatch, reason, failure
):
    path = ready_confirmation(tmp_path) if reason is None else saved_pause(tmp_path, reason)
    original = Graph.load(path)
    state = original.interview.planning
    limit, answers = state.question_limit, state.history
    correction = "Actually, use shared notes"

    def offline(prompt):
        pending = Graph.load(path).interview.planning
        assert pending.phase == "correcting" and pending.answer == correction
        assert pending.question_limit == limit and pending.answered == 15
        assert pending.stop_reason is None
        raise failure("offline")

    provider = FakeProvider(offline)
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    failed = resume(tmp_path, f"/correct\n{correction}\n")
    assert failed.exit_code == 1, failed.output
    assert len(provider.prompts) == 1
    assert Graph.load(path).root_hash() == original.root_hash()
    assert str(tmp_path) in failed.output and "--resume" in failed.output

    provider = FakeProvider(
        [
            '{"edits":[{"id":"topic-14","content":"Shared notes"}]}',
            assessment(questions=[question("topic-15", "next")]),
        ]
    )
    recovered = resume(tmp_path, "/done\n")
    assert recovered.exit_code == 2, recovered.output
    checkpoint = Graph.load(path).interview
    state = checkpoint.planning
    assert state.answer is None and state.corrections == [correction]
    assert state.history == answers and state.answered == 15 and state.question_limit == limit
    assert checkpoint.round_limit == 8 and len(provider.prompts) == 2
    assert Graph.load(path).nodes["topic-14"].content == "Shared notes"
    assert not (tmp_path / "docs/plan").exists()


@pytest.mark.parametrize("input", ["/correct\n/done\n", "/correct\n   \n/quit\n"])
def test_cancelling_correction_stops_without_changing_saved_input(tmp_path, monkeypatch, input):
    path = saved_pause(tmp_path)
    original = path.read_bytes()
    provider = FakeProvider([])
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = resume(tmp_path, input)
    assert result.exit_code == 2, result.output
    assert path.read_bytes() == original and provider.prompts == []


def test_confirmation_details_and_invalid_actions_do_not_imply_approval(tmp_path, monkeypatch):
    path = ready_confirmation(tmp_path)
    original = path.read_bytes()
    provider = FakeProvider([])
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = resume(tmp_path, "/unknown\n/details\n\n")
    assert result.exit_code == 2, result.output
    assert "Unknown action" in result.output and "Previous answer 14" in result.output
    assert "/approve" in result.output and "Review before approval" in result.output
    assert path.read_bytes() == original and provider.prompts == []


def test_confirmation_still_accepts_a_direct_text_correction(tmp_path, monkeypatch):
    path = ready_confirmation(tmp_path)
    graph = Graph.load(path)
    graph.interview.planning.no_progress = 2
    InterviewDurability(path).save(graph)
    provider = FakeProvider(
        [
            '{"edits":[{"id":"topic-14","content":"Shared notes"}]}',
            assessment(questions=[question("topic-15", "next")]),
        ]
    )
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = resume(tmp_path, "Make it shared notes\n/done\n")
    assert result.exit_code == 2, result.output
    state = Graph.load(path).interview.planning
    assert state.corrections == ["Make it shared notes"] and state.answered == 15
    assert state.question_limit == 18 and state.approved_hash is None
    assert state.stop_reason is None and state.no_progress == 0
    assert "Decide next?" in result.output


@pytest.mark.parametrize("legacy", [False, True])
def test_recovery_details_and_consent_preserve_decisions_and_history(tmp_path, monkeypatch, legacy):
    path = saved_pause(tmp_path)
    graph = Graph.load(path)
    if legacy:
        graph.interview = None
        graph.save(path)
        action = "/start"
    else:
        data = json.loads(path.read_text())
        data["interview"]["version"] = 99
        path.write_text(json.dumps(data))
        action = "/reset"
    original = path.read_bytes()
    history = path.with_name("history.jsonl")
    history.write_text('{"event":"existing"}\n')
    provider = FakeProvider([])
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    declined = resume(tmp_path, "/details\n\n")
    assert declined.exit_code == 2, declined.output
    assert "Recovery explanation (read-only)" in declined.output
    assert path.read_bytes() == original and provider.prompts == []

    provider = FakeProvider([assessment(questions=[question("topic-14", "next")])])
    accepted = resume(tmp_path, f"{action}\n/done\n")
    assert accepted.exit_code == 2, accepted.output
    restored = Graph.load(path)
    assert restored.root_hash() == graph.root_hash()
    assert restored.interview.planning.question_limit == 12
    assert restored.interview.planning.answered == 0
    assert history.read_text() == '{"event":"existing"}\n' and len(provider.prompts) == 1


def test_material_blockers_are_not_abbreviated(tmp_path, monkeypatch):
    path = saved_pause(tmp_path)
    graph = Graph.load(path)
    blocker = "A blocking acceptance decision remains unresolved. " * 8 + "Critical final clause."
    graph.interview.planning.assessment.blockers = [blocker]
    InterviewDurability(path).save(graph)
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: FakeProvider([]))
    result = resume(tmp_path, "/done\n")
    assert result.exit_code == 2, result.output
    assert blocker.strip() in " ".join(result.output.split())


def test_incomplete_legacy_pause_explains_uncertainty_instead_of_inventing_a_cause(
    tmp_path, monkeypatch
):
    path = saved_pause(tmp_path)
    graph = Graph.load(path)
    state = graph.interview.planning
    state.assessment = None
    state.batch = [PlanningQuestion(**question("topic-14", "unanswered"))]
    InterviewDurability(path).save(graph)
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: FakeProvider([]))
    result = resume(tmp_path, "/done\n")
    assert result.exit_code == 2, result.output
    assert "not recorded" in result.output and "not yet assessed" in result.output
    assert "repeated answered question" not in result.output


def test_invalid_view_context_is_an_error_not_an_empty_success(tmp_path):
    with pytest.raises(GraphError, match="saved bounded interview"):
        planning_ui.show_summary(Graph(goal="Notes", nodes={}), tmp_path)
    path = saved_pause(tmp_path)
    with pytest.raises(GraphError, match="Unknown interview stop reason"):
        planning_ui.continue_plan(Graph.load(path), tmp_path, "invalid", "", 8)


@pytest.mark.parametrize("failure", [ProviderError("offline"), OSError("disk unavailable")])
def test_initialization_errors_report_recovery_without_replacing_saved_state(
    tmp_path, monkeypatch, failure
):
    path = saved_pause(tmp_path)
    original = path.read_bytes()

    def fail(_):
        raise failure

    monkeypatch.setattr("automagic.cli.get_provider", fail)
    result = resume(tmp_path, "")
    assert result.exit_code == 1, result.output
    assert "Recovery:" in result.output and "No automatic retry" in result.output
    assert "input that failed to save must be entered again" in result.output
    assert path.read_bytes() == original


def test_invalid_configuration_reports_the_cause_and_resume_command(tmp_path, monkeypatch):
    path = saved_pause(tmp_path)
    path.parent.parent.joinpath("config.toml").write_text("max_questions = [")
    monkeypatch.setattr(
        "automagic.cli.get_provider", lambda _: pytest.fail("Invalid configuration must stop first")
    )
    result = resume(tmp_path, "")
    assert result.exit_code == 1, result.output
    assert "invalid planning data or configuration" in result.output
    assert "--resume" in result.output and str(tmp_path) in result.output
