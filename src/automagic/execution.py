"""Reviewed Markdown inputs and helper-owned execution approvals."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess  # nosec B404
import tempfile
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from automagic.config import Config

INDEX = "docs/work/_index.md"
GUIDANCE = (
    "AGENTS.md",
    "GLOSSARY.md",
    "docs/CONTEXT.md",
    "docs/GUARDRAILS.md",
    ".github/copilot-instructions.md",
)
_METADATA = re.compile(r"^```toml\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
_ID = r"^[a-z][a-z0-9-]{0,63}$"


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # nosec B603 B607
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def git_root(start: Path) -> Path:
    result = git(["rev-parse", "--show-toplevel"], start)
    if result.returncode:
        raise RuntimeError(f"Not inside a Git repository: {result.stderr.strip()}")
    return Path(result.stdout.strip()).resolve()


def repository_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if (
        not parts
        or PurePosixPath(relative).is_absolute()
        or ".." in parts
        or ".git" in parts
        or "\\" in relative
        or "\n" in relative
        or "\0" in relative
    ):
        raise ValueError(f"Invalid repository-relative path: {relative!r}")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or path.resolve() == root.resolve():
        raise ValueError(f"Path escapes repository: {relative}")
    return path


def state_path(root: Path, relative: str) -> Path:
    path = repository_path(root, f".automagic/execution/{relative}")
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError(f"Execution state cannot use symlinks: {parent}")
    return path


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def mutation_lock(root: Path) -> Iterator[None]:
    path = state_path(root, "mutation.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stream = path.open("x")
    except FileExistsError as exc:
        raise RuntimeError(
            f"Execution is locked: {path}. Check for active helpers/workers before "
            "explicitly removing an abandoned lock."
        ) from exc
    try:
        with stream:
            stream.write(f"helper PID: {os.getpid()}\n")
        yield
    finally:
        path.unlink()


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True, strict=True)

    @field_validator("*")
    @classmethod
    def nonempty_strings(cls, value: object) -> object:
        if isinstance(value, list):
            if any(isinstance(item, str) and not item.strip() for item in value):
                raise ValueError("List entries must not be blank")
            if len(value) != len(set(value)):
                raise ValueError("List entries must be distinct")
        return value

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer, not a boolean or string")
        return value


class WorkItem(Metadata):
    id: str = Field(pattern=_ID)
    title: str = Field(min_length=1)
    depends_on: list[str]
    files: list[str] = Field(min_length=1)
    est_lines: int = Field(gt=0)
    acceptance_criteria: list[str] = Field(min_length=1)
    verify_command: str = Field(min_length=1)
    verify_sources: list[str] = Field(min_length=1)
    content: str = Field(min_length=1)

    @field_validator("verify_command")
    @classmethod
    def valid_command(cls, value: str) -> str:
        if not shlex.split(value):
            raise ValueError("Verification command is required")
        return value


class Index(Metadata):
    schema_version: Literal[1]
    goal: str = Field(min_length=1)
    sources: list[str] = Field(min_length=1)
    items: list[str] = Field(min_length=1)
    blockers: list[str]


class Approval(Metadata):
    schema_version: Literal[1]
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    base: str = Field(pattern=r"^[a-f0-9]{40,64}$")


def context_paths(index: Index, inputs: dict[str, bytes], item: WorkItem) -> set[str]:
    """Agent context excludes other work items' verification evidence."""
    return {
        INDEX,
        "INTENT.md",
        *index.sources,
        *item.verify_sources,
        *(name for name in GUIDANCE if name in inputs),
    }


@dataclass(frozen=True)
class Plan:
    index: Index
    items: tuple[WorkItem, ...]
    inputs: dict[str, bytes]
    base: str
    digest: str

    @property
    def goal(self) -> str:
        return self.index.goal

    def runtime_path(self, root: Path, name: str) -> Path:
        return state_path(root, f"{self.digest}/{name}")


def _document(text: str, name: str) -> tuple[dict[str, object], str]:
    matches = list(_METADATA.finditer(text))
    if len(matches) != 1:
        raise ValueError(f"{name}: exactly one fenced TOML metadata block is required")
    match = matches[0]
    return tomllib.loads(match[1]), (text[: match.start()] + text[match.end() :]).strip()


def load_plan(root: Path, cfg: Config | None = None) -> Plan:
    cfg = cfg or Config()
    inputs: dict[str, bytes] = {}

    def read(relative: str) -> str:
        path = repository_path(root, relative)
        if not path.is_file():
            raise ValueError(f"Missing execution input: {relative}")
        if relative not in inputs:
            inputs[relative] = path.read_bytes()
        if path.suffix == ".md" and len(inputs[relative]) > cfg.max_instruction_bytes:
            raise ValueError(f"{relative}: exceeds {cfg.max_instruction_bytes} bytes")
        return inputs[relative].decode("utf-8")

    if not (root / INDEX).is_file():
        raise ValueError(f"Missing {INDEX}; finish external planning, then use worktrees.")
    metadata, _ = _document(read(INDEX), INDEX)
    index = Index.model_validate(metadata)
    if index.blockers:
        raise ValueError(f"Resolve design blockers with external grilling: {index.blockers}")
    read("INTENT.md")
    for relative in index.sources:
        if relative.startswith(".automagic/"):
            raise ValueError(f"Runtime state is not a design input: {relative}")
        read(relative)
    for relative in GUIDANCE:
        if (root / relative).is_file():
            read(relative)
    items: dict[str, WorkItem] = {}
    for relative in index.items:
        path = repository_path(root, relative)
        if path.parent.resolve() != (root / "docs/work").resolve() or relative == INDEX:
            raise ValueError(f"Work-item documents must be directly in docs/work: {relative}")
        metadata, content = _document(read(relative), relative)
        if "content" in metadata:
            raise ValueError(f"{relative}: content belongs outside the metadata block")
        item = WorkItem.model_validate({**metadata, "content": content})
        if item.id in items:
            raise ValueError(f"Duplicate work-item ID: {item.id}")
        for name in item.files:
            target = repository_path(root, name)
            if (
                target.is_dir()
                or name == ".automagic/BRIEF.md"
                or name.startswith(".automagic/execution/")
            ):
                raise ValueError(f"{item.id}: declare individual non-runtime files: {name}")
        evidence = [read(name) for name in item.verify_sources]
        if not any(item.verify_command in text for text in evidence):
            raise ValueError(f"{item.id}: verification command is not in verify_sources")
        context = context_paths(index, inputs, item) | {relative}
        tokens = 2_000 + sum((len(inputs[name]) + 3) // 4 for name in context)
        tokens += item.est_lines * 10
        tokens += sum(
            (repository_path(root, name).stat().st_size + 3) // 4
            for name in item.files
            if repository_path(root, name).is_file()
        )
        if item.est_lines > cfg.max_changed_lines or tokens > cfg.max_context_tokens:
            raise ValueError(f"{item.id}: oversized work item; review a smaller split")
        items[item.id] = item
    ordered: list[WorkItem] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item_id: str) -> None:
        if item_id in visiting:
            raise ValueError(f"Work-item dependency cycle at {item_id}")
        if item_id in visited:
            return
        if item_id not in items:
            raise ValueError(f"Unknown prerequisite: {item_id}")
        visiting.add(item_id)
        for dependency in items[item_id].depends_on:
            visit(dependency)
        visiting.remove(item_id)
        visited.add(item_id)
        ordered.append(items[item_id])

    for item_id in sorted(items):
        visit(item_id)
    head = git(["rev-parse", "HEAD"], root)
    if head.returncode:
        raise RuntimeError(f"An initial commit is required: {head.stderr.strip()}")
    base = head.stdout.strip()
    payload = {
        "base": base,
        "inputs": {k: hashlib.sha256(v).hexdigest() for k, v in inputs.items()},
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return Plan(index, tuple(ordered), inputs, base, digest)


def require_input_contents(root: Path, plan: Plan) -> None:
    for relative, content in plan.inputs.items():
        path = repository_path(root, relative)
        if not path.is_file() or path.read_bytes() != content:
            raise ValueError(f"Input changed during preparation: {relative}; preview again")


def require_committed(root: Path, plan: Plan) -> None:
    require_input_contents(root, plan)
    head = git(["rev-parse", "HEAD"], root)
    if head.returncode or head.stdout.strip() != plan.base:
        raise ValueError("Base commit changed; preview and approve the new snapshot")
    tracked = git(
        ["--literal-pathspecs", "ls-files", "--stage", "-z", "--error-unmatch", "--", *plan.inputs],
        root,
    )
    if tracked.returncode:
        raise ValueError(f"Commit all handoff inputs first: {tracked.stderr.strip()}")
    for entry in filter(None, tracked.stdout.split("\0")):
        mode, separator, name = entry.partition("\t")
        if not separator or mode.split()[0] not in {"100644", "100755"}:
            raise ValueError(f"Handoff inputs must be regular tracked files: {name}")
    diff = git(["--literal-pathspecs", "diff", "--quiet", "HEAD", "--", *plan.inputs], root)
    if diff.returncode == 1:
        raise ValueError("Commit all handoff input changes before approval or execution")
    if diff.returncode:
        raise RuntimeError(f"Cannot check committed inputs: {diff.stderr.strip()}")


def approve(root: Path, plan: Plan) -> None:
    require_committed(root, plan)
    approval = Approval(schema_version=1, digest=plan.digest, base=plan.base)
    atomic_write(state_path(root, "approval.json"), approval.model_dump_json(indent=2) + "\n")


def require_approval(root: Path, plan: Plan) -> None:
    path = state_path(root, "approval.json")
    if not path.is_file():
        raise ValueError("No reviewed execution approval; use worktrees to approve this snapshot")
    approval = Approval.model_validate_json(path.read_text())
    if approval.digest != plan.digest or approval.base != plan.base:
        raise ValueError("Approved inputs changed; review and approve a new execution snapshot")
    require_committed(root, plan)
