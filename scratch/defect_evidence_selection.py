"""Minimal reproduction: a statement backed by selected evidence carries no ids.

Run:
  PYTHONPATH=<worktree>/src python -m pytest scratch/defect_evidence_selection.py -q
"""

from __future__ import annotations

from deep_research.utils.types import (
    Claim,
    EvidencePassage,
    EvidenceUnit,
    derive_statement,
)


def _claim() -> Claim:
    return Claim(
        claim_id="claim-1",
        text="The measured efficiency of the Acme widget is 42 percent.",
        source_urls=["https://official.example/measurement"],
        verdict="insufficient_evidence",
        confidence=0.5,
        evidence=["The measured efficiency of the Acme widget is 42 percent."],
        contradictions=[],
        verification_evidence=[
            EvidencePassage(
                source_url="https://official.example/measurement",
                source_title="Acme measurement report 2025",
                locator="chunk-0",
                excerpt=(
                    "The measured efficiency of the Acme widget is 42 percent."
                ),
                stance="supports",
            )
        ],
        evidence_status="source_supported",
        # The written contract: keyed by the evidence id, valued with the
        # stance it was selected under (types.py Claim.evidence_selection;
        # tests/test_agents/test_fact_checker.py:5027 asserts
        # set(claim.evidence_selection) == {"ev-left", "ev-right"}).
        evidence_selection={"ev-1": "supports"},
    )


def _unit() -> EvidenceUnit:
    return EvidenceUnit(
        evidence_id="ev-1",
        read_id="read-1",
        source_url="https://official.example/measurement",
        source_title="Acme measurement report 2025",
        locator="chunk-0",
        excerpt="The measured efficiency of the Acme widget is 42 percent.",
        target_ids=["topic-01"],
        origin="researcher",
    )


def test_a_selected_evidence_id_reaches_the_statement() -> None:
    """The statement map has to carry the ids the claim selected."""
    statement = derive_statement(
        statement_id="C001",
        text="Acme widget",
        claims=[_claim()],
        clusters={},
        evidence={"ev-1": _unit()},
        dimensions_by_target={},
        mode="attributed",
    )

    assert statement.evidence_ids == ["ev-1"]

