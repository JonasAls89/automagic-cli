import json
from pathlib import Path

import pytest
from test_runner import CHAIN, WRITE, configure, make_repo
from typer.testing import CliRunner

from automagic import guardrails, plandocs
from automagic.cli import app
from automagic.config import Config
from automagic.htmlview import render_html, view_data
from automagic.interview import amend_graph
from automagic.merkle import Graph, Node
from automagic.providers import FakeProvider
from automagic.render import render_history
from automagic.runner import ItemRun, RunError, RunState, select
from automagic.sizing import WorkItem, partition

runner = CliRunner()

ITEMS = [
    WorkItem(id="wi-01", node_ids=["a"]),
    WorkItem(id="wi-02", node_ids=["b"], depends_on=["wi-01"]),
    WorkItem(id="wi-03", node_ids=["c"], depends_on=["wi-01"]),
    WorkItem(id="wi-04", node_ids=["d"], depends_on=["wi-02", "wi-03"]),
]


def test_select_adds_unfinished_prerequisites_only():
    assert select(ITEMS, ["wi-02"], set()) == {"wi-01", "wi-02"}
    assert select(ITEMS, ["wi-04"], set()) == {"wi-01", "wi-02", "wi-03", "wi-04"}
    assert select(ITEMS, ["wi-04"], {"wi-01", "wi-02"}) == {"wi-03", "wi-04"}
    assert select(ITEMS, ["d"], {"wi-01", "wi-02", "wi-03"}) == {"wi-04"}
    assert select(ITEMS, ["wi-01"], {"wi-01"}) == set()
    with pytest.raises(RunError, match="unknown work item or planning topic 'nope'"):
        select(ITEMS, ["nope"], set())


def test_run_only_runs_subset_then_the_rest(tmp_path):
    repo = make_repo(tmp_path, CHAIN)
    configure(repo, WRITE)
    first = runner.invoke(app, ["run", "--path", str(repo), "--yes", "--only", "wi-02"])
    assert first.exit_code == 0, first.output
    assert "wi-01: done" in first.output and "wi-02: done" in first.output
    assert "wi-03" not in first.output and "wi-04" not in first.output
    state = RunState.load(repo / ".automagic" / "plan" / "run.json")
    assert state.items["wi-03"].status == "pending"
    rest = runner.invoke(app, ["run", "--path", str(repo), "--yes", "--only", "d"])
    assert rest.exit_code == 0, rest.output
    assert "wi-03: done" in rest.output and "wi-04: done" in rest.output
    assert "wi-01: running" not in rest.output
    nothing = runner.invoke(app, ["run", "--path", str(repo), "--yes", "--only", "wi-04"])
    assert "nothing to run" in nothing.output


def test_show_writes_node_docs_html_and_detects_stale_docs(tmp_path):
    repo = make_repo(tmp_path, CHAIN)
    out = runner.invoke(app, ["show", "--path", str(repo)])
    assert out.exit_code == 0, out.output
    docs = repo / "docs" / "plan"
    assert (docs / "graph.json").exists() and (docs / "_index.md").exists()
    doc = (docs / "b.md").read_text()
    assert "## Agreed scope" in doc and "do b" in doc and "[A](a.md)" in doc
    assert "Planning topic `b`" in doc
    assert "| Planning topic | Work item |" in (docs / "_index.md").read_text()
    assert (repo / ".automagic" / "plan" / "plan.html").exists()
    assert guardrails.check(repo, Config()) == []
    (docs / "b.md").write_text(f"<!-- automagic:node b {'0' * 64} -->\n" + doc.split("\n", 1)[1])
    assert any("b.md" in p for p in plandocs.stale(repo))


def test_graph_loads_from_committed_docs_when_state_is_missing(tmp_path):
    repo = make_repo(tmp_path, CHAIN)
    runner.invoke(app, ["show", "--path", str(repo)])
    (repo / ".automagic" / "plan" / "graph.json").unlink()
    again = runner.invoke(app, ["show", "--path", str(repo)])
    assert again.exit_code == 0, again.output


def test_html_embeds_status_and_escapes_script_end():
    g = Graph(goal="G")
    g.add(Node(id="a", title="A", content="</script><b>x</b> __BREAKDOWN__"))
    g.add(Node(id="b", title="B", content="b", depends_on=["a"]))
    items = partition(g, Path(), Config())
    state = RunState(root_hash="old", items={items[0].id: ItemRun(status="done")})
    data = view_data(g, items, state)
    assert data["stale"] and data["items"][0]["status"] == "done"
    page = render_html(g, items, state)
    assert "</script><b>" not in page
    assert "__BREAKDOWN__" in page
    assert page.count("</script>") == 2


def test_html_renders_circular_graph_with_detail_panel():
    g = Graph(goal="G")
    g.add(Node(id="a", title="A", content="a"))
    page = render_html(g, partition(g, Path(), Config()), None)
    assert 'id="graph"' in page and 'id="panel"' in page and "createElementNS" in page
    assert "Planning topics" in page
    assert "Each graph node represents a work item containing one or more planning topics." in page


def test_html_distinguishes_eight_graph_nodes_from_nineteen_planning_topics(tmp_path):
    g = Graph(goal="Example plan")
    for i in range(19):
        g.add(
            Node(
                id=f"topic-{i:02d}",
                title=f"Topic {i}",
                content="Scope",
                est_lines=125 if i < 9 else 175,
            )
        )
    items = partition(g, tmp_path, Config(max_changed_lines=400, max_context_tokens=40_000))
    assert len(items) == 8
    page = render_html(g, items, None)
    assert "<h2>Work breakdown: 8 nodes covering 19 planning topics</h2>" in page
    data = view_data(g, items, None)
    assert len(data["items"]) == 8
    assert sum(len(item["nodes"]) for item in data["items"]) == 19


@pytest.mark.parametrize(
    ("topic_count", "expected"),
    [
        (0, "Work breakdown: 0 nodes covering 0 planning topics"),
        (1, "Work breakdown: 1 node covering 1 planning topic"),
        (2, "Work breakdown: 1 node covering 2 planning topics"),
    ],
)
def test_html_work_breakdown_handles_zero_and_singular_counts(tmp_path, topic_count, expected):
    g = Graph(goal="Example plan")
    for i in range(topic_count):
        g.add(Node(id=f"topic-{i}", title=f"Topic {i}", content="Scope"))
    items = partition(g, tmp_path, Config())
    assert f"<h2>{expected}</h2>" in render_html(g, items, None)


def test_amend_marks_touched_nodes_and_conflicts():
    g = Graph(goal="G")
    g.add(Node(id="db", title="DB", content="postgres"))
    g.add(Node(id="api", title="API", content="rest", depends_on=["db"]))
    g.add(Node(id="ui", title="UI", content="web"))
    for n in g.nodes:
        g.validate(n)
    reply = {
        "edits": [{"id": "db", "content": "sqlite"}, {"id": "ghost", "content": "x"}],
        "new_nodes": [{"id": "cache", "title": "Cache", "content": "c", "depends_on": ["db"]}],
        "conflicts": [{"node": "db", "reason": "was postgres"}],
    }
    result = amend_graph(g, FakeProvider(lambda _: json.dumps(reply)), "use sqlite")
    assert result.edited == ["db"] and result.added == ["cache"]
    assert result.affected == ["api"]
    assert set(g.invalid()) == {"db", "api", "cache"}
    assert g.nodes["ui"].validated_hash == g.node_hash("ui")
    assert result.conflicts == [{"node": "db", "reason": "was postgres"}]
    event = {"event": "amended", "round": 0, "change": "use sqlite", **vars(result)}
    assert "amended" in "\n".join(render_history([event]))


def test_amend_rolls_back_on_cycle():
    g = Graph(goal="G")
    g.add(Node(id="a", title="A", content="a"))
    g.add(Node(id="b", title="B", content="b", depends_on=["a"]))
    reply = {"edits": [{"id": "a", "depends_on": ["b"]}]}
    with pytest.raises(ValueError):
        amend_graph(g, FakeProvider(lambda _: json.dumps(reply)), "cycle")
    assert g.nodes["a"].depends_on == []
    g.check()


def test_plan_amend_cli_revalidates_and_logs(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, CHAIN)

    def reply(prompt: str) -> str:
        if prompt.startswith("You maintain an agreed plan"):
            return json.dumps({"edits": [{"id": "a", "content": "do a differently"}]})
        return json.dumps({"question": None})

    monkeypatch.setattr("automagic.cli.get_provider", lambda cfg: FakeProvider(reply))
    out = runner.invoke(app, ["plan", "--amend", "change a", "--path", str(repo), "--no-create"])
    assert out.exit_code == 0, out.output
    assert "Edited planning topics: a" in out.output
    assert "New planning topics: -" in out.output
    assert "Settled planning topics re-opened for review: b, c, d" in out.output
    graph = Graph.load(repo / ".automagic" / "plan" / "graph.json")
    assert graph.is_stable() and graph.nodes["a"].content == "do a differently"
    history = (repo / ".automagic" / "plan" / "history.jsonl").read_text()
    assert '"event": "amended"' in history
    assert "do a differently" in (repo / "docs" / "plan" / "a.md").read_text()
