"""Context-size and grounding checks for markdown context files."""

from __future__ import annotations

import re
from pathlib import Path

from automagic import plandocs
from automagic.config import Config

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE = re.compile(r"`[^`\n]*`")
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
START, END = "<!-- automagic:context:start -->", "<!-- automagic:context:end -->"


def context_files(root: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in (
        "AGENTS.md",
        ".github/copilot-instructions.md",
        ".github/instructions/*.md",
        ".github/prompts/*.md",
        ".github/skills/*/SKILL.md",
        "docs/*.md",
        "docs/plan/*.md",
    ):
        found.update(p for p in root.glob(pattern) if p.is_file())
    return sorted(found)


def _links(text: str) -> list[str]:
    text = _INLINE.sub("", _FENCE.sub("", text))
    return _LINK.findall(text)


def check(root: Path, cfg: Config) -> list[str]:
    problems: list[str] = []
    base = root.resolve()
    for path in context_files(root):
        rel = path.relative_to(root).as_posix()
        size = path.stat().st_size
        if size > cfg.max_instruction_bytes:
            problems.append(f"{rel}: {size} bytes exceeds limit {cfg.max_instruction_bytes}")
        for link in _links(path.read_text()):
            if re.match(r"^[a-z][a-z0-9+.-]*:", link) or link.startswith("#"):
                continue
            target = (path.parent / link.split("#", 1)[0]).resolve()
            if not target.exists():
                problems.append(f"{rel}: broken link {link}")
            elif base not in (target, *target.parents):
                problems.append(f"{rel}: link escapes repository {link}")
    index = root / "docs" / "CONTEXT.md"
    if index.exists():
        indexed = {
            (index.parent / link.split("#", 1)[0]).resolve() for link in _links(index.read_text())
        }
        for doc in sorted((root / "docs").glob("*.md")):
            if doc.name != "CONTEXT.md" and doc.resolve() not in indexed:
                problems.append(f"docs/{doc.name}: not listed in docs/CONTEXT.md")
    return problems + plandocs.stale(root)


def _title(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            return line.lstrip("# ").strip()
    return path.stem


def build_index(root: Path) -> str:
    docs = root / "docs"
    entries = []
    for doc in sorted(docs.glob("*.md")):
        if doc.name != "CONTEXT.md":
            entries.append(f"- [{_title(doc)}]({doc.name})")
    if (docs / "adr").is_dir():
        entries.append("- [Architecture decision records](adr/0000-template.md)")
    if (docs / "plan" / "_index.md").exists():
        entries.append("- [Plan documents](plan/_index.md)")
    for rel in ("AGENTS.md", ".github/copilot-instructions.md"):
        if (root / rel).exists():
            entries.append(f"- [{rel}](../{rel})")
    return "\n".join(entries)


def update_index(root: Path) -> bool:
    path = root / "docs" / "CONTEXT.md"
    text = path.read_text() if path.exists() else f"# Repository context\n\n{START}\n{END}\n"
    if START not in text or END not in text:
        raise ValueError("docs/CONTEXT.md is missing the automagic:context markers")
    head, rest = text.split(START, 1)
    tail = rest.split(END, 1)[1]
    new = f"{head}{START}\n{build_index(root)}\n{END}{tail}"
    path.parent.mkdir(parents=True, exist_ok=True)
    changed = new != text
    if changed:
        path.write_text(new)
    return changed
