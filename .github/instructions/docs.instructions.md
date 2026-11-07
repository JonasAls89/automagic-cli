---
applyTo: "docs/**/*.md"
---

- Documentation must describe what exists in the repository today; link to the source file for each claim.
- Keep each document focused and under 8000 bytes; split instead of growing.
- New decisions go in `docs/adr/NNNN-title.md`; never edit an accepted ADR, supersede it.
- After adding or moving a document, refresh `docs/CONTEXT.md` with `/automagic:context` (in this checkout: `uv run python scripts/helper.py context`).
