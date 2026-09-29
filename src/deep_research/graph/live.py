"""Live publication: the run-scoped sink agents and nodes publish events to as they happen.

``_stream_graph_result`` binds a sink for the duration of one streamed run
(live-briefs spec E2); ``publish_live`` hands an event to it and is a no-op when
nothing is bound — a run without an event handler, or a unit test calling an agent
directly. The event published live is the same object the node later returns in its
state update, so the orchestrator's snapshot loop recognises it by ``event_id`` and
never publishes it twice.

A ContextVar rather than a parameter: LangGraph runs each node in a task that copies
the current context, and every task an agent starts copies it again, so a sink bound
around the stream reaches the researcher's concurrent sub-topic loops without being
threaded through each call. Call ``publish_live`` on the event loop only, never from a
worker thread: the sink calls the session's ``publish``, which is not thread-safe.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from deep_research.utils.types import ResearchEvent

LiveSink = Callable[[ResearchEvent], None]

_LIVE_SINK: ContextVar[LiveSink | None] = ContextVar(
    "deep_research_live_sink", default=None
)


def publish_live(event: ResearchEvent) -> None:
    """Hand one event to the run's live sink, if one is bound."""
    sink = _LIVE_SINK.get()
    if sink is not None:
        sink(event)


@contextmanager
def bind_live_sink(sink: LiveSink) -> Iterator[None]:
    """Bind ``sink`` for the body of the ``with`` block, then restore the previous one."""
    token = _LIVE_SINK.set(sink)
    try:
        yield
    finally:
        _LIVE_SINK.reset(token)
