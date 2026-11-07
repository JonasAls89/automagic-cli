"""Private plugin entrypoint. Resolve the bundle independently of the target project."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

BUNDLE = Path(__file__).resolve().parents[1]
SOURCES = BUNDLE / "src"
if not (SOURCES / "automagic/__init__.py").is_file():
    raise SystemExit(f"Missing bundled runtime sources: {SOURCES}")
# The Git-distributed plugin, not a cached/global editable install, owns its runtime.
sys.path.insert(0, str(SOURCES))

import automagic  # noqa: E402
from automagic import guardrails, runner, worktrees  # noqa: E402
from automagic.config import Config  # noqa: E402
from automagic.execution import (  # noqa: E402
    approve,
    git_root,
    load_plan,
    mutation_lock,
    require_approval,
    require_committed,
)
from automagic.scaffold import scaffold  # noqa: E402

if automagic.__file__ is None or not Path(automagic.__file__).resolve().is_relative_to(SOURCES):
    raise SystemExit("Loaded runtime does not belong to this plugin bundle")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="action", required=True)
    for action in ("init", "context", "worktrees", "run"):
        command = commands.add_parser(action)
        command.add_argument("--path", type=Path, default=Path.cwd())
        if action == "init":
            command.add_argument("--name")
            command.add_argument("--force", action="store_true")
            command.add_argument("--dry-run", action="store_true")
        elif action == "context":
            command.add_argument("--check", action="store_true")
        elif action == "worktrees":
            command.add_argument("--approve", action="store_true")
            command.add_argument("--create", action="store_true")
            command.add_argument("--yes", action="store_true")
        else:
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--yes", action="store_true")
            command.add_argument("--only", action="append")
            command.add_argument("--max-concurrency", type=int, default=3)
            command.add_argument("--runner-command", default=Config().runner_command)
    return result


def validate(root: Path, cfg: Config) -> None:
    if (root / "docs/CONTEXT.md").exists():
        guardrails.index_parts(root)
    problems = guardrails.check(root, cfg)
    if problems:
        raise ValueError("\n".join(problems))


def confirmed(yes: bool, question: str) -> bool:
    return yes or input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}


def show_proposals(props: list[worktrees.Proposal]) -> None:
    for prop in props:
        print(f"{prop.item.id}: {prop.item.title}\n  {shlex.join(prop.command)}")
        print(f"  verify: {prop.item.verify_command}; prerequisites: {prop.item.depends_on}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.path.resolve()
    cfg = Config()
    try:
        if args.action == "init":
            guardrails.index_parts(root)
            actions = scaffold(
                root,
                args.name or root.name,
                BUNDLE / "skills/init/assets",
                cfg,
                force=args.force,
                dry_run=args.dry_run,
            )
            for action in actions:
                print(f"{action.status:>15}  {action.path}")
            if not args.dry_run:
                guardrails.update_index(root)
                validate(root, cfg)
            print(
                "Setup: tailor pre-commit hooks and Dependabot ecosystems "
                "to the language/framework."
            )
            print("Next: /automagic:intent, then /mattpocock-skills:grill-with-docs")
            return 0
        if args.action == "context":
            if not args.check:
                changed = guardrails.update_index(root)
                print("updated docs/CONTEXT.md" if changed else "context index already up to date")
            validate(root, cfg)
            print("context guardrails passed")
            return 0
        root = git_root(root)
        if args.action == "run":
            cfg = Config(runner_command=args.runner_command, max_concurrency=args.max_concurrency)
        validate(root, cfg)
        plan = load_plan(root, cfg)
        props = worktrees.propose(plan, root)
        print(f"snapshot: {plan.digest}\nbase: {plan.base}")
        if args.action == "worktrees":
            show_proposals(props)
            if args.approve and args.create:
                raise ValueError("Approval and worktree creation are separate confirmed operations")
            if args.approve:
                if confirmed(args.yes, "Approve this exact committed execution snapshot?"):
                    require_committed(root, plan)
                    with mutation_lock(root):
                        validate(root, cfg)
                        approve(root, plan)
                    print("execution snapshot approved; creation still requires confirmation")
                else:
                    print("approval declined; no worktrees created")
            elif args.create:
                require_approval(root, plan)
                if confirmed(args.yes, "Create these worktrees?"):
                    with mutation_lock(root):
                        validate(root, cfg)
                        for path in worktrees.create(plan, props, root):
                            print(f"created {path}")
            else:
                print("preview only; no approval or worktree creation")
            return 0
        require_approval(root, plan)
        state_path = plan.runtime_path(root, "run.json")
        only = (
            [part.strip() for group in args.only for part in group.split(",")]
            if args.only
            else None
        )
        prepared = runner.prepare_run(plan, state_path, only=only)
        show_proposals([prop for prop in props if prop.item.id in prepared.selected])
        for number, wave in enumerate(prepared.waves, 1):
            print(f"wave {number}: {', '.join(wave)}")
        print(f"agent: {shlex.join(runner.agent_command(cfg))}; concurrency: {cfg.max_concurrency}")
        if not prepared.selected:
            print("nothing to run: selected work items are already complete")
            return 0
        if args.dry_run:
            return 0
        if not confirmed(args.yes, "Launch unattended agents, leaving commits to you?"):
            print("execution declined")
            return 0
        with mutation_lock(root):
            validate(root, cfg)
            require_approval(root, plan)
            state = runner.saved_state(plan, state_path)
            prepared = runner.prepare_run(plan, state_path, only=only)
            state = runner.run_plan(
                plan,
                props,
                root,
                cfg,
                state,
                state_path,
                on_update=lambda item_id, run: print(f"{item_id} [{run.status}]: {run.detail}"),
                only=prepared.selected,
            )
        for item_id, run in state.items.items():
            print(f"{item_id} [{run.status}] {run.branch}: {run.detail}")
            if run.status in (runner.FAILED, runner.BLOCKED):
                print(f"  log: {run.log or 'agent not launched'}")
        return int(
            any(
                state.items[item_id].status in (runner.FAILED, runner.BLOCKED)
                for item_id in prepared.selected
            )
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
