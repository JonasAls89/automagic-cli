import json
from pathlib import Path

import pytest
from test_interview_resume import graph_with_nodes, questioning_provider

from automagic.interview import run_interview
from automagic.merkle import Graph, GraphError, ValidatedEvent


def paused_graph() -> Graph:
    graph = graph_with_nodes()
    run_interview(graph, questioning_provider(), lambda _: None)
    return graph


def test_checkpoint_roundtrip_and_graph_only_exports(tmp_path):
    graph = graph_with_nodes()
    root_hash = graph.root_hash()
    node_hash = graph.node_hash("node-00")
    run_interview(graph, questioning_provider(), lambda _: None)
    assert graph.interview is not None
    assert graph.root_hash() == root_hash and graph.node_hash("node-00") == node_hash
    fingerprint = graph.interview_hash()
    graph.interview.question = "An updated saved question?"
    assert graph.interview_hash() == fingerprint
    graph.check_interview()

    path = tmp_path / "graph.json"
    graph.save(path)
    assert Graph.load(path) == graph
    assert Graph.load(path, include_checkpoint=False).interview is None
    exported = tmp_path / "published.json"
    graph.save(exported, include_checkpoint=False)
    assert "interview" not in json.loads(exported.read_text())
    assert Graph.load(exported).nodes == graph.nodes
    assert graph.interview is not None


@pytest.mark.parametrize("field", ["goal", "identity", "validation"])
def test_checkpoint_fingerprint_covers_non_merkle_graph_state(field):
    graph = paused_graph()
    root_hash = graph.root_hash()
    if field == "goal":
        graph.goal = "changed"
    elif field == "identity":
        graph.nodes["node-00"].id = "changed"
    else:
        graph.validate("node-00")
    assert graph.root_hash() == root_hash
    with pytest.raises(GraphError, match="does not match"):
        graph.check_interview()


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 2},
        {"round_no": 9},
        {"pending": ["node-00", "node-00"]},
        {"pending": []},
        {"phase": "checking"},
        {"phase": "revising"},
        {"answer": "not revising"},
        {"unexpected": True},
        {"pending_event": {"event": "unknown"}},
        {
            "pending_event": {
                "id": "event",
                "event": "validated",
                "round": 1,
                "node": "node-00",
                "hash": "hash",
            }
        },
    ],
)
def test_malformed_checkpoint_does_not_prevent_explicit_graph_only_loading(tmp_path, changes):
    graph = paused_graph()
    data = graph.model_dump()
    data["interview"].update(changes)
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        Graph.load(path)
    assert Graph.load(path, include_checkpoint=False).nodes == graph.nodes


@pytest.mark.parametrize(
    ("node", "round_no", "event_hash"),
    [
        ("missing", 1, "correct"),
        ("node-00", 2, "correct"),
        ("node-00", 1, "wrong"),
        ("node-00", 1, "unvalidated"),
    ],
)
def test_checkpoint_rejects_an_event_that_does_not_match_its_graph(node, round_no, event_hash):
    graph = paused_graph()
    if event_hash != "unvalidated":
        graph.validate("node-00")
    checkpoint = graph.interview
    assert checkpoint is not None
    checkpoint.phase, checkpoint.question, checkpoint.pending = "checking", None, []
    checkpoint.pending_event = ValidatedEvent(
        id="event",
        event="validated",
        round=round_no,
        node=node,
        hash=graph.node_hash("node-00") if event_hash != "wrong" else event_hash,
    )
    checkpoint.graph_hash = graph.interview_hash()
    with pytest.raises(GraphError, match="event does not match"):
        graph.check_interview()


@pytest.mark.parametrize("failure", [OSError("replace failed"), KeyboardInterrupt()])
def test_failed_atomic_save_preserves_previous_snapshot_and_cleans_temporary_files(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "graph.json"
    graph = paused_graph()
    graph.save(path)
    original = path.read_bytes()
    graph.goal = "unsaved change"

    def fail(*args):
        raise failure

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(type(failure)):
        graph.save(path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_temporary_file_creation_failure_preserves_previous_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "graph.json"
    paused_graph().save(path)
    original = path.read_bytes()

    def fail(**kwargs):
        raise OSError("cannot create temporary file")

    monkeypatch.setattr("automagic.merkle.tempfile.NamedTemporaryFile", fail)
    with pytest.raises(OSError, match="cannot create"):
        paused_graph().save(path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
