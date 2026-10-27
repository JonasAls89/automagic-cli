"""Propose and (on confirmation) create git worktrees for work items."""

from __future__ import annotations

import re
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path

from automagic.merkle import Graph
from automagic.sizing import WorkItem


@dataclass
class Proposal:
    item: WorkItem
    branch: str
    path: Path
    base: str = "HEAD"

    @property
    def command(self) -> list[str]:
        return ["git", "worktree", "add", "-b", self.branch, str(self.path), self.base]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "work"


def git_root(start: Path) -> Path:
    out = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
        cwd=start,
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        raise RuntimeError("Not inside a git repository")
    return Path(out.stdout.strip())


def is_dirty(repo: Path) -> bool:
    out = subprocess.run(  # nosec B603 B607
        ["git", "status", "--porcelain"],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(out.stdout.strip())


def propose(graph: Graph, items: list[WorkItem], repo: Path) -> list[Proposal]:
    base = repo.parent / f"{repo.name}.worktrees"
    goal = _slug(graph.goal)
    return [
        Proposal(
            item=item,
            branch=f"automagic/{goal}/{item.id}",
            path=base / f"{goal}-{item.id}",
            # Like `automagic run`: dependents start from their first prerequisite's branch.
            base=f"automagic/{goal}/{item.depends_on[0]}" if item.depends_on else "HEAD",
        )
        for item in items
    ]


def brief(graph: Graph, proposal: Proposal, all_items: list[WorkItem]) -> str:
    item = proposal.item
    lines = [
        f"# Work item {item.id}",
        "",
        f"Overall goal: {graph.goal}",
        f"Plan root hash: `{graph.root_hash()}`",
        f"Estimated size: ~{item.tokens} tokens, ~{item.lines} changed lines",
        f"Prerequisite work items: {', '.join(item.depends_on) or 'none'}",
        "",
        "Implement only the nodes below. Do not expand scope. Ask when unsure.",
        "",
    ]
    for nid in item.node_ids:
        node = graph.nodes[nid]
        lines += [
            f"## {node.title} (`{nid}`, hash `{graph.node_hash(nid)[:12]}`)",
            node.content,
            "",
        ]
        if node.files:
            lines.append("Relevant files: " + ", ".join(f"`{f}`" for f in node.files))
            lines.append("")
    return "\n".join(lines)


def create(
    graph: Graph, proposals: list[Proposal], all_items: list[WorkItem], repo: Path
) -> list[Path]:
    created = []
    for p in proposals:
        result = subprocess.run(  # noqa: S603  # nosec B603
            p.command, cwd=repo, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(f"{' '.join(p.command)} failed: {result.stderr.strip()}")
        target = p.path / ".automagic"
        target.mkdir(parents=True, exist_ok=True)
        (target / "BRIEF.md").write_text(brief(graph, p, all_items))
        created.append(p.path)
    return created
