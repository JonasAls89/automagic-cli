"""Progress feedback while the planning interview runs.

The number of nodes can grow and settled nodes can be re-opened by an answer, so no percentage
is promised: the line shows settled/total and a lower bound on the checks still to come.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from rich.console import Console
from rich.status import Status
from rich.text import Text

BAR_WIDTH = 20

Phase = Literal["checking", "settled", "asking", "revising", "revised"]


@dataclass(frozen=True)
class Progress:
    phase: Phase
    round_no: int
    max_rounds: int
    total: int
    settled: int
    left_in_round: int
    node: str
    added: int = 0
    reopened: int = 0

    @property
    def to_go(self) -> int:
        """Unsettled nodes; each needs at least one more check, so this is a lower bound."""
        return self.total - self.settled


def bar(settled: int, total: int, width: int = BAR_WIDTH) -> str:
    filled = settled * width // total if total else 0
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def summary(p: Progress) -> str:
    return (
        f"{bar(p.settled, p.total)} {p.settled}/{p.total} settled"
        f" · round {p.round_no}/{p.max_rounds}"
        f" · {p.left_in_round} left this round"
        f" · at least {_plural(p.to_go, 'check')} to go"
    )


def delta(p: Progress) -> str:
    """What an answer changed; empty when it neither added nor re-opened nodes."""
    parts = []
    if p.added:
        parts.append(f"+{_plural(p.added, 'new node')}")
    if p.reopened:
        parts.append(f"{_plural(p.reopened, 'settled node')} re-opened")
    return ("answer applied: " + ", ".join(parts)) if parts else ""


class Reporter:
    """Prints progress lines and shows a spinner while the provider is working.

    Spinners are animated only on a terminal; elsewhere only the progress lines are printed.
    """

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._status: Status | None = None

    def handle(self, p: Progress) -> None:
        if p.phase == "asking":
            self._stop()
            self._print(summary(p))
        elif p.phase == "revised":
            self._stop()
            if line := delta(p):
                self._print(line)
        elif p.phase == "revising":
            self._spin(f"Applying your answer to {p.node}...")
        elif p.phase == "checking":
            self._spin(f"Checking {p.node}  {bar(p.settled, p.total)} {p.settled}/{p.total}")
        else:
            self._spin(f"Settled {p.node}  {bar(p.settled, p.total)} {p.settled}/{p.total}")

    @contextmanager
    def working(self, message: str) -> Iterator[None]:
        """Spinner for a single provider call; off a terminal the message is printed once."""
        if self._console.is_terminal:
            self._spin(message)
        else:
            self._print(message)
        try:
            yield
        finally:
            self._stop()

    def close(self) -> None:
        self._stop()

    def _print(self, line: str) -> None:
        self._console.print(line, markup=False, highlight=False, soft_wrap=True)

    def _spin(self, message: str) -> None:
        if not self._console.is_terminal:
            return
        # Text is not parsed as markup, so node ids and the bar's brackets print literally.
        if self._status is None:
            self._status = self._console.status(Text(message))
            self._status.start()
        else:
            self._status.update(Text(message))

    def _stop(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None
