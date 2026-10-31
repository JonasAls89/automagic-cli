import json

import pytest

from automagic.config import Config
from automagic.interview import run_interview, seed_graph, split_node
from automagic.merkle import Graph, GraphError, Node
from automagic.providers import FakeProvider
from automagic.sizing import oversized_nodes, partition


def chain() -> Graph:
    g = Graph(goal="g")
    g.add(Node(id="a", title="A", content="a"))
    g.add(Node(id="b", title="B", content="b", depends_on=["a"]))
    g.add(Node(id="c", title="C", content="c", depends_on=["b"]))
    return g


def test_change_invalidates_node_and_ancestors_only():
    g = chain()
    for n in g.nodes:
        g.validate(n)
    assert g.is_stable()
    root = g.root_hash()
    g.nodes["b"].content = "changed"
    assert g.invalid() == ["b", "c"]
    assert g.root_hash() != root


def test_hash_is_whitespace_and_order_insensitive():
    g = chain()
    h = g.node_hash("a")
    g.nodes["a"].content = "  a \n"
    assert g.node_hash("a") == h


def test_cycle_and_unknown_dependency_rejected():
    g = chain()
    with pytest.raises(GraphError):
        g.add(Node(id="a", title="A", content="a", depends_on=["c"]))
    with pytest.raises(GraphError):
        g.add(Node(id="z", title="Z", content="z", depends_on=["nope"]))


def test_save_load_roundtrip(tmp_path):
    g = chain()
    g.validate("a")
    g.save(tmp_path / "p" / "g.json")
    assert Graph.load(tmp_path / "p" / "g.json") == g


def scripted(seed_nodes, questions):
    """questions: node id -> list of questions (then null)."""

    def reply(prompt: str) -> str:
        if prompt.startswith("You are a planning agent"):
            return json.dumps({"nodes": seed_nodes})
        node_id = prompt.split("Node ", 1)[1].split(":", 1)[0]
        if prompt.startswith("You are a rigorous interviewer"):
            queue = questions.get(node_id, [])
            return json.dumps({"question": queue.pop(0) if queue else None})
        if prompt.startswith("Revise the node"):
            return json.dumps({"content": f"{node_id} revised by answer"})
        raise AssertionError(prompt)

    return FakeProvider(reply)


SEED = [
    {"id": "db", "title": "DB", "content": "pick db"},
    {"id": "api", "title": "API", "content": "api", "depends_on": ["db"]},
]


def test_interview_reasks_ancestors_until_stable():
    provider = scripted(SEED, {"db": ["Which db?"]})
    g = seed_graph(provider, "goal", "ctx")
    answers = []
    result = run_interview(g, provider, lambda q: answers.append(q) or "postgres", max_rounds=5)
    assert result.converged and g.is_stable()
    assert answers == ["Which db?"]
    assert g.nodes["db"].content == "db revised by answer"
    # api was invalidated by db's change and had to be re-questioned
    api_prompts = [
        p for p in provider.prompts if p.startswith("You are a rigorous") and "Node api" in p
    ]
    assert len(api_prompts) >= 1


def test_interview_stops_on_user_done_and_on_round_cap():
    provider = scripted(SEED, {"db": ["q"] * 10})
    g = seed_graph(provider, "goal", "ctx")
    assert not run_interview(g, provider, lambda q: None).converged
    g = seed_graph(provider, "goal", "ctx")
    result = run_interview(g, provider, lambda q: "a", max_rounds=2)
    assert not result.converged and result.rounds == 2


def test_split_moves_dependents_to_last_part():
    g = chain()
    provider = FakeProvider(
        [json.dumps({"parts": [{"title": "b1", "content": "x"}, {"title": "b2", "content": "y"}]})]
    )
    ids = split_node(g, provider, "b", 100)
    assert ids == ["b-part-1", "b-part-2"]
    assert g.nodes["c"].depends_on == ["b-part-2"]
    assert g.nodes["b-part-1"].depends_on == ["a"]


def test_partition_respects_budgets(tmp_path):
    g = Graph(goal="g")
    prev: list[str] = []
    for i in range(6):
        g.add(Node(id=f"n{i}", title="t", content="c", depends_on=prev, est_lines=150))
        prev = [f"n{i}"]
    cfg = Config(max_changed_lines=400, max_context_tokens=40_000)
    items = partition(g, tmp_path, cfg)
    assert [i.node_ids for i in items] == [["n0", "n1"], ["n2", "n3"], ["n4", "n5"]]
    assert all(i.lines <= 400 and not i.oversized for i in items)
    assert items[1].depends_on == ["wi-01"]


def test_partition_flags_oversized_and_counts_files(tmp_path):
    (tmp_path / "big.txt").write_text("x" * 200_000)
    g = Graph(goal="g")
    g.add(Node(id="small", title="s", content="c", est_lines=10))
    g.add(Node(id="big", title="b", content="c", files=["big.txt"], est_lines=10))
    g.add(Node(id="huge", title="h", content="c", est_lines=900))
    cfg = Config()
    assert oversized_nodes(g, tmp_path, cfg) == ["big", "huge"]
    items = partition(g, tmp_path, cfg)
    assert [(i.node_ids, i.oversized) for i in items] == [
        (["big"], True),
        (["huge"], True),
        (["small"], False),
    ]
