"""Replay mode: run a scripted offline case through the real graph on the API's loop.

``ReplayRunner`` satisfies ``SessionStore``'s runner contract with the e2e
replay harness's own pieces — ``replay_settings`` and ``build_replay_runtime``
— called through ``run_research`` directly (never ``run_replay_scenario``,
which owns its own ``asyncio.run``). Events are released one every ``delay``
seconds through a queue drained by a task on the same loop, so the running
stage is watchable; order and content are untouched. ``ReplayCaseMiddleware``
reads ``X-Replay-Case`` on ``POST /research`` and rewrites the request's
``query`` to the case's own question, so the session records what ran.

The two harness guards (``offline_credentials``, ``network_denied``) are not
entered here: the start command holds them for the whole server process.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import JsonValue
from starlette.types import ASGIApp, Receive, Scope, Send

from deep_research.e2e_evaluation.replay import (
    ReplayScenario,
    build_replay_runtime,
    replay_settings,
)
from deep_research.e2e_evaluation.replay_matrix import REPLAY_CASE_IDS, scenario_by_id
from deep_research.main import ProgressHandler, run_research
from deep_research.runtime.errors import configuration_error
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import ResearchEvent

REPLAY_CASE_HEADER = "x-replay-case"
requested_case: ContextVar[str | None] = ContextVar("deep_research_replay_case", default=None)
_log = logging.getLogger(__name__)


def resolve_scenario(case_id: str) -> ReplayScenario:
    """The case, or the enumerated configuration failure an unknown id earns (R1)."""
    try:
        return scenario_by_id(case_id)
    except KeyError:
        _log.warning("replay: unknown case %r; known cases: %s", case_id, ", ".join(REPLAY_CASE_IDS))
        raise configuration_error(
            reason="config_invalid", message=f"No replay case named {case_id!r}."
        ) from None


@dataclass
class ReplayRunner:
    """Run one replay case as a ``SessionStore`` runner, paced onto the stream."""

    default_case: str
    delay: float
    root: Path
    _build_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def __call__(
        self,
        *,
        question: str,
        session_id: str,
        max_extra_passes: int | None,
        output_format: str,
        config_overrides: Mapping[str, JsonValue],
        config_path: str,
        event_handler: ProgressHandler | None,
    ) -> ResearchOutcome:
        # The case decides the question and the ceiling: its scripted completer
        # requires its own question, and its script is written for its ceiling.
        del question, max_extra_passes
        scenario = resolve_scenario(requested_case.get() or self.default_case)
        session_root = self.root / session_id
        session_root.mkdir(parents=True, exist_ok=True)

        async def builder(current: ConfigSettings, *, session_id: str) -> Any:
            effective = replay_settings(scenario, root=session_root, base=current)
            # build_replay_runtime patches runtime.assembly.compile_research_graph
            # while it builds (replay.py:2031-2050): one build at a time.
            async with self._build_lock:
                replay = await build_replay_runtime(
                    scenario, root=session_root, session_id=session_id, settings=effective
                )
            return replay.runtime

        queue: asyncio.Queue[ResearchEvent | None] = asyncio.Queue()
        drain_task: asyncio.Task[None] | None = None
        paced: ProgressHandler | None = None
        if event_handler is not None:
            paced = queue.put_nowait
            drain_task = asyncio.create_task(self._drain(queue, event_handler))
        try:
            outcome = await run_research(
                question=scenario.question,
                session_id=session_id,
                config_path=config_path,
                max_extra_passes=scenario.max_extra_passes,
                output_format=output_format,
                config_overrides=config_overrides,
                runtime_builder=builder,
                event_handler=paced,
            )
            if drain_task is not None:
                queue.put_nowait(None)
                await drain_task
            return outcome
        except asyncio.CancelledError:
            # Shutdown: stop releasing events now; nothing is published afterwards.
            if drain_task is not None:
                drain_task.cancel()
                await asyncio.gather(drain_task, return_exceptions=True)
            raise
        except BaseException:
            # A failed run still publishes the events it produced before failing.
            if drain_task is not None and not drain_task.done():
                queue.put_nowait(None)
                await drain_task
            raise

    async def _drain(self, queue: asyncio.Queue[ResearchEvent | None], publish: ProgressHandler) -> None:
        while (event := await queue.get()) is not None:
            publish(event)
            if self.delay > 0:
                await asyncio.sleep(self.delay)


class ReplayCaseMiddleware:
    """Pick the case from ``X-Replay-Case`` and record the case's question on the session.

    Pure ASGI so the ContextVar it sets travels in the request's own task
    into ``SessionStore.start``'s ``create_task``. Only ``POST /research`` is
    touched; an unknown case rewrites nothing and lets the runner fail the
    session with the enumerated reason.
    """

    def __init__(self, app: ASGIApp, *, default_case: str) -> None:
        self.app = app
        self.default_case = default_case
        self._questions: dict[str, str] = {}

    def _question(self, case_id: str) -> str:
        if case_id not in self._questions:
            self._questions[case_id] = scenario_by_id(case_id).question
        return self._questions[case_id]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") != "/research":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        case_id = headers.get(REPLAY_CASE_HEADER) or self.default_case
        requested_case.set(case_id)
        if case_id not in REPLAY_CASE_IDS:
            await self.app(scope, receive, send)
            return
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            payload["query"] = self._question(case_id)
            body = json.dumps(payload).encode("utf-8")
            scope = dict(scope)
            scope["headers"] = [
                (k, v) for k, v in scope["headers"] if k.lower() != b"content-length"
            ] + [(b"content-length", str(len(body)).encode("latin-1"))]
        delivered = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)
