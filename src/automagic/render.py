"""Render the plan, Merkle graph (Mermaid) and interview history as markdown."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from automagic.config import Config
from automagic.merkle import Graph
from automagic.sizing import WorkItem


def _label(text: str) -> str:
    return text.replace('"', "#quot;").replace("\n", " ")[:60]


def render_mermaid(graph: Graph, items: list[WorkItem]) -> str:
    """Flowchart: edges run from dependency to dependent; work items are subgraphs."""
    alias = {nid: f"n{i}" for i, nid in enumerate(graph.order())}
    lines = ["flowchart TD", f'    root(["root {graph.root_hash()[:12]}"])']
    placed: set[str] = set()
    for item in items:
        lines.append(f'    subgraph {item.id.replace("-", "_")}["{item.id}"]')
        for nid in item.node_ids:
            node = graph.nodes[nid]
            label = f"{_label(node.title)}<br/>{graph.node_hash(nid)[:8]}"
            lines.append(f'        {alias[nid]}["{label}"]')
            placed.add(nid)
        lines.append("    end")
    for nid in graph.order():
        if nid not in placed:
            node = graph.nodes[nid]
            label = f"{_label(node.title)}<br/>{graph.node_hash(nid)[:8]}"
            lines.append(f'    {alias[nid]}["{label}"]')
    depended = {d for n in graph.nodes.values() for d in n.depends_on}
    for nid in graph.order():
        for dep in graph.nodes[nid].depends_on:
            lines.append(f"    {alias[dep]} --> {alias[nid]}")
        if nid not in depended:
            lines.append(f"    {alias[nid]} --> root")
    for item in items:
        if item.oversized:
            lines.append(f"    style {item.id.replace('-', '_')} stroke:#d00,stroke-width:2px")
    return "\n".join(lines)


def load_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_history(events: list[dict]) -> list[str]:
    if not events:
        return ["_No interview history recorded._"]
    lines = [
        "| Interview pass | Planning topic | Event | Question | Answer | Hash | Invalidated |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in events:
        if e["event"] == "amended":
            touched = ", ".join(e["edited"] + e["added"] + e["affected"]) or "-"
            lines.append(f"| {e['round']} | - | amended | | {_cell(e['change'])} | | {touched} |")
        elif e["event"] == "answered":
            hashes = f"`{e['old_hash'][:8]}` → `{e['new_hash'][:8]}`"
            lines.append(
                f"| {e['round']} | {e['node']} | answered | {_cell(e['question'])} "
                f"| {_cell(e['answer'])} | {hashes} | {', '.join(e['invalidated']) or '-'} |"
            )
        else:
            lines.append(f"| {e['round']} | {e['node']} | validated | | | `{e['hash'][:8]}` | |")
    return lines


def hash_chain(graph: Graph, nid: str, depth: int = 0) -> list[str]:
    node = graph.nodes[nid]
    status = "valid" if node.validated_hash == graph.node_hash(nid) else "INVALID"
    out = [f"{'  ' * depth}{nid} {graph.node_hash(nid)} ({status})"]
    for dep in sorted(node.depends_on):
        out += hash_chain(graph, dep, depth + 1)
    return out


def render_work_breakdown(
    graph: Graph,
    items: list[WorkItem],
    *,
    item_label: Literal["work item", "node"] = "work item",
) -> str:
    item_count, topic_count = len(items), len(graph.nodes)
    items_label = item_label if item_count == 1 else f"{item_label}s"
    topic_label = "planning topic" if topic_count == 1 else "planning topics"
    return f"Work breakdown: {item_count} {items_label} covering {topic_count} {topic_label}"


def render_plan(
    graph: Graph, items: list[WorkItem], cfg: Config, history: list[dict] | None = None
) -> str:
    lines = [
        f"# Plan: {graph.goal}",
        "",
        f"Root hash: `{graph.root_hash()}`",
        f"Budget per work item: {cfg.max_context_tokens} tokens, "
        f"{cfg.max_changed_lines} changed lines",
        "",
        "## Merkle graph",
        "",
        "```mermaid",
        render_mermaid(graph, items),
        "```",
        "",
        f"## {render_work_breakdown(graph, items)}",
        "",
        "| Work item | Planning topics | Est. tokens | Est. lines | Depends on | Flags |",
        "|---|---|---|---|---|---|",
    ]
    for item in items:
        lines.append(
            f"| {item.id} | {', '.join(item.node_ids)} | {item.tokens} | {item.lines} "
            f"| {', '.join(item.depends_on) or '-'} | {'OVERSIZED' if item.oversized else ''} |"
        )
    lines.append("")
    for item in items:
        lines.append(f"### {item.id}")
        for nid in item.node_ids:
            node = graph.nodes[nid]
            lines += [
                f"#### {node.title} (`{nid}`, `{graph.node_hash(nid)}`)",
                node.content,
                "",
            ]
            if node.files:
                lines.append("Files: " + ", ".join(f"`{f}`" for f in node.files))
                lines.append("")
    lines += ["## Interview history", "", *render_history(history or [])]
    return "\n".join(lines) + "\n"
