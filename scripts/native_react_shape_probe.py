"""Native ReAct shape probe v2: the reviewed output-integrity instrument.

This replaces the ignored v1 probe at
``output/transport-probes/native-react-v1/native_react_shape_probe.py`` as the
evidence source for the native-shape release gate. Two modes:

  --dry-run --requests 30
      Injects a recording fake Chat Completions client, makes exactly one
      offline ``complete_react`` call through the reviewed public provider, and
      refuses success unless the captured wire request has the reviewed shape.
      Prints one content-free JSON inventory line and exits. Makes no network
      call, executes no tool, and creates no LangSmith client.

  --execute --requests 30 --output PATH
      Builds the real provider with tracing disabled and ``retry_count=0``,
      makes exactly N sequential first-attempt calls, NEVER executes a selected
      tool, writes only bounded structural records, and exits non-zero when the
      predeclared gate fails.

What changed from v1, and why it matters
----------------------------------------
v1's gate inspected a final answer only for *non-blankness*, so 29 valid calls
plus one non-blank DSML final answer passed it. v2 classifies every returned
final answer with the provider's own detector (``native_text_violation``), so
tool-protocol text can never pass as an answer, and it retains one bounded
boolean per prohibited shape instead of a character count.

v1 also ended its classifier with a catch-all that reported any unrecognised
``ProviderResponseError`` as ``malformed_envelope``. v2 has no catch-all:
a ``ProviderResponseError`` is classified strictly by its required
``failure_category`` and ``failure_origin`` pair, an output-limit failure is
classified by its own explicit kind, and any pair the repository cannot produce
is ``instrument_error``, which fails the gate. There is deliberately no
``malformed_envelope`` outcome to default to.

Privacy
-------
A record retains exactly the enumerated fields in ``RECORD_FIELDS`` and nothing
else. Prompt text, response text, reasoning, tool arguments, parsed payloads,
invalid JSON, exception messages, provider object types, and secrets are never
retained, printed, or written. A provider-chosen tool name is provider text, so
only the first call's allow-listed name is retained; anything else records
``None``, and the number of calls in the turn is retained only as a bounded
count. Character and token counts are measured from the object actually
returned, never hard-coded.

This script builds its request through the repository's own helpers
(``cases_for``, ``EvaluationCase.fresh_state``, ``CriticAgent.build_task``,
``render_react_messages``, ``AgentToolset.provider_definitions``) and calls only
the public ``DeepSeekSchemaChatProvider.complete_react``. It copies no private
provider serialization code and no prompt text.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from deep_research.agents.critic import CriticAgent
from deep_research.agents.prompts import render_react_messages
from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.config import (
    GitMetadata,
    build_runtime_config,
    target_llm_config,
)
from deep_research.main import load_settings
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers import (
    DeepSeekSchemaChatProvider,
    NativeToolTurn,
    ProviderError,
    ProviderOutputLimitError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)

# Private on purpose: see ``sdk_max_retries``.
from deep_research.providers.deepseek_provider import _build_client
from deep_research.providers.native_output import native_text_violation

AGENT_NAME = "critic"
CASE_ID = "critic-live-review"
EXPERIMENT_PREFIX = "native-react-shape-probe"
PROBE_SPAN_ID = "probe-shape"

EXPECTED_MODEL = "deepseek-v4-flash"
EXPECTED_REASONING_EFFORT = "max"
EXPECTED_THINKING = "enabled"
EXPECTED_MAX_TOKENS = 32768
EXPECTED_TOOL_CHOICE = "auto"
EXPECTED_NATIVE_TOOLS = ("web_search", "query_memory")

AUTHORIZED_REQUESTS = 30

# The retry policy the probe forces on its own copy of the LLM config so the
# batch measures first attempts. It is a constant this script writes into its
# own config, so it can never disagree with itself; the repository's configured
# ``llm.retry_count`` is read separately and reported as
# ``repository_retry_count``.
PROBE_RETRY_COUNT = 0

# The retired prompt-encoded ReAct markers. Their presence in an outgoing
# request would mean the probe is testing the wrong transport.
SIMULATED_ACTION_MARKERS = ("tool_input_json", "ReActDecision", "## Tools")

# A sentinel used only to build a real client object offline. It is passed
# directly to the constructor and is never read from, or written to, the
# environment.
_OFFLINE_CLIENT_KEY = "sk-probe-offline-sentinel"

ACCEPTED_OUTCOMES: frozenset[str] = frozenset({"tool_call", "final_answer"})

SHAPE_FAILURE_OUTCOMES: frozenset[str] = frozenset(
    {
        "tool_protocol_text",
        "unknown_tool",
        "malformed_arguments",
        "local_rejection",
        "instrument_error",
    }
)

PROVIDER_FAILURE_OUTCOMES: frozenset[str] = frozenset(
    {
        "transport_error",
        "http_error",
        "response_sdk",
        "timeout",
        "rate_limit",
        "output_limit",
    }
)

OUTCOMES: frozenset[str] = (
    ACCEPTED_OUTCOMES | SHAPE_FAILURE_OUTCOMES | PROVIDER_FAILURE_OUTCOMES
)

# Step 3's whole substance: a ``ProviderResponseError`` is classified only by
# the pair of typed fields it is required to carry. Every pair the repository
# can actually produce on this path is listed; nothing is inferred and there is
# no default, so an unlisted pair becomes ``instrument_error`` instead of
# inheriting a shape verdict it has not earned.
_RESPONSE_FAILURE_OUTCOMES: dict[tuple[str, str], str] = {
    ("response", "local_response"): "local_rejection",
    ("response", "sdk"): "response_sdk",
    ("transport", "sdk"): "transport_error",
    ("http", "sdk"): "http_error",
}

# One prohibited shape name to the record flag that reports it. The keys are
# exactly ``NativeTextViolation``'s members, so a new prohibited shape in the
# shared detector cannot be silently unrepresented here.
VIOLATION_FLAGS: dict[str, str] = {
    "dsml_markup": "dsml_marker_present",
    "tool_markup": "tool_markup_present",
    "markdown_fence": "fence_present",
    "legacy_action_json": "legacy_action_json_present",
}

# Which reviewed parser rejection fired, as a bounded literal.
#
# `local_rejection` alone is not actionable: eight rejections of that category
# are indistinguishable from each other, so a failed gate names no cause. Every
# prefix below is a *project-authored* constant from the provider parsers, not
# provider text, so naming which one fired is privacy-safe in a way that reading
# an SDK exception's message is not -- and matching is by bounded prefix, so no
# provider text can reach a record even if an SDK error shared a message.
#
# The single-call rules are gone from both parsers: a turn carrying one *or
# more* native calls is accepted, and only a `tool_calls` finish with no call at
# all is still malformed. The bounded literal that replaced them says *zero*,
# because "not one" was the name the second live release gate could report
# without being able to say which side of one the count fell on.
_REJECTION_REASON_PREFIXES: tuple[tuple[str, str], ...] = (
    ("DeepSeek native tool response carried tool protocol text", "tool_protocol_text"),
    ("OpenAI native tool response carried tool protocol text", "tool_protocol_text"),
    ("DeepSeek native tool response mixed a final answer", "mixed_envelope"),
    ("OpenAI native tool response mixed a final answer", "mixed_envelope"),
    ("DeepSeek native tool response must carry at least one", "call_count_zero"),
    ("DeepSeek native tool response carried a non-function", "non_function_call"),
    ("OpenAI native tool response named an unavailable tool", "unavailable_tool"),
    ("DeepSeek native tool response named an unavailable tool", "unavailable_tool"),
    (
        "DeepSeek native tool response carried malformed arguments",
        "malformed_arguments",
    ),
    (
        "OpenAI native tool response carried malformed arguments",
        "malformed_arguments",
    ),
    ("DeepSeek native tool response carried a malformed", "malformed_envelope_field"),
    ("OpenAI response contained malformed output", "malformed_envelope_field"),
    ("DeepSeek native tool response carried no usable final", "no_usable_final_answer"),
    ("OpenAI native tool response carried no usable final", "no_usable_final_answer"),
    ("DeepSeek response did not stop cleanly", "did_not_stop_cleanly"),
    ("OpenAI response did not complete", "did_not_complete"),
    ("DeepSeek response contained malformed choices", "malformed_choices"),
    ("DeepSeek response contained no message", "no_message"),
    ("OpenAI response contained an unknown output item", "unknown_output_item"),
)


def rejection_reason(error: BaseException) -> str | None:
    """Name which reviewed parser rejection fired, or ``None``.

    Only a project-authored constant can match. The exception's message is
    consulted, but the *return* is always a bounded literal from the table
    above, so no provider text can become reachable from a record.
    """
    if not isinstance(error, ProviderResponseError):
        return None
    message = str(error)
    for prefix, reason in _REJECTION_REASON_PREFIXES:
        if message.startswith(prefix):
            return reason
    return None


# The exact, complete record schema. Nothing outside this tuple is ever
# retained for a call.
RECORD_FIELDS: tuple[str, ...] = (
    "run",
    "outcome",
    "finish_category",
    "call_count",
    "tool_name",
    "arguments_are_object",
    "ordinary_text_present",
    "ordinary_text_chars",
    "dsml_marker_present",
    "tool_markup_present",
    "fence_present",
    "legacy_action_json_present",
    "failure_category",
    "failure_origin",
    "rejection_reason",
    "retryable",
    "status_code",
    "input_tokens",
    "output_tokens",
    "total_tokens",
)

_SHAPE_FLAG_NAMES: tuple[str, ...] = (
    "ordinary_text_present",
    "ordinary_text_chars",
    "dsml_marker_present",
    "tool_markup_present",
    "fence_present",
    "legacy_action_json_present",
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _config_path() -> Path:
    return _repository_root() / "config.yaml"


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def classify_failure(error: BaseException) -> str:
    """Name one failing turn from typed fields only.

    Branch order is load-bearing. An output-limit failure is a subclass of
    ``ProviderResponseError``, and the artifact projection of that failure
    legitimately carries no origin, so it is classified by its own explicit
    kind before any origin is consulted -- a missing origin there is a normal
    shape, not an unknown combination. Timeouts and rate limits are separate
    classes that carry no category at all. Everything left is a
    ``ProviderResponseError``, which is classified strictly by its required
    ``(failure_category, failure_origin)`` pair.

    There is no default shape verdict: an unrecognised pair, or a type outside
    the reviewed hierarchy, is ``instrument_error``, which fails the gate.
    """
    if isinstance(error, ProviderOutputLimitError):
        return "output_limit"
    if isinstance(error, ProviderTimeoutError):
        return "timeout"
    if isinstance(error, ProviderRateLimitError):
        return "rate_limit"
    if isinstance(error, ProviderResponseError):
        pair = (
            getattr(error, "failure_category", None),
            getattr(error, "failure_origin", None),
        )
        return _RESPONSE_FAILURE_OUTCOMES.get(pair, "instrument_error")
    return "instrument_error"


def shape_flags(text: str | None) -> dict[str, Any]:
    """Describe one returned final answer without retaining any of it.

    The verdict comes from the provider's own detector, so "this text is a tool
    protocol, not an answer" is decided in exactly one place for production and
    for this instrument. Only bounded booleans and a measured character count
    travel back out with it.
    """
    raw = text or ""
    violation = native_text_violation(raw) if raw.strip() else None
    markers = {flag: False for flag in VIOLATION_FLAGS.values()}
    if violation is not None:
        markers[VIOLATION_FLAGS[violation]] = True
    ordinary = violation is None
    return {
        "ordinary_text_present": ordinary,
        "ordinary_text_chars": len(raw) if ordinary else 0,
        **markers,
    }


def _arguments_are_object(arguments_json: object) -> bool:
    """Whether a typed call's arguments decode to a JSON object."""
    if not isinstance(arguments_json, str):
        return False
    try:
        decoded = json.loads(arguments_json)
    except json.JSONDecodeError:
        return False
    return isinstance(decoded, dict)


# --------------------------------------------------------------------------
# Content-free records
# --------------------------------------------------------------------------


def _base_record(run: int) -> dict[str, Any]:
    """Every field, in ``RECORD_FIELDS`` order, with no measurement invented."""
    return {
        "run": run,
        "outcome": None,
        "finish_category": None,
        "call_count": 0,
        "tool_name": None,
        "arguments_are_object": None,
        "ordinary_text_present": False,
        "ordinary_text_chars": 0,
        "dsml_marker_present": False,
        "tool_markup_present": False,
        "fence_present": False,
        "legacy_action_json_present": False,
        "failure_category": None,
        "failure_origin": None,
        "rejection_reason": None,
        "retryable": None,
        "status_code": None,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }


def build_turn_record(
    *, run: int, turn: NativeToolTurn, allowed: frozenset[str]
) -> dict[str, Any]:
    """Describe one returned native turn, retaining none of its content.

    A turn carries one *or more* calls, so the verdict is about the whole
    batch. Only the **first** call's name can be retained, and only when the
    allow-list admits it: the allow-list is re-checked here rather than trusted,
    so a provider that started offering an unavailable tool could not slip past
    this instrument. ``arguments_are_object`` is true only when *every* call is
    allow-listed and *every* call's arguments decode to an object, so one bad
    call in a batch cannot be averaged away by its well-formed siblings.

    ``call_count`` is the measured number of calls. It exists because the
    second live release gate could name the rule it failed on
    (``call_count_not_one``) but not the count behind it, so a diagnosis could
    not tell zero calls from several.
    """
    record = _base_record(run)
    usage = turn.usage
    record.update(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
    )
    calls = turn.tool_calls
    if calls:
        name = calls[0].tool_name
        allow_listed = isinstance(name, str) and name in allowed
        every_call_allow_listed = all(
            isinstance(call.tool_name, str) and call.tool_name in allowed
            for call in calls
        )
        arguments_are_object = every_call_allow_listed and all(
            _arguments_are_object(call.arguments_json) for call in calls
        )
        if not allow_listed:
            outcome = "unknown_tool"
        elif arguments_are_object:
            outcome = "tool_call"
        else:
            outcome = "malformed_arguments"
        record.update(
            outcome=outcome,
            finish_category="tool_calls",
            call_count=len(calls),
            tool_name=name if allow_listed else None,
            arguments_are_object=arguments_are_object,
        )
        return record
    if turn.final_answer is None:
        record.update(outcome="instrument_error", finish_category="stop")
        return record
    flags = shape_flags(turn.final_answer)
    record.update(
        outcome=(
            "final_answer" if flags["ordinary_text_present"] else "tool_protocol_text"
        ),
        finish_category="stop",
        **flags,
    )
    return record


def _failure_category(error: BaseException) -> str | None:
    if isinstance(error, ProviderResponseError):
        return error.failure_category
    return None


def _failure_origin(error: BaseException) -> str | None:
    if isinstance(error, ProviderResponseError):
        return error.failure_origin
    return None


def build_failure_record(*, run: int, error: BaseException) -> dict[str, Any]:
    """Describe one failed call from typed fields only.

    The exception's message is never read, so provider text an SDK exception
    might carry cannot become reachable from the record.
    """
    record = _base_record(run)
    telemetry = (
        error.telemetry if isinstance(error, ProviderOutputLimitError) else None
    )
    if telemetry is not None:
        usage = telemetry.usage
        record.update(
            finish_category=telemetry.finish_reason_category,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
        )
    record.update(
        outcome=classify_failure(error),
        failure_category=_failure_category(error),
        failure_origin=_failure_origin(error),
        rejection_reason=rejection_reason(error),
        retryable=getattr(error, "retryable", None),
        status_code=getattr(error, "http_status_code", None),
    )
    return record


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def evaluate_gate(records: list[dict[str, Any]], requests: int) -> dict[str, Any]:
    """Apply the predeclared gate to one batch of records.

    Every outcome that is not an accepted shape fails the batch. The split
    between a shape failure and a provider failure exists so a failure can be
    diagnosed, never so it can be excused.
    """
    shape_failures = [
        record
        for record in records
        if record["outcome"] in SHAPE_FAILURE_OUTCOMES
    ]
    provider_failures = [
        record
        for record in records
        if record["outcome"] in PROVIDER_FAILURE_OUTCOMES
    ]
    unclassified = [
        record for record in records if record["outcome"] not in OUTCOMES
    ]
    failed = [*shape_failures, *provider_failures, *unclassified]
    return {
        "agent": AGENT_NAME,
        "requests": len(records),
        "accepted": sum(
            1 for record in records if record["outcome"] in ACCEPTED_OUTCOMES
        ),
        "tool_calls": sum(
            1 for record in records if record["outcome"] == "tool_call"
        ),
        "final_answers": sum(
            1 for record in records if record["outcome"] == "final_answer"
        ),
        "shape_failures": len(shape_failures),
        "shape_failed_runs": [record["run"] for record in shape_failures],
        "shape_failure_kinds": sorted(
            {record["outcome"] for record in shape_failures}
        ),
        "instrument_errors": sum(
            1 for record in records if record["outcome"] == "instrument_error"
        ),
        "provider_failures": len(provider_failures),
        "provider_failed_runs": [record["run"] for record in provider_failures],
        "provider_failure_kinds": sorted(
            {record["outcome"] for record in provider_failures}
        ),
        "failed_runs": [record["run"] for record in failed],
        "expected_requests": requests,
        "passed": len(records) == requests and not failed,
    }


# --------------------------------------------------------------------------
# Offline doubles
# --------------------------------------------------------------------------


class _ForbiddenToolClient:
    """Stands in for every tool's external client. Any call is a hard failure.

    Every attribute read is counted and then refused, so the dry run's
    ``tool_executions`` is an observation rather than an assumption.
    """

    def __init__(self, counter: list[str]) -> None:
        self._counter = counter

    def __getattr__(self, name: str) -> Any:
        self._counter.append(name)
        raise AssertionError(f"the probe must never execute a tool (touched {name!r})")


class _ForbiddenMemory:
    """Stands in for the long-term memory a tool would write to."""

    def __init__(self, counter: list[str]) -> None:
        self._counter = counter

    def _refuse(self, name: str) -> None:
        self._counter.append(name)
        raise AssertionError("the probe must never execute a tool")

    async def save(self, *args: Any, **kwargs: Any) -> Any:
        self._refuse("save")

    async def query(self, *args: Any, **kwargs: Any) -> Any:
        self._refuse("query")

    async def get_source_reputation(self, url: str) -> Any:
        self._refuse("get_source_reputation")


def _stub_response() -> Any:
    """One synthetic, well-formed native tool-call response.

    Its usage values are zero placeholders for a response no server ever sent.
    They appear only inside the dry run's sample record, are labelled as such by
    the inventory's ``proves`` scope, and are never a measurement of a live call:
    every live record reads usage from the turn the provider actually returned.
    """
    return SimpleNamespace(
        id="probe-stub",
        model=EXPECTED_MODEL,
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    reasoning_content=None,
                    tool_calls=[
                        SimpleNamespace(
                            type="function",
                            function=SimpleNamespace(
                                name=EXPECTED_NATIVE_TOOLS[0],
                                arguments='{"query":"offline-probe"}',
                            ),
                        )
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=0, completion_tokens=0, total_tokens=0
        ),
    )


class SdkCallRecorder:
    """The counting wrapper around one injected SDK client's ``create``.

    ``create_calls`` is the number of requests the SDK client was actually
    asked to make. It is the only source of that number: the logical request
    count is what was intended, and this is what happened.
    """

    def __init__(self, responder: Callable[[], Any] | None = None) -> None:
        self.create_calls = 0
        self.requests: list[dict[str, Any]] = []
        self._responder = responder if responder is not None else _stub_response

    async def create(self, **kwargs: Any) -> Any:
        self.create_calls += 1
        self.requests.append(dict(kwargs))
        return self._responder()


class RecordingSDKClient:
    """A fake SDK client whose only ``create`` is the counting wrapper."""

    def __init__(self, recorder: SdkCallRecorder) -> None:
        self.recorder = recorder
        self.chat = SimpleNamespace(completions=recorder)


def _offline_tracker(creations: list[str]) -> Tracker:
    """A tracker that contacts nothing and proves it by construction.

    Tracing is disabled, so the tracker never builds a LangSmith client. The
    factory is still supplied and still refuses, so a regression that enabled
    tracing would be counted here and recorded in ``tracker.errors`` instead of
    quietly opening a network path.
    """

    def client_factory(**kwargs: Any) -> Any:
        creations.append("langsmith-client")
        raise AssertionError("the probe must not create a LangSmith client")

    return Tracker(
        LangSmithRuntimeConfig(tracing_enabled=False),
        client_factory=client_factory,
    )


class _UnusedStructuredProvider:
    """The agent is only asked to build its task; this is never called."""

    async def complete_structured(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the probe must not make a structured call")

    async def complete_react(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the probe calls the provider directly")


# --------------------------------------------------------------------------
# Request construction
# --------------------------------------------------------------------------


@contextlib.contextmanager
def _offline_environment() -> Iterator[None]:
    """Placeholders so ``load_settings`` validates during the dry run.

    A name that is already set is never overwritten, and nothing here is
    printed. The dry run injects a recording client and contacts nothing, so
    these are never used as credentials. ``--execute`` does not call this.
    Exactly the names this call had to add are removed again on exit, so the
    process environment is left as it was found and a dry run cannot make a
    later test order-dependent.
    """
    added: list[str] = []
    # ``LANGSMITH_PROJECT`` is in this list because the repository's config
    # enables LangSmith tracing by default, and strict-mode validation requires
    # that name in the ENVIRONMENT whenever tracing is on
    # (utils/config.py:_validate_runtime_secrets). It is a name, not a
    # credential, and the probe's own tracker is built with tracing disabled, so
    # nothing here reaches LangSmith.
    for name in (
        "DEEPSEEK_API_KEY",
        "TAVILY_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
    ):
        if name not in os.environ:
            os.environ[name] = _OFFLINE_CLIENT_KEY
            added.append(name)
    try:
        yield
    finally:
        for name in added:
            os.environ.pop(name, None)


def _runtime(settings: Any) -> Any:
    return build_runtime_config(
        settings,
        agent_name=AGENT_NAME,
        tier="live",
        case_id=CASE_ID,
        reasoning_effort=None,
        judge_reasoning_effort=None,
        output_directory=None,
        experiment_prefix=EXPERIMENT_PREFIX,
        now=datetime.now(timezone.utc),
        git=GitMetadata(commit="probe", short_sha="probe", dirty=True),
    )


def _critic_case() -> Any:
    return next(
        case for case in cases_for(AGENT_NAME, "live") if case.case_id == CASE_ID
    )


def _tools(tracker: Tracker, counter: list[str]) -> list[Any]:
    from deep_research.tools.memory_tools import QueryMemoryTool
    from deep_research.tools.web_search import WebSearchTool

    return [
        WebSearchTool(
            tracker, api_key="", client=_ForbiddenToolClient(counter)
        ),
        QueryMemoryTool(tracker, _ForbiddenMemory(counter)),
    ]


def build_first_request(settings: Any, tracker: Tracker, counter: list[str]) -> Any:
    """The real first spot-check messages and tool definitions.

    Built through the agent's own public surface so the probe measures the
    request a live turn would send, not a hand-written imitation of it.
    """
    case = _critic_case()
    state = case.fresh_state()
    agent = CriticAgent(
        provider=_UnusedStructuredProvider(),
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id=state.session_id, agent_name=AGENT_NAME, max_entries=20
        ),
        tools=_tools(tracker, counter),
        config=settings.agents,
    )
    task = agent.build_task(state)
    messages = render_react_messages(
        system_prompt=agent.system_prompt(task),
        task=task,
        scratchpad=agent.scratchpad.recent(agent.config.prompt_context_entries),
        iteration=1,
        max_iterations=agent.config.max_iterations,
    )
    definitions = tuple(agent.toolset.provider_definitions())
    return messages, definitions, agent.config


def repository_retry_count(settings: Any) -> int:
    """The repository's own ``llm.retry_count``, before the probe overrides it.

    This is what the inventory reports as the repository's retry policy. It is
    read from the loaded repository settings, not from the probe's overridden
    copy, so a ``config.yaml`` edit moves the published number and the override
    stays visible as its own field.
    """
    return int(settings.llm.retry_count)


def provider_config(settings: Any) -> Any:
    """The reviewed target LLM config with the retry count forced to zero.

    The live probe measures first attempts, so the repository-owned retry policy
    must not silently convert one logical request into several. The override is
    the probe's own; the repository's configured value is reported separately by
    ``repository_retry_count``.
    """
    return target_llm_config(_runtime(settings), settings.llm).model_copy(
        update={"retry_count": PROBE_RETRY_COUNT}
    )


def sdk_max_retries() -> int:
    """The SDK retry count on the client production builds for the live path.

    This reads the flag off the real construction on purpose. The SDK's
    ``max_retries`` is not reachable through any public accessor of a
    constructed provider, and an injected recording client is a stub with no
    retry machinery at all, so production's own client construction is the only
    honest place to verify that the SDK will not secretly multiply a logical
    request. Building the client opens no connection: construction is local, and
    the sentinel key is passed directly rather than read from the environment.
    """
    client = _build_client(
        provider_config(load_settings(str(_config_path()))),
        api_key=_OFFLINE_CLIENT_KEY,
        client=None,
    )
    return int(client.max_retries)


def check_wire_request(
    call: dict[str, Any], definitions: tuple[Any, ...]
) -> list[str]:
    """Verify the captured wire request has the reviewed shape.

    Problems name only the field and its bounded value. The message body is
    parsed transiently and never retained; only the names of the retired
    prompt-encoded markers could ever appear.
    """
    problems: list[str] = []
    if call.get("model") != EXPECTED_MODEL:
        problems.append(f"model is {call.get('model')!r}")
    if call.get("reasoning_effort") != EXPECTED_REASONING_EFFORT:
        problems.append(
            f"reasoning_effort is {call.get('reasoning_effort')!r}"
        )
    if call.get("extra_body") != {"thinking": {"type": EXPECTED_THINKING}}:
        problems.append(f"thinking body is {call.get('extra_body')!r}")
    if call.get("max_tokens") != EXPECTED_MAX_TOKENS:
        problems.append(f"max_tokens is {call.get('max_tokens')!r}")
    if call.get("tool_choice") != EXPECTED_TOOL_CHOICE:
        problems.append(f"tool_choice is {call.get('tool_choice')!r}")
    if "response_format" in call:
        problems.append("response_format was sent")
    names = tuple(definition.name for definition in definitions)
    if names != EXPECTED_NATIVE_TOOLS:
        problems.append(f"agent declared tools {names!r}")
    wire_tools = call.get("tools")
    if not isinstance(wire_tools, list):
        problems.append("tools is not a list")
    else:
        wire_names = tuple(
            entry.get("function", {}).get("name") for entry in wire_tools
        )
        if wire_names != EXPECTED_NATIVE_TOOLS:
            problems.append(f"wire tool names are {wire_names!r}")
        for entry in wire_tools:
            function = entry.get("function", {})
            parameters = function.get("parameters", {})
            if set(parameters) != {
                "type",
                "properties",
                "required",
                "additionalProperties",
            }:
                problems.append(
                    f"{function.get('name')} schema keys {sorted(parameters)}"
                )
    body = json.dumps(call.get("messages", []), default=str)
    for marker in SIMULATED_ACTION_MARKERS:
        if marker in body:
            problems.append(f"simulated action schema leaked: {marker}")
    return problems


def check_agent_config(agent_config: Any, config: Any) -> list[str]:
    """The frozen budgets this probe is authorized to measure.

    The retry assertion pins the probe's own override, not the repository's
    configuration: it fails if the batch ever runs with repository retries
    enabled. The repository's configured value is published alongside it.
    """
    problems: list[str] = []
    if agent_config.react_decision_max_tokens != EXPECTED_MAX_TOKENS:
        problems.append(
            f"react_decision_max_tokens is {agent_config.react_decision_max_tokens}"
        )
    if config.max_tokens != EXPECTED_MAX_TOKENS:
        problems.append(f"llm.max_tokens is {config.max_tokens}")
    if getattr(config, "retry_count", None) != PROBE_RETRY_COUNT:
        problems.append(f"the probe's retry override is {config.retry_count!r}")
    return problems


@dataclass
class RequestMeasurement:
    """What one offline request measured, with no content in it."""

    problems: list[str] = field(default_factory=list)
    record: dict[str, Any] = field(default_factory=dict)
    definitions: tuple[Any, ...] = ()
    agent_config: Any = None
    config: Any = None
    repository_retry_count: int = 0
    tracker_errors: int = 0


async def measure_one_request(
    *,
    client: Any,
    recorder: SdkCallRecorder,
    creations: list[str] | None = None,
    tool_calls: list[str] | None = None,
) -> RequestMeasurement:
    """One offline ``complete_react`` call through the reviewed provider.

    The provider is constructed by the probe, so the only client it can reach
    is the injected recorder and the only tracker is the one that refuses to
    build a LangSmith client.
    """
    creations = [] if creations is None else creations
    tool_calls = [] if tool_calls is None else tool_calls
    settings = load_settings(str(_config_path()))
    tracker = _offline_tracker(creations)
    config = provider_config(settings)
    provider = DeepSeekSchemaChatProvider(config, tracker, client=client)
    messages, definitions, agent_config = build_first_request(
        settings, tracker, tool_calls
    )
    measurement = RequestMeasurement(
        definitions=definitions,
        agent_config=agent_config,
        config=config,
        repository_retry_count=repository_retry_count(settings),
    )
    measurement.problems.extend(check_agent_config(agent_config, config))
    if getattr(provider, "_config", None) is not config:
        measurement.problems.append(
            "the provider does not hold the probe's zero-retry config"
        )
    allowed = frozenset(definition.name for definition in definitions)
    async with tracker.session_span(PROBE_SPAN_ID, "critic native react shape"):
        try:
            turn = await provider.complete_react(
                messages,
                definitions,
                agent_name=AGENT_NAME,
                max_tokens=agent_config.react_decision_max_tokens,
            )
        except ProviderError as error:
            measurement.record = build_failure_record(run=1, error=error)
            measurement.problems.append(
                f"the offline call failed as {measurement.record['outcome']}"
            )
        else:
            measurement.record = build_turn_record(
                run=1, turn=turn, allowed=allowed
            )
            measurement.problems.extend(
                check_wire_request(recorder.requests[0], definitions)
            )
            if measurement.record["outcome"] not in ACCEPTED_OUTCOMES:
                measurement.problems.append(
                    f"the offline call produced {measurement.record['outcome']}"
                )
    measurement.tracker_errors = len(tracker.errors)
    return measurement


# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------


async def run_dry_run(requests: int) -> dict[str, Any]:
    """Prove request construction offline and publish a content-free inventory.

    This proves request construction only. It exercises no tool execution, no
    LangSmith transport, and no live provider, so it says nothing about
    execution behavior; the offline agent-boundary tests are what prove that.

    The offline placeholders are installed for the duration of the call and the
    ones this call added are removed again on exit, so a dry run leaves the
    process environment exactly as it found it.
    """
    with _offline_environment():
        return await _dry_run_inventory(requests)


async def _dry_run_inventory(requests: int) -> dict[str, Any]:
    """The dry run's body, run with the offline placeholders installed."""
    creations: list[str] = []
    tool_calls: list[str] = []
    recorder = SdkCallRecorder()
    measurement = await measure_one_request(
        client=RecordingSDKClient(recorder),
        recorder=recorder,
        creations=creations,
        tool_calls=tool_calls,
    )
    problems = list(measurement.problems)
    request_count = len(recorder.requests)
    if recorder.create_calls != 1:
        problems.append(
            f"the SDK client was asked to make {recorder.create_calls} calls"
        )
    if request_count != 1:
        problems.append(f"recorded {request_count} requests")
    if tool_calls:
        problems.append(f"a tool client was touched {len(tool_calls)} times")
    if creations:
        problems.append(f"{len(creations)} LangSmith clients were created")
    if measurement.tracker_errors:
        problems.append(
            f"the tracker recorded {measurement.tracker_errors} transport failures"
        )
    try:
        sdk_retries = sdk_max_retries()
    except Exception as error:  # pragma: no cover - fail closed, never guess
        problems.append(f"SDK retry count could not be read: {type(error).__name__}")
        sdk_retries = -1
    if sdk_retries != 0:
        problems.append(f"SDK max_retries is {sdk_retries}")
    call = recorder.requests[0] if request_count else {}
    inventory: dict[str, Any] = {
        "agent": AGENT_NAME,
        "dry_run": True,
        "logical_requests": requests,
        "sdk_request_ceiling": requests,
        "sdk_create_calls": recorder.create_calls,
        "model": call.get("model"),
        "reasoning_effort": call.get("reasoning_effort"),
        "thinking": call.get("extra_body", {}).get("thinking", {}).get("type"),
        "max_tokens": call.get("max_tokens"),
        "tool_choice": call.get("tool_choice"),
        "response_format_present": "response_format" in call,
        "native_tool_names": [
            entry.get("function", {}).get("name") for entry in call.get("tools", [])
        ],
        "repository_retry_count": measurement.repository_retry_count,
        "probe_retry_override": getattr(measurement.config, "retry_count", None),
        "sdk_retry_count": sdk_retries,
        "tool_executions": len(tool_calls),
        "langsmith_requests": len(creations),
        "record": measurement.record,
        "problems": problems,
        "passed": not problems,
        "proves": "request construction only",
        "does_not_prove": (
            "execution behavior; the offline agent-boundary tests prove that"
        ),
    }
    return inventory


# --------------------------------------------------------------------------
# Live execution (authorized separately, by Task 8)
# --------------------------------------------------------------------------


async def _one_request(
    provider: Any,
    messages: list[Any],
    definitions: tuple[Any, ...],
    allowed: frozenset[str],
    index: int,
    agent_config: Any,
) -> dict[str, Any]:
    """One first-attempt call. Retains no content, arguments, or reasoning."""
    try:
        turn = await provider.complete_react(
            messages,
            definitions,
            agent_name=AGENT_NAME,
            max_tokens=agent_config.react_decision_max_tokens,
        )
    except ProviderError as error:
        return build_failure_record(run=index, error=error)
    return build_turn_record(run=index, turn=turn, allowed=allowed)


async def execute(requests: int, output: Path) -> int:
    """Run the authorized batch. Never executes a tool the model names."""
    settings = load_settings(str(_config_path()))
    creations: list[str] = []
    tool_calls: list[str] = []
    tracker = _offline_tracker(creations)
    config = provider_config(settings)
    provider = DeepSeekSchemaChatProvider(config, tracker)
    messages, definitions, agent_config = build_first_request(
        settings, tracker, tool_calls
    )
    allowed = frozenset(definition.name for definition in definitions)
    records: list[dict[str, Any]] = []
    async with tracker.session_span(PROBE_SPAN_ID, "critic native react shape"):
        for index in range(1, requests + 1):
            records.append(
                await _one_request(
                    provider, messages, definitions, allowed, index, agent_config
                )
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "\n".join(json.dumps(record, separators=(",", ":")) for record in records)
        + "\n",
        encoding="utf-8",
    )
    verdict = evaluate_gate(records, requests)
    print(json.dumps(verdict, separators=(",", ":")))
    return 0 if verdict["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--requests", type=int, default=AUTHORIZED_REQUESTS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.dry_run == args.execute:
        parser.error("choose exactly one of --dry-run or --execute")
    if args.requests != AUTHORIZED_REQUESTS:
        parser.error(
            f"this probe is authorized for exactly {AUTHORIZED_REQUESTS} requests"
        )
    if args.dry_run:
        inventory = asyncio.run(run_dry_run(args.requests))
        if not inventory["passed"]:
            for problem in inventory["problems"]:
                print(f"DRY-RUN FAILURE: {problem}", file=sys.stderr)
            return 1
        print(json.dumps(inventory, separators=(",", ":")))
        return 0
    if args.output is None:
        parser.error("--execute requires --output")
    return asyncio.run(execute(args.requests, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
