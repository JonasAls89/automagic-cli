"""Run one agent per work item in its worktree, in dependency waves."""

from __future__ import annotations

import shlex
import subprocess  # nosec B404
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from automagic.config import Config
from automagic.merkle import Graph
from automagic.sizing import WorkItem
from automagic.worktrees import Proposal, brief

PENDING, RUNNING, DONE, FAILED, BLOCKED = "pending", "running", "done", "failed", "blocked"
BRIEF_PATH = ".automagic/BRIEF.md"

PROMPT = (
    "Read .automagic/BRIEF.md and AGENTS.md (if present). Implement only the work item "
    "described in the brief; do not expand scope. Run the project's quality gates, then commit "
    "your work to the current branch with git commit. Do not push, merge, rebase or switch "
    "branches."
)


class ItemRun(BaseModel):
    status: str = PENDING
    branch: str = ""
    path: str = ""
    log: str = ""
    detail: str = ""
    base: str = ""
    review: str = ""


class RunState(BaseModel):
    root_hash: str
    items: dict[str, ItemRun] = Field(default_factory=dict)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> RunState:
        return cls.model_validate_json(path.read_text())


class RunError(RuntimeError):
    pass


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # nosec B603 B607
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def waves(items: list[WorkItem]) -> list[list[str]]:
    """Dependency waves assuming every item succeeds."""
    done: set[str] = set()
    remaining = {i.id: set(i.depends_on) for i in items}
    out: list[list[str]] = []
    while remaining:
        ready = sorted(i for i, deps in remaining.items() if deps <= done)
        if not ready:
            raise RunError("Work item dependencies contain a cycle")
        out.append(ready)
        done.update(ready)
        for i in ready:
            del remaining[i]
    return out


def select(items: list[WorkItem], only: list[str], done: set[str]) -> set[str]:
    """Work item ids to run for the given work item or node ids.

    Unfinished prerequisites are pulled in transitively; items in `done` are left out.
    """
    by_id = {i.id: i for i in items}
    owner = {n: i.id for i in items for n in i.node_ids}
    chosen: set[str] = set()
    stack: list[str] = []
    for ident in only:
        target = ident if ident in by_id else owner.get(ident)
        if target is None:
            raise RunError(f"unknown work item or node {ident!r}")
        stack.append(target)
    while stack:
        current = stack.pop()
        if current in chosen or current in done:
            continue
        chosen.add(current)
        stack.extend(by_id[current].depends_on)
    return chosen


def agent_command(cfg: Config, prompt: str = PROMPT) -> list[str]:
    parts = shlex.split(cfg.runner_command)
    if not any("{prompt}" in p for p in parts):
        raise RunError("runner_command must contain {prompt}")
    return [p.replace("{prompt}", prompt) for p in parts]


def prepare_worktree(
    graph: Graph, prop: Proposal, items: list[WorkItem], props: dict[str, Proposal], repo: Path
) -> None:
    """Create the worktree off its first prerequisite branch and merge the others in."""
    if not prop.path.exists():
        bases = [props[d].branch for d in prop.item.depends_on]
        cmd = ["worktree", "add", "-b", prop.branch, str(prop.path), bases[0] if bases else "HEAD"]
        if git(["rev-parse", "--verify", "-q", prop.branch], repo).returncode == 0:
            cmd = ["worktree", "add", str(prop.path), prop.branch]
        result = git(cmd, repo)
        if result.returncode != 0:
            raise RunError(f"git {' '.join(cmd)}: {result.stderr.strip()}")
        for other in bases[1:]:
            merged = git(["merge", "--no-edit", other], prop.path)
            if merged.returncode != 0:
                git(["merge", "--abort"], prop.path)
                raise RunError(f"merging {other} into {prop.branch} conflicts")
    brief_file = prop.path / BRIEF_PATH
    brief_file.parent.mkdir(parents=True, exist_ok=True)
    brief_file.write_text(brief(graph, prop, items))


def _commit_all(path: Path, message: str) -> bool:
    git(["add", "-A", "--", ".", f":(exclude){BRIEF_PATH}"], path)
    if git(["diff", "--cached", "--quiet"], path).returncode == 0:
        return False
    result = git(["commit", "--no-verify", "-m", message], path)
    if result.returncode != 0:
        raise RunError(f"git commit failed: {result.stderr.strip()}")
    return True


def _run_logged(cmd: list[str], cwd: Path, log: Path, timeout: int) -> int:
    with log.open("a") as fh:
        fh.write(f"$ {shlex.join(cmd)}\n")
        fh.flush()
        try:
            return subprocess.run(  # noqa: S603  # nosec B603
                cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout, check=False
            ).returncode
        except subprocess.TimeoutExpired:
            fh.write(f"\ntimed out after {timeout}s\n")
            return 124
        except OSError as exc:
            fh.write(f"\n{exc}\n")
            return 127


def execute(prop: Proposal, cfg: Config, log: Path) -> tuple[str, str]:
    """Run the agent, commit its work, then gate on the verify command. Returns (status, detail)."""
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("")
    code = _run_logged(agent_command(cfg), prop.path, log, cfg.agent_timeout)
    if code != 0:
        return FAILED, f"agent exited with {code}"
    try:
        _commit_all(prop.path, f"{prop.item.id}: implement work item")
        if cfg.verify_command.strip():
            verify = shlex.split(cfg.verify_command)
            code = _run_logged(verify, prop.path, log, cfg.agent_timeout)
            # Formatters fix files and fail the first run; commit the fixes and check again.
            if code != 0 and _commit_all(prop.path, f"{prop.item.id}: apply automatic fixes"):
                code = _run_logged(verify, prop.path, log, cfg.agent_timeout)
            if code != 0:
                return FAILED, f"verification failed ({cfg.verify_command})"
    except RunError as exc:
        return FAILED, str(exc)
    return DONE, ""


def run_plan(
    graph: Graph,
    items: list[WorkItem],
    props: list[Proposal],
    repo: Path,
    cfg: Config,
    state: RunState,
    state_path: Path,
    log_dir: Path,
    on_update: Callable[[str, ItemRun], None] | None = None,
    only: set[str] | None = None,
) -> RunState:
    by_id = {p.item.id: p for p in props}
    for item in items:
        run = state.items.setdefault(item.id, ItemRun())
        run.branch, run.path = by_id[item.id].branch, str(by_id[item.id].path)
        if run.status != DONE and (only is None or item.id in only):
            run.status, run.detail = PENDING, ""

    def update(item_id: str, status: str, detail: str = "") -> None:
        run = state.items[item_id]
        run.status, run.detail = status, detail
        state.save(state_path)
        if on_update:
            on_update(item_id, run)

    with ThreadPoolExecutor(max_workers=cfg.max_concurrency) as pool:
        while True:
            pending = [
                i
                for i in items
                if state.items[i.id].status == PENDING and (only is None or i.id in only)
            ]
            if not pending:
                break
            for item in pending:
                if any(state.items[d].status in (FAILED, BLOCKED) for d in item.depends_on):
                    update(item.id, BLOCKED, "a prerequisite failed")
            ready = [
                i
                for i in pending
                if state.items[i.id].status == PENDING
                and all(state.items[d].status == DONE for d in i.depends_on)
            ]
            futures = {}
            if not ready:
                break
            for item in ready:
                prop = by_id[item.id]
                try:
                    prepare_worktree(graph, prop, items, by_id, repo)
                except RunError as exc:
                    update(item.id, FAILED, str(exc))
                    continue
                if not state.items[item.id].base:
                    # Fixed once, after prerequisite merges, so reviews diff only the agent's work.
                    head = git(["rev-parse", "HEAD"], prop.path)
                    state.items[item.id].base = head.stdout.strip()
                log = log_dir / f"{item.id}.log"
                state.items[item.id].log = str(log)
                update(item.id, RUNNING)
                futures[item.id] = pool.submit(execute, prop, cfg, log)
            for item_id, future in futures.items():
                try:
                    status, detail = future.result()
                except Exception as exc:  # agent failures must not stop other items
                    status, detail = FAILED, f"unexpected error: {exc}"
                update(item_id, status, detail)
    return state
