"""Merkle graph of assumptions/decisions with hash invalidation.

A node's hash covers its own content and the hashes of its dependencies
(children). Changing a node therefore changes the hash of every ancestor, which
invalidates their previous validation. Planning is stable once every node's
current hash equals the hash recorded when it was last validated.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Literal, Self, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ValidatedEvent(TypedDict):
    id: str
    event: Literal["validated"]
    round: int
    node: str
    hash: str


class AnsweredEvent(TypedDict):
    id: str
    event: Literal["answered"]
    round: int
    node: str
    question: str
    answer: str
    old_hash: str
    new_hash: str
    invalidated: list[str]


InterviewEvent = ValidatedEvent | AnsweredEvent


class InterviewCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    round_no: int = Field(default=1, gt=0)
    round_limit: int = Field(gt=0)
    pending: list[str]
    phase: Literal["checking", "asking", "revising"] = "checking"
    question: str | None = None
    answer: str | None = None
    graph_hash: str
    pending_event: InterviewEvent | None = None

    @model_validator(mode="after")
    def consistent_phase(self) -> Self:
        if self.round_no > self.round_limit or len(set(self.pending)) != len(self.pending):
            raise ValueError("Invalid interview round or pending queue")
        if self.phase != "checking" and (not self.pending or not self.question):
            raise ValueError("A pending interview question is missing")
        if (self.phase == "revising") != (self.answer is not None):
            raise ValueError("Only a pending revision may contain an answer")
        if self.phase == "checking" and self.question is not None:
            raise ValueError("A completed interview step still contains a question")
        if self.pending_event is not None and self.phase != "checking":
            raise ValueError("An interview event cannot precede a completed step")
        return self


class Node(BaseModel):
    id: str
    title: str
    content: str
    depends_on: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    est_lines: int = 0
    validated_hash: str | None = None


class GraphError(ValueError):
    pass


class Graph(BaseModel):
    goal: str = ""
    nodes: dict[str, Node] = Field(default_factory=dict)
    interview: InterviewCheckpoint | None = None

    def add(self, node: Node) -> None:
        self.nodes[node.id] = node
        self.check()

    def check(self) -> None:
        for node in self.nodes.values():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    raise GraphError(f"{node.id} depends on unknown node {dep}")
        self.order()

    def order(self) -> list[str]:
        """Dependencies-first order; raises on cycles. Deterministic."""
        state: dict[str, int] = {}
        out: list[str] = []

        def visit(nid: str) -> None:
            if state.get(nid) == 2:
                return
            if state.get(nid) == 1:
                raise GraphError(f"Cycle detected at {nid}")
            state[nid] = 1
            for dep in sorted(self.nodes[nid].depends_on):
                if dep in self.nodes:
                    visit(dep)
            state[nid] = 2
            out.append(nid)

        for nid in sorted(self.nodes):
            visit(nid)
        return out

    def node_hash(self, nid: str) -> str:
        return self._hashes()[nid]

    def _hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for nid in self.order():
            node = self.nodes[nid]
            payload = json.dumps(
                {
                    "title": node.title.strip(),
                    "content": node.content.strip(),
                    "files": sorted(node.files),
                    "est_lines": node.est_lines,
                    "children": sorted(hashes[d] for d in node.depends_on),
                },
                sort_keys=True,
            )
            hashes[nid] = hashlib.sha256(payload.encode()).hexdigest()
        return hashes

    def root_hash(self) -> str:
        hashes = self._hashes()
        depended = {d for n in self.nodes.values() for d in n.depends_on}
        roots = sorted(hashes[nid] for nid in self.nodes if nid not in depended)
        return hashlib.sha256("".join(roots).encode()).hexdigest()

    def validate(self, nid: str) -> None:
        self.nodes[nid].validated_hash = self.node_hash(nid)

    def invalid(self) -> list[str]:
        """Nodes whose current hash differs from their validated hash, dependencies first."""
        hashes = self._hashes()
        return [n for n in self.order() if self.nodes[n].validated_hash != hashes[n]]

    def is_stable(self) -> bool:
        return not self.invalid()

    def interview_hash(self) -> str:
        payload = json.dumps(self.model_dump(exclude={"interview"}), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def check_interview(self) -> None:
        if self.interview is None:
            return
        checkpoint = InterviewCheckpoint.model_validate(self.interview.model_dump())
        if checkpoint.graph_hash != self.interview_hash() or any(
            nid not in self.nodes for nid in checkpoint.pending
        ):
            raise GraphError("Saved interview checkpoint does not match the graph")
        event = checkpoint.pending_event
        if event is not None:
            expected = event["hash"] if event["event"] == "validated" else event["new_hash"]
            if (
                event["node"] not in self.nodes
                or event["round"] != checkpoint.round_no
                or expected != self.node_hash(event["node"])
                or (
                    event["event"] == "validated"
                    and self.nodes[event["node"]].validated_hash != expected
                )
            ):
                raise GraphError("Saved interview event does not match the graph")

    def save(self, path: Path, *, include_checkpoint: bool = True) -> None:
        text = self.model_dump_json(indent=2, exclude=None if include_checkpoint else {"interview"})
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
            ) as fh:
                temporary = Path(fh.name)
                fh.write(text + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path, *, include_checkpoint: bool = True) -> Graph:
        data = json.loads(path.read_text())
        if not include_checkpoint and isinstance(data, dict):
            data.pop("interview", None)
        return cls.model_validate(data)
