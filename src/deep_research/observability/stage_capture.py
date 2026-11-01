"""Write a live run's stage inputs to disk, for stage replay.

A change to how a stage batches its model calls, or to the effort it asks for,
is judged by running that stage again on exactly the evidence a recorded run
handed it. Three inputs are captured, and only while an experiment has bound a
directory with ``bind_stage_capture``:

- a graph node's input state, just before its agent runs (``<node>-NN.json``);
- each Statement Check call's question and items (``statement_check-NNN.json``);
- each page extraction's request, with the sub-topic, the reads and the target
  ids its reply is admitted against (``page_extraction-NNN.json``).

Unbound -- every CLI, API and test run -- every hook returns at once and writes
nothing, so no request, event or artifact of a run changes. Nothing here imports
the rest of the package: the hooks are called from the graph, from the
evidence verifier and from the researcher, and take any pydantic model.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class _Dumpable(Protocol):
    def model_dump(self, *, mode: str = ...) -> dict[str, Any]: ...


@dataclass(slots=True)
class _Capture:
    directory: Path
    nodes: frozenset[str]
    counts: dict[str, int] = field(default_factory=dict)

    def next_path(self, stem: str, width: int) -> Path:
        number = self.counts.get(stem, 0) + 1
        self.counts[stem] = number
        return self.directory / f"{stem}-{number:0{width}d}.json"


_CAPTURE: ContextVar[_Capture | None] = ContextVar("stage_capture", default=None)


@contextmanager
def bind_stage_capture(directory: Path, *, nodes: Sequence[str]) -> Iterator[Path]:
    """Capture, for the duration of the block, the input of each named node,
    every Statement Check call and every page extraction, as JSON files in
    ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    token = _CAPTURE.set(_Capture(directory=directory, nodes=frozenset(nodes)))
    try:
        yield directory
    finally:
        _CAPTURE.reset(token)


def capture_node_input(node: str, state: _Dumpable) -> None:
    """Write ``state`` as node ``node``'s input, when a capture names it."""
    capture = _CAPTURE.get()
    if capture is None or node not in capture.nodes:
        return
    path = capture.next_path(node, 2)
    payload = {"node": node, "state": state.model_dump(mode="json")}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def capture_statement_check(question: str, items: Sequence[_Dumpable]) -> None:
    """Write one Statement Check call's question and items, when capturing."""
    capture = _CAPTURE.get()
    if capture is None:
        return
    path = capture.next_path("statement_check", 3)
    payload = {
        "question": question,
        "items": [item.model_dump(mode="json") for item in items],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def capture_page_extraction(
    messages: Sequence[_Dumpable],
    *,
    sub_topic: _Dumpable,
    known_reads: Mapping[str, _Dumpable],
    valid_target_ids: Sequence[str] | None,
) -> None:
    """Write one page extraction's request and admission inputs, when capturing.

    ``messages`` is the request as sent; ``sub_topic``, ``known_reads`` and
    ``valid_target_ids`` are what ``build_findings`` checks its reply against,
    so the replay can admit a new reply exactly as the run did.
    """
    capture = _CAPTURE.get()
    if capture is None:
        return
    path = capture.next_path("page_extraction", 3)
    payload = {
        "messages": [message.model_dump(mode="json") for message in messages],
        "sub_topic": sub_topic.model_dump(mode="json"),
        "known_reads": {
            read_id: read.model_dump(mode="json") for read_id, read in known_reads.items()
        },
        "valid_target_ids": None if valid_target_ids is None else list(valid_target_ids),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
