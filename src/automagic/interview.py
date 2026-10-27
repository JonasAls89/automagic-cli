"""Questioning loop that grows a Merkle graph until no hash is invalidated."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from automagic.merkle import Graph, Node
from automagic.progress import Phase, Progress
from automagic.providers import Provider, ProviderError

Ask = Callable[[str], str | None]
Notify = Callable[[Graph], None]
Event = Callable[[dict], None]
OnProgress = Callable[[Progress], None]

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

SEED_PROMPT = """You are a planning agent. Decompose the goal into a graph of assumptions and
decisions that must hold for the work to succeed. Each node is small and concrete.
Reply with JSON only:
{{"nodes": [{{"id": "kebab-id", "title": str, "content": str, "depends_on": [ids],
"files": [repo paths likely touched], "est_lines": int}}]}}
A node's depends_on lists nodes that must be settled first.

Goal: {goal}
Repository context:
{context}
"""

QUESTION_PROMPT = """You are a rigorous interviewer. Decide whether the node below is fully
specified and grounded given its dependencies. If anything is ambiguous, unstated or risky,
ask ONE concrete question for the user. Otherwise reply with a null question.
Reply with JSON only: {{"question": string or null}}

Goal: {goal}
Node {id}: {title}
{content}
Dependencies:
{deps}
"""

REVISE_PROMPT = """Revise the node using the user's answer. Keep it concise. If the answer
reveals prerequisite decisions, add them as new nodes (and list them in depends_on).
If the answer changes nothing, return the node unchanged.
Reply with JSON only: {{"title": str, "content": str, "files": [str], "est_lines": int,
"depends_on": [ids], "new_nodes": [{{"id": str, "title": str, "content": str,
"depends_on": [ids], "files": [str], "est_lines": int}}]}}

Goal: {goal}
Node {id}: {title}
{content}
Files: {files}
Depends on: {depends_on}
Question: {question}
Answer: {answer}
"""

SPLIT_PROMPT = """This work item is too large for one human review and one agent context
window (limit about {lines} changed lines). Split it into 2-4 sequential parts.
Reply with JSON only: {{"parts": [{{"title": str, "content": str, "files": [str],
"est_lines": int}}]}}

Node {id}: {title}
{content}
Files: {files}
Estimated lines: {est_lines}
"""


@dataclass
class InterviewResult:
    graph: Graph
    rounds: int
    converged: bool
    questions_asked: int


def parse_json(text: str) -> dict:
    match = _FENCE.search(text)
    candidate = match.group(1) if match else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ProviderError("Provider reply contained no JSON object")
    try:
        data = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Provider reply was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProviderError("Provider reply JSON was not an object")
    return data


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "node"


def _node_from(data: dict) -> Node:
    title = str(data.get("title") or data.get("id") or "untitled")
    return Node(
        id=_slug(str(data.get("id") or title)),
        title=title,
        content=str(data.get("content", "")),
        depends_on=[_slug(str(d)) for d in data.get("depends_on", [])],
        files=[str(f) for f in data.get("files", [])],
        est_lines=int(data.get("est_lines", 0) or 0),
    )


def seed_graph(provider: Provider, goal: str, context: str) -> Graph:
    data = parse_json(provider.complete(SEED_PROMPT.format(goal=goal, context=context)))
    graph = Graph(goal=goal)
    for raw in data.get("nodes", []):
        node = _node_from(raw)
        graph.nodes[node.id] = node
    if not graph.nodes:
        raise ProviderError("Provider returned no nodes")
    for node in graph.nodes.values():
        node.depends_on = [d for d in node.depends_on if d in graph.nodes and d != node.id]
    graph.check()
    return graph


def _deps_text(graph: Graph, node: Node) -> str:
    return "\n".join(f"- {d}: {graph.nodes[d].content}" for d in node.depends_on) or "(none)"


def _apply_revision(graph: Graph, nid: str, data: dict) -> None:
    node = graph.nodes[nid]
    snapshot = graph.model_copy(deep=True)
    try:
        node.title = str(data.get("title") or node.title)
        node.content = str(data.get("content") or node.content)
        node.files = [str(f) for f in data.get("files", node.files)]
        node.est_lines = int(data.get("est_lines", node.est_lines) or 0)
        for raw in data.get("new_nodes", []):
            new = _node_from(raw)
            if new.id not in graph.nodes:
                graph.nodes[new.id] = new
        node.depends_on = [_slug(str(d)) for d in data.get("depends_on", node.depends_on)]
        node.depends_on = [d for d in dict.fromkeys(node.depends_on) if d != nid]
        graph.check()
    except Exception:
        graph.nodes = snapshot.nodes
        raise


def run_interview(
    graph: Graph,
    provider: Provider,
    ask: Ask,
    max_rounds: int = 8,
    on_change: Notify | None = None,
    on_event: Event | None = None,
    on_progress: OnProgress | None = None,
) -> InterviewResult:
    """Question the user until every node hash stays valid for a whole round.

    Answers that revise a node change its hash and those of all its ancestors,
    so they are re-examined in the next round. A node is validated only when
    the interviewer has no further question for it.
    """
    asked = 0

    def emit(event: dict) -> None:
        if on_event:
            on_event(event)

    def report(
        phase: Phase, round_no: int, nid: str, queue: list[str], added: int = 0, reopened: int = 0
    ) -> None:
        if on_progress is None:
            return
        invalid = set(graph.invalid())
        total = len(graph.nodes)
        on_progress(
            Progress(
                phase=phase,
                round_no=round_no,
                max_rounds=max_rounds,
                total=total,
                settled=total - len(invalid),
                left_in_round=sum(1 for n in queue if n in invalid),
                node=nid,
                added=added,
                reopened=reopened,
            )
        )

    for round_no in range(1, max_rounds + 1):
        pending = graph.invalid()
        if not pending:
            return InterviewResult(graph, round_no - 1, True, asked)
        for index, nid in enumerate(pending):
            if nid not in graph.nodes or nid not in graph.invalid():
                continue
            node = graph.nodes[nid]
            queue = pending[index:]
            report("checking", round_no, nid, queue)
            reply = parse_json(
                provider.complete(
                    QUESTION_PROMPT.format(
                        goal=graph.goal,
                        id=nid,
                        title=node.title,
                        content=node.content,
                        deps=_deps_text(graph, node),
                    )
                )
            )
            question = reply.get("question")
            if not question:
                graph.validate(nid)
                emit(
                    {
                        "event": "validated",
                        "round": round_no,
                        "node": nid,
                        "hash": graph.node_hash(nid),
                    }
                )
                report("settled", round_no, nid, queue)
            else:
                report("asking", round_no, nid, queue)
                answer = ask(str(question))
                if answer is None:
                    return InterviewResult(graph, round_no, False, asked)
                asked += 1
                old_hash = graph.node_hash(nid)
                already_invalid = set(graph.invalid())
                known = set(graph.nodes)
                report("revising", round_no, nid, queue)
                revision = parse_json(
                    provider.complete(
                        REVISE_PROMPT.format(
                            goal=graph.goal,
                            id=nid,
                            title=node.title,
                            content=node.content,
                            files=node.files,
                            depends_on=node.depends_on,
                            question=question,
                            answer=answer,
                        )
                    )
                )
                _apply_revision(graph, nid, revision)
                emit(
                    {
                        "event": "answered",
                        "round": round_no,
                        "node": nid,
                        "question": str(question),
                        "answer": answer,
                        "old_hash": old_hash,
                        "new_hash": graph.node_hash(nid),
                        "invalidated": sorted(set(graph.invalid()) - already_invalid - {nid}),
                    }
                )
                new_ids = set(graph.nodes) - known
                report(
                    "revised",
                    round_no,
                    nid,
                    queue,
                    added=len(new_ids),
                    reopened=len(set(graph.invalid()) - already_invalid - {nid} - new_ids),
                )
            if on_change:
                on_change(graph)
    return InterviewResult(graph, max_rounds, graph.is_stable(), asked)


def split_node(graph: Graph, provider: Provider, nid: str, max_lines: int) -> list[str]:
    """Replace an oversized node with a chain of parts; dependents move to the last part."""
    node = graph.nodes[nid]
    data = parse_json(
        provider.complete(
            SPLIT_PROMPT.format(
                lines=max_lines,
                id=nid,
                title=node.title,
                content=node.content,
                files=node.files,
                est_lines=node.est_lines,
            )
        )
    )
    parts = data.get("parts", [])
    if len(parts) < 2:
        return [nid]
    ids: list[str] = []
    prev: list[str] = list(node.depends_on)
    for i, raw in enumerate(parts, 1):
        part = _node_from({**raw, "id": f"{nid}-part-{i}", "depends_on": prev})
        graph.nodes[part.id] = part
        ids.append(part.id)
        prev = [part.id]
    del graph.nodes[nid]
    for other in graph.nodes.values():
        if nid in other.depends_on and other.id not in ids:
            other.depends_on = [ids[-1] if d == nid else d for d in other.depends_on]
    graph.check()
    return ids


AMEND_PROMPT = """You maintain an agreed plan as a graph of settled assumptions and decisions.
The user now states a change. Update only what the change requires: edit existing nodes,
add new nodes, and never silently drop settled intent. If the change contradicts an agreed
node, still apply it (the user's statement wins) but list the contradiction.
Reply with JSON only: {{"edits": [{{"id": existing id, "title": str, "content": str,
"files": [str], "est_lines": int, "depends_on": [ids]}}],
"new_nodes": [{{"id": str, "title": str, "content": str, "depends_on": [ids],
"files": [str], "est_lines": int}}],
"conflicts": [{{"node": existing id, "reason": str}}]}}

Goal: {goal}
Change: {change}
Agreed nodes:
{nodes}
"""


@dataclass
class AmendResult:
    edited: list[str]
    added: list[str]
    affected: list[str]
    conflicts: list[dict]


def amend_graph(graph: Graph, provider: Provider, change: str) -> AmendResult:
    """Apply a change request to a stable graph; hash invalidation marks what it touches.

    Edited and new nodes get new hashes, which invalidates every node that depends on
    them. `affected` lists those previously agreed nodes so the interview re-examines them.
    """
    nodes = "\n".join(
        f"- {n.id}: {n.title}; {n.content}; depends on {n.depends_on}; files {n.files}"
        for n in graph.nodes.values()
    )
    data = parse_json(
        provider.complete(AMEND_PROMPT.format(goal=graph.goal, change=change, nodes=nodes))
    )
    snapshot = graph.model_copy(deep=True)
    try:
        edited: list[str] = []
        for raw in data.get("edits", []):
            nid = _slug(str(raw.get("id", "")))
            if nid not in graph.nodes:
                continue
            node = graph.nodes[nid]
            node.title = str(raw.get("title") or node.title)
            node.content = str(raw.get("content") or node.content)
            node.files = [str(f) for f in raw.get("files", node.files)]
            node.est_lines = int(raw.get("est_lines", node.est_lines) or 0)
            node.depends_on = list(
                dict.fromkeys(_slug(str(d)) for d in raw.get("depends_on", node.depends_on))
            )
            node.depends_on = [d for d in node.depends_on if d != nid]
            edited.append(nid)
        added: list[str] = []
        for raw in data.get("new_nodes", []):
            new = _node_from(raw)
            if new.id not in graph.nodes:
                graph.nodes[new.id] = new
                added.append(new.id)
        for nid in added:
            node = graph.nodes[nid]
            node.depends_on = [d for d in node.depends_on if d in graph.nodes and d != nid]
        graph.check()
    except Exception:
        graph.nodes = snapshot.nodes
        raise
    changed = set(edited) | set(added)
    affected = [n for n in graph.invalid() if n not in changed]
    conflicts = [
        {"node": _slug(str(c.get("node", ""))), "reason": str(c.get("reason", ""))}
        for c in data.get("conflicts", [])
        if isinstance(c, dict)
    ]
    return AmendResult(sorted(edited), sorted(added), affected, conflicts)
