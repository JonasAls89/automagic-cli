"""Private execution defaults; target repositories do not need a config file."""

from __future__ import annotations

from pydantic import BaseModel, Field

# {prompt} is replaced by the agent prompt. Agents may edit files and run shell commands in
# their own worktree; committing, pushing and merging are denied.
DEFAULT_RUNNER = (
    "copilot -p {prompt} -s --no-ask-user --allow-tool=write --allow-tool=shell "
    "'--deny-tool=shell(git commit)' '--deny-tool=shell(git push)' '--deny-tool=shell(git merge)'"
)


class Config(BaseModel):
    max_context_tokens: int = Field(default=40_000, gt=0)
    max_changed_lines: int = Field(default=400, gt=0)
    max_instruction_bytes: int = Field(default=8_000, gt=0)
    runner_command: str = DEFAULT_RUNNER
    max_concurrency: int = Field(default=3, gt=0)
    agent_timeout: int = Field(default=3_600, gt=0)
