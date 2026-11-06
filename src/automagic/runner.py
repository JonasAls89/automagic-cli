"""Run one agent per work item in its worktree, in dependency waves."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess  # nosec B404
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
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
    "described in the brief; do not expand scope. Run the project's quality gates, then leave "
    "your changes in the worktree for human review and commit grouping. Preserve existing "
    "staging. Do not stage, commit, push, merge, rebase or switch branches."
)


class ItemRun(BaseModel):
    status: str = PENDING
    branch: str = ""
    path: str = ""
    log: str = ""
    detail: str = ""
    base: str = ""
    review: str = ""
    hash: str = ""


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


def item_hash(graph: Graph, item: WorkItem) -> str:
    """Scope of a work item: its nodes and their Merkle hashes, which cover prerequisites."""
    payload = json.dumps([[nid, graph.node_hash(nid)] for nid in item.node_ids])
    return hashlib.sha256(payload.encode()).hexdigest()


def stale_items(graph: Graph, items: list[WorkItem], state: RunState) -> list[str]:
    """Items already run whose nodes changed since, so their saved work no longer matches."""
    if state.root_hash == graph.root_hash():
        return []
    return [
        i.id
        for i in items
        if (run := state.items.get(i.id))
        and run.status != PENDING
        and run.hash != item_hash(graph, i)
    ]


def finished(graph: Graph, items: list[WorkItem], state: RunState) -> set[str]:
    stale = set(stale_items(graph, items, state))
    return {k for k, r in state.items.items() if r.status == DONE and k not in stale}


def adopt_plan(graph: Graph, items: list[WorkItem], state: RunState) -> list[str]:
    """Reset stale items to pending and move the state to the current plan.

    The branch, worktree and base of a reset item are kept, so its agent continues on the
    existing work and a review still sees everything the item changed. Returns the reset ids.
    """
    stale = stale_items(graph, items, state)
    for item in items:
        if run := state.items.get(item.id):
            if item.id in stale:
                run.status, run.detail, run.log, run.review = PENDING, "", "", ""
            run.hash = item_hash(graph, item)
    state.root_hash = graph.root_hash()
    return stale


def fresh_state(graph: Graph, old: RunState | None) -> RunState:
    """Empty run state that keeps the base of branches whose worktree still exists."""
    state = RunState(root_hash=graph.root_hash())
    for key, run in (old.items if old else {}).items():
        if run.base and run.path and Path(run.path).is_dir():
            state.items[key] = ItemRun(base=run.base)
    return state


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # nosec B603 B607
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def needs_manual_commit(path: Path, base: str) -> bool:
    """Require clean prerequisites with committed changes, ignoring the generated brief."""
    if not path.is_dir():
        raise RunError(f"Worktree {path} does not exist")
    result = git(
        ["status", "--porcelain", "--untracked-files=all", "--", ".", f":(exclude){BRIEF_PATH}"],
        path,
    )
    if result.returncode != 0:
        raise RunError(f"git status failed: {result.stderr.strip()}")
    if result.stdout.strip():
        return True
    committed = git(["diff", "--quiet", base, "HEAD"], path)
    if committed.returncode not in (0, 1):
        raise RunError(f"git diff failed: {committed.stderr.strip()}")
    return committed.returncode == 0


def untracked_files(path: Path) -> list[str]:
    result = git(
        ["ls-files", "--others", "--exclude-standard", "-z", "--", ".", f":(exclude){BRIEF_PATH}"],
        path,
    )
    if result.returncode != 0:
        raise RunError(f"git ls-files failed: {result.stderr.strip()}")
    return list(filter(None, result.stdout.split("\0")))


def worktree_diff(path: Path, base: str, *, numstat: bool = False) -> str:
    """Diff all current work from a base without modifying the real index."""
    flags = ["--no-ext-diff", "--no-renames"]
    if numstat:
        flags += ["--numstat", "-z"]
    tracked = git(["diff", *flags, base, "--"], path)
    if tracked.returncode != 0:
        raise RunError(f"git diff failed: {tracked.stderr.strip()}")
    chunks = [tracked.stdout]
    for name in untracked_files(path):
        result = git(["diff", "--no-index", *flags, "--", os.devnull, name], path)
        if result.returncode not in (0, 1):
            raise RunError(f"git diff failed for {name}: {result.stderr.strip()}")
        chunks.append(result.stdout)
    return "".join(chunks)


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
    """Work item ids to run for the given work item or planning topic ids.

    Unfinished prerequisites are pulled in transitively; items in `done` are left out.
    """
    by_id = {i.id: i for i in items}
    owner = {n: i.id for i in items for n in i.node_ids}
    chosen: set[str] = set()
    stack: list[str] = []
    for ident in only:
        target = ident if ident in by_id else owner.get(ident)
        if target is None:
            raise RunError(f"unknown work item or planning topic {ident!r}")
        stack.append(target)
    while stack:
        current = stack.pop()
        if current in chosen or current in done:
            continue
        chosen.add(current)
        stack.extend(by_id[current].depends_on)
    return chosen


@dataclass(frozen=True)
class RunPreparation:
    selected: set[str] | None
    waves: list[list[str]]


def prepare_run(
    graph: Graph,
    items: list[WorkItem],
    state_path: Path,
    *,
    only: list[str] | None = None,
    restart: bool = False,
) -> RunPreparation:
    """Prepare selection and preview waves without changing items or saved state."""
    selected = None
    if only is not None:
        saved = RunState.load(state_path) if not restart and state_path.exists() else None
        done = finished(graph, items, saved) if saved else set()
        selected = select(items, only, done)
    scoped = [
        replace(item, depends_on=[d for d in item.depends_on if selected is None or d in selected])
        for item in items
        if selected is None or item.id in selected
    ]
    return RunPreparation(selected, waves(scoped))


def agent_command(cfg: Config, prompt: str = PROMPT) -> list[str]:
    parts = shlex.split(cfg.runner_command)
    if not any("{prompt}" in p for p in parts):
        raise RunError("runner_command must contain {prompt}")
    return [p.replace("{prompt}", prompt) for p in parts]


def prepare_worktree(
    graph: Graph, prop: Proposal, items: list[WorkItem], props: dict[str, Proposal], repo: Path
) -> None:
    """Create the worktree off its first prerequisite branch and merge in every prerequisite.

    Safe to repeat: the worktree may already exist (`automagic worktrees --create`, or a retry
    after a failed merge), so each prerequisite not yet in its history is merged now.
    """
    bases = [props[d].branch for d in prop.item.depends_on]
    if not prop.path.exists():
        cmd = ["worktree", "add", "-b", prop.branch, str(prop.path), bases[0] if bases else "HEAD"]
        if git(["rev-parse", "--verify", "-q", prop.branch], repo).returncode == 0:
            cmd = ["worktree", "add", str(prop.path), prop.branch]
        result = git(cmd, repo)
        if result.returncode != 0:
            raise RunError(f"git {' '.join(cmd)}: {result.stderr.strip()}")
    for other in bases:
        if git(["merge-base", "--is-ancestor", other, "HEAD"], prop.path).returncode == 0:
            continue
        merged = git(["merge", "--no-edit", other], prop.path)
        if merged.returncode != 0:
            git(["merge", "--abort"], prop.path)
            error = merged.stderr.strip().splitlines()
            reason = "conflicts" if "CONFLICT" in merged.stdout else (error or ["failed"])[0]
            raise RunError(f"merging {other} into {prop.branch}: {reason}")
    brief_file = prop.path / BRIEF_PATH
    brief_file.parent.mkdir(parents=True, exist_ok=True)
    brief_file.write_text(brief(graph, prop, items))


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


def execute(prop: Proposal, cfg: Config, log: Path, base: str = "") -> tuple[str, str]:
    """Run and verify the agent's work, leaving commits to the user."""
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("")
    code = _run_logged(agent_command(cfg), prop.path, log, cfg.agent_timeout)
    if code != 0:
        return FAILED, f"agent exited with {code}"
    try:
        diff = worktree_diff(prop.path, base or "HEAD")
        if not diff:
            return FAILED, "agent made no changes"
        if cfg.verify_command.strip():
            verify = shlex.split(cfg.verify_command)
            code = _run_logged(verify, prop.path, log, cfg.agent_timeout)
            fixed = worktree_diff(prop.path, base or "HEAD")
            if code != 0 and fixed != diff:
                code = _run_logged(verify, prop.path, log, cfg.agent_timeout)
                fixed = worktree_diff(prop.path, base or "HEAD")
            if code != 0:
                return FAILED, f"verification failed ({cfg.verify_command})"
            if not fixed:
                return FAILED, "agent made no changes"
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
        run.hash = item_hash(graph, item)
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
            progressed = False
            for item in ready:
                prop = by_id[item.id]
                try:
                    waiting = [
                        d
                        for d in item.depends_on
                        if needs_manual_commit(by_id[d].path, state.items[d].base)
                    ]
                    if waiting:
                        update(
                            item.id,
                            PENDING,
                            f"waiting for manual commits in prerequisites: {', '.join(waiting)}",
                        )
                        continue
                    progressed = True
                    prepare_worktree(graph, prop, items, by_id, repo)
                except RunError as exc:
                    progressed = True
                    update(item.id, FAILED, str(exc))
                    continue
                if not state.items[item.id].base:
                    # Fixed once, after prerequisite merges, so reviews diff only the agent's work.
                    head = git(["rev-parse", "HEAD"], prop.path)
                    state.items[item.id].base = head.stdout.strip()
                log = log_dir / f"{item.id}.log"
                state.items[item.id].log = str(log)
                update(item.id, RUNNING)
                futures[item.id] = pool.submit(execute, prop, cfg, log, state.items[item.id].base)
            for item_id, future in futures.items():
                try:
                    status, detail = future.result()
                except Exception as exc:  # agent failures must not stop other items
                    status, detail = FAILED, f"unexpected error: {exc}"
                update(item_id, status, detail)
            if not progressed:
                break
    return state
