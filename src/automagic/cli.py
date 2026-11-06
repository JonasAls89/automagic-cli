"""Command line interface."""

from __future__ import annotations

import shlex
import webbrowser
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from automagic import (
    guardrails,
    interview,
    plandocs,
    planning_ui,
    publication,
    render,
    runner,
    sizing,
    worktrees,
)
from automagic import review as review_mod
from automagic.config import Config, load_config, state_dir
from automagic.durability import InterviewDurability
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
    """Set up tooling, pre-commit, a glossary, ADRs, guardrails and slash commands."""
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
        next_steps = "prek install && automagic check"
        if language == "python" and any(
            action.path == "pyproject.toml" and action.status in ("created", "overwritten")
            for action in actions
        ):
            next_steps = "uv sync && uv run prek install && automagic check"
        typer.echo(f"\nNext: {next_steps}")


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


def _repo_context(root: Path, graph: Graph | None = None) -> str:
    parts = [f"Repository root: {root}"]
    index = root / "docs" / "CONTEXT.md"
    if index.exists():
        parts.append(index.read_text()[:4_000])
    tree = sorted(p.name for p in root.iterdir() if not p.name.startswith(".git"))
    parts.append("Top-level entries: " + ", ".join(tree[:60]))
    paths = ["README.md", "GLOSSARY.md", "pyproject.toml", "package.json"]
    if graph is not None:
        paths.extend(f for node in graph.nodes.values() for f in node.files)
    remaining = 32_000
    for name in dict.fromkeys(paths):
        candidate = (root / name).resolve()
        if (
            not candidate.is_relative_to(root)
            or any(
                part.startswith(".") or part in {"node_modules", "__pycache__"}
                for part in candidate.relative_to(root).parts
            )
            or candidate.suffix
            not in {".md", ".py", ".ts", ".tsx", ".js", ".json", ".toml", ".yaml", ".yml"}
        ):
            parts.append(f"{name}: not included (outside readable repository sources)")
            continue
        if not candidate.is_file():
            parts.append(f"{name}: not present as a file")
            continue
        if remaining <= 0:
            parts.append("Source context limit reached; omitted facts remain unverified.")
            break
        with candidate.open() as source:
            text = source.read(min(4_000, remaining))
        parts.append(f"Source {name} (up to 4000 characters):\n{text}")
        remaining -= len(text)
    return "\n".join(parts)


def _ask(question: str) -> str | None:
    answer = typer.prompt(f"\n? {question}\n(answer, or /done to stop)")
    return None if answer.strip() in {"/done", "/quit"} else answer


def _show_proposals(graph: Graph, items: list[sizing.WorkItem], cfg: Config, root: Path) -> str:
    published = publication.publish(root, graph, items, cfg)
    typer.echo(f"\nRoot hash {graph.root_hash()[:12]}; {len(items)} work item(s):")
    for item in items:
        flag = "  OVERSIZED" if item.oversized else ""
        typer.echo(
            f"  {item.id}: {', '.join(item.node_ids)} "
            f"(~{item.tokens} tokens, ~{item.lines} lines){flag}"
        )
    typer.echo(f"Graph view: {published.html_path.relative_to(root)}")
    typer.echo(
        f"Node documents: {published.documents_dir.relative_to(root).as_posix()}/ (commit these)"
    )
    return published.markdown


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
        if not planning_ui.recovery_consent(
            "Saved interview checkpoint cannot be continued.",
            "/reset",
            "Discard position and pending input; keep agreed decisions and history",
            problem,
        ):
            typer.echo("Nothing saved. Existing graph and history were left unchanged.")
            raise typer.Exit(2)
        graph.interview = None
    elif graph.interview is None or graph.interview.planning is None:
        typer.echo(
            "Legacy plan: historical question counts and final approval are unknown. "
            "No new-style interview position is saved."
        )
        if not planning_ui.recovery_consent(
            "Start a bounded interview?",
            "/start",
            "Keep decisions/history; discard old pending position/input; "
            "start new question accounting",
            "Legacy plans have no reliable historical question count or final approval. "
            "Starting preserves agreed decisions and history, but replaces the old pending "
            "position and input. Stopping leaves all existing saved files unchanged.",
        ):
            typer.echo("Nothing saved. Existing graph and history were left unchanged.")
            raise typer.Exit(2)
        InterviewDurability(state_dir(root) / "plan" / "graph.json").flush_event(graph)
        graph.interview = None
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
    graph_path = state_dir(root) / "plan" / "graph.json"
    history_path = graph_path.parent / "history.jsonl"
    durability = InterviewDurability(graph_path)
    reporter = Reporter()
    graph: Graph | None = None

    try:
        cfg = load_config(root)
        provider = get_provider(cfg)
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
            if (
                graph.interview is not None
                and graph.interview.planning is not None
                and graph.interview.planning.phase != "approved"
            ):
                typer.echo(
                    "error: approve the open plan first: `automagic plan --resume`", err=True
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
            durability.save(graph)
            durability.append(
                {"event": "amended", "round": 0, "node": "-", "change": amend, **vars(change)}
            )
        elif resume:
            graph = _resume_graph(root)
        else:
            if not goal:
                goal = typer.prompt("Goal")
            with reporter.working("Drafting initial planning topics..."):
                graph = interview.seed_graph(provider, goal, _repo_context(root))
            durability.save(graph)
            history_path.unlink(missing_ok=True)

        result = interview.run_interview(
            graph,
            provider,
            _ask,
            cfg.max_rounds,
            on_progress=reporter.handle,
            durability=durability,
            max_questions=cfg.max_questions,
            confirm=lambda summary: planning_ui.confirm_plan(graph, root, summary),
            continue_interview=lambda reason, summary: planning_ui.continue_plan(
                graph, root, reason, summary, cfg.max_rounds
            ),
            repository_context=lambda: _repo_context(root, graph),
        )
        reporter.close()
        if not result.converged:
            checkpoint = graph.interview
            if checkpoint is None or checkpoint.planning is None:
                raise GraphError("Bounded interview returned without its saved position")
            typer.echo(
                "\nPlan saved but not approved. "
                f"{checkpoint.planning.remaining} questions remaining. "
                f"Resume with `{planning_ui.resume_command(root)}`.",
                err=True,
            )
            raise typer.Exit(2)
        for nid in sizing.oversized_nodes(graph, root, cfg):
            with reporter.working(f"Splitting oversized planning topic {nid}..."):
                interview.split_node(graph, provider, nid, cfg.max_changed_lines)
        for nid in graph.nodes:
            graph.validate(nid)
        if graph.interview is not None and graph.interview.planning is not None:
            graph.interview.planning.approved_hash = graph.root_hash()
        durability.save(graph)
    except (KeyboardInterrupt, typer.Abort) as exc:
        typer.echo(
            "\nPlanning interrupted. Continue from the last saved state with "
            f"`{planning_ui.resume_command(root)}`."
            if graph is not None
            and (graph_path.exists() or (plandocs.plan_dir(root) / "graph.json").exists())
            else "\nPlanning interrupted before the initial graph was saved.",
            err=True,
        )
        raise typer.Exit(1) from exc
    except (ProviderError, ValueError, OSError) as exc:
        reporter.close()
        category = (
            "provider failure"
            if isinstance(exc, ProviderError)
            else "filesystem failure"
            if isinstance(exc, OSError)
            else "invalid planning data or configuration"
        )
        typer.echo(f"error: {category}: {exc}", err=True)
        typer.echo(
            "Recovery: only successfully saved input is recoverable; "
            "input that failed to save must be entered again. "
            f"Fix the reported cause, then resume with `{planning_ui.resume_command(root)}`. "
            "If no plan was saved, rerun with your goal. No automatic retry is performed.",
            err=True,
        )
        raise typer.Exit(1) from exc
    finally:
        reporter.close()
    total_questions = (
        graph.interview.planning.answered
        if graph.interview is not None and graph.interview.planning is not None
        else result.questions_asked
    )
    typer.echo(
        f"\nPlanning settled after {result.rounds} interview "
        f"{'pass' if result.rounds == 1 else 'passes'}, "
        f"{total_questions} {'question' if total_questions == 1 else 'questions'}."
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
    """Print the plan and regenerate plan.html and docs/plan/ (no AI calls)."""
    root = path.resolve()
    graph = _load_graph(root)
    if node:
        if node not in graph.nodes:
            typer.echo(f"error: unknown planning topic {node!r}", err=True)
            raise typer.Exit(1)
        typer.echo("\n".join(render.hash_chain(graph, node)))
        return
    cfg = load_config(root)
    items = sizing.partition(graph, root, cfg)
    typer.echo(_show_proposals(graph, items, cfg, root))
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
        typer.Option(
            help="After a plan change, redo only work items whose planning topics changed."
        ),
    ] = False,
    yes: Annotated[bool, typer.Option(help="Skip the confirmation prompt.")] = False,
    max_concurrency: Annotated[int | None, typer.Option(help="Parallel agents per wave.")] = None,
    only: Annotated[
        list[str] | None,
        typer.Option(
            help="Work item or planning topic ids, comma-separated or repeated. "
            "Unfinished prerequisites are added automatically."
        ),
    ] = None,
) -> None:
    """Run and verify work items in dependency waves. Leave changes uncommitted for review."""
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
        requested = [x.strip() for v in only for x in v.split(",") if x.strip()] if only else None
        prepared = runner.prepare_run(
            graph,
            items,
            state_dir(root) / "plan" / "run.json",
            only=requested,
            restart=restart,
        )
        selected = prepared.selected
        command = runner.agent_command(cfg)
    except (RuntimeError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if selected is not None and not selected:
        typer.echo("nothing to run: the selected work items are already done")
        return
    for n, wave in enumerate(prepared.waves, 1):
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
    typer.echo("\nReview each branch, then commit and merge it yourself:")
    for item in shown:
        r = state.items[item.id]
        typer.echo(f"  {item.id} [{r.status}] {r.branch}  {r.detail}".rstrip())
    if any(state.items[i.id].status == runner.PENDING for i in shown):
        typer.echo(
            "Waiting for prerequisites: review with `automagic review <id> --verify`, "
            "commit their changes, then re-run `automagic run` to resume."
        )
    if any(state.items[i.id].status in (runner.FAILED, runner.BLOCKED) for i in shown):
        typer.echo(
            "Re-run `automagic run` to retry failed items; logs are in .automagic/plan/runs/"
        )
        raise typer.Exit(1)


@app.command()
def review(
    target: Annotated[
        str | None,
        typer.Argument(help="Work item id, planning topic id or branch; default: current branch."),
    ] = None,
    path: PathOpt = Path(),
    base: Annotated[str | None, typer.Option(help="Override the diff base commit.")] = None,
    verify: Annotated[bool, typer.Option(help="Also run verify_command in the worktree.")] = False,
    ai: Annotated[
        bool, typer.Option(help="Add an AI review of the diff via the provider.")
    ] = False,
) -> None:
    """Review a work item's branch against its planning topics, guardrails and initial scope."""
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
        verification_findings: list[review_mod.Finding] = []
        if verify:
            checks = review_mod.Review(prop.item, prop.branch, prop.path, base_ref)
            review_mod.verify(checks, cfg)
            verification_findings = checks.findings
        result = review_mod.collect(
            graph,
            prop,
            base_ref,
            cfg,
            plan_changed=bool(state and state.root_hash != graph.root_hash()),
        )
        result.findings.extend(verification_findings)
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
