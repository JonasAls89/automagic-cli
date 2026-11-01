"""Publish derived plan artifacts without terminal presentation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from automagic import guardrails, htmlview, plandocs, render, runner
from automagic.config import Config, state_dir
from automagic.merkle import Graph
from automagic.sizing import WorkItem


@dataclass(frozen=True)
class PublishedPlan:
    markdown: str
    html_path: Path
    documents_dir: Path


def publish(root: Path, graph: Graph, items: list[WorkItem], cfg: Config) -> PublishedPlan:
    plan_dir = state_dir(root) / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    history = render.load_history(plan_dir / "history.jsonl")
    markdown = render.render_plan(graph, items, cfg, history)
    state_path = plan_dir / "run.json"
    state = runner.RunState.load(state_path) if state_path.exists() else None
    html_path = plan_dir / "plan.html"
    html_path.write_text(htmlview.render_html(graph, items, state))
    plandocs.write(root, graph, items, history, cfg.max_changed_lines)
    if (root / "docs" / "CONTEXT.md").exists():
        guardrails.update_index(root)
    return PublishedPlan(markdown, html_path, plandocs.plan_dir(root))
