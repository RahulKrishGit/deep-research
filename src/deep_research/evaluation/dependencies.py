"""Controlled dependency bundles: scripted, isolated, and guarded.

One repetition of one controlled case gets its own fresh bundle: scripted
search and HTTP clients, an in-process long-term memory double, an isolated
document sink, and a per-repetition ledger. Nothing here can construct a
real Tavily client, a real httpx client, a ChromaDB collection, or an
OpenAI embedding call — the scripted collaborators are always injected and
``tavily_api_key=""`` is always passed, so even an accidentally
constructed client could not authenticate. Unscripted search queries and
HTTP fetches raise ``ProhibitedDependencyError``, which
``BaseTool.execute`` converts into a failed ``ToolResult`` the agent can
see and the gates can count.

Live bundles (Task 8) build on this module's ledger and bundle contracts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from math import sqrt
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import JsonValue

from deep_research.agents.source_evaluator import ReputationSource
from deep_research.evaluation.config import EvaluationRuntimeConfig
from deep_research.evaluation.factory import evaluation_session_id
from deep_research.evaluation.models import DependencyLedger, ToolCallSummary
from deep_research.memory.entries import MemoryEntry, SourceReputation
from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.observability import Tracker
from deep_research.runtime.assembly import build_tools
from deep_research.runtime.memory_bridge import LongTermMemoryBridge
from deep_research.tools.base import BaseTool
from deep_research.utils.config import ConfigSettings

# Mirrors ``memory.entries._RESERVED_METADATA_KEYS``; duplicated here the
# same way ``memory_bridge`` duplicates it, so seeding does not import a
# private name across modules.
_ENTRY_FIELD_KEYS = frozenset(
    {
        "agent_id",
        "confidence",
        "entry_type",
        "session_id",
        "source_title",
        "source_url",
        "timestamp",
    }
)

_EMBEDDING_DIMENSION = 8


class ProhibitedDependencyError(RuntimeError):
    """A controlled bundle was asked to touch a real external service."""

    def __init__(self, service: str, operation: str) -> None:
        super().__init__(
            f"{service}.{operation} is prohibited in controlled evaluation"
        )


class DependencyRecorder:
    """Mutable per-repetition ledger builder.

    Tool outcomes live in ``dict[str, list[bool]]`` (one entry per tool
    invocation); ``ledger()`` materializes an immutable ``DependencyLedger``
    with one ``ToolCallSummary`` per invoked tool name.
    """

    def __init__(self) -> None:
        self._outcomes: dict[str, list[bool]] = {}
        self._prohibited: list[str] = []
        self._real_services: list[str] = []
        self._memory_reads = 0
        self._memory_writes = 0
        self._document_writes = 0

    def record_tool_call(self, name: str, *, success: bool) -> None:
        self._outcomes.setdefault(name, []).append(bool(success))

    def record_prohibited(self, description: str) -> None:
        self._prohibited.append(description)

    def record_real_service(self, name: str) -> None:
        self._real_services.append(name)

    def record_memory_read(self) -> None:
        self._memory_reads += 1

    def record_memory_write(self) -> None:
        self._memory_writes += 1

    def record_document_write(self) -> None:
        self._document_writes += 1

    def ledger(self) -> DependencyLedger:
        return DependencyLedger(
            tool_calls=[
                ToolCallSummary(
                    tool_name=name,
                    calls=len(outcomes),
                    failures=sum(1 for outcome in outcomes if not outcome),
                )
                for name, outcomes in sorted(self._outcomes.items())
            ],
            prohibited_calls=list(self._prohibited),
            real_services_used=list(self._real_services),
            memory_reads=self._memory_reads,
            memory_writes=self._memory_writes,
            document_writes=self._document_writes,
        )


@dataclass(frozen=True)
class ScenarioScript:
    """Everything one dependency scenario scripts for one case.

    Failure injection is expressed by storing an ``Exception`` instance as
    the value: a scripted search query or page URL mapped to an exception
    raises it when fetched. ``failures`` is keyed by tool name and is
    consumed by the memory double (``query_memory``, ``save_to_memory``).
    """

    search_responses: Mapping[str, Mapping[str, Any] | Exception] = field(
        default_factory=dict
    )
    http_pages: Mapping[str, str | Exception] = field(default_factory=dict)
    memory_entries: Sequence[Mapping[str, JsonValue]] = field(
        default_factory=tuple
    )
    reputations: Mapping[str, float] = field(default_factory=dict)
    failures: dict[str, Exception] = field(default_factory=dict)
    scripted_search_urls: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class DependencyBundle:
    """One repetition's isolated collaborators, none of them shared."""

    settings: ConfigSettings
    tools: tuple[BaseTool, ...]
    long_term: LongTermMemory
    procedural: ProceduralMemory
    reputation: ReputationSource | None
    recorder: DependencyRecorder
    document_directory: Path
    collection_name: str
    strategies_path: Path


def _vector(text: str, dimension: int) -> list[float]:
    digest = sha256(text.encode("utf-8")).digest()
    return [digest[index % len(digest)] / 255.0 for index in range(dimension)]


class _DeterministicEmbeddings:
    """Fixed-width vectors from a stable hash; never an OpenAI call."""

    def __init__(self, dimension: int = _EMBEDDING_DIMENSION) -> None:
        self._dimension = dimension

    def embed_query(self, text: str) -> list[float]:
        return _vector(text, self._dimension)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [_vector(text, self._dimension) for text in texts]


def _matches(where: Mapping[str, Any] | None, metadata: Mapping[str, Any]) -> bool:
    if not where:
        return True
    if "$and" in where:
        return all(_matches(clause, metadata) for clause in where["$and"])
    for key, condition in where.items():
        expected = condition["$eq"] if isinstance(condition, Mapping) else condition
        if metadata.get(key) != expected:
            return False
    return True


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    return sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))


class _InMemoryCollection:
    """Brute-force in-process stand-in for the ChromaDB collection protocol.

    Same shapes as ``tests/memory_fakes.py``'s ``FakeCollection``, but this
    double ships with the harness: controlled evaluation must never open
    ChromaDB at all.
    """

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    def upsert(
        self,
        *,
        ids: Sequence[str],
        documents: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        metadatas: Sequence[Mapping[str, Any]],
    ) -> None:
        for entry_id, document, embedding, metadata in zip(
            ids, documents, embeddings, metadatas, strict=True
        ):
            self.records[entry_id] = {
                "document": document,
                "embedding": list(embedding),
                "metadata": dict(metadata),
            }

    def get(
        self,
        *,
        ids: Sequence[str],
        include: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        del include
        found = [
            (entry_id, self.records[entry_id])
            for entry_id in ids
            if entry_id in self.records
        ]
        return {
            "ids": [entry_id for entry_id, _ in found],
            "documents": [record["document"] for _, record in found],
            "metadatas": [record["metadata"] for _, record in found],
        }

    def query(
        self,
        *,
        query_embeddings: Sequence[Sequence[float]],
        n_results: int,
        where: Mapping[str, Any] | None = None,
        include: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        del include
        vector = list(query_embeddings[0])
        matches = [
            (entry_id, record)
            for entry_id, record in self.records.items()
            if _matches(where, record["metadata"])
        ]
        matches.sort(key=lambda item: _distance(vector, item[1]["embedding"]))
        matches = matches[:n_results]
        return {
            "ids": [[entry_id for entry_id, _ in matches]],
            "documents": [[record["document"] for _, record in matches]],
            "metadatas": [[record["metadata"] for _, record in matches]],
            "distances": [
                [_distance(vector, record["embedding"]) for _, record in matches]
            ],
        }

    def count(self) -> int:
        return len(self.records)


class _ScriptedSearchClient:
    """Serves scripted Tavily responses; any unscripted query is prohibited."""

    def __init__(
        self, script: ScenarioScript, *, recorder: DependencyRecorder
    ) -> None:
        self._responses = dict(script.search_responses)
        self._recorder = recorder

    def search(
        self,
        *,
        query: str,
        search_depth: str,
        max_results: int,
    ) -> Mapping[str, Any]:
        del search_depth, max_results
        response = self._responses.get(query)
        if response is None:
            self._recorder.record_prohibited(f"tavily.search({query!r})")
            self._recorder.record_tool_call("web_search", success=False)
            raise ProhibitedDependencyError("tavily", "search")
        if isinstance(response, Exception):
            self._recorder.record_tool_call("web_search", success=False)
            raise response
        self._recorder.record_tool_call("web_search", success=True)
        return response


class _ScriptedHTTPClient:
    """Serves scripted pages; any unscripted URL is prohibited.

    ``robots.txt`` is answered for every host with a permissive policy:
    ``WebScraperTool`` probes it before every scrape, so gating it would
    make every scripted scrape fail on the probe. The page fetch itself
    stays the guarded operation. The tool name is inferred from the call
    shape the two consumers use: ``WebScraperTool`` always sends its
    ``User-Agent`` header, ``DocumentReaderTool`` never sends headers.
    """

    _PERMISSIVE_ROBOTS = "User-agent: *\nAllow: /\n"

    def __init__(
        self, script: ScenarioScript, *, recorder: DependencyRecorder
    ) -> None:
        self._pages = dict(script.http_pages)
        self._recorder = recorder

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        # ``httpx.Response.raise_for_status`` needs a request attached; a
        # ``httpx.Request`` is a plain object, not a network connection.
        request = httpx.Request("GET", url)
        if urlsplit(url).path == "/robots.txt":
            return httpx.Response(
                status_code=200,
                text=self._PERMISSIVE_ROBOTS,
                headers={"content-type": "text/plain; charset=utf-8"},
                request=request,
            )
        tool_name = "web_scraper" if "headers" in kwargs else "document_reader"
        page = self._pages.get(url)
        if page is None:
            self._recorder.record_prohibited(f"http.get {url}")
            self._recorder.record_tool_call(tool_name, success=False)
            raise ProhibitedDependencyError("http", "get")
        if isinstance(page, Exception):
            self._recorder.record_tool_call(tool_name, success=False)
            raise page
        self._recorder.record_tool_call(tool_name, success=True)
        return httpx.Response(
            status_code=200,
            text=page,
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )


class _ScriptedMemoryDouble:
    """LongTermMemory-shaped wrapper injecting scripted tool failures.

    ``ScenarioScript.failures`` is keyed by tool name; ``query_memory`` and
    ``save_to_memory`` map onto this double's ``query`` and ``save``. It is
    also the harness's instrumentation point for memory read/write counts.
    """

    def __init__(
        self,
        memory: LongTermMemory,
        failures: Mapping[str, Exception],
        *,
        recorder: DependencyRecorder,
    ) -> None:
        self._memory = memory
        self._failures = dict(failures)
        self._recorder = recorder

    async def save(self, entry: MemoryEntry) -> bool:
        failure = self._failures.get("save_to_memory")
        if failure is not None:
            self._recorder.record_tool_call("save_to_memory", success=False)
            raise failure
        saved = await self._memory.save(entry)
        if saved:
            self._recorder.record_memory_write()
        self._recorder.record_tool_call("save_to_memory", success=saved)
        return saved

    async def query(
        self,
        text: str,
        *,
        top_k: int = 5,
        entry_type: str | None = None,
        where: Mapping[str, Any] | None = None,
    ) -> list[Any]:
        failure = self._failures.get("query_memory")
        if failure is not None:
            self._recorder.record_tool_call("query_memory", success=False)
            raise failure
        results = await self._memory.query(
            text, top_k=top_k, entry_type=entry_type, where=where
        )
        self._recorder.record_memory_read()
        self._recorder.record_tool_call("query_memory", success=True)
        return results


def _memory_entry(
    payload: Mapping[str, JsonValue], *, session_id: str, agent_id: str
) -> MemoryEntry:
    """Build one seed entry from a scripted mapping, filling the defaults."""
    attributes = {
        key: value
        for key, value in payload.items()
        if key not in _ENTRY_FIELD_KEYS
        and isinstance(value, (str, int, float, bool))
    }
    return MemoryEntry(
        content=payload["content"],
        entry_type=payload.get("entry_type", "finding"),
        session_id=payload.get("session_id", session_id),
        agent_id=payload.get("agent_id", agent_id),
        confidence=payload.get("confidence", 1.0),
        source_url=payload.get("source_url"),
        source_title=payload.get("source_title"),
        attributes=attributes,
    )


def _seed_memory(
    collection: _InMemoryCollection,
    embeddings: _DeterministicEmbeddings,
    script: ScenarioScript,
    *,
    session_id: str,
    agent_id: str,
) -> None:
    """Seed the in-memory collection from the scripted entries and reputations.

    Seeding happens synchronously through the same collection the
    ``LongTermMemory`` was constructed with, so a scripted memory read
    returns exactly what the scenario says — and a malformed entry fails
    the repetition at construction, before any model call.
    """
    entries = [
        _memory_entry(payload, session_id=session_id, agent_id=agent_id)
        for payload in script.memory_entries
    ]
    for url, score in script.reputations.items():
        record = SourceReputation(
            url=url, title=url, reputation_score=score, observations=1
        )
        entries.append(record.to_entry(session_id=session_id, agent_id=agent_id))
    if not entries:
        return
    documents = [entry.content for entry in entries]
    vectors = embeddings.embed_documents(documents)
    collection.upsert(
        ids=[entry.entry_id for entry in entries],
        documents=documents,
        embeddings=[list(vector) for vector in vectors],
        metadatas=[entry.to_metadata() for entry in entries],
    )


def isolated_settings(
    settings: ConfigSettings,
    runtime: EvaluationRuntimeConfig,
    *,
    case_id: str,
    repetition: int,
    root: Path,
) -> ConfigSettings:
    """A deep copy of ``settings`` with every external path isolated.

    ``agents`` and ``graph`` bounds are copied unchanged: the evaluation
    must run the agent under production bounds. Everything else that could
    collide with a production session or another repetition is redirected
    beneath ``root``.
    """
    long_term = settings.memory.long_term.model_copy(
        deep=True,
        update={
            "collection_name": (
                f"evaluation_{runtime.agent_name}_{case_id}_{repetition}".replace(
                    "-", "_"
                )
            ),
            "persist_directory": str(root / "memory"),
        },
    )
    procedural = settings.memory.procedural.model_copy(
        deep=True,
        update={
            "strategies_path": (
                str(root / "procedural" / f"{case_id}-r{repetition}.json")
            )
        },
    )
    memory = settings.memory.model_copy(
        deep=True, update={"long_term": long_term, "procedural": procedural}
    )
    output = settings.output.model_copy(
        deep=True,
        update={
            "directory": str(root / "documents" / f"{case_id}-r{repetition}")
        },
    )
    return settings.model_copy(
        deep=True, update={"memory": memory, "output": output}
    )


def build_controlled_dependencies(
    runtime: EvaluationRuntimeConfig,
    case: Any,
    *,
    tracker: Tracker,
    settings: ConfigSettings,
    root: Path,
    repetition: int = 1,
) -> DependencyBundle:
    """Build one fresh, fully scripted dependency bundle for a repetition.

    The guard is constructive, not advisory: the scripted search and HTTP
    clients are always injected, ``tavily_api_key=""`` is always passed
    explicitly (``build_tools`` would otherwise fall back to
    ``os.getenv("TAVILY_API_KEY")``), memory is an in-process collection
    with deterministic embeddings, and the document directory sits beneath
    ``root``. A case whose scenario nobody scripted fails here, before any
    model call.
    """
    try:
        script = SCENARIOS[case.dependency_scenario]
    except KeyError:
        raise KeyError(
            f"no scenario scripted for {case.case_id!r}: "
            f"{case.dependency_scenario!r} is not in SCENARIOS"
        ) from None

    isolated = isolated_settings(
        settings,
        runtime,
        case_id=case.case_id,
        repetition=repetition,
        root=root,
    )
    document_directory = Path(isolated.output.directory)
    document_directory.mkdir(parents=True, exist_ok=True)
    session_id = evaluation_session_id(
        runtime, case_id=case.case_id, repetition=repetition
    )

    recorder = DependencyRecorder()
    collection = _InMemoryCollection()
    embeddings = _DeterministicEmbeddings()
    long_term = LongTermMemory(
        collection=collection, embeddings=embeddings, tracker=tracker
    )
    _seed_memory(
        collection,
        embeddings,
        script,
        session_id=session_id,
        agent_id=runtime.agent_name,
    )

    bridge = LongTermMemoryBridge(
        _ScriptedMemoryDouble(long_term, script.failures, recorder=recorder),
        session_id=session_id,
    )
    tools = build_tools(
        isolated,
        tracker=tracker,
        memory=bridge,
        tavily_api_key="",
        search_client=_ScriptedSearchClient(script, recorder=recorder),
        http_client=_ScriptedHTTPClient(script, recorder=recorder),
    )
    procedural = ProceduralMemory.from_config(
        isolated.memory.procedural, tracker=tracker
    )
    return DependencyBundle(
        settings=isolated,
        tools=tuple(tools),
        long_term=long_term,
        procedural=procedural,
        reputation=long_term if script.reputations else None,
        recorder=recorder,
        document_directory=document_directory,
        collection_name=isolated.memory.long_term.collection_name,
        strategies_path=Path(isolated.memory.procedural.strategies_path),
    )


# Sample scenarios. Each agent gets exactly one scenario here so the Task 7
# bundle tests have something real to drive; the per-agent task (10-15)
# replaces the helper with the full three-scenario script.

_MULTI_SOURCE_RESULTS = (
    (
        "https://www.nrel.gov/solar-basics",
        "NREL solar basics",
        "Two-sentence extract on solar panel conversion efficiency.",
    ),
    (
        "https://www.iea.org/solar-report",
        "IEA solar market report",
        "Two-sentence extract on installed capacity growth.",
    ),
    (
        "https://www.sciencedirect.com/solar-review",
        "ScienceDirect solar review",
        "Two-sentence extract from a peer-reviewed survey.",
    ),
)


def _planner_scenarios() -> dict[str, ScenarioScript]:
    return {
        "planner-clean-memory": ScenarioScript(),
    }


def _researcher_scenarios() -> dict[str, ScenarioScript]:
    http_pages = {url: content for url, _, content in _MULTI_SOURCE_RESULTS}
    return {
        "researcher-multi-source": ScenarioScript(
            search_responses={
                "solar panel efficiency trends": {
                    "results": [
                        {"url": url, "title": title, "content": content}
                        for url, title, content in _MULTI_SOURCE_RESULTS
                    ]
                }
            },
            http_pages=http_pages,
            scripted_search_urls=tuple(http_pages),
        ),
    }


def _source_evaluator_scenarios() -> dict[str, ScenarioScript]:
    return {
        "source-evaluator-mixed": ScenarioScript(
            reputations={"ipcc.ch": 0.95, "noaa.gov": 0.92},
        ),
    }


def _fact_checker_scenarios() -> dict[str, ScenarioScript]:
    return {
        "fact-checker-mixed": ScenarioScript(
            memory_entries=(
                {
                    "content": (
                        "Grid-scale batteries provide fast frequency response, "
                        "a claim the sources support."
                    ),
                    "entry_type": "finding",
                },
                {
                    "content": (
                        "Battery costs fell 90% since 2010, a claim the "
                        "sources contradict."
                    ),
                    "entry_type": "finding",
                },
            ),
        ),
    }


def _synthesizer_scenarios() -> dict[str, ScenarioScript]:
    return {
        "synthesizer-complete": ScenarioScript(
            memory_entries=(
                {
                    "content": (
                        "Efficiency gains are modest but real across three "
                        "peer-reviewed studies."
                    ),
                    "entry_type": "finding",
                },
                {
                    "content": (
                        "Deployment is growing fastest in China and Europe."
                    ),
                    "entry_type": "finding",
                },
            ),
            reputations={"nrel.gov": 0.9},
        ),
    }


def _critic_scenarios() -> dict[str, ScenarioScript]:
    return {
        "critic-strong-report": ScenarioScript(
            memory_entries=(
                {
                    "content": (
                        "The report answers the question, cites per-claim "
                        "sources, and flags its uncertainty."
                    ),
                    "entry_type": "finding",
                },
            ),
        ),
    }


SCENARIOS: dict[str, ScenarioScript] = {
    **_planner_scenarios(),
    **_researcher_scenarios(),
    **_source_evaluator_scenarios(),
    **_fact_checker_scenarios(),
    **_synthesizer_scenarios(),
    **_critic_scenarios(),
}
