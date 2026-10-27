"""Pluggable AI provider interface."""

from __future__ import annotations

import shlex
import subprocess  # nosec B404
from collections.abc import Callable
from typing import Protocol

from automagic.config import Config


class ProviderError(RuntimeError):
    pass


class Provider(Protocol):
    def complete(self, prompt: str) -> str: ...


class CopilotCLIProvider:
    """Runs prompts through the GitHub Copilot CLI in non-interactive mode."""

    def __init__(self, command: str = "copilot", timeout: int = 300) -> None:
        self.command = command
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        try:
            result = subprocess.run(  # noqa: S603  # nosec B603
                [*shlex.split(self.command), "-p", prompt],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderError(f"Copilot CLI failed: {exc}") from exc
        if result.returncode != 0:
            raise ProviderError(result.stderr.strip() or "Copilot CLI returned an error")
        return result.stdout


class FakeProvider:
    """Deterministic provider for tests: pops scripted replies or calls a function."""

    def __init__(self, replies: list[str] | Callable[[str], str]) -> None:
        self._replies = replies
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if callable(self._replies):
            return self._replies(prompt)
        if not self._replies:
            raise ProviderError("FakeProvider ran out of replies")
        return self._replies.pop(0)


def get_provider(config: Config) -> Provider:
    if config.provider == "copilot":
        return CopilotCLIProvider(config.provider_command)
    raise ProviderError(f"Unknown provider: {config.provider!r}")
