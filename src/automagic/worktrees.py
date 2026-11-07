"""Propose and (on confirmation) create git worktrees for work items."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from automagic.execution import (
    Plan,
    WorkItem,
    atomic_write,
    context_paths,
    git,
    require_approval,
    require_input_contents,
)


@dataclass
class Proposal:
    item: WorkItem
    branch: str
    path: Path
    base: str = "HEAD"

    @property
    def command(self) -> list[str]:
        return ["git", "worktree", "add", "-b", self.branch, str(self.path), self.base]


def _slug(text: str, max_length: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:max_length] or "work"


def _category(text: str) -> str:
    if re.search(
        r"\b(refactor\w*|restructur\w*|reorganiz\w*|modulariz\w*|decoupl\w*|simplif\w*)\b",
        text,
        re.I,
    ):
        return "refactor"
    if re.search(
        r"\b(chore|maintenan\w*|document\w*|docs|dependenc\w*|tooling|format\w*|lint\w*|"
        r"test\w*|clean\w*|housekeep\w*|packag\w*|release)\b",
        text,
        re.I,
    ):
        return "chore"
    return "feature"


def _branch_name(item: WorkItem) -> str:
    action = re.match(
        r"^(?:add|build|create|implement|introduce|improve|update|refactor|restructure|"
        r"reorganize|modularize|decouple|simplify|document|maintain|bump|upgrade|fix)"
        r"\s+(?:(?:a|an|the|new)\s+)?(.+)$",
        item.title,
        flags=re.I,
    )
    title = action.group(1) if action else item.title
    intent = _slug(title, 32).rstrip("-") or "work"
    return f"{_category(item.title)}/{item.id}_{intent}"


def propose(plan: Plan, repo: Path) -> list[Proposal]:
    base = repo.parent / f"{repo.name}.worktrees"
    goal = _slug(plan.goal)
    branches = {item.id: f"{_branch_name(item)}-{plan.digest[:12]}" for item in plan.items}
    return [
        Proposal(
            item=item,
            branch=branches[item.id],
            path=base / f"{goal}-{plan.digest[:12]}-{item.id}",
            base=branches[item.depends_on[0]] if item.depends_on else plan.base,
        )
        for item in plan.items
    ]


def brief(plan: Plan, proposal: Proposal) -> str:
    item = proposal.item
    lines = [
        f"# Work item {item.id}",
        "",
        f"Overall goal: {plan.goal}",
        f"Approved snapshot: `{plan.digest}`",
        f"Estimated size: ~{item.est_lines} changed lines",
        f"Prerequisite work items: {', '.join(item.depends_on) or 'none'}",
        "",
        "Implement only this work item. Leave changes uncommitted; preserve staging.",
        f"Required verification: `{item.verify_command}`",
        "Allowed files: " + ", ".join(f"`{name}`" for name in item.files),
        "",
        "## Acceptance criteria",
        *[f"- [ ] {criterion}" for criterion in item.acceptance_criteria],
        "",
        "## Approved inputs",
        *[
            f"- [{name}](../{name})"
            for name in sorted(context_paths(plan.index, plan.inputs, item))
            if name != "docs/work/_index.md"
        ],
        "",
        item.content,
        "",
    ]
    return "\n".join(lines)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch: str
    path: str


class Ownership(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot: str
    items: dict[str, Record]


def _ownership(plan: Plan, repo: Path) -> Ownership:
    path = plan.runtime_path(repo, "worktrees.json")
    state = (
        Ownership.model_validate_json(path.read_text())
        if path.exists()
        else Ownership(snapshot=plan.digest, items={})
    )
    if state.snapshot != plan.digest or set(state.items) - {item.id for item in plan.items}:
        raise ValueError("Worktree ownership does not match the approved snapshot")
    return state


def _validate(prop: Proposal, owned: Ownership, repo: Path) -> None:
    record = owned.items.get(prop.item.id)
    if prop.path.is_symlink() or prop.path.parent.is_symlink():
        raise RuntimeError(f"Refusing symlink worktree path: {prop.path}")
    if record:
        if record.path != str(prop.path) or record.branch != prop.branch or not prop.path.is_dir():
            raise RuntimeError(f"Recorded worktree is missing or mismatched: {prop.path}")
        branch = git(["symbolic-ref", "--short", "HEAD"], prop.path)
        root = git(["rev-parse", "--show-toplevel"], prop.path)
        common = git(["rev-parse", "--git-common-dir"], prop.path)
        expected = git(["rev-parse", "--git-common-dir"], repo)
        if (
            branch.returncode
            or root.returncode
            or common.returncode
            or expected.returncode
            or branch.stdout.strip() != prop.branch
            or Path(root.stdout.strip()).resolve() != prop.path.resolve()
            or (prop.path / common.stdout.strip()).resolve()
            != (repo / expected.stdout.strip()).resolve()
        ):
            raise RuntimeError(f"Worktree Git identity changed: {prop.path}")
    elif (
        prop.path.exists()
        or git(["show-ref", "--verify", "--quiet", f"refs/heads/{prop.branch}"], repo).returncode
        == 0
    ):
        raise RuntimeError(f"Unowned worktree/branch collision: {prop.path}; resolve explicitly")


def create(plan: Plan, proposals: list[Proposal], repo: Path) -> list[Path]:
    require_approval(repo, plan)
    owned = _ownership(plan, repo)
    for prop in proposals:
        _validate(prop, owned, repo)
        if prop.item.id in owned.items:
            require_input_contents(prop.path, plan)
    created: list[Path] = []
    for prop in proposals:
        if prop.item.id in owned.items:
            continue
        result = git(prop.command[1:], repo)
        if result.returncode != 0:
            raise RuntimeError(f"{' '.join(prop.command)} failed: {result.stderr.strip()}")
        owned.items[prop.item.id] = Record(branch=prop.branch, path=str(prop.path))
        atomic_write(
            plan.runtime_path(repo, "worktrees.json"), owned.model_dump_json(indent=2) + "\n"
        )
        require_input_contents(prop.path, plan)
        target = prop.path / ".automagic"
        target.mkdir(parents=True, exist_ok=True)
        (target / "BRIEF.md").write_text(brief(plan, prop))
        created.append(prop.path)
    return created
