import json

import pytest

from automagic.interview import run_interview
from automagic.merkle import Graph, Node
from automagic.progress import Progress
from automagic.providers import FakeProvider, ProviderError


def graph_with_nodes(count: int = 1) -> Graph:
    return Graph(
        goal="goal",
        nodes={
            f"node-{i:02}": Node(id=f"node-{i:02}", title=f"Node {i}", content="undecided")
            for i in range(count)
        },
    )


def questioning_provider() -> FakeProvider:
    def reply(prompt: str) -> str:
        nid = prompt.split("Node ", 1)[1].split(":", 1)[0]
        if prompt.startswith("You are a rigorous interviewer"):
            return json.dumps({"question": f"Question for {nid}?"})
        if prompt.startswith("Revise the node"):
            return json.dumps({"content": f"Saved decision for {nid}"})
        raise AssertionError(prompt)

    return FakeProvider(reply)


def test_resume_preserves_seventeen_node_ten_checks_position_and_question(tmp_path):
    graph = graph_with_nodes(17)
    path = tmp_path / "graph.json"
    graph.save(path)
    answers = iter([*["answer"] * 7, None])
    questions: list[str] = []
    progress: list[Progress] = []

    def ask(question: str) -> str | None:
        questions.append(question)
        return next(answers)

    first = run_interview(
        graph,
        questioning_provider(),
        ask,
        on_change=lambda current: current.save(path),
        on_progress=progress.append,
    )
    paused = [p for p in progress if p.phase == "asking"][-1]
    assert not first.converged and first.rounds == 1
    assert (paused.settled, paused.total, paused.left_in_round) == (0, 17, 10)

    restored = Graph.load(path)
    provider = questioning_provider()
    resumed_questions: list[str] = []
    progress.clear()
    second = run_interview(
        restored,
        provider,
        lambda question: resumed_questions.append(question),
        on_change=lambda current: current.save(path),
        on_progress=progress.append,
    )
    resumed = next(p for p in progress if p.phase == "asking")
    assert not second.converged and second.rounds == 1
    assert (resumed.round_no, resumed.max_rounds, resumed.left_in_round) == (1, 8, 10)
    assert (resumed.settled, resumed.total) == (0, 17)
    assert resumed_questions == [questions[-1]]
    assert provider.prompts == []
    assert sum(n.content.startswith("Saved decision") for n in restored.nodes.values()) == 7


def test_repeated_pauses_do_not_change_checkpoint_or_replenish_budget(tmp_path):
    graph = graph_with_nodes()
    path = tmp_path / "graph.json"
    run_interview(
        graph,
        questioning_provider(),
        lambda _: None,
        max_rounds=2,
        on_change=lambda current: current.save(path),
    )
    original = path.read_bytes()
    for _ in range(3):
        restored = Graph.load(path)
        provider = FakeProvider([])
        run_interview(
            restored,
            provider,
            lambda _: None,
            max_rounds=99,
            on_change=lambda current: current.save(path),
        )
        assert restored.interview is not None
        assert (restored.interview.round_no, restored.interview.round_limit) == (1, 2)
        assert path.read_bytes() == original and provider.prompts == []


@pytest.mark.parametrize("max_rounds", [0, -1])
def test_invalid_resume_budget_is_reported_without_mutating_checkpoint(max_rounds):
    graph = graph_with_nodes()
    run_interview(graph, questioning_provider(), lambda _: None)
    original = graph.model_dump_json()
    with pytest.raises(ValueError, match="must be positive"):
        run_interview(graph, FakeProvider([]), lambda _: None, max_rounds=max_rounds)
    assert graph.model_dump_json() == original


@pytest.mark.parametrize(
    ("initial", "extension", "next_round", "next_limit"), [(2, 3, 3, 5), (8, 8, 9, 16)]
)
def test_explicit_resume_after_exhaustion_extends_cumulative_round_budget(
    tmp_path, initial, extension, next_round, next_limit
):
    graph = graph_with_nodes()
    path = tmp_path / "graph.json"
    first = run_interview(
        graph,
        questioning_provider(),
        lambda _: "answer",
        max_rounds=initial,
        on_change=lambda current: current.save(path),
    )
    assert not first.converged and not first.paused and first.rounds == initial
    progress: list[Progress] = []
    restored = Graph.load(path)
    result = run_interview(
        restored,
        questioning_provider(),
        lambda _: None,
        max_rounds=extension,
        on_progress=progress.append,
    )
    asking = next(p for p in progress if p.phase == "asking")
    assert result.paused
    assert (asking.round_no, asking.max_rounds, asking.left_in_round) == (
        next_round,
        next_limit,
        1,
    )


@pytest.mark.parametrize("failure", [ProviderError("offline"), KeyboardInterrupt()])
def test_failure_generating_question_retains_checking_phase(tmp_path, failure):
    graph = graph_with_nodes()
    path = tmp_path / "graph.json"

    def fail(prompt):
        raise failure

    with pytest.raises(type(failure)):
        run_interview(
            graph, FakeProvider(fail), lambda _: None, on_change=lambda current: current.save(path)
        )
    restored = Graph.load(path)
    assert restored.interview is not None and restored.interview.phase == "checking"
    restored.check_interview()
    result = run_interview(restored, questioning_provider(), lambda _: None)
    assert result.paused


@pytest.mark.parametrize(
    "failure",
    [
        ProviderError("offline"),
        KeyboardInterrupt(),
        "not json",
        json.dumps({"content": "not committed", "depends_on": ["missing"]}),
    ],
)
def test_interrupted_revision_reuses_submitted_answer_without_asking_again(tmp_path, failure):
    path = tmp_path / "graph.json"
    graph = graph_with_nodes()

    def fail_revision(prompt):
        if prompt.startswith("You are a rigorous interviewer"):
            return json.dumps({"question": "Original question?"})
        if isinstance(failure, BaseException):
            raise failure
        return failure

    with pytest.raises((ProviderError, KeyboardInterrupt, ValueError)):
        run_interview(
            graph,
            FakeProvider(fail_revision),
            lambda _: "saved answer",
            on_change=lambda current: current.save(path),
        )
    restored = Graph.load(path)
    assert restored.nodes["node-00"].content == "undecided"
    assert restored.interview is not None
    assert (restored.interview.phase, restored.interview.answer) == ("revising", "saved answer")

    def recover(prompt):
        if prompt.startswith("Revise the node"):
            assert "Question: Original question?" in prompt and "Answer: saved answer" in prompt
            return json.dumps({"content": "applied saved answer"})
        return json.dumps({"question": None})

    provider = FakeProvider(recover)
    events: list[dict] = []
    result = run_interview(
        restored,
        provider,
        lambda _: pytest.fail("A saved answer must not be requested again"),
        on_change=lambda current: current.save(path),
        on_event=events.append,
    )
    assert result.converged and result.questions_asked == 0
    assert provider.prompts[0].startswith("Revise the node")
    assert [event["event"] for event in events] == ["answered", "validated"]
    assert restored.interview is None and Graph.load(path).is_stable()


def test_resume_finishes_current_pass_before_revisiting_previously_answered_nodes(tmp_path):
    graph = graph_with_nodes(3)
    path = tmp_path / "graph.json"
    answers = iter(["answer", None])
    run_interview(
        graph,
        questioning_provider(),
        lambda _: next(answers),
        on_change=lambda current: current.save(path),
    )
    restored = Graph.load(path)
    provider = questioning_provider()
    answers = iter(["answer", None])
    progress: list[Progress] = []
    run_interview(restored, provider, lambda _: next(answers), on_progress=progress.append)
    assert all("Node node-00:" not in prompt for prompt in provider.prompts)
    assert provider.prompts[0].startswith("Revise the node")
    assert [p.left_in_round for p in progress if p.phase == "asking"] == [2, 1]
    assert all(p.round_no == 1 for p in progress)


def test_added_prerequisite_is_scheduled_in_the_next_round():
    graph = graph_with_nodes()

    def reply(prompt):
        if prompt.startswith("Revise the node"):
            return json.dumps(
                {
                    "content": "revised",
                    "depends_on": ["prerequisite"],
                    "new_nodes": [{"id": "prerequisite", "title": "Prerequisite", "content": "?"}],
                }
            )
        return json.dumps({"question": "Clarify this node?"})

    answers = iter(["answer", None])
    progress: list[Progress] = []
    run_interview(graph, FakeProvider(reply), lambda _: next(answers), on_progress=progress.append)
    asking = [p for p in progress if p.phase == "asking"]
    assert [(p.round_no, p.node) for p in asking] == [(1, "node-00"), (2, "prerequisite")]
    assert graph.interview is not None
    assert graph.interview.pending == ["prerequisite", "node-00"]
