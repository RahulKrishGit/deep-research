"""Offline agent doubles and domain builders for graph tests.

No provider, no tracker, no scratchpad, no toolset: the node wrappers
depend on the ``ResearchAgent`` protocol (``name`` plus ``run``), so a
graph test never has to assemble a real agent — which would need an API
key this repository deliberately does not have.

Not collected by pytest: the filename does not match ``test_*.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deep_research.agents.base import AgentRun
from deep_research.agents.steps import ReActRun
from deep_research.utils.types import (
    Claim,
    Critique,
    Finding,
    ResearchError,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
    SubTopic,
)

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"
SOURCE_URL = "https://example.org/a"


class FakeAgent:
    """Serve scripted state updates instead of running a real agent.

    ``updates`` is consumed one entry per call and the final entry repeats,
    which is how "a critic that asks for one refinement and then accepts"
    is expressed without scripting every pass. A queued ``BaseException``
    is raised instead of returned, which is how agent failures are
    simulated.
    """

    def __init__(
        self,
        name: str,
        updates: Sequence[ResearchStateUpdate | BaseException] = (),
    ) -> None:
        self.name = name
        self._updates: list[Any] = list(updates) or [{}]
        self.calls: list[ResearchState] = []

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        self.calls.append(state)
        position = min(len(self.calls) - 1, len(self._updates) - 1)
        update = self._updates[position]
        if isinstance(update, BaseException):
            raise update
        return AgentRun(
            agent_name=self.name,
            result=None,
            react=ReActRun(agent_name=self.name, stop_reason="finished"),
            errors=[],
            state_update=update,
        )


def fake_sub_topic(title: str = "Error correction") -> SubTopic:
    return SubTopic(
        title=title,
        rationale="It is the bottleneck.",
        search_queries=["qec 2025"],
        success_criteria=["a logical error rate is quoted"],
        priority=1,
    )


def fake_finding(content: str = "Break-even was reached.") -> Finding:
    return Finding(
        content=content,
        source_url=SOURCE_URL,
        source_title="QEC 2025",
        extracted_at=EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic="Error correction",
    )


def fake_scored_source(url: str = SOURCE_URL) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="QEC 2025",
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        corroboration_score=0.5,
        overall_score=0.76,
        rationale="Peer-reviewed and corroborated.",
    )


def fake_claim(text: str = "Break-even was reached in 2025.") -> Claim:
    return Claim(
        text=text,
        source_urls=[SOURCE_URL],
        verdict="verified",
        confidence=0.8,
        evidence=["An independent review states the same figure."],
        contradictions=[],
    )


def fake_critique(*, should_continue: bool, score: int = 5) -> Critique:
    return Critique(
        score=score,
        gaps=["No cost data."] if should_continue else [],
        unsupported_claims=[],
        recommended_queries=["qec cost 2025"] if should_continue else [],
        should_continue=should_continue,
        rationale="Recorded for graph tests.",
    )


def halting_error(node: str = "researcher") -> ResearchError:
    """One already-recorded graph halt, for tests that start from a dead run."""
    return ResearchError(
        error_type="graph_invalid_agent_state",
        source=f"graph.{node}",
        message="An agent returned a state update the research state rejected.",
        recoverable=False,
    )
