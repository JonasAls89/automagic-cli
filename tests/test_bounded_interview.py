import json

import pytest

from automagic.durability import InterviewDurability
from automagic.interview import InterviewCorrection, run_interview
from automagic.merkle import Graph, Node
from automagic.providers import FakeProvider, ProviderError


def draft():
    return Graph(
        goal="Build a private notes app",
        nodes={"scope": Node(id="scope", title="Scope", content="Private notes")},
    )


def assessment(*, questions=(), settled=(), ready=False):
    return json.dumps(
        {
            "criteria": {
                name: {
                    "status": "supported" if ready else "unresolved",
                    "evidence": "Specified in the goal" if ready else "Needs clarification",
                }
                for name in ("outcome", "users", "scope", "constraints", "acceptance")
            },
            "blockers": [] if ready else ["Who may read notes?"],
            "unknowns": [],
            "settled": list(settled),
            "questions": list(questions),
        }
    )


def question(node="scope", key="readers"):
    return {
        "node": node,
        "key": key,
        "question": "Who may read notes?" if key == "readers" else f"Decide {key}?",
        "recommendation": "Only the author",
        "tradeoff": "No shared notebooks",
        "reopen_reason": None,
    }


def test_budget_survives_saved_answer_and_resume_without_silent_extension(tmp_path):
    graph = draft()
    path = tmp_path / "graph.json"
    first = run_interview(
        graph,
        FakeProvider(
            [assessment(questions=[question()]), '{"content":"Author only"}', assessment()]
        ),
        lambda _: "Only me",
        max_questions=1,
        durability=InterviewDurability(path),
    )
    assert not first.converged and first.stop_reason == "question_budget"
    restored = Graph.load(path)
    resumed = run_interview(restored, FakeProvider([]), lambda _: None, max_questions=99)
    assert resumed.stop_reason == "question_budget"
    assert restored.interview.planning.answered == 1
    assert restored.interview.planning.question_limit == 1


def test_complete_goal_needs_durable_explicit_approval_even_with_zero_allowance(tmp_path):
    graph = draft()
    path = tmp_path / "graph.json"
    first = run_interview(
        graph,
        FakeProvider([assessment(settled=["scope"], ready=True)]),
        lambda _: (_ for _ in ()).throw(AssertionError("No interview question needed")),
        max_questions=0,
        confirm=lambda _: None,
        durability=InterviewDurability(path),
    )
    assert not first.converged
    summaries = []
    restored = Graph.load(path)
    resumed = run_interview(
        restored,
        FakeProvider([]),
        lambda _: None,
        max_questions=0,
        confirm=lambda summary: summaries.append(summary) or "/approve",
    )
    assert resumed.converged and restored.is_stable()
    assert "Outcome:" in summaries[0] and "Acceptance:" in summaries[0]
    assert restored.interview.planning.phase == "approved"


def test_dependent_questions_cannot_be_batched_before_prerequisites_are_settled(tmp_path):
    graph = draft()
    graph.nodes["sharing"] = Node(
        id="sharing", title="Sharing", content="Undecided", depends_on=["scope"]
    )
    provider = FakeProvider(
        lambda _: assessment(questions=[question(), question("sharing", "sharing")])
    )
    path = tmp_path / "graph.json"
    with pytest.raises(ProviderError, match="prerequisite"):
        run_interview(
            graph,
            provider,
            lambda _: pytest.fail("Invalid batch must not reach the user"),
            max_questions=12,
            durability=InterviewDurability(path),
        )
    assert len(provider.prompts) == 3
    restored = Graph.load(path)
    state = restored.interview.planning
    assert state.phase == "assessing" and state.assessment is None
    assert state.answered == 0 and state.remaining == 12
    assert state.batch == [] and state.history == []
    assert set(restored.invalid()) == {"scope", "sharing"}


@pytest.mark.parametrize(
    ("questions", "settled", "feedback"),
    [
        ([question(), question("sharing", "sharing")], [], "unsettled prerequisite"),
        ([question("sharing", "sharing")], [], "unsettled prerequisite"),
        ([], ["sharing"], "unsettled prerequisite"),
        ([question()], ["policy", "sharing"], "unsettled prerequisite"),
        ([question(), question(key="hosting")], [], "duplicate topics"),
        ([question()], ["scope", "policy"], "settled topics"),
    ],
)
@pytest.mark.parametrize("invalid_attempts", [1, 2])
def test_invalid_batch_is_corrected_before_mutating_the_graph(
    questions, settled, feedback, invalid_attempts
):
    graph = draft()
    graph.nodes["policy"] = Node(
        id="policy", title="Policy", content="Undecided", depends_on=["scope"]
    )
    graph.nodes["sharing"] = Node(
        id="sharing", title="Sharing", content="Undecided", depends_on=["policy"]
    )
    original_hash = graph.root_hash()
    provider = FakeProvider(
        [assessment(questions=questions, settled=settled)] * invalid_attempts
        + [assessment(questions=[question()])]
    )
    prompts = []
    result = run_interview(
        graph,
        provider,
        lambda prompt: prompts.append(prompt),
        max_questions=12,
    )
    assert result.paused and result.questions_asked == 0
    assert len(provider.prompts) == invalid_attempts + 1 and feedback in provider.prompts[1]
    assert "Previous assessment rejected" in provider.prompts[1]
    assert "Who may read notes?" in prompts[0] and "Decide sharing?" not in prompts[0]
    assert graph.root_hash() == original_hash
    state = graph.interview.planning
    assert state.answered == 0 and state.remaining == 12
    assert [item.node for item in state.batch] == ["scope"]
    assert set(graph.invalid()) == {"scope", "policy", "sharing"}


@pytest.mark.parametrize("already_settled", [False, True])
def test_supported_prerequisites_allow_a_dependent_question_without_reassessment(already_settled):
    graph = draft()
    graph.nodes["sharing"] = Node(
        id="sharing", title="Sharing", content="Undecided", depends_on=["scope"]
    )
    if already_settled:
        graph.validate("scope")
    provider = FakeProvider(
        [
            assessment(
                questions=[question("sharing", "sharing")],
                settled=[] if already_settled else ["scope"],
            )
        ]
    )
    result = run_interview(graph, provider, lambda _: None, max_questions=12)
    assert result.paused and len(provider.prompts) == 1
    assert graph.invalid() == ["sharing"]
    assert [item.node for item in graph.interview.planning.batch] == ["sharing"]


def test_rejected_batches_preserve_saved_answers_and_allow_resuming(tmp_path):
    graph = draft()
    graph.nodes["sharing"] = Node(
        id="sharing", title="Sharing", content="Undecided", depends_on=["scope"]
    )
    path = tmp_path / "graph.json"
    with pytest.raises(ProviderError, match="after 3 attempts"):
        run_interview(
            graph,
            FakeProvider(
                [
                    assessment(questions=[question()]),
                    '{"content":"Author only","resolved":true}',
                    *[assessment(questions=[question("sharing", "sharing")])] * 3,
                ]
            ),
            lambda _: "Only me",
            max_questions=12,
            durability=InterviewDurability(path),
        )
    restored = Graph.load(path)
    state = restored.interview.planning
    assert state.phase == "assessing" and state.answered == 1
    assert state.remaining == 11 and state.completed_rounds == 1
    assert state.history[0].answer == "Only me"
    assert restored.nodes["scope"].content == "Author only"
    result = run_interview(
        restored,
        FakeProvider([assessment(questions=[question("sharing", "sharing")], settled=["scope"])]),
        lambda _: None,
        max_questions=99,
        durability=InterviewDurability(path),
    )
    assert result.paused and result.questions_asked == 0
    saved = Graph.load(path).interview.planning
    assert saved.question_limit == 12 and saved.remaining == 11
    assert [item.node for item in saved.batch] == ["sharing"]


@pytest.mark.parametrize("during_correction", [False, True])
def test_provider_execution_failure_is_not_retried(tmp_path, during_correction):
    graph = draft()
    graph.nodes["sharing"] = Node(
        id="sharing", title="Sharing", content="Undecided", depends_on=["scope"]
    )

    def reply(_):
        if during_correction and len(provider.prompts) == 1:
            return assessment(questions=[question("sharing", "sharing")])
        raise ProviderError("offline")

    provider = FakeProvider(reply)
    path = tmp_path / "graph.json"
    with pytest.raises(ProviderError, match="offline"):
        run_interview(
            graph,
            provider,
            lambda _: pytest.fail("No valid batch"),
            max_questions=12,
            durability=InterviewDurability(path),
        )
    assert len(provider.prompts) == 1 + during_correction
    state = Graph.load(path).interview.planning
    assert state.phase == "assessing" and state.remaining == 12 and state.answered == 0


def test_batch_pause_retains_individual_answers_and_explicit_deferral(tmp_path):
    graph = draft()
    graph.nodes["platform"] = Node(id="platform", title="Platform", content="Undecided")
    path = tmp_path / "graph.json"
    answers = iter(["/skip", None])
    prompts = []
    provider = FakeProvider([assessment(questions=[question(), question("platform", "platform")])])
    first = run_interview(
        graph,
        provider,
        lambda prompt: prompts.append(prompt) or next(answers),
        max_questions=3,
        durability=InterviewDurability(path),
    )
    assert first.paused
    assert "1." in prompts[0] and "2." in prompts[0]
    restored = Graph.load(path)
    resumed = run_interview(
        restored,
        FakeProvider([]),
        lambda _: None,
        max_questions=100,
    )
    assert resumed.paused and restored.nodes["scope"].content == "Private notes"
    assert restored.interview.planning.answered == 1
    assert restored.interview.planning.remaining == 2
    assert restored.interview.planning.history[0].deferred


def test_exhaustion_requires_explicit_six_question_extension(tmp_path):
    graph = draft()
    path = tmp_path / "graph.json"
    first = run_interview(
        graph,
        FakeProvider(
            [
                assessment(questions=[question()]),
                '{"content":"Author only","resolved":true}',
                assessment(questions=[question(key="hosting")]),
            ]
        ),
        lambda _: "Only me",
        max_questions=1,
        durability=InterviewDurability(path),
    )
    assert first.stop_reason == "question_budget"
    choices = []
    resumed = run_interview(
        Graph.load(path),
        FakeProvider([]),
        lambda _: None,
        max_questions=1,
        continue_interview=lambda reason, summary: choices.append((reason, summary)) or True,
        durability=InterviewDurability(path),
    )
    assert resumed.paused and choices[0][0] == "question_budget"
    assert "Who may read notes?" in choices[0][1]
    saved = Graph.load(path).interview.planning
    assert (saved.question_limit, saved.answered, saved.remaining) == (7, 1, 6)


@pytest.mark.parametrize(("passes", "expected"), [(1, "pass_limit"), (8, "no_progress")])
def test_unchanged_answers_pause_for_the_actual_safeguard(passes, expected):
    provider = FakeProvider(
        [
            assessment(questions=[question()]),
            '{"content":"Reworded"}',
            assessment(questions=[question(key="next")]),
            '{"content":"Reworded again"}',
            assessment(questions=[question(key="third")]),
        ]
    )
    result = run_interview(
        draft(),
        provider,
        lambda _: "Maybe",
        max_questions=12,
        max_rounds=passes,
    )
    assert not result.converged and result.stop_reason == expected


@pytest.mark.parametrize(
    ("replies", "explanation"),
    [
        ([assessment()], "no next question"),
        (
            [
                assessment(questions=[question()]),
                '{"content":"Reworded"}',
                assessment(questions=[question(key="next")]),
                '{"content":"Reworded again"}',
                assessment(questions=[question(key="third")]),
            ],
            "Two consecutive completed questioning rounds",
        ),
        (
            [
                assessment(questions=[question()]),
                '{"content":"Author only","resolved":true}',
                assessment(questions=[question()]),
            ],
            "previously answered question",
        ),
    ],
)
def test_no_progress_explains_the_specific_cause(replies, explanation):
    summaries = []
    result = run_interview(
        draft(),
        FakeProvider(replies),
        lambda _: "Only me",
        max_questions=12,
        continue_interview=lambda reason, summary: summaries.append(summary) or False,
    )
    assert result.stop_reason == "no_progress"
    assert explanation in summaries[-1]
    assert "No decisions settled." not in summaries[-1]


@pytest.mark.parametrize("reason", ["question_budget", "pass_limit", "no_progress"])
def test_active_pause_correction_reuses_durable_flow_without_extending(tmp_path, reason):
    graph = draft()
    if reason == "question_budget":
        replies = [assessment(questions=[question()])]
    elif reason == "pass_limit":
        replies = [
            assessment(questions=[question()]),
            '{"content":"Reworded"}',
            assessment(questions=[question(key="next")]),
        ]
    else:
        replies = [assessment()]
    replies.extend(
        [
            '{"edits":[{"id":"scope","content":"Shared notes"}]}',
            assessment(settled=["scope"], ready=True),
        ]
    )
    choices = []
    limit = 0 if reason == "question_budget" else 12
    passes = 1 if reason == "pass_limit" else 8
    result = run_interview(
        graph,
        FakeProvider(replies),
        lambda _: "Maybe",
        max_questions=limit,
        max_rounds=passes,
        continue_interview=lambda actual, summary: (
            choices.append(actual) or InterviewCorrection("Make it shared notes")
        ),
        confirm=lambda _: None,
        durability=InterviewDurability(tmp_path / "graph.json"),
    )
    assert choices == [reason] and result.paused
    checkpoint = Graph.load(tmp_path / "graph.json").interview
    state = checkpoint.planning
    assert state.phase == "confirming" and state.approved_hash is None
    assert state.corrections == ["Make it shared notes"]
    assert state.question_limit == limit and checkpoint.round_limit == passes
    assert state.answered == (1 if reason == "pass_limit" else 0)
    assert graph.nodes["scope"].content == "Shared notes"


@pytest.mark.parametrize("choice", ["yes", InterviewCorrection("   ")])
def test_invalid_continuation_does_not_grant_an_extension(tmp_path, choice):
    path = tmp_path / "graph.json"
    with pytest.raises(ValueError, match=r"continuation requires|correction cannot be empty"):
        run_interview(
            draft(),
            FakeProvider([assessment(questions=[question()])]),
            lambda _: pytest.fail("No question allowance"),
            max_questions=0,
            continue_interview=lambda reason, summary: choice,
            durability=InterviewDurability(path),
        )
    state = Graph.load(path).interview.planning
    assert state.question_limit == 0 and state.stop_reason == "question_budget"


def test_repeated_answered_question_needs_an_explained_new_gap():
    result = run_interview(
        draft(),
        FakeProvider(
            [
                assessment(questions=[question()]),
                '{"content":"Author only","resolved":true}',
                assessment(questions=[question()]),
            ]
        ),
        lambda _: "Only me",
        max_questions=12,
    )
    assert result.questions_asked == 1 and result.stop_reason == "no_progress"


def test_known_blocker_prevents_approval_even_if_model_supports_all_criteria():
    data = json.loads(assessment(settled=["scope"], ready=True))
    data["blockers"] = ["Who may read notes?"]
    result = run_interview(
        draft(),
        FakeProvider([json.dumps(data)]),
        lambda _: None,
        max_questions=0,
        confirm=lambda _: pytest.fail("A known blocker must prevent approval"),
    )
    assert result.stop_reason == "question_budget"


@pytest.mark.parametrize("answer", ["/skip", "I don't know.", "I don\u2019t know"])
def test_explicit_deferral_is_not_revised_into_agreement(answer):
    graph = draft()
    result = run_interview(
        graph,
        FakeProvider([assessment(questions=[question()]), assessment()]),
        lambda _: answer,
        max_questions=1,
    )
    assert result.stop_reason == "question_budget"
    assert graph.nodes["scope"].content == "Private notes"
    assert graph.interview.planning.history[0].deferred


@pytest.mark.parametrize(
    "revision",
    [
        {"resolved": "yes", "content": "Invented"},
        {"files": "not-a-list", "content": "Invented"},
        {"new_nodes": [{"id": "empty", "title": "Extra", "content": ""}]},
    ],
)
def test_invalid_revision_keeps_submitted_answer_recoverable(tmp_path, revision):
    path = tmp_path / "graph.json"
    with pytest.raises(ProviderError, match="revision"):
        run_interview(
            draft(),
            FakeProvider([assessment(questions=[question()]), json.dumps(revision)]),
            lambda _: "Only me",
            max_questions=12,
            durability=InterviewDurability(path),
        )
    restored = Graph.load(path)
    assert restored.nodes["scope"].content == "Private notes"
    assert restored.interview.planning.answered == 1
    recovered = run_interview(
        restored,
        FakeProvider(
            [
                '{"content":"Author only","resolved":true}',
                assessment(settled=["scope"], ready=True),
            ]
        ),
        lambda _: pytest.fail("The answer is already saved"),
        max_questions=12,
        confirm=lambda _: "/approve",
        durability=InterviewDurability(path),
    )
    assert recovered.converged and restored.interview.planning.answered == 1


def test_reopening_a_settled_prerequisite_still_prevents_dependent_batching():
    graph = draft()
    graph.nodes["sharing"] = Node(
        id="sharing", title="Sharing", content="Undecided", depends_on=["scope"]
    )
    graph.validate("scope")
    with pytest.raises(ProviderError, match="prerequisite"):
        run_interview(
            graph,
            FakeProvider(
                lambda _: assessment(questions=[question(), question("sharing", "sharing")])
            ),
            lambda _: pytest.fail("A reopened prerequisite must be answered first"),
            max_questions=12,
        )


def test_yes_answer_is_revised_with_the_actual_recommendation():
    def reply(prompt):
        if prompt.startswith("Assess"):
            return assessment(questions=[question()])
        assert "Only the author" in prompt and "No shared notebooks" in prompt
        return '{"content":"Author only","resolved":true}'

    answers = iter(["yes", None])
    graph = draft()
    result = run_interview(
        graph,
        FakeProvider(reply),
        lambda _: next(answers),
        max_questions=1,
    )
    assert result.stop_reason == "question_budget"
    assert graph.nodes["scope"].content == "Author only"


def test_interrupted_confirmation_correction_is_replayed_without_approval_or_extra_slots(tmp_path):
    path = tmp_path / "graph.json"

    def offline(prompt):
        if prompt.startswith("Assess"):
            return assessment(settled=["scope"], ready=True)
        raise ProviderError("offline")

    with pytest.raises(ProviderError, match="offline"):
        run_interview(
            draft(),
            FakeProvider(offline),
            lambda _: pytest.fail("No substantive question needed"),
            max_questions=0,
            confirm=lambda _: "Actually, shared notes",
            durability=InterviewDurability(path),
        )
    restored = Graph.load(path)
    assert restored.interview.planning.phase == "correcting"
    assert restored.interview.planning.answered == 0
    result = run_interview(
        restored,
        FakeProvider(
            [
                '{"edits":[{"id":"scope","content":"Shared notes"}]}',
                assessment(questions=[question(key="sharing")]),
            ]
        ),
        lambda _: pytest.fail("No allowance remains"),
        max_questions=0,
        confirm=lambda _: pytest.fail("A correction is not approval"),
        durability=InterviewDurability(path),
    )
    assert result.stop_reason == "question_budget"
    assert restored.nodes["scope"].content == "Shared notes"
    assert restored.interview.planning.answered == 0


def test_new_gap_is_explained_and_does_not_reopen_a_dependency_in_the_same_batch():
    graph = draft()
    reopened = question()
    reopened["reopen_reason"] = "The later sharing requirement contradicts author-only access"
    answers = iter(["Only me", None])
    prompts = []
    result = run_interview(
        graph,
        FakeProvider(
            [
                assessment(questions=[question()]),
                '{"content":"Author only","resolved":true}',
                assessment(questions=[reopened]),
            ]
        ),
        lambda prompt: prompts.append(prompt) or next(answers),
        max_questions=12,
    )
    assert result.paused and result.questions_asked == 1
    assert reopened["reopen_reason"] in prompts[-1]


def test_new_prerequisite_waits_until_the_next_round_without_losing_independent_questions():
    graph = draft()
    graph.nodes["sharing"] = Node(id="sharing", title="Sharing", content="Undecided")
    provider = FakeProvider(
        [
            assessment(questions=[question(), question("sharing", "sharing")]),
            json.dumps(
                {
                    "new_nodes": [{"id": "policy", "title": "Policy", "content": "Who may share?"}],
                    "depends_on": ["policy"],
                }
            ),
            '{"content":"Single team","resolved":true}',
            assessment(questions=[question("policy", "policy")]),
        ]
    )
    answers = iter(["Need a policy", "Single team", None])
    result = run_interview(
        graph,
        provider,
        lambda _: next(answers),
        max_questions=12,
    )
    assert result.paused
    assert result.questions_asked == 2
    assert graph.interview.planning.batch[0].node == "policy"


def test_transitively_dependent_questions_are_not_independent():
    graph = draft()
    graph.nodes["policy"] = Node(
        id="policy",
        title="Policy",
        content="Existing policy",
        depends_on=["scope"],
    )
    graph.nodes["sharing"] = Node(
        id="sharing",
        title="Sharing",
        content="Undecided",
        depends_on=["policy"],
    )
    graph.validate("scope")
    graph.validate("policy")
    with pytest.raises(ProviderError, match="prerequisite"):
        run_interview(
            graph,
            FakeProvider(
                lambda _: assessment(questions=[question(), question("sharing", "sharing")])
            ),
            lambda _: pytest.fail("These questions are transitively dependent"),
            max_questions=12,
        )


@pytest.mark.parametrize("reason", ["question_budget", "no_progress"])
def test_explicit_continuation_can_reassess_a_stopped_interview_without_a_batch(reason):
    graph = draft()
    initial = 0 if reason == "question_budget" else 12
    result = run_interview(
        graph,
        FakeProvider([assessment(), assessment(questions=[question()])]),
        lambda _: None,
        max_questions=initial,
        continue_interview=lambda actual, _: actual == reason,
    )
    assert result.stop_reason == "paused"
    assert graph.interview.planning.remaining == (6 if initial == 0 else 12)


def test_unknown_topic_or_malformed_readiness_never_reaches_user_approval():
    for reply in ["{}", assessment(questions=[question("absent")])]:
        with pytest.raises(ProviderError):
            run_interview(
                draft(),
                FakeProvider([reply]),
                lambda _: pytest.fail("Invalid assessment"),
                max_questions=12,
                confirm=lambda _: pytest.fail("Invalid readiness"),
            )
