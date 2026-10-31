import json
from io import StringIO

import pytest
from rich.console import Console
from test_planning import SEED, scripted
from test_runner import CHAIN, make_repo
from typer.testing import CliRunner

from automagic.cli import app
from automagic.interview import run_interview
from automagic.merkle import Graph, Node
from automagic.progress import Progress, Reporter, bar, delta, summary
from automagic.providers import FakeProvider

runner = CliRunner()


def sample(**kwargs) -> Progress:
    fields = {
        "phase": "asking",
        "round_no": 2,
        "max_rounds": 8,
        "total": 10,
        "settled": 4,
        "left_in_round": 5,
        "node": "db",
    }
    return Progress(**{**fields, **kwargs})


def provider_with(revision: dict, questions: dict[str, list[str]]) -> FakeProvider:
    def reply(prompt: str) -> str:
        if prompt.startswith("You are a rigorous interviewer"):
            node_id = prompt.split("Node ", 1)[1].split(":", 1)[0]
            queue = questions.get(node_id, [])
            return json.dumps({"question": queue.pop(0) if queue else None})
        if prompt.startswith("Revise the node"):
            return json.dumps(revision)
        raise AssertionError(prompt)

    return FakeProvider(reply)


def test_bar_is_full_only_when_everything_is_settled():
    assert bar(0, 4, 4) == "[----]"
    assert bar(2, 4, 4) == "[##--]"
    assert bar(2, 3, 4) == "[##--]"
    assert bar(4, 4, 4) == "[####]"
    assert bar(0, 0, 4) == "[----]"
    assert bar(19, 20) == "[" + "#" * 19 + "-" + "]"


def test_summary_and_delta_text():
    assert summary(sample()) == (
        "Planning: [########------------] 4/10 topics settled · pass 2/8\n"
        "5 topics pending this pass · 6 unsettled overall"
    )
    assert summary(sample(settled=9)).endswith("1 unsettled overall")
    assert delta(sample()) == ""
    assert delta(sample(added=1, reopened=2)) == (
        "answer applied: +1 new topic, 2 settled topics re-opened"
    )
    assert delta(sample(added=2, reopened=1)) == (
        "answer applied: +2 new topics, 1 settled topic re-opened"
    )


def test_summary_distinguishes_the_current_pass_from_all_unsettled_topics():
    assert summary(sample(round_no=1, total=19, settled=1, left_in_round=8)) == (
        "Planning: [#-------------------] 1/19 topics settled · pass 1/8\n"
        "8 topics pending this pass · 18 unsettled overall"
    )


@pytest.mark.parametrize(
    ("total", "settled", "pending", "expected"),
    [
        (
            0,
            0,
            0,
            "0/0 topics settled · pass 2/8\n0 topics pending this pass · 0 unsettled overall",
        ),
        (1, 0, 1, "0/1 topic settled · pass 2/8\n1 topic pending this pass · 1 unsettled overall"),
        (1, 1, 0, "1/1 topic settled · pass 2/8\n0 topics pending this pass · 0 unsettled overall"),
    ],
)
def test_summary_handles_zero_and_single_topic_counts(total, settled, pending, expected):
    assert summary(sample(total=total, settled=settled, left_in_round=pending)).endswith(expected)


def test_interview_reports_state_before_the_first_question():
    provider = scripted(SEED, {"db": ["Which db?"]})
    graph = Graph(goal="goal")
    for raw in SEED:
        graph.add(Node(**raw))
    events: list[Progress] = []
    result = run_interview(graph, provider, lambda q: "postgres", 5, on_progress=events.append)
    assert result.converged
    assert [e.phase for e in events[:4]] == ["checking", "asking", "revising", "revised"]
    first = events[1]
    assert (first.round_no, first.total, first.settled, first.left_in_round) == (1, 2, 0, 2)
    assert first.max_rounds == 5 and first.to_go == 2
    assert events[-1].phase == "settled"
    assert events[-1].settled == events[-1].total


def test_answer_that_adds_a_node_grows_the_total():
    graph = Graph(goal="goal")
    graph.add(Node(id="db", title="DB", content="pick db"))
    revision = {
        "content": "db revised",
        "new_nodes": [{"id": "migrations", "title": "Migrations", "content": "m"}],
        "depends_on": ["migrations"],
    }
    events: list[Progress] = []
    provider = provider_with(revision, {"db": ["Which db?"]})
    run_interview(graph, provider, lambda q: "a", 5, on_progress=events.append)
    revised = next(e for e in events if e.phase == "revised")
    assert (revised.added, revised.reopened, revised.total) == (1, 0, 2)


def test_answer_that_changes_a_settled_dependent_reopens_it():
    graph = Graph(goal="goal")
    graph.add(Node(id="db", title="DB", content="pick db"))
    graph.add(Node(id="api", title="API", content="api", depends_on=["db"]))
    graph.validate("api")
    events: list[Progress] = []
    provider = provider_with({"content": "db revised"}, {"db": ["Which db?"]})
    run_interview(graph, provider, lambda q: "a", 5, on_progress=events.append)
    asking = next(e for e in events if e.phase == "asking")
    assert (asking.settled, asking.total, asking.left_in_round) == (1, 2, 1)
    revised = next(e for e in events if e.phase == "revised")
    assert (revised.added, revised.reopened) == (0, 1)
    assert revised.settled == 0


def test_reporter_prints_summary_before_question_and_delta_only_when_changed():
    out = StringIO()
    reporter = Reporter(Console(file=out, width=40))
    reporter.handle(sample(phase="checking"))
    reporter.handle(sample())
    reporter.handle(sample(phase="revised"))
    reporter.handle(sample(phase="revised", added=1))
    reporter.close()
    assert out.getvalue().splitlines() == [
        *summary(sample()).splitlines(),
        "answer applied: +1 new topic",
    ]


def test_working_prints_message_off_terminal_and_cleans_up_on_error():
    out = StringIO()
    reporter = Reporter(Console(file=out))
    with pytest.raises(RuntimeError), reporter.working("Drafting..."):
        raise RuntimeError("provider failed")
    reporter.close()
    assert out.getvalue() == "Drafting...\n"


@pytest.mark.parametrize(
    ("phase", "label"),
    [
        ("checking", "Reviewing topic"),
        ("revising", "Applying your answer to topic"),
        ("settled", "Settled topic"),
    ],
)
def test_spinner_text_is_not_parsed_as_markup(monkeypatch, phase, label):
    monkeypatch.setenv("TERM", "xterm")
    out = StringIO()
    reporter = Reporter(Console(file=out, force_terminal=True, width=120))
    reporter.handle(sample(phase=phase, node="[bold]x[/bold]"))
    reporter.close()
    reporter.close()
    assert "[bold]x[/bold]" in out.getvalue()
    assert label in out.getvalue()


def stale_repo(tmp_path):
    repo = make_repo(tmp_path, CHAIN)
    path = repo / ".automagic" / "plan" / "graph.json"
    graph = Graph.load(path)
    graph.nodes["a"].validated_hash = None
    graph.save(path)
    return repo


def test_plan_cli_shows_progress_and_what_the_answer_reopened(tmp_path, monkeypatch):
    repo = stale_repo(tmp_path)
    provider = scripted([], {"a": ["Which approach?"]})
    monkeypatch.setattr("automagic.cli.get_provider", lambda cfg: provider)
    out = runner.invoke(
        app, ["plan", "--resume", "--path", str(repo), "--no-create"], input="my answer\n"
    )
    assert out.exit_code == 0, out.output
    line = "3/4 topics settled · pass 1/8\n1 topic pending this pass · 1 unsettled overall"
    assert line in out.output
    assert out.output.index(line) < out.output.index("? Which approach?")
    assert "answer applied: 3 settled topics re-opened" in out.output
    assert "Planning settled after 2 interview passes, 1 question." in out.output


def test_plan_cli_stopping_early_still_reports_progress(tmp_path, monkeypatch):
    repo = stale_repo(tmp_path)
    provider = scripted([], {"a": ["Which approach?"]})
    monkeypatch.setattr("automagic.cli.get_provider", lambda cfg: provider)
    out = runner.invoke(
        app, ["plan", "--resume", "--path", str(repo), "--no-create"], input="/done\n"
    )
    assert out.exit_code == 2
    assert "3/4 topics settled" in out.output
    assert "Plan paused during interview pass 1/8" in out.output
    assert "1 topic pending this pass" in out.output
