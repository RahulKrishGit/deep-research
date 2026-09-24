"""Builders for reads, findings and targets, shared by the Evidence Verifier tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deep_research.agents.evidence import build_read_record
from deep_research.utils.types import EvidenceTarget, Finding, FindingFigure, ReadRecord

EIA_URL = "https://www.eia.gov/todayinenergy/detail.php?id=64705"
EIA_TITLE = "U.S. battery capacity increased 66% in 2024"
EIA_PAGE = (
    "U.S. battery capacity increased 66% in 2024. Generators added 10.4 "
    "gigawatts (GW) of new battery storage capacity in 2024, the second-largest "
    "generating capacity addition after solar, according to our January 2025 "
    "Preliminary Monthly Electric Generator Inventory. In 2025, capacity growth "
    "from battery storage could set a record as operators report plans to add "
    "19.6 GW of utility-scale battery storage to the grid."
)


def make_read(
    text: str = EIA_PAGE,
    *,
    url: str = EIA_URL,
    title: str = EIA_TITLE,
    passages: dict[str, str] | None = None,
) -> ReadRecord:
    return build_read_record(
        session_id="test-session",
        reader="web_scraper",
        requested_url=url,
        resolved_url=url,
        title=title,
        retrieved_at="2026-09-24T00:00:00+00:00",
        text=text,
        passages=passages or {"page-1-chunk-0": text},
    )


def figure(
    value: str, unit: str, period: str | None = None, kind: str | None = None
) -> FindingFigure:
    return FindingFigure(value=value, unit=unit, period=period, kind=kind)


def make_finding(
    read: ReadRecord,
    snippet: str,
    *,
    figures: Sequence[FindingFigure] = (),
    content: str | None = None,
    target_ids: Sequence[str] = (),
    **fields: Any,
) -> Finding:
    locator = next(
        (key for key, value in read.passages.items() if snippet in value),
        next(iter(read.passages)),
    )
    return Finding(
        content=content or snippet,
        source_url=read.resolved_url,
        source_title=read.title,
        extracted_at="2026-09-24T00:00:00+00:00",
        confidence=0.9,
        related_sub_topic="Battery storage",
        snippet=snippet,
        read_id=read.read_id,
        locator=locator,
        figures=list(figures),
        target_ids=list(target_ids),
        **fields,
    )


def make_target(target_id: str = "topic-01-target-01", **fields: Any) -> EvidenceTarget:
    """A target that validates in the current phase (Task 5.1 deletes the legacy branch)."""
    base: dict[str, Any] = {
        "target_id": target_id,
        "coverage_id": target_id.rsplit("-target-", 1)[0],
        "question": "How much battery storage capacity was added in the United States in 2024?",
        "measure": "battery storage power capacity added",
        "unit_dimension": "power",
        "period": "2024",
        "kind": "actual",
        "geography": "United States",
        "organisation": None,
        "required": True,
    }
    if "required_dimensions" in EvidenceTarget.model_fields:
        base.update(
            required_dimensions=["measure: battery storage power capacity added"],
            critical=False,
            support_policy="primary_attribution",
        )
    base.update(fields)
    return EvidenceTarget(**base)
