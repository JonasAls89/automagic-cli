import json

import pytest
from typer.testing import CliRunner

from automagic.cli import app
from automagic.config import Config
from automagic.interview import run_interview, seed_graph
from automagic.merkle import Graph, Node
from automagic.providers import FakeProvider
from automagic.render import hash_chain, render_mermaid, render_plan
from automagic.sizing import partition

runner = CliRunner()


def test_interview_events_record_invalidated_ancestors():
    seed = [
        {"id": "db", "title": "DB", "content": "pick db"},
        {"id": "api", "title": "API", "content": "api", "depends_on": ["db"]},
    ]
    asked = {"db": ["Which db?"]}

    def reply(prompt: str) -> str:
        if prompt.startswith("You are a planning agent"):
            return json.dumps({"nodes": seed})
        node_id = prompt.split("Node ", 1)[1].split(":", 1)[0]
        if prompt.startswith("You are a rigorous interviewer"):
            queue = asked.get(node_id, [])
            return json.dumps({"question": queue.pop(0) if queue else None})
        return json.dumps({"content": "db is postgres"})

    provider = FakeProvider(reply)
    graph = seed_graph(provider, "goal", "ctx")
    # Validate api first so the db change visibly invalidates it.
    graph.validate("api")
    events: list[dict] = []
    run_interview(graph, provider, lambda q: "postgres", on_event=events.append)
    answered = [e for e in events if e["event"] == "answered"]
    assert len(answered) == 1
    assert answered[0]["old_hash"] != answered[0]["new_hash"]
    assert answered[0]["invalidated"] == ["api"]
    assert {e["node"] for e in events if e["event"] == "validated"} == {"db", "api"}


def make_graph() -> Graph:
    g = Graph(goal="Demo")
    g.add(Node(id="a-b", title='A "quoted"', content="do a", est_lines=10))
    g.add(Node(id="c", title="C", content="do c", depends_on=["a-b"], est_lines=900))
    for nid in g.nodes:
        g.validate(nid)
    return g


def test_mermaid_has_edges_subgraphs_and_oversized_style(tmp_path):
    g = make_graph()
    items = partition(g, tmp_path, Config())
    text = render_mermaid(g, items)
    assert text.startswith("flowchart TD")
    assert "n0 --> n1" in text and "n1 --> root" in text
    assert "subgraph wi_01" in text and "#quot;" in text
    assert "stroke:#d00" in text


def test_plan_contains_mermaid_and_history(tmp_path):
    g = make_graph()
    items = partition(g, tmp_path, Config())
    history = [
        {
            "event": "answered",
            "round": 1,
            "node": "c",
            "question": "a | b?",
            "answer": "yes",
            "old_hash": "a" * 64,
            "new_hash": "b" * 64,
            "invalidated": [],
        }
    ]
    text = render_plan(g, items, Config(), history)
    assert "```mermaid" in text and "## Interview history" in text
    assert "## Work breakdown: 2 work items covering 2 planning topics" in text
    assert "| Work item | Planning topics |" in text
    assert "| Interview pass | Planning topic |" in text
    assert "a \\| b?" in text and g.root_hash() in text


def test_hash_chain_marks_invalid():
    g = make_graph()
    g.nodes["a-b"].content = "changed"
    chain = hash_chain(g, "c")
    assert chain[0].endswith("(INVALID)") and chain[1].startswith("  a-b")


@pytest.mark.parametrize("open_html", [False, True])
@pytest.mark.parametrize("legacy_content", [None, b"Legacy overview with manual edits\n"])
def test_show_command(tmp_path, monkeypatch, open_html, legacy_content):
    g = make_graph()
    plan_dir = tmp_path / ".automagic" / "plan"
    graph_path = plan_dir / "graph.json"
    g.save(graph_path)
    history = [{"event": "validated", "round": 1, "node": "c", "hash": g.node_hash("c")}]
    history_path = plan_dir / "history.jsonl"
    history_path.write_text(json.dumps(history[0]) + "\n")
    saved_graph, saved_history = graph_path.read_bytes(), history_path.read_bytes()
    overview = plan_dir / "plan.md"
    if legacy_content is not None:
        overview.write_bytes(legacy_content)
    opened: list[str] = []
    monkeypatch.setattr("automagic.cli.webbrowser.open", opened.append)
    monkeypatch.setattr(
        "automagic.cli.get_provider", lambda _: pytest.fail("show must not call an AI provider")
    )
    cfg = Config()
    expected = render_plan(g, partition(g, tmp_path, cfg), cfg, history)
    args = ["show", "--path", str(tmp_path), *(["--open"] if open_html else [])]
    for _ in range(2):
        out = runner.invoke(app, args)
        assert out.exit_code == 0, out.output
        assert expected in out.output
        assert "Plan written to" not in out.output
        assert (overview.read_bytes() if overview.exists() else None) == legacy_content
        assert graph_path.read_bytes() == saved_graph
        assert history_path.read_bytes() == saved_history
    node = runner.invoke(app, ["show", "--path", str(tmp_path), "--node", "c"])
    assert "a-b" in node.output
    bad = runner.invoke(app, ["show", "--path", str(tmp_path), "--node", "zzz"])
    assert bad.exit_code == 1
    assert opened == [(plan_dir / "plan.html").resolve().as_uri()] * (2 if open_html else 0)
