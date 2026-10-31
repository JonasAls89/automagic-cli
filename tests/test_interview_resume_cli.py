import json
from pathlib import Path

import pytest
import typer
from test_interview_resume import graph_with_nodes, questioning_provider
from test_runner import make_repo
from typer.testing import CliRunner

from automagic.cli import app
from automagic.config import Config
from automagic.interview import run_interview
from automagic.merkle import Graph
from automagic.providers import FakeProvider

runner = CliRunner()


def saved_repo(tmp_path, *, paused: bool = True) -> tuple[Path, Path]:
    repo = make_repo(tmp_path, list(graph_with_nodes().nodes.values()))
    graph = graph_with_nodes()
    if paused:
        run_interview(graph, questioning_provider(), lambda _: None)
    path = repo / ".automagic" / "plan" / "graph.json"
    graph.save(path)
    return repo, path


def resume(repo: Path, input: str = "/done\n"):
    return runner.invoke(app, ["plan", "--resume", "--path", str(repo), "--no-create"], input=input)


def test_cli_resumes_same_question_and_ten_pending_topics_without_a_provider_call(
    tmp_path, monkeypatch
):
    repo = make_repo(tmp_path, list(graph_with_nodes(17).nodes.values()))
    graph = graph_with_nodes(17)
    path = repo / ".automagic" / "plan" / "graph.json"
    answers = iter([*["answer"] * 7, None])
    run_interview(
        graph,
        questioning_provider(),
        lambda _: next(answers),
        on_change=lambda current: current.save(path),
    )
    provider = FakeProvider([])
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = resume(repo)
    assert result.exit_code == 2, result.output
    assert "0/17 topics settled" in result.output
    assert "pass 1/8" in result.output and "10 topics pending this pass" in result.output
    assert "? Question for node-07?" in result.output
    assert "Plan paused during interview pass" in result.output and provider.prompts == []


def test_legacy_graph_receives_a_notice_once_and_retains_saved_decisions(tmp_path, monkeypatch):
    repo, path = saved_repo(tmp_path, paused=False)
    graph = Graph.load(path)
    path.write_text(graph.model_dump_json(exclude={"interview"}))
    provider = questioning_provider()
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    first = resume(repo)
    assert first.exit_code == 2 and "No saved interview position" in first.output
    assert Graph.load(path).nodes == graph.nodes
    provider = FakeProvider([])
    second = resume(repo)
    assert second.exit_code == 2 and "No saved interview position" not in second.output
    assert provider.prompts == []


@pytest.mark.parametrize("change", ["goal", "validation", "version", "question", "queue"])
def test_checkpoint_reset_requires_confirmation_and_preserves_graph_and_history(
    tmp_path, monkeypatch, change
):
    repo, path = saved_repo(tmp_path)
    data = json.loads(path.read_text())
    if change == "goal":
        data["goal"] = "Updated goal"
    elif change == "validation":
        data["nodes"]["node-00"]["validated_hash"] = "external validation state"
    elif change == "version":
        data["interview"]["version"] = 2
    elif change == "question":
        data["interview"]["question"] = None
    else:
        data["interview"]["pending"] = ["missing-node"]
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    graph_only = Graph.load(path, include_checkpoint=False)
    history = path.with_name("history.jsonl")
    previous = (
        json.dumps(
            {
                "event": "validated",
                "round": 0,
                "node": "node-00",
                "hash": graph_only.node_hash("node-00"),
            }
        )
        + "\n"
    )
    history.write_text(previous)
    provider = questioning_provider()
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    declined = resume(repo, "n\n")
    assert declined.exit_code == 2 and "cannot be continued" in declined.output
    assert "Nothing saved." in declined.output
    assert path.read_bytes() == original and provider.prompts == []
    accepted = resume(repo, "y\n/done\n")
    assert accepted.exit_code == 2, accepted.output
    assert Graph.load(path).model_dump(exclude={"interview"}) == graph_only.model_dump(
        exclude={"interview"}
    )
    assert history.read_text() == previous
    assert Graph.load(path).interview is not None


@pytest.mark.parametrize("data", ["not json", "[]", '{"nodes": []}'])
def test_corrupt_graph_is_an_error_not_an_offer_to_reset(tmp_path, monkeypatch, data):
    repo, path = saved_repo(tmp_path)
    path.write_text(data)
    provider = FakeProvider([])
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = resume(repo)
    assert result.exit_code == 1 and "error:" in result.output
    assert "Discard saved interview" not in result.output
    assert path.read_text() == data and provider.prompts == []


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), typer.Abort()])
def test_ctrl_c_preserves_pending_question_and_nonzero_exit(tmp_path, monkeypatch, failure):
    repo, path = saved_repo(tmp_path)
    provider = FakeProvider([])
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)

    def interrupt(question):
        raise failure

    monkeypatch.setattr("automagic.cli._ask", interrupt)
    original = path.read_bytes()
    result = resume(repo)
    assert result.exit_code == 1 and "Planning interrupted" in result.output
    assert path.read_bytes() == original and provider.prompts == []
    monkeypatch.setattr("automagic.cli._ask", lambda _: None)
    assert resume(repo).exit_code == 2
    assert provider.prompts == []


@pytest.mark.parametrize("boundary", ["before-snapshot", "before-history", "after-history"])
def test_history_and_answers_recover_once_across_commit_boundaries(tmp_path, monkeypatch, boundary):
    repo, path = saved_repo(tmp_path)
    history = path.with_name("history.jsonl")
    real_save, real_open = Graph.save, Path.open
    failed = False

    def save(graph, target, **kwargs):
        nonlocal failed
        checkpoint = graph.interview
        completed = (
            checkpoint is not None
            and checkpoint.phase == "checking"
            and graph.nodes["node-00"].content.startswith("Saved decision")
        )
        if completed and not failed and boundary != "before-history":
            has_event = checkpoint is not None and checkpoint.pending_event is not None
            if has_event == (boundary == "before-snapshot"):
                failed = True
                raise OSError("injected snapshot failure")
        return real_save(graph, target, **kwargs)

    def open_path(target, mode="r", *args, **kwargs):
        nonlocal failed
        if target == history and mode == "a" and boundary == "before-history" and not failed:
            failed = True
            raise OSError("injected history failure")
        return real_open(target, mode, *args, **kwargs)

    monkeypatch.setattr(Graph, "save", save)
    monkeypatch.setattr(Path, "open", open_path)
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: questioning_provider())
    first = resume(repo, "saved answer\n")
    assert first.exit_code == 1 and "injected" in first.output
    restored = Graph.load(path)
    restored.check_interview()
    assert restored.interview is not None
    if boundary == "before-snapshot":
        assert restored.interview.answer == "saved answer"
    else:
        assert restored.interview.pending_event is not None

    def recover(prompt):
        if prompt.startswith("Revise the node"):
            assert boundary == "before-snapshot" and "Answer: saved answer" in prompt
            return json.dumps({"content": "Saved decision for node-00"})
        return json.dumps({"question": None})

    provider = FakeProvider(recover)
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    second = resume(repo, "")
    assert second.exit_code == 0, second.output
    events = [json.loads(line) for line in history.read_text().splitlines()]
    assert [event["event"] for event in events] == ["answered", "validated"]
    assert events[0]["answer"] == "saved answer"
    assert len({event["id"] for event in events}) == 2
    assert Graph.load(path).interview is None


@pytest.mark.parametrize("unsupported_checkpoint", [False, True])
def test_show_omits_checkpoint_from_exports_without_changing_active_snapshot(
    tmp_path, unsupported_checkpoint
):
    repo, path = saved_repo(tmp_path)
    if unsupported_checkpoint:
        data = json.loads(path.read_text())
        data["interview"]["version"] = 2
        path.write_text(json.dumps(data))
    original = path.read_bytes()
    result = runner.invoke(app, ["show", "--path", str(repo)])
    assert result.exit_code == 0, result.output
    assert path.read_bytes() == original
    exported = json.loads((repo / "docs" / "plan" / "graph.json").read_text())
    assert "interview" not in exported


def test_resume_from_committed_graph_creates_a_local_checkpoint(tmp_path, monkeypatch):
    repo, path = saved_repo(tmp_path)
    graph = Graph.load(path)
    published = repo / "docs" / "plan" / "graph.json"
    graph.save(published, include_checkpoint=False)
    path.unlink()
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: questioning_provider())
    result = resume(repo)
    assert result.exit_code == 2 and "No saved interview position" in result.output
    assert Graph.load(path).interview is not None
    assert "interview" not in json.loads(published.read_text())


def test_new_plan_discards_old_cursor_and_history(tmp_path, monkeypatch):
    repo, path = saved_repo(tmp_path)
    history = path.with_name("history.jsonl")
    history.write_text("obsolete history\n")
    provider = FakeProvider(
        [
            json.dumps({"nodes": [{"id": "new", "title": "New", "content": "new decision"}]}),
            json.dumps({"question": None}),
        ]
    )
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = runner.invoke(app, ["plan", "New goal", "--path", str(repo), "--no-create"], input="")
    assert result.exit_code == 0, result.output
    graph = Graph.load(path)
    assert graph.interview is None and graph.goal == "New goal" and set(graph.nodes) == {"new"}
    assert "obsolete history" not in history.read_text()


def test_amendment_starts_a_fresh_interview_not_an_old_question(tmp_path, monkeypatch):
    repo, path = saved_repo(tmp_path)
    graph = Graph.load(path)
    graph.validate("node-00")
    graph.save(path)
    provider = FakeProvider(
        [
            json.dumps({"edits": [{"id": "node-00", "content": "amended"}]}),
            json.dumps({"question": None}),
        ]
    )
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = runner.invoke(
        app, ["plan", "--amend", "Change it", "--path", str(repo), "--no-create"], input=""
    )
    assert result.exit_code == 0, result.output
    assert Graph.load(path).interview is None
    assert Graph.load(path).nodes["node-00"].content == "amended"
    assert "Planning settled after 1 interview pass, 0 questions." in result.output


def test_cli_round_limit_notice_and_changed_configuration_extend_only_after_exhaustion(
    tmp_path, monkeypatch
):
    repo, path = saved_repo(tmp_path, paused=False)
    config = repo / ".automagic" / "config.toml"
    config.write_text(Config(max_rounds=1).to_toml())
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: questioning_provider())
    first = resume(repo, "answer\n")
    assert first.exit_code == 2, first.output
    assert "interview pass limit reached after pass 1" in first.output
    assert "resume grants 1 more interview pass;" in first.output
    config.write_text(Config(max_rounds=2).to_toml())
    second = resume(repo)
    assert second.exit_code == 2 and "pass 2/3" in second.output
    graph = Graph.load(path)
    assert graph.interview is not None
    assert (graph.interview.round_no, graph.interview.round_limit) == (2, 3)


def test_amendment_cannot_discard_pending_completed_history(tmp_path, monkeypatch):
    repo, path = saved_repo(tmp_path, paused=False)
    graph = Graph.load(path)

    def fail_history(event):
        raise OSError("history unavailable")

    with pytest.raises(OSError):
        run_interview(
            graph,
            FakeProvider([json.dumps({"question": None})]),
            lambda _: None,
            on_change=lambda current: current.save(path),
            on_event=fail_history,
        )
    original = path.read_bytes()
    provider = FakeProvider([])
    monkeypatch.setattr("automagic.cli.get_provider", lambda _: provider)
    result = runner.invoke(
        app, ["plan", "--amend", "Change it", "--path", str(repo), "--no-create"], input=""
    )
    assert result.exit_code == 1 and "pending interview history" in result.output
    assert path.read_bytes() == original and provider.prompts == []
    assert resume(repo, "").exit_code == 0
    assert provider.prompts == []


def test_interrupting_new_plan_generation_does_not_replace_old_saved_plan(tmp_path, monkeypatch):
    repo, path = saved_repo(tmp_path)
    original = path.read_bytes()

    def interrupt(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("automagic.cli.get_provider", lambda _: FakeProvider(interrupt))
    result = runner.invoke(
        app, ["plan", "Different goal", "--path", str(repo), "--no-create"], input=""
    )
    assert result.exit_code == 1 and "before the initial graph was saved" in result.output
    assert path.read_bytes() == original
