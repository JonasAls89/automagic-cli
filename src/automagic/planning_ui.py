"""Compact, read-only planning views and explicit interactive choices."""

import shlex
from collections.abc import Iterable
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from automagic.interview import InterviewCorrection
from automagic.merkle import Graph, GraphError, InterviewCheckpoint
from automagic.planning_state import PlanningState


def resume_command(root: Path) -> str:
    return shlex.join(["automagic", "plan", "--resume", "--path", str(root)])


def _position(graph: Graph) -> tuple[InterviewCheckpoint, PlanningState]:
    checkpoint = graph.interview
    if checkpoint is None or checkpoint.planning is None:
        raise GraphError("Planning view requires a saved bounded interview position")
    return checkpoint, checkpoint.planning


def _brief(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _table(console: Console, columns: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> None:
    table = Table(box=None, pad_edge=False, padding=(0, 1))
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(*(Text(value) for value in row))
    console.print(table)


def _preview(console: Console, label: str, values: Iterable[str], limit: int = 3) -> None:
    items = list(values)
    text = "; ".join(_brief(item) for item in items[:limit]) or "none"
    if len(items) > limit:
        text += f" (+{len(items) - limit} more; /details)"
    console.print(Text(f"{label}: {text}"))


def _pause_reason(reason: str, state: PlanningState, round_limit: int) -> tuple[str, str]:
    if reason == "question_budget":
        return (
            "question allowance exhausted",
            f"The allowance of {state.question_limit} individual answers is used up. "
            "An extension requires your explicit consent.",
        )
    if reason == "pass_limit":
        return (
            "pass safeguard reached",
            f"The {round_limit}-pass safeguard was reached. "
            "Passes count questioning rounds, not individual answers.",
        )
    if reason == "no_progress":
        if state.no_progress >= 2:
            title = "no material progress"
        elif not state.batch:
            title = "no next question"
        elif state.repeated_question:
            title = "repeated answered question"
        else:
            title = "saved progress pause"
        return title, state.no_progress_detail
    raise GraphError(f"Unknown interview stop reason: {reason}")


def show_summary(
    graph: Graph, root: Path, reason: str | None = None, *, console: Console | None = None
) -> None:
    console = console or Console(highlight=False, markup=False)
    checkpoint, state = _position(graph)
    if reason is None:
        console.print("Review before approval")
    else:
        title, explanation = _pause_reason(reason, state, checkpoint.round_limit)
        console.print(Text(f"Planning paused: {title}"))
        console.print(Text(f"Why: {explanation}"))
    console.print(Text(f"Repository: {root}"))
    console.print(Text(f"Goal: {_brief(graph.goal, 180)}"))
    invalid = graph.invalid()
    console.print(
        Text(
            f"Topics: {len(graph.nodes) - len(invalid)}/{len(graph.nodes)} settled | "
            f"Questions: {state.answered}/{state.question_limit} used; {state.remaining} remaining"
        )
    )
    console.print(Text(f"Completed passes: {state.completed_rounds}/{checkpoint.round_limit}"))
    assessment = state.assessment
    if invalid:
        _preview(console, "Unsettled topics", (graph.nodes[nid].title for nid in invalid), limit=5)
        console.print("Unsettled topics still prevent approval.")
    if assessment is None:
        console.print("Readiness and material blockers: not yet assessed.")
    else:
        statuses = {"supported": "Supported", "unresolved": "Unresolved", "not_applicable": "N/A"}
        criteria = assessment.criteria
        _table(
            console,
            ("Criterion", "Status", "Essential evidence"),
            (
                (name, statuses[item.status], _brief(item.evidence))
                for name, item in (
                    ("Outcome:", criteria.outcome),
                    ("Users:", criteria.users),
                    ("Scope:", criteria.scope),
                    ("Constraints:", criteria.constraints),
                    ("Acceptance:", criteria.acceptance),
                )
            ),
        )
        console.print(
            "Material blockers:" if assessment.blockers else "Material blockers: none reported"
        )
        for blocker in assessment.blockers:
            console.print(Text(f"- {blocker}"))
        _preview(console, "Remaining unknowns", assessment.unknowns)
    _preview(
        console,
        "Discussed tradeoffs",
        dict.fromkeys(item.question.tradeoff for item in state.history),
    )
    console.print(
        "Previews may be abbreviated. /details includes full content and recorded answers."
    )


def _choose(
    console: Console,
    actions: dict[str, str],
    *,
    affirmative: str | None = None,
    corrections: bool = False,
) -> str:
    _table(console, ("Action", "Effect"), actions.items())
    aliases = {"/quit": "/done"}
    if affirmative is not None:
        aliases.update({"y": affirmative, "yes": affirmative, "n": "/done", "no": "/done"})
    while True:
        answer = typer.prompt("Action or correction" if corrections else "Action", default="/done")
        choice = aliases.get(answer.strip().casefold(), answer.strip().casefold())
        if choice in actions:
            return choice
        if corrections and choice and not choice.startswith("/"):
            return answer
        console.print(Text(f"Unknown action. Choose {', '.join(actions)}."))


def _correction() -> str | None:
    while True:
        answer = typer.prompt("Describe your correction (/done to save and stop)")
        if answer.strip().casefold() in {"/done", "/quit"}:
            return None
        if answer.strip():
            return answer
        typer.echo("Correction cannot be empty.")


def _details(console: Console, summary: str, title: str = "Full saved plan") -> None:
    console.print(Text(f"{title} (read-only):"))
    console.print(Text(summary), soft_wrap=True)


def confirm_plan(graph: Graph, root: Path, summary: str) -> str | None:
    console = Console(highlight=False, markup=False)
    show_summary(graph, root, console=console)
    console.print("Recommended: review scope, tradeoffs and unknowns before approving.")
    actions = {
        "/details": "Inspect full saved information; no AI calls or changes",
        "/correct": "Correct intent; follow-up questions use the remaining allowance",
        "/approve": "Explicitly approve this plan for work-item preparation",
        "/done": "Save and stop; resume later (default)",
    }
    while True:
        choice = _choose(console, actions, corrections=True)
        if choice == "/details":
            _details(console, summary)
        elif choice == "/correct":
            return _correction()
        else:
            return None if choice == "/done" else choice


def continue_plan(
    graph: Graph, root: Path, reason: str, summary: str, pass_increment: int
) -> bool | InterviewCorrection:
    console = Console(highlight=False, markup=False)
    checkpoint, state = _position(graph)
    show_summary(graph, root, reason, console=console)
    console.print(
        "Recommended: inspect outstanding decisions with /details, then clarify with /correct."
    )
    if reason == "question_budget":
        forward = "/extend"
        effect = (
            f"Add six questions ({state.question_limit} -> {state.question_limit + 6}); "
            "passes unchanged"
        )
    elif reason == "pass_limit":
        forward = "/extend"
        effect = (
            f"Add {pass_increment} passes "
            f"({checkpoint.round_limit} -> {checkpoint.round_limit + pass_increment}); "
            "question allowance unchanged"
        )
    else:
        forward, effect = "/retry", "Reassess unchanged; no extra questions; may pause again"
    actions = {
        "/details": "Inspect full saved information; no AI calls or changes",
        "/correct": "Clarify intent; follow-up questions use the remaining allowance",
        forward: effect,
        "/done": "Save and stop; resume later (default)",
    }
    while True:
        choice = _choose(console, actions, affirmative=forward)
        if choice == "/details":
            _details(console, summary)
        elif choice == "/correct":
            correction = _correction()
            return InterviewCorrection(correction) if correction is not None else False
        else:
            return choice == forward


def recovery_consent(title: str, action: str, effect: str, details: str) -> bool:
    console = Console(highlight=False, markup=False)
    console.print(Text(title))
    console.print("Recommended: inspect /details before changing the saved interview position.")
    actions = {
        "/details": "Inspect the recovery explanation; no changes or AI calls",
        action: effect,
        "/done": "Stop and leave existing saved files unchanged (default)",
    }
    while True:
        choice = _choose(console, actions, affirmative=action)
        if choice == "/details":
            _details(console, details, "Recovery explanation")
        else:
            return choice == action
