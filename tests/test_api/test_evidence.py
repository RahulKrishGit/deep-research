"""E1: the evidence JSON agrees with the evidence log the same run composed."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from deep_research.api.app import create_app
from deep_research.api.evidence import build_evidence_response, evidence_from_composition
from deep_research.api.models import EvidenceFindingResponse, EvidenceResponse
from deep_research.runtime.outcome import ResearchOutcome
from tests.test_api.fakes import GateRunner, ScriptedRunner
from tests.test_api.replay_support import DROPPED_FINDING_CASE, EXTRA_PASS_CASE, replay_outcome
from tests.test_api.test_app import valid_preflight, wait_until_terminal
from tests.test_api.test_sessions import judged_state

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


E1_KEYS = {"session_id", "iteration", "findings", "not_found", "refused"}
FINDING_KEYS = {"label", "status", "dropped_reason", "context_unchecked", "cited", "target_ids", "content", "snippet", "passage", "source", "figures"}
SOURCE_KEYS = {"url", "title", "organisation", "evaluation_status", "low_confidence", "authority_score", "recency_score", "relevance_score", "overall_score"}


def test_evidence_route_codes() -> None:
    gate = GateRunner()
    app = create_app(runner=gate, preflight=valid_preflight)
    with TestClient(app) as client:
        assert client.get("/research/nope/evidence").status_code == 404
        session_id = client.post("/research", json={"query": "Question"}).json()["session_id"]
        running = client.get(f"/research/{session_id}/evidence")
        assert running.status_code == 409
        assert running.json()["error"]["code"] == "session_not_complete"
        assert client.get(f"/research/{session_id}/evidence?format=pdf").status_code == 422
        client.portal.call(gate.release.set)  # set the asyncio.Event on the app's loop, as the existing tests do
        wait_until_terminal(client, session_id)
    # a finished run without a composition or a log
    app = create_app(runner=ScriptedRunner(), preflight=valid_preflight)
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": "Question"}).json()["session_id"]
        wait_until_terminal(client, session_id)
        for query in ("", "?format=markdown"):
            response = client.get(f"/research/{session_id}/evidence{query}")
            assert response.status_code == 409
            assert response.json()["error"] == {
                "code": "evidence_unavailable",
                "message": "Research session finished without an evidence log.",
                "reason": None,
                "issues": [],
            }


def test_evidence_route_serves_json_and_markdown() -> None:
    state = judged_state().model_copy(update={"report_evidence": "# Evidence log: q\n\n## Findings\n"})
    app = create_app(runner=ScriptedRunner(state=state), preflight=valid_preflight)
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": "Question"}).json()["session_id"]
        wait_until_terminal(client, session_id)
        body = client.get(f"/research/{session_id}/evidence").json()
        assert set(body) == E1_KEYS
        assert [f["label"] for f in body["findings"]] == ["X01", "X02"]
        assert all(set(f) == FINDING_KEYS and set(f["source"]) == SOURCE_KEYS for f in body["findings"])
        assert body["not_found"][0]["target_id"] == "topic-02-target-01"
        markdown = client.get(f"/research/{session_id}/evidence?format=markdown")
        assert markdown.status_code == 200
        assert markdown.headers["content-type"].startswith("text/markdown")
        assert markdown.text == "# Evidence log: q\n\n## Findings\n"
