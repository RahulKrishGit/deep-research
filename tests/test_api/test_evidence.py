"""E1: the evidence JSON agrees with the evidence log the same run composed."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from deep_research.api.evidence import build_evidence_response, evidence_from_composition
from deep_research.api.models import EvidenceFindingResponse, EvidenceResponse
from deep_research.runtime.outcome import ResearchOutcome
from tests.test_api.replay_support import DROPPED_FINDING_CASE, EXTRA_PASS_CASE, replay_outcome

HEADING = re.compile(r"^### (\S+) — ", re.MULTILINE)


@pytest.fixture(scope="module", params=[EXTRA_PASS_CASE, DROPPED_FINDING_CASE])
def outcome(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> ResearchOutcome:
    return replay_outcome(request.param, tmp_path_factory.mktemp(request.param))


def test_labels_follow_the_evidence_log_headings(outcome: ResearchOutcome) -> None:
    evidence = build_evidence_response(outcome)
    assert outcome.state.report_evidence is not None
    assert [f.label for f in evidence.findings] == HEADING.findall(outcome.state.report_evidence)
    if any(f.status == "dropped" for f in evidence.findings):
        assert evidence.findings[0].label == "X01"


def test_counts_agree_with_the_outcome(outcome: ResearchOutcome) -> None:
    evidence = build_evidence_response(outcome)
    counts = outcome.evidence_counts
    assert counts is not None
    assert sum(f.cited for f in evidence.findings) == counts.cited_findings
    by_status = {s: sum(1 for f in evidence.findings if f.status == s) for s in ("verified", "verified_corrected", "quoted", "dropped")}
    assert by_status == {
        "verified": counts.verified_findings,
        "verified_corrected": counts.corrected_findings,
        "quoted": counts.quoted_findings,
        "dropped": counts.dropped_findings,
    }
    composition = outcome.composition
    assert composition is not None
    assert len(evidence.not_found) == len(composition.not_found)
    assert len(evidence.refused) == len(composition.rejected_points)
    assert evidence.session_id == composition.session_id
    assert evidence.iteration == composition.iteration


def test_kept_figures_carry_context_and_dropped_ones_do_not(outcome: ResearchOutcome) -> None:
    evidence = build_evidence_response(outcome)
    figures = [fig for f in evidence.findings for fig in f.figures]
    assert figures, "the cases carry figures"
    for fig in figures:
        if fig.kept:
            assert fig.organisation and fig.attribution and fig.kind
        else:
            assert fig.dropped_reason is not None
            assert (fig.period, fig.scope, fig.organisation, fig.attribution, fig.kind, fig.release) == (None,) * 6


def test_a_finding_without_a_scored_source_serialises_with_null_scores(outcome: ResearchOutcome) -> None:
    composition = outcome.composition
    assert composition is not None
    evidence = evidence_from_composition(composition.model_copy(update={"sources": []}))
    for f in evidence.findings:
        assert f.source.evaluation_status is None
        assert f.source.low_confidence is False
        assert (f.source.authority_score, f.source.recency_score, f.source.relevance_score, f.source.overall_score) == (None,) * 4
        assert f.source.organisation
    EvidenceResponse.model_validate(evidence.model_dump(mode="json"))


def test_response_models_keep_verbatim_whitespace() -> None:
    finding = EvidenceFindingResponse.model_validate(
        {
            "label": "F01", "status": None, "dropped_reason": None, "context_unchecked": False, "cited": False,
            "target_ids": [], "content": "c", "snippet": "  two spaces either side  ", "passage": "\tpassage\n",
            "source": {"url": "https://example.org/", "title": "t", "organisation": "o", "evaluation_status": None,
                       "low_confidence": False, "authority_score": None, "recency_score": None,
                       "relevance_score": None, "overall_score": None},
            "figures": [],
        }
    )
    again = EvidenceFindingResponse.model_validate(finding.model_dump(mode="json"))
    assert again.snippet == "  two spaces either side  "
    assert again.passage == "\tpassage\n"
