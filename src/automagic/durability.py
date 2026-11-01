"""Durable interview snapshots and idempotent history delivery."""

from __future__ import annotations

import json
import os
from pathlib import Path

from automagic.merkle import Graph, InterviewEvent
from automagic.render import load_history


class InterviewDurability:
    def __init__(self, graph_path: Path) -> None:
        self.graph_path = graph_path
        self.history_path = graph_path.with_name("history.jsonl")
        self._event_ids: set[str] | None = None

    def _snapshot(self, graph: Graph) -> None:
        if graph.interview is not None:
            graph.interview.graph_hash = graph.interview_hash()
        graph.save(self.graph_path)

    def save(self, graph: Graph) -> None:
        """Save the graph and pending event before delivering history."""
        self._snapshot(graph)
        self.flush_event(graph)

    def flush_event(self, graph: Graph) -> None:
        """Recover an event already saved in the graph without duplicating it."""
        checkpoint = graph.interview
        if checkpoint is not None and checkpoint.pending_event is not None:
            self.append(checkpoint.pending_event)
            checkpoint.pending_event = None
            self._snapshot(graph)

    def append(self, event: InterviewEvent | dict[str, object]) -> None:
        if self._event_ids is None:
            self._event_ids = {
                event_id
                for previous in load_history(self.history_path)
                if isinstance(event_id := previous.get("id"), str)
            }
        event_id = event.get("id")
        if isinstance(event_id, str) and event_id in self._event_ids:
            return
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a") as fh:
            fh.write(json.dumps(event) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        if isinstance(event_id, str):
            self._event_ids.add(event_id)
