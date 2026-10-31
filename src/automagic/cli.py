"""Command line interface."""

from __future__ import annotations

import json
import os
import shlex
import webbrowser
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from automagic import guardrails, htmlview, interview, plandocs, render, runner, sizing, worktrees
from automagic import review as review_mod
from automagic.config import Config, load_config, state_dir
from automagic.merkle import Graph, GraphError
from automagic.progress import Reporter
from automagic.providers import ProviderError, get_provider
from automagic.scaffold import LANGUAGES, scaffold

app = typer.Typer(help="Scaffold repo baselines and plan work with a Merkle assumption graph.")

PathOpt = Annotated[Path, typer.Option("--path", help="Repository root.")]


def _detect_language(root: Path) -> str | None:
    if (root / "pyproject.toml").exists():
        return "python"
    if (root / "package.json").exists():
        return "typescript"
    return None


@app.command()
def init(
    path: PathOpt = Path(),
    language: Annotated[str | None, typer.Option(help=f"One of: {', '.join(LANGUAGES)}")] = None,
    name: Annotated[str | None, typer.Option(help="Project name.")] = None,
    force: Annotated[bool, typer.Option(help="Overwrite existing files.")] = False,
    dry_run: Annotated[bool, typer.Option(help="Show what would change.")] = False,
) -> None:
    """Set up pre-commit, ADRs, Copilot instructions, guardrails and slash commands."""
    root = path.resolve()
    language = language or _detect_language(root)
    if language is None:
        language = typer.prompt(f"Language ({'/'.join(LANGUAGES)})", default="python")
    cfg = load_config(root)
    try:
        actions = scaffold(root, language, name or root.name, cfg, force, dry_run)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    for action in actions:
        typer.echo(f"{action.status:>15}  {action.path}")
    if not dry_run:
        guardrails.update_index(root)
        typer.echo("\nNext: prek install && automagic check")


@app.command()
def check(path: PathOpt = Path()) -> None:
    """Verify context-size and grounding guardrails."""
    root = path.resolve()
    problems = guardrails.check(root, load_config(root))
    for problem in problems:
        typer.echo(problem, err=True)
    if problems:
        raise typer.Exit(1)
    typer.echo("guardrails ok")


@app.command()
def context(path: PathOpt = Path()) -> None:
    """Regenerate the docs/CONTEXT.md index."""
    root = path.resolve()
    try:
        changed = guardrails.update_index(root)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("updated docs/CONTEXT.md" if changed else "docs/CONTEXT.md already up to date")


def _repo_context(root: Path) -> str:
    parts = []
    index = root / "docs" / "CONTEXT.md"
    if index.exists():
        parts.append(index.read_text()[:4_000])
    tree = sorted(p.name for p in root.iterdir() if not p.name.startswith(".git"))
    parts.append("Top-level entries: " + ", ".join(tree[:60]))
    return "\n".join(parts)


def _ask(question: str) -> str | None:
    answer = typer.prompt(f"\n? {question}\n(answer, or /done to stop)")
    return None if answer.strip() in {"/done", "/quit"} else answer


def _show_proposals(graph: Graph, items: list[sizing.WorkItem], cfg: Config, root: Path) -> None:
    plan_dir = state_dir(root) / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "plan.md"
    history = render.load_history(plan_dir / "history.jsonl")
    plan_path.write_text(render.render_plan(graph, items, cfg, history))
    state_path = plan_dir / "run.json"
    state = runner.RunState.load(state_path) if state_path.exists() else None
    (plan_dir / "plan.html").write_text(htmlview.render_html(graph, items, state))
    plandocs.write(root, graph, items, history, cfg.max_changed_lines)
    if (root / "docs" / "CONTEXT.md").exists():
        guardrails.update_index(root)
    typer.echo(f"\nRoot hash {graph.root_hash()[:12]}; {len(items)} work item(s):")
    for item in items:
        flag = "  OVERSIZED" if item.oversized else ""
        typer.echo(
            f"  {item.id}: {', '.join(item.node_ids)} "
            f"(~{item.tokens} tokens, ~{item.lines} lines){flag}"
        )
    typer.echo(f"Plan written to {plan_path.relative_to(root)}")
    typer.echo(f"Graph view: {plan_path.with_name('plan.html').relative_to(root)}")
    typer.echo(f"Node documents: {plandocs.PLAN_DOCS.as_posix()}/ (commit these)")


def _load_graph(root: Path, *, include_checkpoint: bool = False) -> Graph:
    graph_path = state_dir(root) / "plan" / "graph.json"
    committed = plandocs.plan_dir(root) / "graph.json"
    if not graph_path.exists() and committed.exists():
        return Graph.load(committed, include_checkpoint=include_checkpoint)
    if not graph_path.exists():
        typer.echo("error: no plan found; run `automagic plan` first", err=True)
        raise typer.Exit(1)
    return Graph.load(graph_path, include_checkpoint=include_checkpoint)


def _resume_graph(root: Path) -> Graph:
    problem: str | None = None
    try:
        graph = _load_graph(root, include_checkpoint=True)
    except ValidationError as exc:
        graph = _load_graph(root, include_checkpoint=False)
        problem = str(exc)
    graph.check()
    if problem is None:
        try:
            graph.check_interview()
        except (GraphError, ValidationError) as exc:
            problem = str(exc)
    if problem is not None:
        typer.echo(f"Saved interview checkpoint cannot be continued: {problem}", err=True)
        if not typer.confirm(
            "Discard saved interview position and pending input, "
            "keeping agreed decisions and history, and start a fresh pass?"
        ):
            typer.echo("Nothing saved.")
            raise typer.Exit(2)
        graph.interview = None
    elif graph.interview is None and not graph.is_stable():
        typer.echo("No saved interview position; starting a fresh pass over saved decisions.")
    return graph


def _propose(root: Path, create: bool, yes: bool) -> None:
    cfg = load_config(root)
    graph = _load_graph(root)
    if not graph.is_stable():
        typer.echo("error: plan is not stable; run `automagic plan --resume`", err=True)
        raise typer.Exit(1)
    items = sizing.partition(graph, root, cfg)
    _show_proposals(graph, items, cfg, root)
    try:
        repo = worktrees.git_root(root)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    proposals = worktrees.propose(graph, items, repo)
    typer.echo("\nProposed worktrees:")
    for p in proposals:
        typer.echo("  " + shlex.join(p.command))
    if not create:
        typer.echo("\nRe-run with --create to create them.")
        return
    if not yes and not typer.confirm("Create these worktrees?"):
        raise typer.Exit(0)
    try:
        for created in worktrees.create(graph, proposals, items, repo):
            typer.echo(f"created {created}")
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _report_amendment(change: interview.AmendResult) -> None:
    typer.echo(f"\nEdited planning topics: {', '.join(change.edited) or '-'}")
    typer.echo(f"New planning topics: {', '.join(change.added) or '-'}")
    typer.echo(f"Settled planning topics re-opened for review: {', '.join(change.affected) or '-'}")
    for c in change.conflicts:
        typer.echo(f"contradicts {c['node']}: {c['reason']}", err=True)


@app.command()
def plan(
    goal: Annotated[str | None, typer.Argument(help="What you want to build.")] = None,
    path: PathOpt = Path(),
    resume: Annotated[bool, typer.Option(help="Continue the saved plan.")] = False,
    create: Annotated[bool, typer.Option(help="Offer to create worktrees when stable.")] = True,
    amend: Annotated[
        str | None,
        typer.Option(
            "--amend",
            help="Describe a change to a finished plan; "
            "only the planning topics it touches are re-examined.",
        ),
    ] = None,
    yes: Annotated[bool, typer.Option(help="Accept contradictions with agreed intent.")] = False,
) -> None:
    """Interview until all planning topics are settled, then propose worktrees."""
    root = path.resolve()
    cfg = load_config(root)
    graph_path = state_dir(root) / "plan" / "graph.json"
    history_path = graph_path.parent / "history.jsonl"
    provider = get_provider(cfg)
    reporter = Reporter()
    graph: Graph | None = None
    event_ids: set[str] | None = None

    def log_event(event: dict) -> None:
        nonlocal event_ids
        if event_ids is None:
            event_ids = set()
            for previous in render.load_history(history_path):
                previous_id = previous.get("id")
                if isinstance(previous_id, str):
                    event_ids.add(previous_id)
        event_id = event.get("id")
        if isinstance(event_id, str) and event_id in event_ids:
            return
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("a") as fh:
            fh.write(json.dumps(event) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        if isinstance(event_id, str):
            event_ids.add(event_id)

    try:
        if amend:
            graph = _load_graph(root, include_checkpoint=True)
            if not graph.is_stable():
                typer.echo("error: finish the open plan first: `automagic plan --resume`", err=True)
                raise typer.Exit(1)
            if graph.interview is not None and graph.interview.pending_event is not None:
                typer.echo(
                    "error: save the pending interview history with `plan --resume`", err=True
                )
                raise typer.Exit(1)
            graph.interview = None
            with reporter.working("Applying your change to the plan..."):
                change = interview.amend_graph(graph, provider, amend)
            _report_amendment(change)
            if (
                change.conflicts
                and not yes
                and not typer.confirm("This contradicts agreed intent. Apply the change anyway?")
            ):
                typer.echo("Nothing saved.")
                raise typer.Exit(0)
            graph.save(graph_path)
            log_event(
                {"event": "amended", "round": 0, "node": "-", "change": amend, **vars(change)}
            )
        elif resume:
            graph = _resume_graph(root)
        else:
            if not goal:
                goal = typer.prompt("Goal")
            with reporter.working("Drafting initial planning topics..."):
                graph = interview.seed_graph(provider, goal, _repo_context(root))
            graph.save(graph_path)
            history_path.unlink(missing_ok=True)

        result = interview.run_interview(
            graph,
            provider,
            _ask,
            cfg.max_rounds,
            lambda g: g.save(graph_path),
            log_event,
            reporter.handle,
        )
        reporter.close()
        if not result.converged:
            checkpoint = graph.interview
            if result.paused and checkpoint is not None:
                invalid = set(graph.invalid())
                left = sum(nid in invalid for nid in checkpoint.pending)
                message = (
                    f"Plan paused during interview pass {result.rounds}/{checkpoint.round_limit}; "
                    f"{left} {'topic' if left == 1 else 'topics'} pending this pass; "
                )
            else:
                message = (
                    f"Plan unsettled: interview pass limit reached after pass {result.rounds}; "
                    f"resume grants {cfg.max_rounds} more interview "
                    f"{'pass' if cfg.max_rounds == 1 else 'passes'}; "
                )
            unsettled = len(graph.invalid())
            typer.echo(
                f"\n{message}"
                f"{unsettled} {'topic' if unsettled == 1 else 'topics'} unsettled overall. "
                "Continue with `automagic plan --resume`.",
                err=True,
            )
            raise typer.Exit(2)
        for nid in sizing.oversized_nodes(graph, root, cfg):
            with reporter.working(f"Splitting oversized planning topic {nid}..."):
                interview.split_node(graph, provider, nid, cfg.max_changed_lines)
        for nid in graph.nodes:
            graph.validate(nid)
        graph.save(graph_path)
    except (KeyboardInterrupt, typer.Abort) as exc:
        typer.echo(
            "\nPlanning interrupted. Continue from the last saved state "
            "with `automagic plan --resume`."
            if graph is not None
            and (graph_path.exists() or (plandocs.plan_dir(root) / "graph.json").exists())
            else "\nPlanning interrupted before the initial graph was saved.",
            err=True,
        )
        raise typer.Exit(1) from exc
    except (ProviderError, ValueError, OSError) as exc:
        reporter.close()
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        reporter.close()
    typer.echo(
        f"\nPlanning settled after {result.rounds} interview "
        f"{'pass' if result.rounds == 1 else 'passes'}, "
        f"{result.questions_asked} {'question' if result.questions_asked == 1 else 'questions'}."
    )
    _propose(root, create=create, yes=False)


@app.command()
def show(
    path: PathOpt = Path(),
    node: Annotated[
        str | None, typer.Option(help="Print this planning topic's hash chain.")
    ] = None,
    open_html: Annotated[bool, typer.Option("--open", help="Open plan.html in a browser.")] = False,
) -> None:
    """Regenerate plan.md, plan.html and docs/plan/ (no AI calls) and print the plan."""
    root = path.resolve()
    graph = _load_graph(root)
    if node:
        if node not in graph.nodes:
            typer.echo(f"error: unknown node {node!r}", err=True)
            raise typer.Exit(1)
        typer.echo("\n".join(render.hash_chain(graph, node)))
        return
    cfg = load_config(root)
    items = sizing.partition(graph, root, cfg)
    _show_proposals(graph, items, cfg, root)
    typer.echo((state_dir(root) / "plan" / "plan.md").read_text())
    if open_html:
        webbrowser.open((state_dir(root) / "plan" / "plan.html").resolve().as_uri())


def _saved_run(root: Path) -> runner.RunState | None:
    path = state_dir(root) / "plan" / "run.json"
    return runner.RunState.load(path) if path.exists() else None


@app.command()
def run(
    path: PathOpt = Path(),
    dry_run: Annotated[bool, typer.Option(help="Show waves and commands only.")] = False,
    restart: Annotated[bool, typer.Option(help="Discard saved run state.")] = False,
    update: Annotated[
        bool,
        typer.Option(help="After a plan change, redo only work items whose nodes changed."),
    ] = False,
    yes: Annotated[bool, typer.Option(help="Skip the confirmation prompt.")] = False,
    max_concurrency: Annotated[int | None, typer.Option(help="Parallel agents per wave.")] = None,
    only: Annotated[
        list[str] | None,
        typer.Option(
            help="Work item or node ids, comma-separated or repeated. "
            "Unfinished prerequisites are added automatically."
        ),
    ] = None,
) -> None:
    """Run one agent per work item in its worktree, in dependency waves. Never pushes or merges."""
    root = path.resolve()
    cfg = load_config(root)
    if max_concurrency:
        cfg.max_concurrency = max_concurrency
    graph = _load_graph(root)
    if not graph.is_stable():
        typer.echo("error: plan is not stable; run `automagic plan --resume`", err=True)
        raise typer.Exit(1)
    items = sizing.partition(graph, root, cfg)
    try:
        repo = worktrees.git_root(root)
        props = worktrees.propose(graph, items, repo)
        selected = None
        if only:
            saved = None if restart else _saved_run(root)
            done = runner.finished(graph, items, saved) if saved else set()
            selected = runner.select(
                items, [x.strip() for v in only for x in v.split(",") if x.strip()], done
            )
        scoped = [
            replace(i, depends_on=[d for d in i.depends_on if selected is None or d in selected])
            for i in items
            if selected is None or i.id in selected
        ]
        plan_waves = runner.waves(scoped)
        command = runner.agent_command(cfg)
    except (RuntimeError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if selected is not None and not selected:
        typer.echo("nothing to run: the selected work items are already done")
        return
    for n, wave in enumerate(plan_waves, 1):
        typer.echo(f"wave {n}: {', '.join(wave)}")
    typer.echo(f"agent: {shlex.join(command)}")
    typer.echo(f"verify: {cfg.verify_command or '(none)'}; concurrency {cfg.max_concurrency}")
    if dry_run:
        return
    oversized = [i.id for i in items if i.oversized]
    if oversized:
        typer.echo(f"warning: oversized work items: {', '.join(oversized)}", err=True)
    if worktrees.is_dirty(repo):
        typer.echo(
            "warning: uncommitted changes are invisible to agents; commit AGENTS.md and the plan",
            err=True,
        )
    if not yes and not typer.confirm("Launch agents unattended in these worktrees?"):
        raise typer.Exit(0)
    state_path = state_dir(root) / "plan" / "run.json"
    state = runner.fresh_state(graph, _saved_run(root) if restart else None)
    if state_path.exists() and not restart:
        saved = runner.RunState.load(state_path)
        if saved.root_hash != graph.root_hash():
            stale = runner.stale_items(graph, items, saved)
            if not update:
                typer.echo(
                    "error: plan changed since the last run "
                    f"(stale work items: {', '.join(stale) or 'none'}); use --update to redo "
                    "only those, or --restart to redo everything",
                    err=True,
                )
                raise typer.Exit(1)
            runner.adopt_plan(graph, items, saved)
            typer.echo(f"plan changed: redoing {', '.join(stale) or 'no finished work items'}")
        state = saved
    state = runner.run_plan(
        graph,
        items,
        props,
        repo,
        cfg,
        state,
        state_path,
        state_dir(root) / "plan" / "runs",
        lambda i, r: typer.echo(f"{i}: {r.status} {r.detail}".rstrip()),
        selected,
    )
    shown = [i for i in items if selected is None or i.id in selected]
    typer.echo("\nReview each branch, then merge it yourself:")
    for item in shown:
        r = state.items[item.id]
        typer.echo(f"  {item.id} [{r.status}] {r.branch}  {r.detail}".rstrip())
    if any(state.items[i.id].status != runner.DONE for i in shown):
        typer.echo(
            "Re-run `automagic run` to retry failed items; logs are in .automagic/plan/runs/"
        )
        raise typer.Exit(1)


@app.command()
def review(
    target: Annotated[
        str | None, typer.Argument(help="Work item id, node id or branch; default: current branch.")
    ] = None,
    path: PathOpt = Path(),
    base: Annotated[str | None, typer.Option(help="Override the diff base commit.")] = None,
    verify: Annotated[bool, typer.Option(help="Also run verify_command in the worktree.")] = False,
    ai: Annotated[
        bool, typer.Option(help="Add an AI review of the diff via the provider.")
    ] = False,
) -> None:
    """Review a work item's branch against its nodes, the guardrails and the initial scope."""
    root = path.resolve()
    cfg = load_config(root)
    graph = _load_graph(root)
    items = sizing.partition(graph, root, cfg)
    try:
        repo = worktrees.git_root(root)
        props = worktrees.propose(graph, items, repo)
        prop = review_mod.resolve(graph, props, target, Path.cwd())
        state_path = state_dir(root) / "plan" / "run.json"
        state = runner.RunState.load(state_path) if state_path.exists() else None
        recorded = state.items.get(prop.item.id) if state else None
        base_ref = (
            base
            or (recorded.base if recorded else "")
            or review_mod.default_base(prop, {p.item.id: p for p in props}, repo)
        )
        result = review_mod.collect(
            graph,
            prop,
            base_ref,
            cfg,
            plan_changed=bool(state and state.root_hash != graph.root_hash()),
        )
        if verify:
            review_mod.verify(result, cfg)
        if ai:
            review_mod.ai_review(graph, result, get_provider(cfg), cfg)
    except (review_mod.ReviewError, RuntimeError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    report = review_mod.render(result)
    out = state_dir(root) / "plan" / "reviews" / f"{prop.item.id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    if recorded:
        recorded.review = result.verdict
        state.save(state_path)  # type: ignore[union-attr]
    typer.echo(report)
    typer.echo(f"Report written to {out.relative_to(root)}")
    if result.verdict == "fail":
        raise typer.Exit(1)


@app.command(name="worktrees")
def worktrees_cmd(
    path: PathOpt = Path(),
    create: Annotated[bool, typer.Option(help="Create the proposed worktrees.")] = False,
    yes: Annotated[bool, typer.Option(help="Skip the confirmation prompt.")] = False,
) -> None:
    """Propose (and optionally create) worktrees for the saved plan."""
    _propose(path.resolve(), create, yes)
