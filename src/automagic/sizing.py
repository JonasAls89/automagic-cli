"""Estimate node cost and pack nodes into reviewable, context-sized work items."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from automagic.config import Config
from automagic.merkle import Graph, Node

CHARS_PER_TOKEN = 4
TOKENS_PER_CHANGED_LINE = 10
BASE_OVERHEAD_TOKENS = 2_000  # system prompt, instructions, brief


def _file_tokens(root: Path, files: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for rel in files:
        path = (root / rel).resolve()
        if root.resolve() in path.parents and path.is_file():
            out[rel] = math.ceil(path.stat().st_size / CHARS_PER_TOKEN)
    return out


def node_tokens(node: Node) -> int:
    text = len(node.title) + len(node.content)
    return math.ceil(text / CHARS_PER_TOKEN) + node.est_lines * TOKENS_PER_CHANGED_LINE


@dataclass
class WorkItem:
    id: str
    node_ids: list[str] = field(default_factory=list)
    files: set[str] = field(default_factory=set)
    tokens: int = 0
    lines: int = 0
    depends_on: list[str] = field(default_factory=list)
    oversized: bool = False


def oversized_nodes(graph: Graph, root: Path, cfg: Config) -> list[str]:
    result = []
    for nid, node in graph.nodes.items():
        tokens = (
            BASE_OVERHEAD_TOKENS + node_tokens(node) + sum(_file_tokens(root, node.files).values())
        )
        if tokens > cfg.max_context_tokens or node.est_lines > cfg.max_changed_lines:
            result.append(nid)
    return sorted(result)


def partition(graph: Graph, root: Path, cfg: Config) -> list[WorkItem]:
    """Greedy dependencies-first packing under token and changed-line budgets."""
    too_big = set(oversized_nodes(graph, root, cfg))
    items: list[WorkItem] = []
    current: WorkItem | None = None

    def close() -> None:
        nonlocal current
        if current and current.node_ids:
            items.append(current)
        current = None

    for nid in graph.order():
        node = graph.nodes[nid]
        files = _file_tokens(root, node.files)
        if nid in too_big:
            close()
            item = WorkItem(
                id="", node_ids=[nid], files=set(files), oversized=True, lines=node.est_lines
            )
            item.tokens = BASE_OVERHEAD_TOKENS + node_tokens(node) + sum(files.values())
            items.append(item)
            continue
        if current is None:
            current = WorkItem(id="", tokens=BASE_OVERHEAD_TOKENS)
        added_files = {f: t for f, t in files.items() if f not in current.files}
        extra = node_tokens(node) + sum(added_files.values())
        if current.node_ids and (
            current.tokens + extra > cfg.max_context_tokens
            or current.lines + node.est_lines > cfg.max_changed_lines
        ):
            close()
            current = WorkItem(id="", tokens=BASE_OVERHEAD_TOKENS)
            added_files = files
            extra = node_tokens(node) + sum(files.values())
        current.node_ids.append(nid)
        current.files |= set(files)
        current.tokens += extra
        current.lines += node.est_lines
    close()

    owner: dict[str, str] = {}
    for i, item in enumerate(items, 1):
        item.id = f"wi-{i:02d}"
        for nid in item.node_ids:
            owner[nid] = item.id
    for item in items:
        deps = {owner[d] for nid in item.node_ids for d in graph.nodes[nid].depends_on} - {item.id}
        item.depends_on = sorted(deps)
    return items
