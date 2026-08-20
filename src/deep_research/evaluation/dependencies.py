"""Controlled and live dependency bundles: scripted or real, always isolated.

One repetition of one controlled case gets its own fresh bundle: scripted
search and HTTP clients, an in-process long-term memory double, an isolated
document sink, and a per-repetition ledger. Nothing in the controlled half
can construct a real Tavily client, a real httpx client, a ChromaDB
collection, or an OpenAI embedding call — the scripted collaborators are
always injected and ``tavily_api_key=""`` is always passed, so even an
accidentally constructed client could not authenticate. Unscripted search
queries and HTTP fetches raise ``ProhibitedDependencyError``, which
``BaseTool.execute`` converts into a failed ``ToolResult`` the agent can
see and the gates can count.

The live half builds production-parity bundles: real Chroma-backed
``LongTermMemory`` under a per-repetition persist path, real document
writes to an evaluation-only directory, and real Tavily/httpx clients where
the selected agent declares those tools — requiring only the credentials
that agent can actually use. Both halves share the ledger and bundle
contracts.
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

from deep_research.agents.critic import CriticAgent
from deep_research.agents.fact_checker import FactCheckerAgent
from deep_research.agents.planner import PlannerAgent
from deep_research.agents.researcher import ResearcherAgent
from deep_research.agents.source_evaluator import (
    ReputationSource,
    SourceEvaluatorAgent,
)
from deep_research.agents.sources import source_domain
from deep_research.agents.synthesizer import SynthesizerAgent
from deep_research.evaluation.config import EvaluationRuntimeConfig
from deep_research.evaluation.factory import evaluation_session_id
from deep_research.evaluation.models import (
    AGENT_NAMES,
    AgentName,
    DependencyLedger,
    ToolCallSummary,
)
from deep_research.memory.entries import MemoryEntry, SourceReputation
from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.observability import Tracker
from deep_research.providers import (
    LOCAL_EMBEDDING_PROVIDER,
    build_embedding_provider,
)
from deep_research.runtime.assembly import build_tools
from deep_research.runtime.memory_bridge import LongTermMemoryBridge
from deep_research.tools.base import BaseTool, ToolResult
from deep_research.tools.write_document import WriteDocumentTool
from deep_research.utils.config import ConfigSettings, ProviderName

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


class MissingCredentialError(RuntimeError):
    """A live bundle cannot be built without an applicable credential."""

    def __init__(self, variables: Sequence[str]) -> None:
        self.reason = "missing_credentials"
        self.variables = tuple(variables)
        super().__init__(f"missing credentials: {', '.join(self.variables)}")


# Tool name to the real service it reaches, one entry per tool the shared
# registry builds. ``allowed_tools`` is the source of truth: an agent that
# declares a tool neither maps to a service nor is omitted here is loud at
# import time, so a change to an agent's declarations cannot silently leave
# the live tier requiring the wrong credentials.
_TOOL_SERVICES: Mapping[str, str] = {
    "web_search": "tavily",
    "web_scraper": "http",
    "document_reader": "http",
    "query_memory": "memory",
    "save_to_memory": "memory",
    "write_document": "documents",
}

_AGENT_CLASSES: dict[AgentName, type[Any]] = {
    "planner": PlannerAgent,
    "researcher": ResearcherAgent,
    "source_evaluator": SourceEvaluatorAgent,
    "fact_checker": FactCheckerAgent,
    "synthesizer": SynthesizerAgent,
    "critic": CriticAgent,
}


def _live_services(agent_name: AgentName) -> tuple[str, ...]:
    """The real services one agent's declared tools can reach.

    Computed once at import from each agent class's ``allowed_tools``,
    deduplicated in declaration order.
    """
    services: list[str] = []
    for tool in _AGENT_CLASSES[agent_name].allowed_tools:
        service = _TOOL_SERVICES.get(tool)
        if service is None:
            raise KeyError(f"no live service mapped for tool {tool!r}")
        if service not in services:
            services.append(service)
    return tuple(services)


LIVE_DEPENDENCIES: dict[AgentName, tuple[str, ...]] = {
    name: _live_services(name) for name in AGENT_NAMES
}


CHAT_PROVIDER_CREDENTIALS: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def required_credentials(
    agent_name: AgentName, *, provider: ProviderName
) -> tuple[str, ...]:
    """The environment variables one live run of ``agent_name`` must provide.

    The selected chat provider's key and LangSmith are required for every
    agent; Tavily only for agents whose declared tools reach it.
    ``LANGSMITH_PROJECT`` is validated by the tracker's own runtime config,
    so it is not duplicated here. Embeddings contribute nothing: the
    baseline embedding provider is local and has no credential.
    """
    chat_key = CHAT_PROVIDER_CREDENTIALS[provider]
    if "tavily" in LIVE_DEPENDENCIES[agent_name]:
        return (chat_key, "LANGSMITH_API_KEY", "TAVILY_API_KEY")
    return (chat_key, "LANGSMITH_API_KEY")


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
    consumed by the memory double (``query_memory``, ``save_to_memory``)
    and by ``build_controlled_dependencies`` itself (``write_document``,
    which turns the document directory into a plain file so the tool's
    ``mkdir`` fails deterministically); ``reputation_failures`` is keyed
    by registrable domain and is consumed by the reputation double
    (``get_source_reputation``).
    """

    search_responses: Mapping[str, Mapping[str, Any] | Exception] = field(
        default_factory=dict
    )
    http_pages: Mapping[str, str | Exception] = field(default_factory=dict)
    memory_entries: Sequence[Mapping[str, JsonValue]] = field(
        default_factory=tuple
    )
    reputations: Mapping[str, float] = field(default_factory=dict)
    reputation_failures: Mapping[str, Exception] = field(default_factory=dict)
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
    # The real services this bundle can reach: ``LIVE_DEPENDENCIES[agent]``
    # for live, ``()`` for controlled. What a run actually touched is
    # recorded separately, in ``real_services_used``.
    available_services: tuple[str, ...] = ()


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


class _ScriptedReputationDouble:
    """Domain-keyed reputation double for the Source Evaluator.

    The scenarios script reputations and reputation failures by registrable
    domain, but the agent calls ``get_source_reputation`` with a full URL.
    This double keys both maps by the same domain ``source_domain`` derives
    (``https://www.ipcc.ch/ar6-wg1`` -> ``ipcc.ch``): a listed failing
    domain raises its mapped exception, a listed domain returns a fresh
    ``SourceReputation`` at the scripted score, and every other URL falls
    through to the seeded memory, which the controlled tier leaves unseeded
    at full-URL granularity (``None``).
    """

    def __init__(
        self,
        memory: LongTermMemory,
        script: ScenarioScript,
    ) -> None:
        self._memory = memory
        self._scores = dict(script.reputations)
        self._failures = dict(script.reputation_failures)

    async def get_source_reputation(self, url: str) -> SourceReputation | None:
        domain = source_domain(url)
        failure = self._failures.get(domain)
        if failure is not None:
            raise failure
        score = self._scores.get(domain)
        if score is not None:
            return SourceReputation(
                url=url, title=url, reputation_score=score, observations=1
            )
        return await self._memory.get_source_reputation(url)


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
    persist_directory: Path | None = None,
) -> ConfigSettings:
    """A deep copy of ``settings`` with every external path isolated.

    ``agents`` and ``graph`` bounds are copied unchanged: the evaluation
    must run the agent under production bounds. Everything else that could
    collide with a production session or another repetition is redirected
    beneath ``root``.

    ``persist_directory`` overrides where Chroma persists. The live tier
    passes a per-repetition path so repetitions never share a backing
    store; controlled mode keeps the shared ``root/memory`` default, which
    its in-memory collection never touches.
    """
    long_term = settings.memory.long_term.model_copy(
        deep=True,
        update={
            "collection_name": (
                f"evaluation_{runtime.agent_name}_{case_id}_{repetition}".replace(
                    "-", "_"
                )
            ),
            "persist_directory": str(persist_directory or (root / "memory")),
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


class _RecordingDocumentWriter(WriteDocumentTool):
    """``WriteDocumentTool`` that records every outcome in the ledger.

    Task 18's ``persistence_truthful`` gate reads the ledger's
    ``write_document`` summary, so controlled mode must record both
    successes and failures — exactly what ``_ScriptedMemoryDouble``
    already does for ``save_to_memory``. ``BaseTool.execute`` converts
    every exception into a ``ToolResult`` (never raising), so the wrapper
    records whatever came back. Live runs record real tool spans instead
    (Task 21), which is why this wrapper exists only in controlled
    bundles.
    """

    def __init__(
        self,
        tracker: Tracker,
        output_root: Path | str,
        *,
        recorder: DependencyRecorder,
    ) -> None:
        super().__init__(tracker, output_root)
        self._recorder = recorder

    async def execute(self, **kwargs: Any) -> ToolResult:
        result = await super().execute(**kwargs)
        self._recorder.record_tool_call("write_document", success=result.success)
        if result.success:
            self._recorder.record_document_write()
        return result


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
    if "write_document" in script.failures:
        # The brief's deterministic failure hook: put a plain *file* where
        # the document directory should be. ``WriteDocumentTool`` resolves
        # its output root and calls ``root.mkdir(parents=True,
        # exist_ok=True)``, which raises ``FileExistsError`` against an
        # existing file on every platform — so the write fails before any
        # content is produced, and the recording wrapper below records it
        # in the ledger.
        document_directory.parent.mkdir(parents=True, exist_ok=True)
        document_directory.touch()
    else:
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
    tools = [
        (
            _RecordingDocumentWriter(tracker, document_directory, recorder=recorder)
            if tool.name == "write_document"
            else tool
        )
        for tool in tools
    ]
    procedural = ProceduralMemory.from_config(
        isolated.memory.procedural, tracker=tracker
    )
    return DependencyBundle(
        settings=isolated,
        tools=tuple(tools),
        long_term=long_term,
        procedural=procedural,
        reputation=(
            _ScriptedReputationDouble(long_term, script)
            if script.reputations or script.reputation_failures
            else None
        ),
        recorder=recorder,
        document_directory=document_directory,
        collection_name=isolated.memory.long_term.collection_name,
        strategies_path=Path(isolated.memory.procedural.strategies_path),
    )


def build_live_dependencies(
    runtime: EvaluationRuntimeConfig,
    case: Any,
    *,
    tracker: Tracker,
    settings: ConfigSettings,
    root: Path,
    environ: Mapping[str, str],
    repetition: int = 1,
    search_client: Any | None = None,
    http_client: Any | None = None,
    embeddings: Any | None = None,
) -> DependencyBundle:
    """Build one live, production-parity dependency bundle for a repetition.

    Only the credentials applicable to the selected agent are required: an
    agent that never declares ``web_search`` needs no ``TAVILY_API_KEY``,
    and an absent applicable credential raises ``MissingCredentialError``
    before anything is constructed. Memory is the real Chroma-backed
    ``LongTermMemory`` at a per-repetition persist path, document writes
    land in an evaluation-only directory beneath ``root``, and the tools
    carry the real Tavily/httpx clients unless a double is injected —
    offline tests always inject doubles, so nothing here ever touches the
    network. ``real_services_used`` stays empty on purpose: what a live run
    actually exercised is recorded from real tool spans (Task 21), not from
    what this bundle wired.
    """
    missing = [
        variable
        for variable in required_credentials(
            runtime.agent_name, provider=settings.llm.provider
        )
        if not environ.get(variable, "").strip()
    ]
    if missing:
        raise MissingCredentialError(missing)

    services = LIVE_DEPENDENCIES[runtime.agent_name]
    isolated = isolated_settings(
        settings,
        runtime,
        case_id=case.case_id,
        repetition=repetition,
        root=root,
        persist_directory=root / "memory" / f"{case.case_id}-r{repetition}",
    )
    document_directory = Path(isolated.output.directory)
    document_directory.mkdir(parents=True, exist_ok=True)
    session_id = evaluation_session_id(
        runtime, case_id=case.case_id, repetition=repetition
    )

    recorder = DependencyRecorder()
    # ``embedding_model`` carries one of two things: the sentinel "local",
    # which selects the offline provider, or an OpenAI embedding model
    # name. There is only ever one local model, so it needs no name of its
    # own and the sentinel doubles as the provider selector.
    embedding_provider_name = (
        "local"
        if runtime.embedding_model == LOCAL_EMBEDDING_PROVIDER
        else "openai"
    )
    long_term = LongTermMemory.from_config(
        isolated.memory.long_term,
        embeddings=embeddings
        or build_embedding_provider(
            embedding_provider_name, model=runtime.embedding_model
        ),
        tracker=tracker,
    )
    bridge = LongTermMemoryBridge(long_term, session_id=session_id)
    tools = build_tools(
        isolated,
        tracker=tracker,
        memory=bridge,
        # Only an agent that can reach Tavily receives the real key; the
        # others get an empty key, so a live bundle never holds credentials
        # its agent cannot use.
        tavily_api_key=(
            environ.get("TAVILY_API_KEY") if "tavily" in services else ""
        ),
        search_client=search_client,
        http_client=http_client,
    )
    procedural = ProceduralMemory.from_config(
        isolated.memory.procedural, tracker=tracker
    )
    return DependencyBundle(
        settings=isolated,
        tools=tuple(tools),
        long_term=long_term,
        procedural=procedural,
        reputation=long_term,
        recorder=recorder,
        document_directory=document_directory,
        collection_name=isolated.memory.long_term.collection_name,
        strategies_path=Path(isolated.memory.procedural.strategies_path),
        available_services=services,
    )


# Sample scenarios. Each agent gets exactly one scenario here so the Task 7
# bundle tests have something real to drive; the per-agent task (10-15)
# replaces the helper with the full three-scenario script.

# The scripted URLs mirror cases/researcher.py's known_source_urls: the
# gates check every finding's source_url against the case's declared list,
# so a drift between the two lists would make every finding look invented.
# The case tests pin both sides to the same literal.
_MULTI_SOURCE_URLS = (
    "https://www.nrel.gov/heat-pump-cop-below-freezing",
    "https://www.iea.org/heat-pump-cop-report",
    "https://www.sciencedirect.com/heat-pump-cop-review",
    "https://www.nrel.gov/cold-climate-field-trial",
    "https://www.iea.org/cold-climate-heat-pumps",
    "https://www.sciencedirect.com/cold-climate-trial-results",
    "https://www.nrel.gov/backup-heating-guidance",
    "https://www.iea.org/backup-heating-report",
    "https://www.sciencedirect.com/backup-heating-analysis",
)

_CONFLICT_URLS = (
    "https://www.aeaweb.org/four-day-work-week-trial",
    "https://www.aeaweb.org/four-day-work-week-replication",
    "https://www.nber.org/four-day-self-selection",
)

_FAILURE_URLS = (
    "https://www.nrel.gov/perovskite-encapsulation-study",
    "https://www.sciencedirect.com/perovskite-encapsulation-review",
)


def _planner_scenarios() -> dict[str, ScenarioScript]:
    return {
        "planner-clean-memory": ScenarioScript(
            search_responses={},
            http_pages={},
            memory_entries=(),
            reputations={},
            scripted_search_urls=(),
        ),
        "planner-ambiguous-scope": ScenarioScript(
            search_responses={},
            http_pages={},
            memory_entries=(
                {
                    "content": "Hospital triage pilots reported mixed results.",
                    "entry_type": "finding",
                },
                {
                    "content": "Diagnostic imaging models improved recall.",
                    "entry_type": "finding",
                },
            ),
            reputations={},
            scripted_search_urls=(),
        ),
        "planner-memory-failure": ScenarioScript(
            search_responses={
                "intermittent fasting metabolic health review": {
                    "results": [
                        {
                            "url": "https://www.nih.gov/fasting-review",
                            "title": "NIH review of intermittent fasting",
                            "content": "Meta-analysis of 24 trials.",
                        },
                        {
                            "url": "https://www.bmj.com/fasting-trial",
                            "title": "BMJ randomized trial",
                            "content": "12-month randomized comparison.",
                        },
                    ]
                }
            },
            http_pages={},
            memory_entries=(),
            reputations={},
            failures={"query_memory": RuntimeError(
                "long-term memory is unavailable"
            )},
            scripted_search_urls=(
                "https://www.nih.gov/fasting-review",
                "https://www.bmj.com/fasting-trial",
            ),
        ),
    }


def _researcher_scenarios() -> dict[str, ScenarioScript]:
    return {
        "researcher-multi-source": ScenarioScript(
            search_responses={
                "heat pump COP -15C field data": {
                    "results": [
                        {
                            "url": "https://www.nrel.gov/heat-pump-cop-below-freezing",
                            "title": "NREL cold-climate COP field data",
                            "content": "Field COP near 2.0 at -15C.",
                        },
                        {
                            "url": "https://www.iea.org/heat-pump-cop-report",
                            "title": "IEA heat pump performance report",
                            "content": "Median COP of 1.9 at -15C across "
                            "seven programs.",
                        },
                        {
                            "url": "https://www.sciencedirect.com/heat-pump-cop-review",
                            "title": "ScienceDirect heat pump COP review",
                            "content": "Modern units retain 70-80% of rated "
                            "capacity at -15C.",
                        },
                    ]
                },
                "cold climate heat pump field trial results": {
                    "results": [
                        {
                            "url": "https://www.nrel.gov/cold-climate-field-trial",
                            "title": "NREL cold-climate field trial",
                            "content": "100-home Minnesota trial cut heating "
                            "energy by a third.",
                        },
                        {
                            "url": "https://www.iea.org/cold-climate-heat-pumps",
                            "title": "IEA cold-climate heat pump assessment",
                            "content": "Reliable heating through -30C events.",
                        },
                        {
                            "url": "https://www.sciencedirect.com/cold-climate-trial-results",
                            "title": "ScienceDirect cold-climate trial "
                            "outcomes",
                            "content": "Higher comfort scores than resistance "
                            "heating in two trials.",
                        },
                    ]
                },
                "heat pump backup resistance heating cold climate": {
                    "results": [
                        {
                            "url": "https://www.nrel.gov/backup-heating-guidance",
                            "title": "NREL backup heating guidance",
                            "content": "Backup sized for the coldest design "
                            "day.",
                        },
                        {
                            "url": "https://www.iea.org/backup-heating-report",
                            "title": "IEA backup heating requirements",
                            "content": "Some backup capacity needed below "
                            "-20C design temperatures.",
                        },
                        {
                            "url": "https://www.sciencedirect.com/backup-heating-analysis",
                            "title": "ScienceDirect backup heating analysis",
                            "content": "Backup adds 5-15% to annual energy "
                            "use in simulations.",
                        },
                    ]
                },
            },
            http_pages={
                "https://www.nrel.gov/heat-pump-cop-below-freezing": (
                    "Field measurements at -15C show a coefficient of "
                    "performance near 2.0 for a cold-climate heat pump. "
                    "COP stayed above 1.5 even at -25C."
                ),
                "https://www.iea.org/heat-pump-cop-report": (
                    "The report compiles COP measurements below freezing "
                    "from seven national programs. Median COP at -15C was "
                    "1.9 across the tested units."
                ),
                "https://www.sciencedirect.com/heat-pump-cop-review": (
                    "A review of cold-climate COP studies finds modern "
                    "units retain 70-80% of rated capacity at -15C. Older "
                    "units fell below 1.5 COP at the same temperature."
                ),
                "https://www.nrel.gov/cold-climate-field-trial": (
                    "A 100-home field trial in Minnesota measured seasonal "
                    "performance over two winters. Homes using cold-climate "
                    "heat pumps cut heating energy use by a third."
                ),
                "https://www.iea.org/cold-climate-heat-pumps": (
                    "Field trials in Canada and Scandinavia report reliable "
                    "heating through -30C events. Backup demand stayed "
                    "below 10% of total heating energy."
                ),
                "https://www.sciencedirect.com/cold-climate-trial-results": (
                    "Two randomized trials report higher comfort scores "
                    "for heat pumps than resistance heating. Electricity "
                    "use rose modestly during extreme cold snaps."
                ),
                "https://www.nrel.gov/backup-heating-guidance": (
                    "Backup resistance heating is sized for the coldest "
                    "design day in cold climates. A correctly sized heat "
                    "pump covers 90% of the annual heating load."
                ),
                "https://www.iea.org/backup-heating-report": (
                    "Systems designed for below -20C temperatures "
                    "typically need some backup capacity. The report "
                    "recommends dual-fuel controls to limit resistance use."
                ),
                "https://www.sciencedirect.com/backup-heating-analysis": (
                    "Simulations show backup heating adds 5-15% to annual "
                    "energy use in cold climates. Oversized backup units "
                    "cost more without improving comfort."
                ),
            },
            scripted_search_urls=_MULTI_SOURCE_URLS,
        ),
        "researcher-conflicting": ScenarioScript(
            search_responses={
                "four-day work week trial productivity results": {
                    "results": [
                        {
                            "url": "https://www.aeaweb.org/four-day-work-week-trial",
                            "title": "AEA four-day work week trial",
                            "content": "Six-month trial reported a 22% "
                            "productivity gain.",
                        },
                        {
                            "url": "https://www.aeaweb.org/four-day-work-week-replication",
                            "title": "AEA four-day work week replication",
                            "content": "Matched-control replication found no "
                            "significant change.",
                        },
                    ]
                },
                "four-day work week productivity measurement methodology": {
                    "results": [
                        {
                            "url": "https://www.nber.org/four-day-self-selection",
                            "title": "NBER four-day self-selection analysis",
                            "content": "Apparent gain confounded by a "
                            "self-selected sample.",
                        },
                    ]
                },
            },
            http_pages={
                "https://www.aeaweb.org/four-day-work-week-trial": (
                    "The six-month trial reported a 22% productivity gain "
                    "for the four-day group. Output per employee rose while "
                    "absenteeism fell by a third."
                ),
                "https://www.aeaweb.org/four-day-work-week-replication": (
                    "A replication with matched control firms found no "
                    "significant change in productivity. The results "
                    "disagree with the earlier trial's headline gain."
                ),
                "https://www.nber.org/four-day-self-selection": (
                    "The apparent gain was confounded by self-selection: "
                    "the sample was self-selected from firms already "
                    "committed to a four-day week. The full sample shows "
                    "mixed results, suggesting the reported gain does not "
                    "generalize."
                ),
            },
            scripted_search_urls=_CONFLICT_URLS,
        ),
        "researcher-partial-failure": ScenarioScript(
            search_responses={
                "perovskite moisture-driven degradation": RuntimeError(
                    "search backend unavailable"
                ),
                "perovskite encapsulation approaches lifetime": {
                    "results": [
                        {
                            "url": "https://www.nrel.gov/perovskite-encapsulation-study",
                            "title": "NREL perovskite encapsulation study",
                            "content": "Glass-on-glass barrier passed 1,000 "
                            "hours of damp heat.",
                        },
                        {
                            "url": "https://www.sciencedirect.com/perovskite-encapsulation-review",
                            "title": "ScienceDirect perovskite "
                            "encapsulation review",
                            "content": "Peer-reviewed survey of "
                            "encapsulation approaches.",
                        },
                    ]
                },
            },
            http_pages={
                "https://www.nrel.gov/perovskite-encapsulation-study": (
                    "Glass-on-glass encapsulation kept perovskite cells "
                    "above 90% of initial efficiency after 1,000 hours of "
                    "damp heat. The barrier stack blocked moisture ingress "
                    "without adding opaque layers."
                ),
                "https://www.sciencedirect.com/perovskite-encapsulation-review": (
                    RuntimeError("connection reset")
                ),
            },
            scripted_search_urls=_FAILURE_URLS,
        ),
    }


def _source_evaluator_scenarios() -> dict[str, ScenarioScript]:
    return {
        "source-evaluator-mixed": ScenarioScript(
            reputations={"ipcc.ch": 0.95, "noaa.gov": 0.92},
        ),
        "source-evaluator-competing-signals": ScenarioScript(
            reputations={"ingaa.org": 0.55, "edf.org": 0.70},
        ),
        "source-evaluator-reputation-failure": ScenarioScript(
            reputations={"nist.gov": 0.80, "colorado.edu": 0.75},
            reputation_failures={
                "epa.gov": RuntimeError("reputation lookup failed"),
                "aqmd.gov": RuntimeError("reputation lookup failed"),
            },
        ),
    }


def _fact_checker_scenarios() -> dict[str, ScenarioScript]:
    # The scenario search keys are exactly the claim texts the cases pin as
    # expected verdicts (or failing-query anchors) in their references; the
    # case tests assert equality between the two sides.
    return {
        "fact-checker-mixed": ScenarioScript(
            search_responses={
                (
                    "Small modular reactor designs must satisfy the same "
                    "international safety standards as large reactors."
                ): {
                    "results": [
                        {
                            "url": "https://nrc.gov/smr-licensing-framework",
                            "title": "NRC: SMR licensing framework",
                            "content": (
                                "SMR designs undergo the same safety "
                                "assessment and licensing requirements as "
                                "large reactors."
                            ),
                        },
                        {
                            "url": "https://ans.org/smr-safety-assessment",
                            "title": "ANS: SMR safety assessment",
                            "content": (
                                "The ANS confirms that international safety "
                                "standards apply equally to SMR designs."
                            ),
                        },
                    ]
                },
                "No small modular reactor has operated commercially.": {
                    "results": [
                        {
                            "url": "https://nei.org/pevek-floating-plant",
                            "title": "NEI: Pevek floating plant",
                            "content": (
                                "The Akademik Lomonosov floating plant has "
                                "operated commercially at Pevek since 2020, "
                                "powered by two KLT-40S small modular "
                                "reactor units."
                            ),
                        },
                    ]
                },
                (
                    "Small modular reactors will be cheaper to build than "
                    "large reactors at scale."
                ): {
                    "results": [],
                },
            },
        ),
        "fact-checker-dependent-domains": ScenarioScript(
            search_responses={
                "The 2025 grid upgrade reduced outage minutes by 40 percent.": {
                    "results": [
                        {
                            "url": "https://news.example.com/outage-minutes-fall",
                            "title": "News: outage minutes fall after upgrade",
                            "content": (
                                "A follow-up confirms outage minutes fell "
                                "40 percent after the 2025 grid upgrade."
                            ),
                        },
                        {
                            "url": (
                                "https://syndication.news.example.com/"
                                "outage-minutes-fall"
                            ),
                            "title": "Syndicated: outage statistics",
                            "content": (
                                "The same 40 percent figure appears in the "
                                "syndicated outage statistics."
                            ),
                        },
                    ]
                },
            },
        ),
        "fact-checker-search-failure": ScenarioScript(
            search_responses={
                "Ocean heat content is still rising at the rate reported in 2023.": (
                    RuntimeError("search backend unavailable")
                ),
                (
                    "The recent acceleration in ocean heat content is "
                    "driven primarily by greenhouse gas forcing."
                ): {
                    "results": [
                        {
                            "url": "https://agu.org/ocean-heat-attribution",
                            "title": "AGU: ocean heat attribution study",
                            "content": (
                                "An AGU study attributes the post-2020 "
                                "ocean heat acceleration primarily to "
                                "greenhouse gas forcing."
                            ),
                        },
                        {
                            "url": "https://gcos.wmo.int/ocean-heat-bulletin",
                            "title": "GCOS: ocean heat bulletin",
                            "content": (
                                "The GCOS bulletin reports greenhouse gas "
                                "forcing as the dominant driver of the "
                                "recent ocean heat increase."
                            ),
                        },
                    ]
                },
            },
        ),
    }


def _synthesizer_scenarios() -> dict[str, ScenarioScript]:
    # The Synthesizer never queries memory and never queries reputation:
    # its only declared tools are write_document and save_to_memory. The
    # two happy scenarios therefore script nothing at all, and the
    # failure scenario scripts exactly the two tools that must fail —
    # write_document via the document-directory hook in
    # ``build_controlled_dependencies``, save_to_memory via the memory
    # double. Both tools stay *present*; they just fail when called,
    # which is the case's point (the agent's ``_require_tool`` raises on
    # a missing declared tool).
    return {
        "synthesizer-complete": ScenarioScript(),
        "synthesizer-conflicted": ScenarioScript(),
        "synthesizer-write-failure": ScenarioScript(
            failures={
                "write_document": OSError("read-only file system"),
                "save_to_memory": RuntimeError(
                    "long-term memory is unavailable"
                ),
            }
        ),
    }


def _critic_scenarios() -> dict[str, ScenarioScript]:
    """Three scripted Critic scenarios: one spot-check loop that can
    corroborate the strong report, one that surfaces the gaps in the
    gappy report, and one at the final allowed iteration whose memory
    backend is down while its search still succeeds."""
    return {
        "critic-strong-report": ScenarioScript(
            search_responses={
                "measured effect of urban tree canopy on summer surface "
                "temperature": {
                    "results": [
                        {
                            "url": (
                                "https://sciencedirect.com/tree-canopy-"
                                "surface-temperature-review"
                            ),
                            "title": "ScienceDirect: tree canopy surface "
                            "temperature review",
                            "content": (
                                "Peer-reviewed field measurements find "
                                "urban tree canopy lowers summer surface "
                                "temperatures by 1 to 5 degrees Celsius, "
                                "with the largest reductions at midday "
                                "and over impervious surfaces."
                            ),
                        },
                    ],
                },
            },
            memory_entries=(
                {
                    "content": (
                        "Prior sessions established that mature urban "
                        "tree canopy measurably lowers daytime surface "
                        "temperatures in warm climates."
                    ),
                    "entry_type": "finding",
                },
            ),
            scripted_search_urls=(
                "https://sciencedirect.com/tree-canopy-surface-"
                "temperature-review",
            ),
        ),
        "critic-gappy-report": ScenarioScript(
            search_responses={
                "municipal composting mandates participation rates": {
                    "results": [
                        {
                            "url": (
                                "https://citiesclimate.example.org/"
                                "composting-participation"
                            ),
                            "title": "Cities Climate: composting "
                            "participation study",
                            "content": (
                                "A survey of municipal composting "
                                "mandates finds participation rates "
                                "between 20 and 60 percent of eligible "
                                "households, with curbside pickup and "
                                "clear enforcement driving uptake."
                            ),
                        },
                    ],
                },
            },
            memory_entries=(
                {
                    "content": (
                        "Prior sessions noted that participation rates "
                        "drive the climate effect of organics mandates, "
                        "and that landfill methane estimates depend on "
                        "measurement methodology."
                    ),
                    "entry_type": "finding",
                },
            ),
            scripted_search_urls=(
                "https://citiesclimate.example.org/composting-"
                "participation",
            ),
        ),
        "critic-budget-exhausted": ScenarioScript(
            search_responses={
                "congestion pricing particulate pollution evidence": {
                    "results": [
                        {
                            "url": (
                                "https://wri.org/congestion-pricing-"
                                "air-quality-evidence"
                            ),
                            "title": "WRI: congestion pricing and air "
                            "quality evidence",
                            "content": (
                                "World Resources Institute analysis "
                                "finds congestion pricing reduced "
                                "particulate pollution in Stockholm and "
                                "London, with measurable air-quality "
                                "co-benefits in priced zones."
                            ),
                        },
                    ],
                },
            },
            failures={
                "query_memory": RuntimeError(
                    "long-term memory is unavailable"
                ),
            },
            scripted_search_urls=(
                "https://wri.org/congestion-pricing-air-quality-evidence",
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
