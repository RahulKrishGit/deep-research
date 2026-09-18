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
    MAX_EQUIVALENCE_ATOMS,
    AtomicPairDraft,
    ClaimEquivalenceDraft,
    atomic_compatible,
    claim_cluster_id,
    cluster_for_atom,
    consolidate_claims,
    dimension_is_answered,
    equivalence_messages,
    equivalence_strength,
    extract_atoms,
    merge_claim_cluster_registry,
    merge_claim_clusters,
    metadata_dimension_asked_for,
    reverification_cache_key,
    select_claim_batch,
    select_claim_batch_indices,
    target_order_for,
)
from deep_research.observability import TokenUsage
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
)
from deep_research.utils.types import (
    AtomicProposition,
    Claim,
    ClaimCluster,
    EvidencePassage,
    EvidenceUnit,
    ResearchState,
    SubTopic,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter

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


def test_a_different_subject_is_not_compatible() -> None:
    """A different entity is a different assertion, however alike the rest is.

    The reviewer's probe: "Revenue rose 10 percent in 2024" and "Costs rose 10
    percent in 2024" agree on every dimension the contract compared once
    subject was treated as wording, and merged into one settled fact.
    """
    revenue = _claim("Revenue rose 10 percent in 2024.", claim_id="claim-rev")
    costs = _claim("Costs rose 10 percent in 2024.", claim_id="claim-cost")

    (revenue_atom,) = extract_atoms(revenue)
    (costs_atom,) = extract_atoms(costs)

    assert revenue_atom.subject == "Revenue"
    assert costs_atom.subject == "Costs"
    assert not atomic_compatible(revenue_atom, costs_atom)


def test_a_different_predicate_is_not_compatible() -> None:
    rose = _claim("The queue rose 10 percent in 2024.", claim_id="claim-rose")
    fell = _claim("The queue fell 10 percent in 2024.", claim_id="claim-fell")

    (rose_atom,) = extract_atoms(rose)
    (fell_atom,) = extract_atoms(fell)

    assert rose_atom.predicate == "increases_by"
    assert fell_atom.predicate == "decreases_by"
    assert not atomic_compatible(rose_atom, fell_atom)


def test_an_unknown_subject_or_predicate_is_never_a_conflict() -> None:
    """Two clauses that state no subject agree; one that states another does not.

    Empty-vs-empty is not a conflict — an unpopulated field must not block a
    legitimate merge — but empty-vs-stated is: an underivable qualifier is not
    an escape hatch for two clauses that name different things.
    """
    stated = _queue_proposition()
    unknown = _queue_proposition(subject="", predicate="")
    other = _queue_proposition(subject="wind queue")

    assert atomic_compatible(unknown, unknown.model_copy())
    assert not atomic_compatible(stated, unknown)
    assert not atomic_compatible(stated, other)


@pytest.mark.parametrize(
    ("level_side", "delta_side"),
    [
        ("The queue hit 10 GW in 2024.", "The queue rose 10 GW in 2024."),
        ("The queue hit 10 GW in 2024.", "The queue added 10 GW in 2024."),
        ("The queue hit 10 GW in 2024.", "The queue grew 10 GW in 2024."),
        ("Revenue doubled in 2024.", "Revenue rose in 2024."),
        ("Revenue fell 10 percent in 2024.", "Revenue halved 10 percent in 2024."),
    ],
)
def test_a_level_and_a_delta_are_never_one_relation(
    level_side: str, delta_side: str
) -> None:
    """Reaching a level is not changing by it, and doubling is not rising.

    The reviewer's probes: the relation-class table merged these into one
    settled verified cluster. Every pair here states the same number about the
    same subject and is still two different facts.
    """
    (left,) = extract_atoms(_claim(level_side, claim_id="claim-left"))
    (right,) = extract_atoms(_claim(delta_side, claim_id="claim-right"))

    assert left.predicate != right.predicate
    assert not atomic_compatible(left, right)


@pytest.mark.asyncio
async def test_a_level_and_a_delta_never_become_one_settled_claim() -> None:
    claims = [
        _claim("The queue hit 10 GW in 2024.", claim_id="claim-hit"),
        _claim("The queue rose 10 GW in 2024.", claim_id="claim-rose"),
    ]
    completer = ScriptedCompleter(outputs=[_pairs((1, 2))])

    consolidation = await consolidate_claims(completer, claims)

    assert len(consolidation.claims) == 2
    assert consolidation.diagnostics == [
        "equivalence_candidate_incompatible:1:2"
    ]


@pytest.mark.parametrize(
    ("trailing_side", "other_side"),
    [
        (
            "10 GW sat in the 2024 wind queue.",
            "10 GW sat in the 2024 solar queue.",
        ),
        (
            "10 GW sat in the transmission backlog.",
            "The 2024 interconnection queue held 10 GW.",
        ),
        (
            "10 GW was held by the wind fleet.",
            "10 GW was held by the solar fleet.",
        ),
    ],
)
def test_a_post_verbal_entity_is_read_and_never_an_escape_hatch(
    trailing_side: str, other_side: str
) -> None:
    """An empty subject must not let two different entities merge.

    The reviewer's probes: post-verbal and passive clauses came back with an
    empty subject and merged, settled verified, with no diagnostic. The entity
    is read from the trailing phrase where the clause puts it there, and a
    clause that names one never merges with a clause that names another.
    """
    (left,) = extract_atoms(_claim(trailing_side, claim_id="claim-left"))
    (right,) = extract_atoms(_claim(other_side, claim_id="claim-right"))

    assert left.subject
    assert atomic_compatible(left, right) is False


def test_two_clauses_that_name_no_entity_still_merge() -> None:
    """The positive control: unknown on both sides is not a conflict."""
    left = AtomicProposition(text="Interconnection delays are growing.")
    right = AtomicProposition(text="Interconnection delays are increasing.")

    assert atomic_compatible(left, right)


def test_an_identical_subject_and_predicate_still_merges() -> None:
    """The control: strictness about entities must not block one entity."""
    a = _queue_proposition(
        text="The interconnection queue held 10 GW in 2024.",
        subject="interconnection queue",
        predicate="held",
    )
    b = _queue_proposition(
        text="In 2024 the interconnection queue held 10 GW.",
        subject="interconnection queue",
        predicate="held",
    )

    assert atomic_compatible(a, b)


def _stored_cluster(claim_id: str, created_seq: int) -> ClaimCluster:
    """One already-minted cluster, anchored on a claim of the paraphrase set."""
    texts = {
        "claim-a": TEXT_A,
        "claim-b": TEXT_B,
        "claim-c": TEXT_C,
    }
    urls = {
        "claim-a": QUEUE_A,
        "claim-b": QUEUE_B,
        "claim-c": "https://c.test/queue",
    }
    claim = _claim(
        texts[claim_id],
        claim_id=claim_id,
        source_urls=[urls[claim_id]],
        verification_evidence=[_passage(urls[claim_id], texts[claim_id])],
    )
    (atom,) = extract_atoms(claim)
    return cluster_for_atom(atom, claim=claim, created_seq=created_seq)


@pytest.mark.asyncio
async def test_distinct_entities_never_become_one_settled_claim() -> None:
    """End to end: the probe's false merge can no longer happen."""
    claims = [
        _claim("Revenue rose 10 percent in 2024.", claim_id="claim-rev"),
        _claim("Costs rose 10 percent in 2024.", claim_id="claim-cost"),
    ]
    completer = ScriptedCompleter(outputs=[_pairs((1, 2))])

    consolidation = await consolidate_claims(completer, claims)

    assert len(consolidation.claims) == 2
    assert consolidation.diagnostics == [
        "equivalence_candidate_incompatible:1:2"
    ]


def test_a_stored_cluster_records_when_it_was_minted() -> None:
    """Age is persisted, not inferred from whatever order a caller passes."""
    older = _stored_cluster("claim-a", 1)
    newer = _stored_cluster("claim-b", 5)

    assert older.created_seq == 1
    assert newer.created_seq == 5
    assert merge_claim_clusters(newer, older).created_seq == 1


@pytest.mark.asyncio
async def test_a_real_consolidation_mints_a_monotonic_sequence() -> None:
    """Every member is stamped, and the survivor keeps the oldest real stamp.

    The reviewer's probe: member clusters were minted with the default zero and
    ``min()`` dragged the survivor to zero, at which point "oldest" fell back
    to caller order. A sequence of zero means "nobody stamped this", never
    "oldest".
    """
    claims, evidence = _mergeable_claims()
    third = _claim(
        TEXT_C,
        claim_id="claim-c",
        source_urls=["https://c.test/queue"],
    )

    consolidation = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2), (2, 3))]),
        [*claims, third],
        evidence=evidence,
    )

    (cluster,) = consolidation.clusters
    assert cluster.created_seq > 0
    assert cluster.member_claim_ids == ["claim-a", "claim-b", "claim-c"]

    # A second consolidation continues the sequence rather than restarting it.
    later = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]),
        [third],
        existing=[cluster],
        evidence=evidence,
    )
    assert later.clusters[0].created_seq == cluster.created_seq


@pytest.mark.asyncio
async def test_a_stored_cluster_beats_an_unstamped_one_whatever_the_order() -> None:
    """An unstamped sequence is the lowest-priority fallback, never a winner."""
    stamped = _stored_cluster("claim-a", 7)
    unstamped = _stored_cluster("claim-b", 0)

    for order in ([stamped, unstamped], [unstamped, stamped]):
        consolidation = await consolidate_claims(
            ScriptedCompleter(outputs=[_pairs((1, 2))]),
            [],
            existing=order,
        )
        (cluster,) = consolidation.clusters
        assert cluster.cluster_id == stamped.cluster_id
        assert cluster.created_seq == 7


@pytest.mark.asyncio
async def test_the_oldest_stored_cluster_id_survives_a_merge() -> None:
    """The brief's invariant: the OLDEST stable id is the one that survives."""
    older = _stored_cluster("claim-a", 1)
    newer = _stored_cluster("claim-b", 5)

    consolidation = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]),
        [],
        existing=[older, newer],
    )

    assert len(consolidation.clusters) == 1
    (cluster,) = consolidation.clusters
    assert cluster.cluster_id == older.cluster_id
    assert cluster.cluster_aliases == [newer.cluster_id]
    assert consolidation.aliases == {newer.cluster_id: older.cluster_id}


@pytest.mark.asyncio
async def test_the_oldest_survives_regardless_of_arrival_order(
) -> None:
    older = _stored_cluster("claim-a", 1)
    newer = _stored_cluster("claim-b", 5)

    consolidation = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((2, 1))]),
        [],
        existing=[newer, older],
    )

    (cluster,) = consolidation.clusters
    assert cluster.cluster_id == older.cluster_id


@pytest.mark.asyncio
async def test_three_stored_clusters_keep_the_oldest_and_alias_the_rest() -> None:
    oldest = _stored_cluster("claim-a", 1)
    middle = _stored_cluster("claim-b", 4)
    newest = _stored_cluster("claim-c", 9)

    consolidation = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((3, 1), (1, 2))]),
        [],
        existing=[newest, middle, oldest],
    )

    (cluster,) = consolidation.clusters
    assert cluster.cluster_id == oldest.cluster_id
    assert cluster.cluster_aliases == sorted(
        {middle.cluster_id, newest.cluster_id}
    )
    assert consolidation.aliases == {
        middle.cluster_id: oldest.cluster_id,
        newest.cluster_id: oldest.cluster_id,
    }


# --------------------------------------------------------------------------
# The registry: clusters persist, so a refinement is a different invocation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_cluster_registry_survives_a_state_round_trip() -> None:
    """A refinement is another invocation, so the state has to carry them."""
    claims, evidence = _mergeable_claims()
    first = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]), claims, evidence=evidence
    )
    (stored,) = first.clusters

    state = merge_research_state(
        ResearchState(session_id="session-1", original_question="How fast?"),
        {"claim_clusters": {stored.cluster_id: stored}},
    )
    reloaded = ResearchState.model_validate_json(state.model_dump_json())

    assert set(reloaded.claim_clusters) == {stored.cluster_id}
    carried = reloaded.claim_clusters[stored.cluster_id]
    assert carried.source_urls == sorted({QUEUE_A, QUEUE_B})
    assert carried.member_claim_ids == ["claim-a", "claim-b"]

    third = _claim(
        "The 2024 interconnection queue reported 10 GW of capacity.",
        claim_id="claim-c",
        source_urls=["https://c.test/queue"],
    )
    refined = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]),
        [third],
        existing=list(reloaded.claim_clusters.values()),
        evidence=evidence,
    )

    assert refined.clusters[0].cluster_id == stored.cluster_id
    assert refined.claims[0].source_urls == sorted(
        {QUEUE_A, QUEUE_B, "https://c.test/queue"}
    )
    assert refined.claims[0].cluster_id == stored.cluster_id


def test_a_snapshot_written_before_the_registry_still_loads() -> None:
    """No cluster is invented for a snapshot that recorded none."""
    payload = {
        "session_id": "session-1",
        "original_question": "How fast?",
    }

    state = ResearchState.model_validate(payload)

    assert state.claim_clusters == {}


def test_a_cluster_registry_merge_unions_one_id_and_refuses_a_reanchor() -> None:
    cluster = _stored_cluster("claim-a", 1)
    grown = cluster.model_copy(
        update={
            "member_claim_ids": ["claim-a", "claim-b"],
            "source_urls": [*cluster.source_urls, "https://b.test/queue"],
        }
    )

    merged = merge_claim_cluster_registry(
        {cluster.cluster_id: cluster}, {cluster.cluster_id: grown}
    )

    assert merged[cluster.cluster_id].member_claim_ids == [
        "claim-a",
        "claim-b",
    ]

    forged = cluster.model_copy(
        update={
            "proposition": _queue_proposition(
                text="A wholly different assertion about something else."
            )
        }
    )
    with pytest.raises(ValueError):
        merge_claim_cluster_registry(
            {cluster.cluster_id: cluster}, {cluster.cluster_id: forged}
        )


@pytest.mark.asyncio
async def test_each_verdict_records_the_evidence_behind_it() -> None:
    """Which sources stood behind the contradiction, and which behind the rest."""
    claims = [
        _claim(TEXT_A, claim_id="claim-a", source_urls=[QUEUE_A]),
        _claim(
            TEXT_B,
            claim_id="claim-b",
            source_urls=[QUEUE_B],
            verdict="contradicted",
        ),
    ]
    completer = ScriptedCompleter(outputs=[_pairs((1, 2))])

    consolidation = await consolidate_claims(completer, claims)

    (cluster,) = consolidation.clusters
    assert cluster.verdict_evidence == {
        "contradicted": [QUEUE_B],
        "verified": [QUEUE_A],
    }
    assert cluster.diagnostics == [
        f"cluster_verdict_disagreement:contradicted:{QUEUE_B}",
        f"cluster_verdict_disagreement:verified:{QUEUE_A}",
    ]


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
        picked, _ = select_claim_batch_indices(
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


# --------------------------------------------------------------------------
# Consolidation: the provider proposes, local code decides
# --------------------------------------------------------------------------


def _unit(evidence_id: str, url: str, excerpt: str) -> EvidenceUnit:
    return EvidenceUnit(
        evidence_id=evidence_id,
        read_id=f"read-{evidence_id}",
        source_url=url,
        source_title="Queue report",
        locator="p. 4",
        excerpt=excerpt,
        origin="fact_checker",
    )


def _passage(url: str, excerpt: str) -> EvidencePassage:
    return EvidencePassage(
        source_url=url,
        source_title="Queue report",
        locator="p. 4",
        excerpt=excerpt,
        stance="supports",
    )


QUEUE_A = "https://a.test/queue"
QUEUE_B = "https://b.test/queue"
TEXT_A = "The 2024 interconnection queue held 10 GW of capacity."
TEXT_B = "10 GW sat in the 2024 interconnection queue."
TEXT_C = "The 2024 interconnection queue reported 10 GW of capacity."


def _mergeable_claims() -> tuple[list[Claim], list[EvidenceUnit]]:
    """Two claims from two sources that state one fact two ways."""
    evidence = [
        _unit("evidence-a", QUEUE_A, "The 2024 queue held 10 GW."),
        _unit("evidence-b", QUEUE_B, "10 GW sat in the 2024 queue."),
    ]
    claims = [
        _claim(
            TEXT_A,
            claim_id="claim-a",
            source_urls=[QUEUE_A],
            verification_evidence=[_passage(QUEUE_A, "The 2024 queue held 10 GW.")],
        ),
        _claim(
            TEXT_B,
            claim_id="claim-b",
            source_urls=[QUEUE_B],
            verification_evidence=[_passage(QUEUE_B, "10 GW sat in the 2024 queue.")],
        ),
    ]
    return claims, evidence


def _pairs(*pairs: tuple[int, int]) -> ClaimEquivalenceDraft:
    return ClaimEquivalenceDraft(
        pairs=[
            AtomicPairDraft(left=left, right=right) for left, right in pairs
        ]
    )


def _output_limit_error() -> ProviderOutputLimitError:
    return ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=5, output_tokens=4096),
            request_attempt=1,
        )
    )


@pytest.mark.asyncio
async def test_a_and_b_findings_become_one_proposition_with_both_evidence_ids() -> None:
    claims, evidence = _mergeable_claims()
    completer = ScriptedCompleter(outputs=[_pairs((1, 2))])

    consolidation = await consolidate_claims(
        completer, claims, evidence=evidence
    )

    assert len(consolidation.claims) == 1
    (cluster,) = consolidation.clusters
    assert cluster.evidence_ids == ["evidence-a", "evidence-b"]
    assert cluster.member_claim_ids == ["claim-a", "claim-b"]
    snapshot = consolidation.claims[0]
    assert snapshot.cluster_id == cluster.cluster_id
    assert snapshot.source_urls == sorted({QUEUE_A, QUEUE_B})


@pytest.mark.asyncio
async def test_two_claims_from_one_source_stay_distinct() -> None:
    """Sharing a URL is not identity: the two assertions differ by year."""
    claims = [
        _claim(
            "The 2024 queue held 10 GW.",
            claim_id="claim-2024",
            source_urls=[QUEUE_A],
        ),
        _claim(
            "The 2025 queue held 10 GW.",
            claim_id="claim-2025",
            source_urls=[QUEUE_A],
        ),
    ]
    completer = ScriptedCompleter(outputs=[_pairs()])

    consolidation = await consolidate_claims(completer, claims)

    assert len(consolidation.claims) == 2
    assert len({claim.cluster_id for claim in consolidation.claims}) == 2
    assert {claim.text for claim in consolidation.claims} == {
        "The 2024 queue held 10 GW",
        "The 2025 queue held 10 GW",
    }


@pytest.mark.asyncio
async def test_an_incompatible_candidate_pair_is_a_diagnostic_not_a_failure() -> None:
    """A proposal local code refuses is recorded, and both claims survive."""
    claims = [
        _claim("The 2024 queue held 10 GW.", claim_id="claim-2024"),
        _claim("The 2025 queue held 10 GW.", claim_id="claim-2025"),
    ]
    completer = ScriptedCompleter(outputs=[_pairs((1, 2))])

    consolidation = await consolidate_claims(completer, claims)

    assert len(consolidation.claims) == 2
    assert consolidation.diagnostics == [
        "equivalence_candidate_incompatible:1:2"
    ]
    assert consolidation.provider_failed is False


@pytest.mark.asyncio
async def test_a_provider_failure_never_merges_on_error() -> None:
    claims, evidence = _mergeable_claims()
    completer = ScriptedCompleter(outputs=[_output_limit_error()])

    consolidation = await consolidate_claims(
        completer, claims, evidence=evidence
    )

    assert consolidation.provider_failed is True
    assert consolidation.diagnostics == ["equivalence_provider_failed"]
    assert len(consolidation.claims) == 2


@pytest.mark.asyncio
async def test_candidate_pairs_come_from_one_bounded_provider_call() -> None:
    claims, evidence = _mergeable_claims()
    completer = ScriptedCompleter(outputs=[_pairs((1, 2))])

    await consolidate_claims(completer, claims, evidence=evidence)

    assert [name for name, _, _ in completer.calls] == [
        "ClaimEquivalenceDraft"
    ]


def test_the_equivalence_prompt_lists_a_bounded_number_of_atoms() -> None:
    atoms = [
        _queue_proposition(
            text=f"The 20{number:02d} queue held 10 GW.",
            observation_period=f"20{number:02d}",
        )
        for number in range(1, MAX_EQUIVALENCE_ATOMS + 21)
    ]

    _, user = equivalence_messages(atoms)
    listed = [
        line
        for line in user.content.splitlines()
        if line[:1].isdigit() and line.split(".")[0].isdigit()
    ]

    assert len(listed) == MAX_EQUIVALENCE_ATOMS


def test_the_prompt_numbers_the_atoms_from_one() -> None:
    """The numbers the provider is shown are the numbers it returns.

    A provider answers with the numbers it was shown, so the prompt and the
    validator have to agree on where the list starts. It starts at one.
    """
    atoms = [_queue_proposition(), _queue_proposition(text=TEXT_B)]

    _, user = equivalence_messages(atoms)
    listed = [
        line
        for line in user.content.splitlines()
        if line[:2] in ("1.", "2.")
    ]

    assert listed[0].startswith("1. ")
    assert listed[1].startswith("2. ")


@pytest.mark.asyncio
async def test_a_proposal_using_the_prompt_numbers_merges() -> None:
    """End to end: the numbers the prompt displayed are the ones accepted."""
    claims, evidence = _mergeable_claims()
    completer = ScriptedCompleter(outputs=[_pairs((1, 2))])

    consolidation = await consolidate_claims(
        completer, claims, evidence=evidence
    )

    assert not [
        diagnostic
        for diagnostic in consolidation.diagnostics
        if "out_of_range" in diagnostic
    ]
    assert len(consolidation.claims) == 1


@pytest.mark.asyncio
async def test_a_textual_duplicate_is_not_excluded_by_exact_number_matching() -> None:
    """The two claims write one number two ways, and they are still one fact."""
    claims = [
        _claim(
            "The 2024 queue withheld 1,200 MW of capacity.",
            claim_id="claim-comma",
        ),
        _claim(
            "The 2024 queue withheld 1200 MW of capacity.",
            claim_id="claim-plain",
        ),
    ]
    completer = ScriptedCompleter(outputs=[_pairs((1, 2))])

    consolidation = await consolidate_claims(completer, claims)

    assert len(consolidation.claims) == 1
    assert consolidation.clusters[0].member_claim_ids == [
        "claim-comma",
        "claim-plain",
    ]


@pytest.mark.asyncio
async def test_a_near_duplicate_publishes_one_representative_with_union() -> None:
    """Two claims too thin to prove identical still publish as ONE fact."""
    claims = [
        _claim(
            "Interconnection delays are growing.",
            claim_id="claim-plain",
            source_urls=[QUEUE_A],
        ),
        _claim(
            "Interconnection delays are increasing.",
            claim_id="claim-reworded",
            source_urls=[QUEUE_B],
        ),
    ]
    completer = ScriptedCompleter(outputs=[_pairs((1, 2))])

    consolidation = await consolidate_claims(completer, claims)

    assert len(consolidation.claims) == 1
    (cluster,) = consolidation.clusters
    assert cluster.status == "duplicate_representative"
    assert cluster.diagnostics == ["equivalence_candidate_uncertain:1:2"]
    assert consolidation.claims[0].source_urls == sorted({QUEUE_A, QUEUE_B})


def test_equivalence_strength_separates_proof_from_uncertainty() -> None:
    stated = _queue_proposition()
    thin = AtomicProposition(text="Interconnection delays are growing.")
    other_thin = AtomicProposition(text="Interconnection delays are increasing.")
    different_year = _queue_proposition(observation_period="2025")

    assert equivalence_strength(stated, stated) == "identical"
    assert equivalence_strength(thin, other_thin) == "uncertain"
    assert equivalence_strength(stated, different_year) == "incompatible"


@pytest.mark.asyncio
async def test_consolidation_is_deterministic_for_the_same_atoms() -> None:
    claims, evidence = _mergeable_claims()

    first = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]), claims, evidence=evidence
    )
    second = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]), claims, evidence=evidence
    )

    assert [claim.cluster_id for claim in first.claims] == [
        claim.cluster_id for claim in second.claims
    ]
    assert first.aliases == second.aliases


@pytest.mark.asyncio
async def test_a_refinement_reuses_the_stored_cluster_identity() -> None:
    """A cluster already on the state keeps its id when a third claim joins."""
    claims, evidence = _mergeable_claims()
    first = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]), claims, evidence=evidence
    )
    stored = first.clusters[0]
    third = _claim(
        "The 2024 interconnection queue reported 10 GW of capacity.",
        claim_id="claim-c",
        source_urls=["https://c.test/queue"],
    )

    refined = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2), (2, 3), (3, 4))]),
        [*claims, third],
        existing=[stored],
        evidence=evidence,
    )

    assert refined.clusters[0].cluster_id == stored.cluster_id
    assert refined.clusters[0].member_claim_ids == [
        "claim-a",
        "claim-b",
        "claim-c",
    ]


@pytest.mark.asyncio
async def test_a_later_pass_adding_only_c_keeps_the_stored_citations() -> None:
    """A refinement resubmits only the NEW claim.

    The brief's requirement is that a later refinement adding C preserves the
    cluster id and the old citations. A pass that restates A and B as well
    would prove nothing: the stored cluster's own provenance has to be
    reconstructed, and the stored proposition has to be offered to the
    provider as a candidate, for the merge to be possible at all.
    """
    claims, evidence = _mergeable_claims()
    first = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]), claims, evidence=evidence
    )
    stored = first.clusters[0]
    assert stored.source_urls == sorted({QUEUE_A, QUEUE_B})
    third = _claim(
        "The 2024 interconnection queue reported 10 GW of capacity.",
        claim_id="claim-c",
        source_urls=["https://c.test/queue"],
        verification_evidence=[_passage("https://c.test/queue", "10 GW in 2024.")],
    )

    refined = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]),
        [third],
        existing=[stored],
        evidence=evidence,
    )

    assert len(refined.claims) == 1
    (cluster,) = refined.clusters
    assert cluster.cluster_id == stored.cluster_id
    assert cluster.member_claim_ids == ["claim-a", "claim-b", "claim-c"]
    assert refined.claims[0].source_urls == sorted(
        {QUEUE_A, QUEUE_B, "https://c.test/queue"}
    )
    assert refined.claims[0].cluster_id == stored.cluster_id


@pytest.mark.asyncio
async def test_a_cluster_whose_members_disagree_never_settles_verified() -> None:
    """One contradiction is enough: a disagreement cannot read as settled."""
    claims = [
        _claim(TEXT_A, claim_id="claim-a", source_urls=[QUEUE_A]),
        _claim(
            TEXT_B,
            claim_id="claim-b",
            source_urls=[QUEUE_B],
            verdict="contradicted",
            evidence=[],
            contradictions=["An independent meter recorded no such capacity."],
        ),
    ]
    completer = ScriptedCompleter(outputs=[_pairs((1, 2))])

    consolidation = await consolidate_claims(completer, claims)

    assert len(consolidation.claims) == 1
    (cluster,) = consolidation.clusters
    assert consolidation.claims[0].verdict != "verified"
    assert cluster.status == "contested"
    assert cluster.verdicts == ["contradicted", "verified"]
    # Each verdict is recorded with the evidence that actually supported it, so
    # a reader can see which sources stood behind the contradiction.
    assert cluster.verdict_evidence == {
        "contradicted": [QUEUE_B],
        "verified": [QUEUE_A],
    }
    assert cluster.diagnostics == [
        f"cluster_verdict_disagreement:contradicted:{QUEUE_B}",
        f"cluster_verdict_disagreement:verified:{QUEUE_A}",
    ]


@pytest.mark.asyncio
async def test_a_cluster_whose_members_agree_keeps_its_verdict() -> None:
    """The control: agreement is not a disagreement."""
    claims, evidence = _mergeable_claims()
    completer = ScriptedCompleter(outputs=[_pairs((1, 2))])

    consolidation = await consolidate_claims(
        completer, claims, evidence=evidence
    )

    (cluster,) = consolidation.clusters
    assert consolidation.claims[0].verdict == "verified"
    assert cluster.status == "canonical"
    assert cluster.verdicts == ["verified"]


@pytest.mark.asyncio
async def test_a_compound_claim_yields_atom_specific_rows() -> None:
    """Two assertions are two rows, not one row printed twice.

    The reviewer's probe: a two-atom claim produced two rows with the SAME
    compound text and claim id, which is exactly the ledger defect atomizing
    is supposed to remove. Each row now carries its own text and its own
    stable atomic id, while both keep the parent claim addressable.
    """
    compound = _claim(
        "The 2024 queue held 10 GW; the 2025 queue reached 25 GW.",
        claim_id="claim-compound",
    )
    completer = ScriptedCompleter(outputs=[_pairs()])

    consolidation = await consolidate_claims(completer, [compound])

    assert len(consolidation.claims) == 2
    texts = [claim.text for claim in consolidation.claims]
    assert texts == ["The 2024 queue held 10 GW", "the 2025 queue reached 25 GW"]
    assert len({claim.claim_id for claim in consolidation.claims}) == 2
    assert all(
        "claim-compound" in cluster.member_claim_ids
        for cluster in consolidation.clusters
    )


def test_every_atom_carries_its_own_stable_id() -> None:
    claim = _claim(
        "The 2024 queue held 10 GW; the 2025 queue reached 25 GW.",
        claim_id="claim-compound",
    )

    first = extract_atoms(claim)
    second = extract_atoms(claim)

    assert [atom.atom_id for atom in first] == [
        atom.atom_id for atom in second
    ]
    assert len({atom.atom_id for atom in first}) == 2
    assert all(atom.parent_claim_id == "claim-compound" for atom in first)


# --------------------------------------------------------------------------
# Extraction populates the qualifiers the atom contract declares
# --------------------------------------------------------------------------


def test_extraction_populates_the_subject_and_predicate() -> None:
    """The qualifier fields are filled, not left empty for a consumer to guess."""
    claim = _claim(
        "The 2024 interconnection queue held 10 GW of capacity.",
        claim_id="claim-subject",
    )

    (atom,) = extract_atoms(claim)

    assert atom.subject == "interconnection queue"
    assert atom.predicate == "states_level"


def test_extraction_reads_a_sentence_initial_attribution() -> None:
    """"According to …" opens a sentence and still attributes the claim."""
    claim = _claim(
        "According to Example Lab, the 2024 queue held 10 GW.",
        claim_id="claim-attribution",
    )

    (atom,) = extract_atoms(claim)

    assert atom.attribution == "Example Lab"


def test_extraction_reads_the_population_a_count_is_taken_from() -> None:
    """A share states its base; a plain count states the population counted."""
    counted = _claim(
        "The 2024 survey counted 4,000 of the interconnection requests.",
        claim_id="claim-counted",
    )
    shared = _claim(
        "The 2024 survey measured 40 percent of installed capacity.",
        claim_id="claim-shared",
    )

    (counted_atom,) = extract_atoms(counted)
    (shared_atom,) = extract_atoms(shared)

    assert counted_atom.population == "the interconnection requests"
    assert counted_atom.denominator == ""
    assert shared_atom.denominator == "installed capacity"
    assert shared_atom.population == ""


# --------------------------------------------------------------------------
# Scheduling: no target can be starved by a finite pass
# --------------------------------------------------------------------------


def _many_claims_over_five_targets() -> list[Claim]:
    """Twenty claims for each of targets 1-5, then the one target-6 claim."""
    claims: list[Claim] = []
    for target_id in ("target-1", "target-2", "target-3", "target-4", "target-5"):
        for copy in range(20):
            claims.append(_targeted_claim(f"{target_id}-claim-{copy}", target_id))
    claims.append(_targeted_claim("target-6-claim-0", "target-6"))
    return claims


def test_a_round_robin_cursor_reaches_a_starved_critical_target() -> None:
    """Six five-item batches must reach the last target.

    Restarting at the first target every batch spends all thirty slots on
    targets 1-5 and never reaches target 6. The cursor is what makes the
    finite pass reach every target that has a claim.
    """
    claims = _many_claims_over_five_targets()
    pending = list(range(len(claims)))
    scheduled: list[int] = []
    cursor = 0

    for _ in range(6):
        if not pending:
            break
        picked, cursor = select_claim_batch_indices(
            [claims[index].target_ids for index in pending],
            SIX_TARGETS,
            limit=5,
            cursor=cursor,
        )
        chosen = [pending[position] for position in picked]
        scheduled.extend(chosen)
        pending = [index for index in pending if index not in set(chosen)]

    reached = {
        target for index in scheduled for target in claims[index].target_ids
    }
    assert "target-6" in reached


def test_a_critical_target_is_served_before_extra_low_value_claims() -> None:
    """Priority targets are served first, whatever the cursor says."""
    claims = [
        _targeted_claim("extra-1", "topic-a"),
        _targeted_claim("extra-2", "topic-a"),
        _targeted_claim("extra-3", "topic-a"),
        _targeted_claim("critical", "topic-z"),
        _targeted_claim("plain", "topic-b"),
    ]

    picked, _ = select_claim_batch_indices(
        [claim.target_ids for claim in claims],
        ["topic-a", "topic-b", "topic-z"],
        limit=2,
        priority=["topic-z"],
    )

    assert [claims[index].claim_id for index in picked][0] == "critical"
    assert "extra-2" not in {claims[index].claim_id for index in picked}


def test_the_cursor_advances_so_the_next_batch_starts_after_it() -> None:
    claims = [
        _targeted_claim("one", "topic-1"),
        _targeted_claim("two", "topic-2"),
        _targeted_claim("three", "topic-3"),
    ]

    picked, cursor = select_claim_batch_indices(
        [claim.target_ids for claim in claims],
        ["topic-1", "topic-2", "topic-3"],
        limit=2,
    )
    outstanding = [claim for claim in claims if claim.claim_id == "three"]
    following, _ = select_claim_batch_indices(
        [claim.target_ids for claim in outstanding],
        ["topic-1", "topic-2", "topic-3"],
        limit=2,
        cursor=cursor,
    )

    assert [claims[index].claim_id for index in picked] == ["one", "two"]
    assert cursor == 2
    assert [outstanding[index].claim_id for index in following] == ["three"]



# --------------------------------------------------------------------------
# Reverification: what invalidates a cached verdict, and what does not
# --------------------------------------------------------------------------


def test_the_reverification_key_is_stable_for_one_proposition_and_evidence() -> None:
    key = reverification_cache_key(
        proposition=TEXT_A,
        evidence_content=["The 2024 queue held 10 GW."],
        assessment_revision="rev-1",
        temporal_scope="2024",
        prompt_version="1",
    )

    assert key == reverification_cache_key(
        proposition=TEXT_A,
        evidence_content=["The 2024 queue held 10 GW."],
        assessment_revision="rev-1",
        temporal_scope="2024",
        prompt_version="1",
    )


def test_new_evidence_invalidates_the_reverification_key() -> None:
    new = reverification_cache_key(
        proposition=TEXT_A,
        evidence_content=["The 2024 queue held 10 GW.", "A second read agreed."],
        assessment_revision="rev-1",
        temporal_scope="2024",
        prompt_version="1",
    )

    assert new != reverification_cache_key(
        proposition=TEXT_A,
        evidence_content=["The 2024 queue held 10 GW."],
        assessment_revision="rev-1",
        temporal_scope="2024",
        prompt_version="1",
    )


def test_an_identity_correction_invalidates_the_reverification_key() -> None:
    """Task 4's ``assessment_revision`` is the evidence of a changed source."""
    corrected = reverification_cache_key(
        proposition=TEXT_A,
        evidence_content=["The 2024 queue held 10 GW."],
        assessment_revision="rev-2",
        temporal_scope="2024",
        prompt_version="1",
    )

    assert corrected != reverification_cache_key(
        proposition=TEXT_A,
        evidence_content=["The 2024 queue held 10 GW."],
        assessment_revision="rev-1",
        temporal_scope="2024",
        prompt_version="1",
    )


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("proposition", TEXT_B),
        ("temporal_scope", "2025"),
        ("prompt_version", "2"),
    ],
)
def test_every_ruled_component_changes_the_reverification_key(
    field: str, changed: str
) -> None:
    fields = {
        "proposition": TEXT_A,
        "evidence_content": ["The 2024 queue held 10 GW."],
        "assessment_revision": "rev-1",
        "temporal_scope": "2024",
        "prompt_version": "1",
    }
    baseline = reverification_cache_key(**fields)

    assert reverification_cache_key(**{**fields, field: changed}) != baseline


def test_a_wording_only_critique_does_not_invalidate_the_reverification_key() -> None:
    """A reworded request is the same request; only evidence changes it."""
    fields = {
        "proposition": TEXT_A,
        "evidence_content": ["The 2024 queue held 10 GW."],
        "assessment_revision": "rev-1",
        "temporal_scope": "2024",
        "prompt_version": "1",
    }
    baseline = reverification_cache_key(**fields)

    assert (
        reverification_cache_key(
            **fields, critique="Please double-check this claim once more."
        )
        == baseline
    )
    assert (
        reverification_cache_key(
            **fields, critique="Re-verify the queue claim."
        )
        == baseline
    )


# --------------------------------------------------------------------------
# Metadata relevance depends on the question
# --------------------------------------------------------------------------


def test_a_publication_date_cannot_answer_a_deployment_mechanism() -> None:
    """Metadata is context: it never stands in for a substantive dimension."""
    assert not dimension_is_answered(
        question=(
            "What deployment mechanisms connect storage to the grid, and "
            "which are approved?"
        ),
        dimension="deployment_mechanism",
        stated_dimensions={"publication_date", "retrieval_date"},
    )


def test_a_publication_date_answers_a_question_asking_when_it_was_published() -> None:
    assert dimension_is_answered(
        question="When was the grid storage report published?",
        dimension="publication_date",
        stated_dimensions={"publication_date"},
    )


def test_metadata_is_context_unless_the_question_asks_for_metadata() -> None:
    assert not dimension_is_answered(
        question="What are the interconnection queue costs?",
        dimension="publication_date",
        stated_dimensions={"publication_date"},
    )
    # The same evidence answers the same dimension once the question asks it.
    assert dimension_is_answered(
        question="What data period does the queue survey cover?",
        dimension="data_period",
        stated_dimensions={"data_period"},
    )


def test_a_substantive_dimension_is_answered_by_its_own_evidence() -> None:
    assert dimension_is_answered(
        question="Which deployment mechanisms are approved?",
        dimension="deployment_mechanism",
        stated_dimensions={"deployment_mechanism"},
    )
    assert not dimension_is_answered(
        question="Which deployment mechanisms are approved?",
        dimension="deployment_mechanism",
        stated_dimensions={"cost"},
    )


@pytest.mark.parametrize(
    ("question", "dimension", "expected"),
    [
        ("When was the report released?", "publication_date", True),
        # Naming a publisher is not asking when it published.
        ("Who published the report?", "publication_date", False),
        ("Who issued the permit, and under what authority?", "effective_date", False),
        ("How much capacity was withheld?", "publication_date", False),
        ("When did the rule take effect?", "effective_date", True),
        ("What is the forecast horizon?", "forecast_horizon", True),
    ],
)
def test_the_metadata_markers_need_temporal_intent(
    question: str, dimension: str, expected: bool
) -> None:
    assert metadata_dimension_asked_for(question, dimension) is expected


def test_a_publisher_question_does_not_unlock_a_publication_date() -> None:
    """The reviewer's probe: "Who published the report?" is not a date request."""
    assert not dimension_is_answered(
        question="Who published the report?",
        dimension="publication_date",
        stated_dimensions={"publication_date"},
    )
    # The same evidence answers it once the question asks for the date.
    assert dimension_is_answered(
        question="When was the report published?",
        dimension="publication_date",
        stated_dimensions={"publication_date"},
    )
