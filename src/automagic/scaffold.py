"""Idempotent template rendering for baseline repository files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from automagic.config import Config

SETUP_CONFIGS = {
    ".github/dependabot.yml",
    ".github/zizmor.yml",
    ".pre-commit-config.yaml",
}

# Jinja is used for text templates, never HTML.
_env = Environment(undefined=StrictUndefined, keep_trailing_newline=True, autoescape=False)  # noqa: S701  # nosec B701


@dataclass
class Action:
    path: str
    status: str  # created | overwritten | skipped | would-create | would-overwrite


def _template_files(assets: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    if not assets.is_dir():
        raise ValueError(f"Missing bundled baseline assets: {assets}")
    for src in sorted(assets.rglob("*")):
        if src.is_file() and src.name != ".DS_Store":
            rel = src.relative_to(assets).as_posix()
            if not rel.endswith((".md", ".md.j2")) and rel not in SETUP_CONFIGS:
                raise ValueError(f"Baseline assets must be Markdown or approved setup YAML: {rel}")
            files[rel] = src.read_text()
    return files


def scaffold(
    root: Path,
    project_name: str,
    assets: Path,
    cfg: Config | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> list[Action]:
    cfg = cfg or Config()
    ctx = {
        "project_name": project_name,
        "date": date.today().isoformat(),
        **cfg.model_dump(),
    }
    rendered: dict[str, str] = {}
    for rel, text in _template_files(assets).items():
        if rel.endswith(".j2"):
            rel, text = rel[:-3], _env.from_string(text).render(**ctx)
        rendered[rel] = text
    actions = []
    base = root.resolve()
    for rel, text in sorted(rendered.items()):
        target = (base / rel).resolve()
        if base not in target.parents:
            raise ValueError(f"Refusing to write outside project: {rel}")
        exists = target.exists()
        if exists and not force:
            actions.append(Action(rel, "skipped"))
            continue
        if dry_run:
            actions.append(Action(rel, "would-overwrite" if exists else "would-create"))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        actions.append(Action(rel, "overwritten" if exists else "created"))
    return actions
