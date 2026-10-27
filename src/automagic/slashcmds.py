"""Slash-command definitions rendered as Copilot prompt files and skills."""

from __future__ import annotations

COMMANDS: dict[str, tuple[str, str]] = {
    "init": (
        "Set up baseline repo files (pre-commit, ADRs, Copilot instructions, guardrails)",
        "Run `automagic init` in the terminal from the repository root. Add `--dry-run` first "
        "if the user wants a preview, and `--language python|typescript` if detection fails. "
        "Summarise created and skipped files. Do not overwrite existing files unless the user "
        "asks for `--force`.",
    ),
    "plan": (
        "Interview me until the plan is stable, then propose reviewable worktrees",
        'Ask the user for the goal if it is not provided. Run `automagic plan "<goal>"` in an '
        "interactive terminal and relay each question to the user verbatim, passing their "
        "answers through. The command builds a Merkle graph of assumptions and keeps asking "
        "until no node hash is invalidated. Afterwards show `.automagic/plan/plan.md` and ask "
        "before creating worktrees with `automagic worktrees --create`. To change a finished "
        'plan, run `automagic plan --amend "<change>"`: it lists the agreed nodes the change '
        "invalidates and asks before accepting contradictions with agreed intent. Before "
        "implementing new work in a repository that has `docs/plan/`, read the node documents "
        "there and say which nodes the work touches; if it contradicts one, propose an amend "
        "instead of proceeding.",
    ),
    "worktrees": (
        "Propose or create git worktrees for the current plan",
        "Run `automagic worktrees` to show the proposal from `.automagic/plan/graph.json`. "
        "Only run `automagic worktrees --create` after the user confirms.",
    ),
    "show": (
        "Show the plan, Merkle graph (Mermaid) and interview history",
        "Run `automagic show` and present `.automagic/plan/plan.md`, including the Mermaid "
        "graph. Use `automagic show --node <id>` to explain a node's hash chain. "
        "`automagic show --open` opens `.automagic/plan/plan.html`, where work items are "
        "ticked and prerequisites are selected automatically.",
    ),
    "run": (
        "Run one agent per work item in its worktree, in dependency waves",
        "Run `automagic run --dry-run` and show the waves, agent command and verify command. "
        "Only after the user confirms, run `automagic run --yes`. Use `--only <ids>` (work "
        "item or node ids; unfinished prerequisites are added) to run a subset, for example "
        "the command copied from `plan.html`. Agents commit in their own "
        "worktree branch and never push or merge. Report each item's status and branch, and point "
        "to `.automagic/plan/runs/<id>.log` for failures.",
    ),
    "review": (
        "Review a work item against its node, the guardrails and the initial scope",
        "Run `automagic review <work item or node id> --verify` and read "
        "`.automagic/plan/reviews/<id>.md`; it lists the worktree, the diff range and "
        "deterministic findings (scope, size, guardrails, verification). Then judge the diff "
        "yourself against the node text in `.automagic/BRIEF.md` and `docs/GUARDRAILS.md`: "
        "is only the node's work done, is every claim grounded in files, is anything missing?\n\n"
        "If GitKraken MCP tools are available (`git_log_or_diff`, `git_commit_composer`, "
        "`git_status`, `gitlens_start_review`), prefer them: call `git_log_or_diff` with "
        "`action: diff` and the report's `base..branch` range, and `action: log` for the "
        "commits, using the worktree path as `directory`; if a pull request exists for the "
        "branch, use `gitlens_start_review` with its URL. Fall back to `git diff` and `git log` "
        "when GitKraken is unavailable.\n\n"
        "For commit messages: if the worktree has uncommitted changes, call "
        "`git_commit_composer` (direction `auto`, `directory` = worktree) to propose how to "
        "group and word the commits; otherwise improve the report's proposed message. Never "
        "commit, push or merge without the user's approval. Finish with the verdict, findings "
        "by severity with file paths, and the proposed commit message(s).",
    ),
    "check": (
        "Check context-size and grounding guardrails",
        "Run `automagic check` and fix each reported problem (oversized instruction files, "
        "broken relative links, unindexed docs). Run `automagic context` to refresh the index.",
    ),
}


def slash_files() -> dict[str, str]:
    files: dict[str, str] = {}
    for name, (description, body) in COMMANDS.items():
        full = f"automagic-{name}"
        files[f".github/prompts/{full}.prompt.md"] = (
            f"---\ndescription: {description}\nagent: agent\n---\n\n{body}\n"
        )
        files[f".github/skills/{full}/SKILL.md"] = (
            f"---\nname: {full}\ndescription: {description}\n---\n\n{body}\n"
        )
    return files
