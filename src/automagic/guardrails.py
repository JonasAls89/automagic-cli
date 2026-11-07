"""Context-size and grounding checks for markdown context files."""

from __future__ import annotations

import re
from pathlib import Path

from automagic.config import Config

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE = re.compile(r"`[^`\n]*`")
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
START, END = "<!-- automagic:context:start -->", "<!-- automagic:context:end -->"


def context_files(root: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in (
        "AGENTS.md",
        "INTENT.md",
        "GLOSSARY.md",
        ".github/copilot-instructions.md",
        ".github/instructions/*.md",
        ".github/skills/*/SKILL.md",
        "skills/*/SKILL.md",
        "docs/*.md",
        "docs/adr/*.md",
        "docs/work/*.md",
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
        expected = set((root / "docs").glob("*.md"))
        expected.update(
            root / relative
            for relative in (
                "AGENTS.md",
                "INTENT.md",
                "GLOSSARY.md",
                ".github/copilot-instructions.md",
                "docs/work/_index.md",
            )
            if (root / relative).is_file()
        )
        for doc in sorted(expected):
            if doc.name != "CONTEXT.md" and doc.resolve() not in indexed:
                problems.append(f"{doc.relative_to(root)}: not listed in docs/CONTEXT.md")
    return problems


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
    if (docs / "work" / "_index.md").exists():
        entries.append("- [Execution work items](work/_index.md)")
    for rel in ("AGENTS.md", "INTENT.md", "GLOSSARY.md", ".github/copilot-instructions.md"):
        if (root / rel).exists():
            entries.append(f"- [{rel}](../{rel})")
    return "\n".join(entries)


def index_parts(root: Path) -> tuple[str, str, str]:
    path = root / "docs" / "CONTEXT.md"
    text = (
        path.read_bytes().decode("utf-8")
        if path.exists()
        else f"# Repository context\n\n{START}\n{END}\n"
    )
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
        raise ValueError("docs/CONTEXT.md requires one ordered pair of automagic:context markers")
    head, rest = text.split(START, 1)
    tail = rest.split(END, 1)[1]
    return text, head, tail


def update_index(root: Path) -> bool:
    path = root / "docs" / "CONTEXT.md"
    text, head, tail = index_parts(root)
    new = f"{head}{START}\n{build_index(root)}\n{END}{tail}"
    path.parent.mkdir(parents=True, exist_ok=True)
    changed = new != text
    if changed:
        path.write_bytes(new.encode("utf-8"))
    return changed
