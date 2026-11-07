"""Run one agent per work item in its worktree, in dependency waves."""

from __future__ import annotations

import os
import shlex
import subprocess  # nosec B404
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from automagic.config import Config
from automagic.execution import (
    Plan,
    WorkItem,
    atomic_write,
    git,
    require_approval,
    require_input_contents,
)
from automagic.guardrails import check
from automagic.worktrees import Proposal, brief, create

PENDING: Final = "pending"
RUNNING: Final = "running"
DONE: Final = "done"
FAILED: Final = "failed"
BLOCKED: Final = "blocked"
RunStatus = Literal["pending", "running", "done", "failed", "blocked"]
BRIEF_PATH = ".automagic/BRIEF.md"

PROMPT = (
    "Read .automagic/BRIEF.md and AGENTS.md (if present). Implement only the work item "
    "described in the brief; do not expand scope. Run the project's quality gates, then leave "
    "your changes in the worktree for human review and commit grouping. Preserve existing "
    "staging. Do not stage, commit, push, merge, rebase or switch branches."
)


class ItemRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: RunStatus = "pending"
    branch: str = ""
    path: str = ""
    log: str = ""
    detail: str = ""
    base: str = Field(default="", pattern=r"^(?:[a-f0-9]{40,64})?$")


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot: str = Field(pattern=r"^[a-f0-9]{64}$")
    items: dict[str, ItemRun] = Field(default_factory=dict)

    def save(self, path: Path) -> None:
        atomic_write(path, self.model_dump_json(indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> RunState:
        return cls.model_validate_json(path.read_text())


class RunError(RuntimeError):
    pass


def saved_state(plan: Plan, path: Path) -> RunState:
    state = RunState.load(path) if path.exists() else RunState(snapshot=plan.digest)
    if state.snapshot != plan.digest or set(state.items) - {item.id for item in plan.items}:
        raise RunError("Run state does not match the approved snapshot")
    return state


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


def changed_lines(output: str) -> int:
    fields = iter(output.split("\0"))
    total = 0
    for row in filter(None, fields):
        columns = row.split("\t", 2)
        if len(columns) != 3:
            raise RunError("Invalid Git numstat output")
        added, removed, name = columns
        if not name:
            try:
                next(fields)
                next(fields)
            except StopIteration as exc:
                raise RunError("Incomplete Git numstat path pair") from exc
        if not added.isdigit() or not removed.isdigit():
            raise RunError("binary changes require explicit human review")
        total += int(added) + int(removed)
    return total


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


def select(items: tuple[WorkItem, ...], only: list[str], done: set[str]) -> set[str]:
    """Select stable work-item IDs and unfinished prerequisites."""
    by_id = {i.id: i for i in items}
    chosen: set[str] = set()
    stack: list[str] = []
    for ident in only:
        if ident not in by_id:
            raise RunError(f"unknown work item {ident!r}")
        stack.append(ident)
    while stack:
        current = stack.pop()
        if current in chosen or current in done:
            continue
        chosen.add(current)
        stack.extend(by_id[current].depends_on)
    return chosen


@dataclass(frozen=True)
class RunPreparation:
    selected: set[str]
    waves: list[list[str]]


def prepare_run(
    plan: Plan,
    state_path: Path,
    *,
    only: list[str] | None = None,
) -> RunPreparation:
    """Prepare selection and preview waves without changing items or saved state."""
    state = saved_state(plan, state_path)
    done = {item_id for item_id, run in state.items.items() if run.status == DONE}
    selected = select(plan.items, only if only is not None else [i.id for i in plan.items], done)
    scoped = [
        item.model_copy(update={"depends_on": [d for d in item.depends_on if d in selected]})
        for item in plan.items
        if item.id in selected
    ]
    return RunPreparation(selected, waves(scoped))


def agent_command(cfg: Config, prompt: str = PROMPT) -> list[str]:
    parts = shlex.split(cfg.runner_command)
    if not any("{prompt}" in p for p in parts):
        raise RunError("runner_command must contain {prompt}")
    return [p.replace("{prompt}", prompt) for p in parts]


def prepare_worktree(plan: Plan, prop: Proposal, props: dict[str, Proposal], repo: Path) -> None:
    """Create the worktree off its first prerequisite branch and merge in every prerequisite.

    Existing worktrees must have orchestrator-owned records. Merge only prerequisites
    not already in history, including on retries.
    """
    bases = [props[d].branch for d in prop.item.depends_on]
    create(plan, [prop], repo)
    if git(["rev-parse", "--verify", "-q", "MERGE_HEAD"], prop.path).returncode == 0:
        raise RunError(f"Finish the existing merge in {prop.path} before resuming")
    for other in bases:
        if git(["merge-base", "--is-ancestor", other, "HEAD"], prop.path).returncode == 0:
            continue
        merged = git(["merge", "--no-edit", other], prop.path)
        if merged.returncode != 0:
            aborted = git(["merge", "--abort"], prop.path)
            if aborted.returncode:
                raise RunError(f"merge failed; abort failed: {aborted.stderr.strip()}")
            error = merged.stderr.strip().splitlines()
            reason = "conflicts" if "CONFLICT" in merged.stdout else (error or ["failed"])[0]
            raise RunError(f"merging {other} into {prop.branch}: {reason}")
    require_input_contents(prop.path, plan)
    brief_file = prop.path / BRIEF_PATH
    brief_file.parent.mkdir(parents=True, exist_ok=True)
    brief_file.write_text(brief(plan, prop))


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


def execute(prop: Proposal, cfg: Config, log: Path, base: str = "") -> tuple[RunStatus, str]:
    """Run and verify the agent's work, leaving commits to the user."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as stream:
        stream.write("\n--- execution attempt ---\n")
    code = _run_logged(agent_command(cfg), prop.path, log, cfg.agent_timeout)
    if code != 0:
        return FAILED, f"agent exited with {code}"
    try:
        diff = worktree_diff(prop.path, base or "HEAD")
        if not diff:
            return FAILED, "agent made no changes"
        verify = shlex.split(prop.item.verify_command)
        code = _run_logged(verify, prop.path, log, cfg.agent_timeout)
        fixed = worktree_diff(prop.path, base or "HEAD")
        if code != 0 and fixed != diff:
            code = _run_logged(verify, prop.path, log, cfg.agent_timeout)
            fixed = worktree_diff(prop.path, base or "HEAD")
        if code != 0:
            return FAILED, f"verification failed ({prop.item.verify_command})"
        if not fixed:
            return FAILED, "agent made no changes"
        changed = git(["diff", "--name-only", "-z", base or "HEAD", "--"], prop.path)
        if changed.returncode:
            raise RunError(f"git diff failed: {changed.stderr.strip()}")
        outside = set(filter(None, changed.stdout.split("\0"))) | set(untracked_files(prop.path))
        outside -= set(prop.item.files)
        if outside:
            return FAILED, f"changes outside approved files: {', '.join(sorted(outside))}"
        counts = worktree_diff(prop.path, base or "HEAD", numstat=True)
        total = changed_lines(counts)
        if total > cfg.max_changed_lines:
            return FAILED, f"changes exceed {cfg.max_changed_lines} lines"
        problems = check(prop.path, cfg)
        if problems:
            return FAILED, "; ".join(problems)
    except RunError as exc:
        return FAILED, str(exc)
    return DONE, ""


def run_plan(
    plan: Plan,
    props: list[Proposal],
    repo: Path,
    cfg: Config,
    state: RunState,
    state_path: Path,
    on_update: Callable[[str, ItemRun], None] | None = None,
    only: set[str] | None = None,
) -> RunState:
    require_approval(repo, plan)
    items = plan.items
    by_id = {p.item.id: p for p in props}
    for item in items:
        run = state.items.setdefault(item.id, ItemRun())
        if (run.branch and run.branch != by_id[item.id].branch) or (
            run.path and run.path != str(by_id[item.id].path)
        ):
            raise RunError(f"{item.id}: saved worktree identity changed")
        run.branch, run.path = by_id[item.id].branch, str(by_id[item.id].path)
        if run.status != DONE and (only is None or item.id in only):
            run.status, run.detail = PENDING, ""

    def update(
        item_id: str,
        status: RunStatus,
        detail: str = "",
    ) -> None:
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
                    for dependency in item.depends_on:
                        require_input_contents(by_id[dependency].path, plan)
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
                    prepare_worktree(plan, prop, by_id, repo)
                except (RuntimeError, ValueError) as exc:
                    progressed = True
                    update(item.id, FAILED, str(exc))
                    continue
                if not state.items[item.id].base:
                    # Fixed once, after prerequisite merges, so reviews diff only the agent's work.
                    head = git(["rev-parse", "HEAD"], prop.path)
                    if head.returncode:
                        update(item.id, FAILED, f"cannot record base: {head.stderr.strip()}")
                        continue
                    state.items[item.id].base = head.stdout.strip()
                log = plan.runtime_path(repo, f"logs/{item.id}.log")
                state.items[item.id].log = str(log)
                update(item.id, RUNNING)
                futures[item.id] = pool.submit(execute, prop, cfg, log, state.items[item.id].base)
            for item_id, future in futures.items():
                error = future.exception()
                if error is None:
                    status, detail = future.result()
                else:
                    status, detail = FAILED, f"unexpected execution error: {error}"
                update(item_id, status, detail)
            if not progressed:
                break
    return state
