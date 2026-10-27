"""Project configuration stored in ``.automagic/config.toml``."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

STATE_DIR = ".automagic"

# {prompt} is replaced by the agent prompt. Agents may edit files and run shell commands in
# their own worktree; pushing and merging are denied.
DEFAULT_RUNNER = (
    "copilot -p {prompt} -s --no-ask-user --allow-tool=write --allow-tool=shell "
    "'--deny-tool=shell(git push)' '--deny-tool=shell(git merge)'"
)


class Config(BaseModel):
    provider: str = "copilot"
    provider_command: str = "copilot"
    max_context_tokens: int = Field(default=40_000, gt=0)
    max_changed_lines: int = Field(default=400, gt=0)
    max_rounds: int = Field(default=8, gt=0)
    max_instruction_bytes: int = Field(default=8_000, gt=0)
    runner_command: str = DEFAULT_RUNNER
    verify_command: str = "prek run --all-files"
    max_concurrency: int = Field(default=3, gt=0)
    agent_timeout: int = Field(default=3_600, gt=0)

    def to_toml(self) -> str:
        lines = []
        for key, value in self.model_dump().items():
            lines.append(f"{key} = {json.dumps(value)}")
        return "\n".join(lines) + "\n"


def state_dir(root: Path) -> Path:
    return root / STATE_DIR


def load_config(root: Path) -> Config:
    path = state_dir(root) / "config.toml"
    if not path.exists():
        return Config()
    return Config(**tomllib.loads(path.read_text()))
