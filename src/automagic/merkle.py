"""Merkle graph of assumptions/decisions with hash invalidation.

A node's hash covers its own content and the hashes of its dependencies
(children). Changing a node therefore changes the hash of every ancestor, which
invalidates their previous validation. Planning is stable once every node's
current hash equals the hash recorded when it was last validated.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field


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

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> Graph:
        return cls.model_validate_json(path.read_text())
