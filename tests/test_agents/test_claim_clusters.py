"""Tests for atomic claim identity and fair claim scheduling.

Task 5 gives a claim a stable semantic identity: one proposition, one cluster
id, and one piece of work per outstanding obligation. Three properties carry
the weight, and each is tested on its own:

* two propositions that assert the same thing are compatible however they are
  worded, and two that differ in any qualifier — year, period, geography,
  population, unit, denominator, attribution, forecast status, negation — are
  not;
* a cluster id is minted once from its first anchor and persisted, so a
  refinement adds evidence without minting a second identity;
* a batch takes one outstanding obligation per target in the plan's own order
  before it takes extra low-value claims, and everything it leaves out stays
  pending rather than disappearing.
"""

from __future__ import annotations

import pytest

from deep_research.agents.claim_clusters import (
    atomic_compatible,
    claim_cluster_id,
    cluster_for_atom,
    extract_atoms,
    merge_claim_clusters,
    select_claim_batch,
    select_claim_batch_indices,
    target_order_for,
)
from deep_research.utils.types import (
    AtomicProposition,
    Claim,
    EvidencePassage,
    EvidenceUnit,
    ResearchState,
    SubTopic,
)

# --------------------------------------------------------------------------
# atomic_compatible: necessary, not sufficient
# --------------------------------------------------------------------------


def _queue_proposition(**overrides: object) -> AtomicProposition:
    """One queue-capacity proposition, with any dimension a test varies."""
    fields: dict[str, object] = {
        "text": "The 2024 interconnection queue held 10 GW of capacity.",
        "subject": "interconnection queue",
        "predicate": "capacity",
        "value": "10",
        "unit": "GW",
        "observation_period": "2024",
    }
    fields.update(overrides)
    return AtomicProposition(**fields)


def test_atomic_numbers_and_periods_do_not_collapse() -> None:
    a = AtomicProposition(
        text="The 2024 queue was 10 GW.",
        subject="queue",
        predicate="capacity",
        value="10",
        unit="GW",
        observation_period="2024",
    )
    b = a.model_copy(update={"value": "10000", "unit": "MW"})
    c = a.model_copy(update={"observation_period": "2025"})
    assert not atomic_compatible(a, c)
    # Unit equivalence requires an explicit checked normalization, not text
    # matching.
    assert not atomic_compatible(a, b)


def test_a_paraphrase_of_one_proposition_is_compatible() -> None:
    """How a claim is worded is not part of what it asserts."""
    stated = _queue_proposition()
    paraphrased = _queue_proposition(
        text="10 GW sat in the 2024 interconnection queue.",
    )
    pjm = _queue_proposition(
        text="PJM's 2024 interconnection queue held 10 GW of capacity.",
        geography="PJM",
    )
    pjm_reworded = _queue_proposition(
        text="10 GW sat in PJM's 2024 interconnection queue.",
        geography="PJM",
    )

    assert stated.text != paraphrased.text
    assert atomic_compatible(stated, paraphrased)
    assert pjm.text != pjm_reworded.text
    assert atomic_compatible(pjm, pjm_reworded)


def test_a_different_year_is_not_compatible() -> None:
    a = _queue_proposition()
    b = _queue_proposition(
        text="The 2025 interconnection queue held 10 GW of capacity.",
        observation_period="2025",
    )

    assert not atomic_compatible(a, b)


def test_a_different_observation_period_is_not_compatible() -> None:
    """A single year and a multi-year period are different observations."""
    a = _queue_proposition()
    b = _queue_proposition(
        text="The 2022-2024 interconnection queue held 10 GW of capacity.",
        observation_period="2022-2024",
    )

    assert not atomic_compatible(a, b)


def test_a_different_geography_is_not_compatible() -> None:
    a = _queue_proposition(geography="PJM")
    b = _queue_proposition(geography="ERCOT")

    assert not atomic_compatible(a, b)


def test_a_different_population_is_not_compatible() -> None:
    a = _queue_proposition(population="all interconnection requests")
    b = _queue_proposition(population="utility-scale interconnection requests")

    assert not atomic_compatible(a, b)


def test_a_different_capacity_unit_is_not_compatible() -> None:
    """10 GW and 10000 MW are one quantity and two units: no conversion here."""
    a = _queue_proposition()
    b = _queue_proposition(value="10000", unit="MW")

    assert not atomic_compatible(a, b)


def test_a_different_percentage_denominator_is_not_compatible() -> None:
    """A share of one base is not a share of another."""
    a = _queue_proposition(
        text="40 percent of surveyed capacity was withdrawn.",
        value="40",
        unit="%",
        denominator="surveyed capacity",
    )
    b = _queue_proposition(
        text="40 percent of installed capacity was withdrawn.",
        value="40",
        unit="%",
        denominator="installed capacity",
    )

    assert not atomic_compatible(a, b)


def test_a_different_attribution_is_not_compatible() -> None:
    a = _queue_proposition(attribution="Example Lab")
    b = _queue_proposition(attribution="Acme Grid")

    assert not atomic_compatible(a, b)


def test_a_different_forecast_status_is_not_compatible() -> None:
    """A projection is not a measurement, however alike the numbers look."""
    a = _queue_proposition(forecast_status="observed")
    b = _queue_proposition(forecast_status="projected")

    assert not atomic_compatible(a, b)


def test_a_negated_proposition_is_not_compatible() -> None:
    a = _queue_proposition()
    b = _queue_proposition(negated=True)

    assert not atomic_compatible(a, b)


def test_a_units_spelling_is_normalized_but_its_scale_is_not() -> None:
    """Spelling folds; conversion does not happen at this boundary."""
    a = _queue_proposition()

    assert atomic_compatible(a, a.model_copy(update={"unit": "gigawatts"}))
    assert not atomic_compatible(a, a.model_copy(update={"unit": "MW"}))
    assert not atomic_compatible(a, a.model_copy(update={"unit": "GWh"}))


def test_a_differently_formatted_number_is_still_the_same_assertion() -> None:
    """Exact string matching on numbers must not exclude a real duplicate."""
    a = _queue_proposition(value="10000")

    assert atomic_compatible(a, a.model_copy(update={"value": "10,000"}))
    assert atomic_compatible(a, a.model_copy(update={"value": "10000.0"}))
    assert not atomic_compatible(a, a.model_copy(update={"value": "1000"}))


def test_a_proposition_stating_a_year_the_other_does_not_is_refused() -> None:
    """A digit the other proposition never states is a different assertion.

    Only the refusal direction is safe: a candidate the atomic check cannot
    prove equal costs extra work, while a false merge silently deletes
    evidence.
    """
    a = _queue_proposition()
    b = _queue_proposition(value="10", observation_period="")

    assert not atomic_compatible(a, b)


# --------------------------------------------------------------------------
# Cluster identity
# --------------------------------------------------------------------------


def test_a_cluster_id_comes_from_its_first_anchor_and_is_persisted() -> None:
    anchor = _queue_proposition()
    cluster = cluster_for_atom(anchor)

    assert cluster.cluster_id == claim_cluster_id(anchor)
    assert cluster.cluster_id == claim_cluster_id(anchor)
    assert cluster.proposition.text == anchor.text


def test_merging_a_cluster_keeps_the_canonical_id_and_aliases_the_other() -> None:
    """The brief's merge invariant, asserted exactly as written."""
    existing_cluster = cluster_for_atom(
        _queue_proposition(),
        claim_id="claim-a",
    )
    incoming = cluster_for_atom(
        _queue_proposition(text="10 GW sat in the 2024 interconnection queue."),
        claim_id="claim-b",
    )
    incoming = incoming.model_copy(
        update={"cluster_id": "cluster-incoming-identity"}
    )

    canonical_id = existing_cluster.cluster_id
    merged = merge_claim_clusters(existing_cluster, incoming)

    assert merged.evidence_ids == sorted(
        set(existing_cluster.evidence_ids) | set(incoming.evidence_ids)
    )
    assert merged.member_claim_ids == sorted(
        set(existing_cluster.member_claim_ids) | set(incoming.member_claim_ids)
    )
    assert merged.target_ids == sorted(
        set(existing_cluster.target_ids) | set(incoming.target_ids)
    )
    assert merged.cluster_id == canonical_id
    # The oldest stable id survives and the other becomes an alias.
    assert merged.cluster_aliases == ["cluster-incoming-identity"]
    assert merged.proposition.text == existing_cluster.proposition.text


def test_a_cluster_id_never_rehashes_its_sorted_members() -> None:
    """Refinement adds evidence; it never mints a second identity."""
    anchor = _queue_proposition()
    small = anchor.model_copy(update={"evidence_ids": ["evidence-1"]})
    large = anchor.model_copy(
        update={"evidence_ids": ["evidence-1", "evidence-2", "evidence-3"]}
    )
    retargeted = anchor.model_copy(update={"target_ids": ["target-9"]})

    assert claim_cluster_id(small) == claim_cluster_id(large)
    assert claim_cluster_id(large) == claim_cluster_id(retargeted)
    assert claim_cluster_id(anchor) == claim_cluster_id(small)


def test_a_refinement_adding_third_evidence_keeps_id_and_old_citations() -> None:
    """A+B merge, then C arrives: one identity, three citations."""
    first = cluster_for_atom(
        _queue_proposition().model_copy(update={"evidence_ids": ["evidence-a"]}),
        claim_id="claim-a",
    )
    second = cluster_for_atom(
        _queue_proposition(
            text="10 GW sat in the 2024 interconnection queue."
        ).model_copy(update={"evidence_ids": ["evidence-b"]}),
        claim_id="claim-b",
    )
    ab = merge_claim_clusters(first, second)
    third = cluster_for_atom(
        _queue_proposition(
            text="The 2024 queue reported 10 GW."
        ).model_copy(update={"evidence_ids": ["evidence-c"]}),
        claim_id="claim-c",
    )

    refined = merge_claim_clusters(ab, third)

    assert refined.cluster_id == first.cluster_id
    assert refined.evidence_ids == ["evidence-a", "evidence-b", "evidence-c"]
    assert refined.member_claim_ids == ["claim-a", "claim-b", "claim-c"]
    assert "evidence-a" in refined.evidence_ids
    assert "claim-a" in refined.member_claim_ids


def test_two_clusters_for_one_anchor_share_one_identity() -> None:
    """The anchor decides, so two identical anchors are one cluster."""
    first = cluster_for_atom(_queue_proposition(), claim_id="claim-a")
    second = cluster_for_atom(_queue_proposition(), claim_id="claim-b")

    assert first.cluster_id == second.cluster_id
    assert merge_claim_clusters(first, second).cluster_id == first.cluster_id


def test_a_cluster_keeps_both_evidence_ids_of_a_union() -> None:
    """A+B findings become one proposition carrying both evidence ids."""
    first = cluster_for_atom(
        _queue_proposition().model_copy(update={"evidence_ids": ["evidence-a"]}),
        claim_id="claim-a",
    )
    second = cluster_for_atom(
        _queue_proposition(
            text="10 GW sat in the 2024 interconnection queue."
        ).model_copy(update={"evidence_ids": ["evidence-b"]}),
        claim_id="claim-b",
    )

    merged = merge_claim_clusters(first, second)

    assert merged.evidence_ids == ["evidence-a", "evidence-b"]
    assert merged.member_claim_ids == ["claim-a", "claim-b"]


# --------------------------------------------------------------------------
# Atom extraction
# --------------------------------------------------------------------------


def _claim(text: str, **overrides: object) -> Claim:
    fields: dict[str, object] = {
        "claim_id": "claim-1",
        "text": text,
        "source_urls": ["https://example.test/report"],
        "verdict": "verified",
        "confidence": 0.9,
        "evidence": [],
        "contradictions": [],
        "verification_evidence": [],
    }
    fields.update(overrides)
    return Claim(**fields)


def test_a_compound_observation_splits_into_one_atom_per_assertion() -> None:
    claim = _claim(
        "The 2024 queue was 10 GW; the 2025 queue reached 25 GW."
    )

    atoms = extract_atoms(claim)

    assert [atom.text for atom in atoms] == [
        "The 2024 queue was 10 GW",
        "the 2025 queue reached 25 GW",
    ]
    assert {atom.observation_period for atom in atoms} == {"2024", "2025"}
    assert all(atom.parent_claim_id == "claim-1" for atom in atoms)
    assert all(atom.member_claim_ids == ["claim-1"] for atom in atoms)


def test_a_causal_assertion_splits_from_the_observation_it_explains() -> None:
    claim = _claim(
        "Capacity fell 10 percent in 2024 because retirements rose to 5 GW."
    )

    atoms = extract_atoms(claim)

    assert len(atoms) == 2
    assert "because" not in atoms[0].text
    assert "5 GW" in atoms[1].text
    assert all(atom.parent_claim_id == "claim-1" for atom in atoms)


def test_extraction_preserves_every_qualifier_it_was_given() -> None:
    """An atom carries the number, unit, period, and negation of its clause."""
    claim = _claim("The 2024 queue did not hold 10 GW of capacity.")

    (atom,) = extract_atoms(claim)

    assert atom.value == "10"
    assert atom.unit == "GW"
    assert atom.observation_period == "2024"
    assert atom.negated is True


def test_extraction_carries_the_evidence_ids_the_claim_rests_on() -> None:
    passage = EvidencePassage(
        source_url="https://example.test/report",
        source_title="Queue report",
        locator="p. 4",
        excerpt="The 2024 queue was 10 GW.",
        stance="supports",
    )
    claim = _claim(
        "The 2024 queue was 10 GW.",
        verification_evidence=[passage],
        target_ids=["target-1"],
    )
    unit = EvidenceUnit(
        evidence_id="evidence-unit-1",
        read_id="read-1",
        source_url="https://example.test/report",
        source_title="Queue report",
        locator="p. 4",
        excerpt="The 2024 queue was 10 GW.",
        origin="fact_checker",
    )

    (atom,) = extract_atoms(claim, evidence=[unit])

    assert atom.evidence_ids == ["evidence-unit-1"]
    assert atom.target_ids == ["target-1"]


# --------------------------------------------------------------------------
# Scheduling: one outstanding obligation per target, before extra slots
# --------------------------------------------------------------------------


def _targeted_claim(claim_id: str, *target_ids: str) -> Claim:
    """One claim that discharges the named targets and nothing else."""
    return _claim(
        f"The queue reported in {claim_id} reached a new level.",
        claim_id=claim_id,
        target_ids=list(target_ids),
    )


SIX_TARGETS = [f"target-{number}" for number in range(1, 7)]


def _twelve_claims_over_six_topics() -> list[Claim]:
    """Two claims for each of six planned targets, in topic order."""
    claims: list[Claim] = []
    for target_id in SIX_TARGETS:
        for copy in (1, 2):
            claims.append(_targeted_claim(f"{target_id}-claim-{copy}", target_id))
    return claims


def test_a_batch_takes_one_obligation_per_target_in_plan_order() -> None:
    claims = [
        _targeted_claim("claim-1", "target-1"),
        _targeted_claim("claim-2", "target-2"),
        _targeted_claim("claim-3", "target-1"),
        _targeted_claim("claim-4", "target-3"),
    ]

    batch = select_claim_batch(
        claims, ["target-1", "target-2", "target-3"], limit=3
    )

    assert [claim.claim_id for claim in batch] == [
        "claim-1",
        "claim-2",
        "claim-4",
    ]


def test_a_five_item_batch_leaves_topic_six_pending() -> None:
    """A five-item batch must not decide that topic six is dropped."""
    claims = _twelve_claims_over_six_topics()

    batch = select_claim_batch(claims, SIX_TARGETS, limit=5)
    selected = {claim.claim_id for claim in batch}
    pending = [claim for claim in claims if claim.claim_id not in selected]

    assert len(batch) == 5
    assert "target-6" not in {
        target for claim in batch for target in claim.target_ids
    }
    # Every unselected claim is still outstanding: it is pending, not gone.
    assert len(pending) == len(claims) - 5
    assert {claim.claim_id for claim in pending} == {
        claim.claim_id for claim in claims
    } - selected


def test_six_topics_and_twelve_claims_reach_the_last_topic() -> None:
    """Six topics, twelve claims, a five-item batch, and a bounded pass.

    The pass bound is what decides whether topic six is reached, so this walks
    the real batches rather than asserting a property of one of them.
    """
    claims = _twelve_claims_over_six_topics()
    pending = list(range(len(claims)))
    scheduled: list[int] = []
    batches = 0

    while pending and batches < 6:
        picked = select_claim_batch_indices(
            [claims[index].target_ids for index in pending],
            SIX_TARGETS,
            limit=5,
        )
        chosen = [pending[position] for position in picked]
        scheduled.extend(chosen)
        pending = [index for index in pending if index not in set(chosen)]
        batches += 1

    assert pending == []
    assert sorted(scheduled) == list(range(len(claims)))
    assert batches < 6
    reached = {
        target
        for index in scheduled
        for target in claims[index].target_ids
    }
    assert reached == set(SIX_TARGETS)


def test_a_rare_but_critical_target_beats_extra_low_value_claims() -> None:
    """One obligation per target is filled before any extra slot is."""
    claims = [
        _targeted_claim("extra-1", "topic-a"),
        _targeted_claim("extra-2", "topic-a"),
        _targeted_claim("critical", "topic-z"),
        _targeted_claim("extra-3", "topic-a"),
    ]

    batch = select_claim_batch(claims, ["topic-a", "topic-z"], limit=2)

    assert [claim.claim_id for claim in batch] == ["extra-1", "critical"]
    assert "extra-2" not in {claim.claim_id for claim in batch}


def test_a_claim_naming_no_target_only_fills_an_extra_slot() -> None:
    claims = [
        _targeted_claim("unattached"),
        _targeted_claim("obligated", "target-1"),
    ]

    batch = select_claim_batch(claims, ["target-1"], limit=2)

    assert [claim.claim_id for claim in batch] == ["obligated", "unattached"]


def test_a_batch_bound_below_one_is_rejected() -> None:
    with pytest.raises(ValueError):
        select_claim_batch([], ["target-1"], limit=0)


def test_target_order_is_the_frozen_union_of_both_target_inventories() -> None:
    """Coverage may grow; the denominator never shrinks."""
    state = ResearchState(
        session_id="session-1",
        original_question="How fast is the queue growing?",
        initial_target_ids=["target-1", "target-2"],
        expanded_target_ids=["target-2", "target-3"],
    )

    assert target_order_for(state) == ["target-1", "target-2", "target-3"]


def test_target_order_falls_back_to_the_plans_own_obligations() -> None:
    """A plan written before the target inventory still has an order."""
    state = ResearchState(
        session_id="session-1",
        original_question="How fast is the queue growing?",
        sub_topics=[
            SubTopic(
                coverage_id="topic-01",
                title="Queue growth",
                rationale="It is the question.",
                search_queries=["queue growth"],
                success_criteria=["A rate is stated."],
                priority=1,
            ),
            SubTopic(
                coverage_id="topic-02",
                title="Withdrawal drivers",
                rationale="They explain the rate.",
                search_queries=["withdrawal drivers"],
                success_criteria=["A driver is named."],
                priority=2,
            ),
        ],
    )

    assert target_order_for(state) == ["topic-01", "topic-02"]

