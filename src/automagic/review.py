"""Review a work item's branch against its Merkle nodes, the guardrails and the initial scope."""

from __future__ import annotations

import fnmatch
import shlex
import subprocess  # nosec B404
from dataclasses import dataclass, field
from pathlib import Path

from automagic import guardrails
from automagic.config import Config
from automagic.interview import parse_json
from automagic.merkle import Graph
from automagic.providers import Provider, ProviderError
from automagic.runner import BRIEF_PATH, git
from automagic.sizing import WorkItem
from automagic.worktrees import Proposal

ERROR, WARN, INFO = "error", "warn", "info"
GUARDED = (
    ".github/workflows/*",
    ".github/copilot-instructions.md",
    ".github/instructions/*",
    ".github/prompts/*",
    ".github/skills/*",
    ".pre-commit-config.yaml",
    "AGENTS.md",
    "docs/GUARDRAILS.md",
    "pyproject.toml",
    "eslint.config.js",
    ".automagic/*",
)
DRIFT_FACTOR = 1.5

AI_PROMPT = """You are a strict code reviewer. Judge whether the diff implements ONLY the work
described below, stays within the guardrails, and makes no ungrounded claims (invented files,
APIs or flags). Do not nitpick style that linters cover.
Reply with JSON only: {{"findings": [{{"severity": "error"|"warn"|"info", "file": str,
"message": str}}], "commit_message": "imperative subject under 72 chars\\n\\noptional body"}}

Goal: {goal}
Work item {item}:
{nodes}

Guardrails:
{guardrails}

Diff{truncated}:
{diff}
"""


@dataclass
class Finding:
    severity: str
    source: str
    message: str
    file: str = ""


@dataclass
class Review:
    item: WorkItem
    branch: str
    path: Path
    base: str
    files: list[str] = field(default_factory=list)
    lines: int = 0
    commits: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    commit_message: str = ""

    @property
    def verdict(self) -> str:
        severities = {f.severity for f in self.findings}
        return "fail" if ERROR in severities else "concerns" if WARN in severities else "pass"

    def add(self, severity: str, source: str, message: str, file: str = "") -> None:
        self.findings.append(Finding(severity, source, message, file))


class ReviewError(RuntimeError):
    pass


def resolve(graph: Graph, props: list[Proposal], target: str | None, cwd: Path) -> Proposal:
    """Find the work item for a work item id, node id or branch; default to the cwd's branch."""
    if target is None:
        head = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
        target = head.stdout.strip() if head.returncode == 0 else ""
        if not target:
            raise ReviewError("No target given and the current directory is not on a branch")
    for prop in props:
        if target in (prop.item.id, prop.branch):
            return prop
    if target in graph.nodes:
        for prop in props:
            if target in prop.item.node_ids:
                return prop
    known = ", ".join(p.item.id for p in props)
    raise ReviewError(f"{target!r} is not a work item, node or branch of the plan ({known})")


def default_base(prop: Proposal, props: dict[str, Proposal], repo: Path) -> str:
    """Fallback when the run recorded no base: fork point from the first prerequisite or HEAD."""
    if not prop.path.is_dir():
        raise ReviewError(f"Worktree {prop.path} does not exist; run `automagic run` first")
    ref = props[prop.item.depends_on[0]].branch if prop.item.depends_on else "HEAD"
    result = git(["merge-base", prop.branch, ref], repo)
    if result.returncode != 0:
        raise ReviewError(f"Cannot find a merge base for {prop.branch} and {ref}")
    return result.stdout.strip()


def _in_scope(path: str, declared: list[str]) -> bool:
    return any(
        path == d or fnmatch.fnmatch(path, d) or (d.endswith("/") and path.startswith(d))
        for d in declared
    )


def _numstat(path: Path, base: str, branch: str) -> list[tuple[str, int]]:
    result = git(["diff", "--numstat", f"{base}..{branch}"], path)
    if result.returncode != 0:
        raise ReviewError(f"git diff failed: {result.stderr.strip()}")
    rows = []
    for line in result.stdout.splitlines():
        added, deleted, name = line.split("\t", 2)
        rows.append((name, int(added) + int(deleted) if added.isdigit() else 0))
    return rows


def collect(
    graph: Graph,
    prop: Proposal,
    base: str,
    cfg: Config,
    plan_changed: bool = False,
) -> Review:
    if not prop.path.is_dir():
        raise ReviewError(f"Worktree {prop.path} does not exist; run `automagic run` first")
    review = Review(prop.item, prop.branch, prop.path, base)
    rows = _numstat(prop.path, base, prop.branch)
    review.files = [name for name, _ in rows]
    review.lines = sum(n for _, n in rows)
    log = git(["log", "--format=%s", f"{base}..{prop.branch}"], prop.path)
    review.commits = log.stdout.splitlines()
    nodes = [graph.nodes[n] for n in prop.item.node_ids if n in graph.nodes]

    if plan_changed:
        review.add(
            WARN, "plan", "The plan changed since this work item was run; scope may be stale"
        )
    if not review.files:
        review.add(ERROR, "scope", "No committed changes relative to the base")
    if git(
        ["status", "--porcelain", "--", ".", f":(exclude){BRIEF_PATH}"], prop.path
    ).stdout.strip():
        review.add(WARN, "scope", "Uncommitted changes exist in the worktree and are not reviewed")
    merges = git(["log", "--merges", "--format=%s", f"{base}..{prop.branch}"], prop.path)
    if merges.stdout.strip():
        review.add(WARN, "scope", "The agent created merge commits")
    if BRIEF_PATH in review.files:
        review.add(ERROR, "scope", "The agent brief was committed", BRIEF_PATH)

    declared = sorted({f for n in nodes for f in n.files})
    for name in review.files:
        if _in_scope(name, declared):
            continue
        if any(fnmatch.fnmatch(name, g) for g in GUARDED):
            review.add(
                ERROR, "scope", "Guardrail/CI/config file changed outside the declared scope", name
            )
        elif declared:
            review.add(WARN, "scope", "File is outside the declared scope", name)
    if declared:
        for d in declared:
            if not any(_in_scope(name, [d]) for name in review.files):
                review.add(INFO, "scope", "Declared file was not changed", d)
    else:
        review.add(
            INFO, "scope", "Nodes declare no files; only guardrail/CI/config files are checked"
        )

    if review.lines > cfg.max_changed_lines:
        review.add(
            ERROR,
            "size",
            f"{review.lines} changed lines exceed the budget of {cfg.max_changed_lines}",
        )
    estimate = sum(n.est_lines for n in nodes)
    if estimate and review.lines > estimate * DRIFT_FACTOR:
        review.add(WARN, "size", f"{review.lines} changed lines vs an estimate of {estimate}")

    changed = set(review.files)
    for problem in guardrails.check(prop.path, cfg):
        name = problem.split(":", 1)[0]
        if name in changed:
            review.add(ERROR, "guardrails", problem.split(": ", 1)[-1], name)
    review.commit_message = propose_message(prop.item, nodes)
    return review


def propose_message(item: WorkItem, nodes: list) -> str:
    titles = [n.title for n in nodes]
    subject = f"{item.id}: " + "; ".join(titles)
    if len(subject) > 72:
        subject = subject[:69].rstrip() + "..."
    body = "\n".join(f"- {n.title} ({n.id})" for n in nodes)
    return f"{subject}\n\n{body}" if len(nodes) > 1 else subject


def verify(review: Review, cfg: Config) -> None:
    if not cfg.verify_command.strip():
        return
    cmd = shlex.split(cfg.verify_command)
    try:
        result = subprocess.run(  # noqa: S603  # nosec B603
            cmd,
            cwd=review.path,
            capture_output=True,
            text=True,
            timeout=cfg.agent_timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        review.add(ERROR, "verify", f"{cfg.verify_command} could not run: {exc}")
        return
    if result.returncode != 0:
        tail = (result.stdout + result.stderr).strip().splitlines()[-5:]
        review.add(ERROR, "verify", f"{cfg.verify_command} failed: " + " | ".join(tail))


def ai_review(graph: Graph, review: Review, provider: Provider, cfg: Config) -> None:
    nodes = [graph.nodes[n] for n in review.item.node_ids if n in graph.nodes]
    node_text = "\n".join(
        f"- {n.id}: {n.title}\n  {n.content}\n  files: {', '.join(n.files) or '(unspecified)'}"
        for n in nodes
    )
    rules = review.path / "docs" / "GUARDRAILS.md"
    rules_text = rules.read_text()[: cfg.max_instruction_bytes] if rules.exists() else "(none)"
    diff = git(["diff", f"{review.base}..{review.branch}"], review.path).stdout
    budget = max(cfg.max_context_tokens * 4 // 2, 1_000)
    truncated = len(diff) > budget
    prompt = AI_PROMPT.format(
        goal=graph.goal,
        item=review.item.id,
        nodes=node_text,
        guardrails=rules_text,
        truncated=" (truncated)" if truncated else "",
        diff=diff[:budget],
    )
    try:
        data = parse_json(provider.complete(prompt))
    except ProviderError as exc:
        review.add(WARN, "ai", f"AI review unavailable: {exc}")
        return
    if truncated:
        review.add(WARN, "ai", f"Diff truncated to {budget} characters for the AI review")
    for raw in data.get("findings", []):
        severity = raw.get("severity") if raw.get("severity") in (ERROR, WARN, INFO) else WARN
        review.add(severity, "ai", str(raw.get("message", "")), str(raw.get("file", "")))
    message = str(data.get("commit_message") or "").strip()
    if message:
        review.commit_message = message


def render(review: Review) -> str:
    lines = [
        f"# Review: {review.item.id}",
        "",
        f"Verdict: **{review.verdict}**",
        f"Branch: `{review.branch}`",
        f"Worktree: `{review.path}`",
        f"Diff: `{review.base}..{review.branch}` ({len(review.files)} files, "
        f"{review.lines} changed lines, {len(review.commits)} commits)",
        f"Nodes: {', '.join(review.item.node_ids)}",
        "",
        "## Findings",
        "",
    ]
    if review.findings:
        lines += ["| Severity | Source | File | Message |", "|---|---|---|---|"]
        order = {ERROR: 0, WARN: 1, INFO: 2}
        for f in sorted(review.findings, key=lambda x: order[x.severity]):
            msg = f.message.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {f.severity} | {f.source} | {f.file or '-'} | {msg} |")
    else:
        lines.append("No findings.")
    lines += ["", "## Changed files", ""] + [f"- `{n}`" for n in review.files]
    lines += ["", "## Proposed commit message", "", "```", review.commit_message, "```", ""]
    return "\n".join(lines)
