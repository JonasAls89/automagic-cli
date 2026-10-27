"""Idempotent template rendering for baseline repository files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from automagic.config import STATE_DIR, Config
from automagic.slashcmds import slash_files

TEMPLATE_ROOT = Path(__file__).parent / "templates"
LANGUAGES = ("python", "typescript")

# Jinja is used for text templates, never HTML.
_env = Environment(undefined=StrictUndefined, keep_trailing_newline=True, autoescape=False)  # noqa: S701  # nosec B701


@dataclass
class Action:
    path: str
    status: str  # created | overwritten | skipped | would-create | would-overwrite


def _template_files(language: str) -> dict[str, str]:
    files: dict[str, str] = {}
    for name in ("common", language):
        base = TEMPLATE_ROOT / name
        for src in sorted(base.rglob("*")):
            if not src.is_file() or src.name == ".DS_Store":
                continue
            rel = src.relative_to(base).as_posix()
            files[rel] = src.read_text()
    return files


def _package_name(project_name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", project_name.lower()).strip("_") or "app"


def scaffold(
    root: Path,
    language: str,
    project_name: str,
    cfg: Config,
    force: bool = False,
    dry_run: bool = False,
) -> list[Action]:
    if language not in LANGUAGES:
        raise ValueError(f"Unsupported language {language!r}; choose from {', '.join(LANGUAGES)}")
    ctx = {
        "project_name": project_name,
        "package_name": _package_name(project_name),
        "language": language,
        "date": date.today().isoformat(),
        **cfg.model_dump(),
    }
    rendered: dict[str, str] = {}
    for rel, text in _template_files(language).items():
        if rel.endswith(".j2"):
            rel, text = rel[:-3], _env.from_string(text).render(**ctx)
        rendered[rel] = text
    rendered.update(slash_files())
    rendered[f"{STATE_DIR}/config.toml"] = cfg.to_toml()

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
