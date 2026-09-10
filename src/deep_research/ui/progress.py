"""Pure projections for the Streamlit research presentation layer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from deep_research.ui.models import (
    UiClaimDetail,
    UiCredibilityTier,
    UiFactCheckSummary,
    UiRecentActivity,
    UiSourceDetail,
    UiSourceSummary,
    UiSubTopicProgress,
    UiTokenUsage,
    UiToolCallSummary,
)
from deep_research.utils.types import (
    Claim,
    Finding,
    ResearchEvent,
    ScoredSource,
)

if TYPE_CHECKING:
    from deep_research.runtime.outcome import ResearchOutcome


class ProgressSummary(BaseModel):
    current_agent: str | None = None
    iteration: int = Field(default=0, ge=0)
    planned_sub_topic_count: int = Field(default=0, ge=0)
    research_phase_complete: bool = False
    sub_topics: list[UiSubTopicProgress] = Field(default_factory=list)
    recent_activity: list[UiRecentActivity] = Field(default_factory=list)
    tool_calls: list[UiToolCallSummary] = Field(default_factory=list)
    events_seen: int = Field(default=0, ge=0)


_TOOL_LABELS = {
    "document_reader": "Document reader",
    "query_memory": "Memory lookup",
    "save_to_memory": "Save to memory",
    "web_scraper": "Web scraper",
    "web_search": "Web search",
}

_GRAPH_ITERATION_EVENT_TYPES = frozenset(
    {
        "graph.node.started",
        "graph.node.completed",
        "graph.node.skipped",
        "graph.refinement.started",
        "graph.route.decided",
        "graph.session.completed",
    }
)

_AGENT_ACTIONS = {
    "planner": "Planning research subtopics",
    "researcher": "Searching and evaluating sources",
    "source_evaluator": "Evaluating source credibility",
    "fact_checker": "Checking research claims",
    "synthesizer": "Drafting the research answer",
    "critic": "Reviewing research quality",
}


def _safe_label(value: str | None, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    words = value.strip().replace("_", " ").replace("-", " ").split()
    if not words:
        return fallback
    normalized = " ".join(word.casefold() for word in words)
    return normalized[0].upper() + normalized[1:]


def display_agent_name(agent: str | None) -> str:
    """Return a readable agent name without exposing its internal spelling."""
    return _safe_label(agent, "Unknown agent")


def display_agent_action(agent: str | None) -> str:
    """Return a safe, plain-language action for the current graph agent."""
    if not isinstance(agent, str):
        return "Working through the research plan"
    return _AGENT_ACTIONS.get(
        agent.strip().casefold(),
        "Working through the research plan",
    )


def display_tool_name(tool_name: str) -> str:
    """Return a plain-language label for a tool identifier."""
    if tool_name in _TOOL_LABELS:
        return _TOOL_LABELS[tool_name]
    return _safe_label(tool_name, "Unknown tool")


def _integer(value: object, *, minimum: int = 0) -> int | None:
    if type(value) is int and value >= minimum:
        return value
    return None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _metadata(event: ResearchEvent) -> Mapping[str, object]:
    return event.metadata


def _topic_title(metadata: Mapping[str, object]) -> str | None:
    return _text(metadata.get("sub_topic"))


def _tool_identifier(metadata: Mapping[str, object]) -> str | None:
    tool = _text(metadata.get("tool"))
    if tool is not None:
        return tool
    return _text(metadata.get("tool_name"))


def _outer_iteration(event: ResearchEvent) -> int | None:
    if event.event_type not in _GRAPH_ITERATION_EVENT_TYPES:
        return None
    return _integer(event.metadata.get("iteration"))


def _clear_unvisited_topics(topics: dict[int, dict[str, object]]) -> None:
    """Remove work that did not become a completed, titled research row."""
    for index in list(topics):
        if topics[index].get("status") != "completed":
            del topics[index]


def _activity_for_event(event: ResearchEvent) -> UiRecentActivity | None:
    metadata = _metadata(event)
    event_type = event.event_type
    title = _topic_title(metadata)

    if event_type == "researcher.sub_topic.started":
        if _integer(metadata.get("index"), minimum=1) is None:
            return None
        return UiRecentActivity(
            event_type=event_type,
            summary=f"Started {title or 'a sub-topic'}",
        )

    if event_type == "researcher.sub_topic.completed":
        if _integer(metadata.get("index"), minimum=1) is None:
            return None
        findings = _integer(metadata.get("findings"))
        if findings is None:
            summary = f"Completed {title or 'a sub-topic'}"
        else:
            noun = "evidence item" if findings == 1 else "evidence items"
            summary = (
                f"Completed {title or 'a sub-topic'}; "
                f"{findings} {noun} recorded"
            )
        return UiRecentActivity(event_type=event_type, summary=summary)

    if event_type == "researcher.tool_call":
        if _tool_identifier(metadata) is None:
            return None
        success = metadata.get("success")
        if success is False:
            return UiRecentActivity(
                event_type=event_type,
                summary="A research step had an issue; continuing",
            )
        if success is True:
            return UiRecentActivity(
                event_type=event_type,
                summary=(
                    f"Evaluated new evidence for {title or 'the research question'}"
                ),
            )
        return None

    known_summaries = {
        "researcher.research.completed": "Research pass completed",
        "source_evaluator.evaluation.completed": "Evaluated source credibility",
        "fact_checker.claim.checked": "Checked a research claim",
        "fact_checker.fact_check.completed": "Fact-checked research claims",
        "synthesizer.synthesis.started": "Drafting the research answer",
        "synthesizer.synthesis.completed": "Research answer drafted",
        "critic.critique.started": "Reviewing research quality",
        "critic.critique.completed": "Research quality review completed",
        "graph.refinement.started": "Research refinement started",
        "graph.session.completed": "Research session completed",
    }
    summary = known_summaries.get(event_type)
    if summary is not None:
        return UiRecentActivity(event_type=event_type, summary=summary)
    return None


def project_progress(events: Sequence[ResearchEvent]) -> ProgressSummary:
    """Project structured events into truthful, user-facing progress data."""
    current_agent: str | None = None
    iteration = 0
    planned_sub_topic_count = 0
    research_phase_seen = False
    research_phase_complete = False
    topics: dict[int, dict[str, object]] = {}
    tools: dict[str, list[int]] = {}
    activities: list[UiRecentActivity] = []
    fallback_activities: list[UiRecentActivity] = []

    for event in events:
        metadata = _metadata(event)
        event_iteration = _outer_iteration(event)
        if event_iteration is not None:
            iteration = event_iteration

        if event.event_type == "graph.node.started":
            node = _text(metadata.get("node"))
            if node is not None:
                if node == "researcher":
                    research_phase_seen = True
                    research_phase_complete = False
                elif research_phase_seen:
                    research_phase_complete = True
                    _clear_unvisited_topics(topics)
                current_agent = node
                fallback_activities.append(
                    UiRecentActivity(
                        event_type=event.event_type,
                        summary=f"Started {display_agent_name(node)}",
                    )
                )

        if event.event_type == "graph.session.completed":
            status = _text(metadata.get("status"))
            if status != "failed":
                current_agent = None
                if research_phase_seen:
                    research_phase_complete = True
                    _clear_unvisited_topics(topics)

        if event.event_type == "planner.planning.completed":
            planned_count = _integer(metadata.get("sub_topic_count"), minimum=1)
            if planned_count is not None:
                planned_sub_topic_count = max(
                    planned_sub_topic_count,
                    planned_count,
                )

        if event.event_type == "researcher.research.completed":
            research_phase_seen = True
            research_phase_complete = True
            planned_count = _integer(
                metadata.get("sub_topics_planned"),
                minimum=1,
            )
            if planned_count is not None:
                planned_sub_topic_count = max(
                    planned_sub_topic_count,
                    planned_count,
                )
            _clear_unvisited_topics(topics)

        index = _integer(metadata.get("index"), minimum=1)
        if index is not None and event.event_type in {
            "researcher.sub_topic.started",
            "researcher.sub_topic.completed",
        }:
            title = _topic_title(metadata)
            priority = _integer(metadata.get("priority"), minimum=1)
            topic = topics.get(index)
            if topic is None:
                if title is None:
                    continue
                topic = {
                    "index": index,
                    "title": title,
                    "status": "queued",
                    "priority": priority,
                    "findings": None,
                }
                topics[index] = topic
            if topic:
                if title is not None and "title" not in topic:
                    topic["title"] = title
                if priority is not None and topic.get("priority") is None:
                    topic["priority"] = priority
                if event.event_type == "researcher.sub_topic.started":
                    topic["status"] = "running"
                else:
                    topic["status"] = "completed"
                    findings = _integer(metadata.get("findings"))
                    if findings is not None:
                        topic["findings"] = findings

        tool = _tool_identifier(metadata)
        if tool is not None:
            counts = tools.setdefault(tool, [0, 0])
            counts[0] += 1
            if metadata.get("success") is False:
                counts[1] += 1

        activity = _activity_for_event(event)
        if activity is not None:
            activities.append(activity)

    meaningful = activities or fallback_activities
    known_topics = [
        UiSubTopicProgress.model_validate(topics[index])
        for index in sorted(topics)
        if topics[index].get("title")
    ]
    sub_topics = known_topics

    return ProgressSummary(
        current_agent=current_agent,
        iteration=iteration,
        planned_sub_topic_count=planned_sub_topic_count,
        research_phase_complete=research_phase_complete,
        sub_topics=sub_topics,
        recent_activity=meaningful[-3:],
        tool_calls=[
            UiToolCallSummary(
                tool_name=tool_name,
                display_label=display_tool_name(tool_name),
                calls=counts[0],
                failures=counts[1],
            )
            for tool_name, counts in tools.items()
        ],
        events_seen=len(events),
    )


def token_usage_from_outcome(outcome: ResearchOutcome) -> UiTokenUsage | None:
    """Return provider usage only when at least one token was reported."""
    usage = outcome.token_usage
    if usage.input_tokens == 0 and usage.output_tokens == 0:
        return None
    return UiTokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )


def credibility_tier(source: ScoredSource) -> UiCredibilityTier:
    if (
        source.low_confidence
        and source.recency_score == 0.0
        and source.relevance_score == 0.0
    ):
        return "unrated"
    if source.low_confidence:
        return "low"
    if source.overall_score < 0.75:
        return "moderate"
    return "high"


def _normalize_source_url(url: str) -> str:
    collapsed = " ".join(url.split())
    try:
        parts = urlsplit(collapsed)
        if not parts.scheme or not parts.hostname:
            return collapsed
        scheme = parts.scheme.casefold()
        host = parts.hostname.casefold()
        if host.startswith("www."):
            host = host[4:]
        netloc = host
        port = parts.port
        if port is not None and not (
            (scheme == "http" and port == 80)
            or (scheme == "https" and port == 443)
        ):
            netloc = f"{host}:{port}"
        return urlunsplit((scheme, netloc, parts.path.rstrip("/"), parts.query, ""))
    except ValueError:
        return collapsed


def source_summary(
    sources: Sequence[ScoredSource],
    findings: Sequence[Finding],
) -> UiSourceSummary:
    """Summarize scored sources and attach finding topics by canonical URL."""
    topics_by_url: dict[str, list[str]] = {}
    for finding in findings:
        url = _normalize_source_url(finding.source_url)
        topic = finding.related_sub_topic
        topics = topics_by_url.setdefault(url, [])
        if topic not in topics:
            topics.append(topic)

    details: list[UiSourceDetail] = []
    tier_counts = {tier: 0 for tier in ("high", "moderate", "low", "unrated")}
    for source in sources:
        tier = credibility_tier(source)
        tier_counts[tier] += 1
        details.append(
            UiSourceDetail(
                title=source.title,
                url=source.url,
                tier=tier,
                overall_score=source.overall_score,
                rationale=source.rationale,
                corroboration_score=source.corroboration_score,
                related_sub_topics=topics_by_url.get(
                    _normalize_source_url(source.url), []
                ),
            )
        )
    return UiSourceSummary(
        total=len(sources),
        high=tier_counts["high"],
        moderate=tier_counts["moderate"],
        low=tier_counts["low"],
        unrated=tier_counts["unrated"],
        details=details,
    )


def fact_check_summary(claims: Sequence[Claim]) -> UiFactCheckSummary:
    counts = {
        "verified": 0,
        "unverified": 0,
        "contradicted": 0,
        "insufficient_evidence": 0,
    }
    details = []
    for claim in claims:
        counts[claim.verdict] += 1
        details.append(
            UiClaimDetail(
                text=claim.text,
                verdict=claim.verdict,
                confidence=claim.confidence,
                source_urls=list(claim.source_urls),
                evidence=list(claim.evidence),
                contradictions=list(claim.contradictions),
            )
        )
    return UiFactCheckSummary(details=details, **counts)


def limitations_from_outcome(outcome: ResearchOutcome) -> list[str]:
    """Project critic gaps without folding execution errors into limitations."""
    critique = outcome.state.critique
    if critique is None:
        return []
    limitations: list[str] = []
    for item in [*critique.gaps, *critique.unsupported_claims]:
        text = item.strip()
        if text and text not in limitations:
            limitations.append(text)
    return limitations


__all__ = [
    "ProgressSummary",
    "credibility_tier",
    "display_agent_action",
    "display_agent_name",
    "display_tool_name",
    "fact_check_summary",
    "limitations_from_outcome",
    "project_progress",
    "source_summary",
    "token_usage_from_outcome",
]
