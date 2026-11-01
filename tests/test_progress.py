import json
from io import StringIO

import pytest
from rich.console import Console
from test_planning import SEED, scripted
from test_runner import CHAIN, make_repo
from typer.testing import CliRunner

from automagic.cli import app
from automagic.interview import run_interview
from automagic.merkle import Graph, InterviewEvent, Node
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
        "node_title": "DB",
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
    assert first.node_title == "DB"
    assert events[-1].phase == "settled"
    assert events[-1].settled == events[-1].total


def test_answer_that_adds_a_node_grows_the_total():
    graph = Graph(goal="goal")
    graph.add(Node(id="db", title="DB", content="pick db"))
    revision = {
        "title": "Storage",
        "content": "db revised",
        "new_nodes": [{"id": "migrations", "title": "Migrations", "content": "m"}],
        "depends_on": ["migrations"],
    }
    events: list[Progress] = []
    provider = provider_with(revision, {"db": ["Which db?"]})
    run_interview(graph, provider, lambda q: "a", 5, on_progress=events.append)
    revised = next(e for e in events if e.phase == "revised")
    assert (revised.added, revised.reopened, revised.total) == (1, 0, 2)
    assert revised.node_title == "Storage"
    migrations = next(e for e in events if e.phase == "checking" and e.node == "migrations")
    assert migrations.node_title == "Migrations"
    assert '- db: "Storage"' in provider.prompts[2]
    assert '- migrations: "Migrations"' in provider.prompts[2]


def test_interviewer_has_exact_titles_for_current_dependency_and_other_topics():
    graph = Graph(goal="goal")
    graph.add(Node(id="scope", title="Scope", content="Every sink must be covered"))
    graph.add(Node(id="sinks", title="Sink handling", content="Decide how", depends_on=["scope"]))
    graph.add(Node(id="delivery", title="Delivery", content="Unrelated full content"))
    graph.validate("scope")
    graph.validate("delivery")
    root_hash = graph.root_hash()
    question = 'Does "Scope" apply to every sink in "Sink handling"?'
    provider = FakeProvider([json.dumps({"question": question})])
    events: list[Progress] = []
    result = run_interview(graph, provider, lambda _: None, on_progress=events.append)
    prompt = provider.prompts[0]
    assert "use its exact title in double quotes" in prompt
    assert "do not invent titles" in prompt
    assert "Node sinks: Sink handling" in prompt
    assert 'Dependencies:\n- scope: "Scope": Every sink must be covered' in prompt
    assert '- delivery: "Delivery"' in prompt and "Unrelated full content" not in prompt
    assert result.paused and graph.root_hash() == root_hash
    assert graph.interview is not None and graph.interview.question == question
    assert next(e for e in events if e.phase == "asking").node_title == "Sink handling"


def test_progress_refreshes_after_checking_callback_changes_validation():
    graph = Graph(goal="goal")
    graph.add(Node(id="a", title="Current topic", content="undecided"))
    graph.add(Node(id="b", title="Other topic", content="specified"))
    events: list[Progress] = []

    def notify(progress: Progress) -> None:
        events.append(progress)
        if progress.phase == "checking":
            graph.validate("b")

    run_interview(
        graph,
        FakeProvider([json.dumps({"question": "Clarify?"})]),
        lambda _: None,
        on_progress=notify,
    )
    asking = next(e for e in events if e.phase == "asking")
    assert (asking.settled, asking.left_in_round) == (1, 1)
    graph.check_interview()


def test_progress_refreshes_after_history_callback_reopens_a_topic(tmp_path):
    graph = Graph(goal="goal")
    graph.add(Node(id="db", title="DB", content="undecided"))
    graph.add(Node(id="api", title="API", content="specified"))
    graph.validate("api")
    events: list[Progress] = []
    answers = iter(["answer", None])
    path = tmp_path / "graph.json"

    def record(event: InterviewEvent) -> None:
        if event["event"] == "answered":
            graph.nodes["api"].content = "Changed by the history callback"

    run_interview(
        graph,
        provider_with({"content": "decided"}, {"db": ["First?", "Again?"]}),
        lambda _: next(answers),
        on_change=lambda current: current.save(path),
        on_event=record,
        on_progress=events.append,
    )
    revised = next(e for e in events if e.phase == "revised")
    assert (revised.settled, revised.reopened) == (0, 1)
    Graph.load(path).check_interview()


def test_progress_retains_reviewed_title_when_history_callback_removes_the_topic():
    graph = Graph(nodes={"a": Node(id="a", title="Ready topic", content="specified")})
    events: list[Progress] = []

    def discard(event: InterviewEvent) -> None:
        graph.nodes.pop(event["node"])

    result = run_interview(
        graph,
        FakeProvider([json.dumps({"question": None})]),
        lambda _: None,
        on_event=discard,
        on_progress=events.append,
    )
    assert result.converged and not graph.nodes and graph.interview is None
    assert events[-1].phase == "settled" and events[-1].node_title == "Ready topic"
    assert (events[-1].total, events[-1].settled, events[-1].left_in_round) == (0, 0, 0)


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
        'Topic: "DB"',
        "answer applied: +1 new topic",
    ]


@pytest.mark.parametrize("title", ["Scope", "[bold]Scope[/bold]", 'A "quoted" topic', "Caf\u00e9"])
def test_reporter_prints_the_current_title_literally_off_terminal(title):
    out = StringIO()
    reporter = Reporter(Console(file=out))
    reporter.handle(sample(node_title=title))
    reporter.close()
    assert out.getvalue().splitlines()[-1] == f'Topic: "{title}"'


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
    reporter.handle(sample(phase=phase, node="hidden-node-id", node_title="[bold]x[/bold]"))
    reporter.close()
    reporter.close()
    assert "[bold]x[/bold]" in out.getvalue()
    assert label in out.getvalue()
    assert "hidden-node-id" not in out.getvalue()


def stale_repo(tmp_path):
    repo = make_repo(tmp_path, CHAIN)
    path = repo / ".automagic" / "plan" / "graph.json"
    graph = Graph.load(path)
    graph.nodes["a"].validated_hash = None
    graph.save(path)
    return repo


def test_plan_cli_shows_progress_and_what_the_answer_reopened(tmp_path, monkeypatch):
    repo = stale_repo(tmp_path)
    title = Graph.load(repo / ".automagic" / "plan" / "graph.json").nodes["a"].title
    provider = scripted([], {"a": ["Which approach?"]})
    monkeypatch.setattr("automagic.cli.get_provider", lambda cfg: provider)
    out = runner.invoke(
        app, ["plan", "--resume", "--path", str(repo), "--no-create"], input="my answer\n"
    )
    assert out.exit_code == 0, out.output
    line = "3/4 topics settled · pass 1/8\n1 topic pending this pass · 1 unsettled overall"
    assert line in out.output
    assert out.output.index(line) < out.output.index(f'Topic: "{title}"')
    assert out.output.index(f'Topic: "{title}"') < out.output.index("? Which approach?")
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
