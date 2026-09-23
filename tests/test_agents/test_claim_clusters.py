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

from itertools import permutations

import pytest

from deep_research.agents.claim_clusters import (
    MAX_EQUIVALENCE_ATOMS,
    OVER_CAP_SUBJECT_DIGEST_CHARS,
    OVER_CAP_SUBJECT_PREFIX,
    AtomicPairDraft,
    ClaimEquivalenceDraft,
    _sentences,
    atom_answers_dimensions,
    atom_answers_target,
    atomic_compatible,
    checkable_dimensions,
    claim_cluster_id,
    cluster_for_atom,
    consolidate_claims,
    dimension_is_answered,
    equivalence_messages,
    equivalence_strength,
    extract_atoms,
    extract_text_atoms,
    merge_claim_cluster_registry,
    merge_claim_clusters,
    metadata_dimension_asked_for,
    oldest_first,
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
    EvidenceTarget,
    EvidenceUnit,
    ResearchState,
    SubTopic,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter

# --------------------------------------------------------------------------
# Qualifiers that change what a clause asserts
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            "Capacity rose to 10 GW in 2024.",
            "Capacity rose by 10 GW in 2024.",
        ),
        (
            "Capacity rose to 10 percent in 2024.",
            "Capacity rose by 10 percent in 2024.",
        ),
        (
            "Capacity was more than 10 GW in 2024.",
            "Capacity was less than 10 GW in 2024.",
        ),
        (
            "Capacity was over 10 GW in 2024.",
            "Capacity was under 10 GW in 2024.",
        ),
        (
            "Capacity was up to 10 GW in 2024.",
            "Capacity was at least 10 GW in 2024.",
        ),
        (
            "Capacity was nearly 10 GW in 2024.",
            "Capacity was 10 GW in 2024.",
        ),
    ],
)
def test_a_different_qualifier_is_not_one_assertion(left: str, right: str) -> None:
    """A bound, an approximation, and a bare value are three assertions.

    "rose to 10 GW" states a level the subject reached and "rose by 10 GW" a
    change it underwent; "more than" and "less than" are bounds in opposite
    directions; "up to" and "at least" are not the same bound; and "nearly
    10 GW" is not 10 GW. Comparing only the number merged all of them, so one
    proposition's pair and badge were presented for another.
    """
    a = extract_text_atoms(left, claim_id="a")[0]
    b = extract_text_atoms(right, claim_id="b")[0]

    assert not atomic_compatible(a, b)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            "Wind capacity rose to over 10 GW in 2025.",
            "Wind capacity rose by over 10 GW in 2025.",
        ),
        (
            "The share fell to under 10 percent in 2025.",
            "The share fell by under 10 percent in 2025.",
        ),
    ],
)
def test_a_comparator_between_the_preposition_and_the_value_keeps_the_kind(
    left: str, right: str
) -> None:
    """The level/delta marker is read past the bound, not just before the value.

    "rose to over 10 GW" reached a level and "rose by over 10 GW" gained that
    much: with a comparator sitting between the preposition and the number, the
    suffix test missed and the relation class was read as a delta, so the two
    merged.
    """
    a = extract_text_atoms(left, claim_id="a")[0]
    b = extract_text_atoms(right, claim_id="b")[0]

    assert a.comparator == b.comparator
    assert a.change_kind != b.change_kind
    assert not atomic_compatible(a, b)


@pytest.mark.parametrize(
    ("text", "subject"),
    [
        ("Output was at least 10 GW in 2024.", "Output"),
        ("Output was at most 10 GW in 2024.", "Output"),
        ("Output was no less than 10 GW in 2024.", "Output"),
        ("Output was no more than 10 GW in 2024.", "Output"),
        ("Output was up to 10 GW in 2024.", "Output"),
    ],
)
def test_a_bounded_clause_keeps_its_measured_thing_as_the_subject(
    text: str, subject: str
) -> None:
    """A comparator ends the subject run; it is not part of the subject.

    The run is read backwards from the measurement, so it meets the phrase's
    last word first: ``"Output was at least 10 GW"`` yielded a subject ending
    in ``least``. Every bounded clause carried that corruption, and a bound
    compared against its complement was refused by the subject rather than by
    the qualifier dimension — right answer, accidental reason.
    """
    atom = extract_text_atoms(text, claim_id="a")[0]

    assert atom.subject == subject


def test_a_bound_pair_is_refused_by_its_qualifier_not_its_subject() -> None:
    """``up to`` against ``at least`` differs in the qualifier, and only there."""
    up_to = extract_text_atoms("Output was up to 10 GW in 2024.", claim_id="a")[0]
    at_least = extract_text_atoms(
        "Output was at least 10 GW in 2024.", claim_id="b"
    )[0]

    assert up_to.subject == at_least.subject == "Output"
    assert up_to.comparator != at_least.comparator
    assert not atomic_compatible(up_to, at_least)


def test_a_plain_value_keeps_its_subject_and_still_merges() -> None:
    """The control: no comparator, no change to the subject or the merge."""
    stated = extract_text_atoms("Output was 10 GW in 2024.", claim_id="a")[0]
    reworded = extract_text_atoms("In 2024, Output was 10 GW.", claim_id="b")[0]

    assert stated.subject == "Output"
    assert atomic_compatible(stated, reworded)


def test_a_qualifier_stated_the_same_way_still_merges() -> None:
    """The refusal is about a difference, not about having a qualifier."""
    stated = extract_text_atoms("Capacity rose to 10 GW in 2024.", claim_id="a")[0]
    reworded = extract_text_atoms("In 2024, capacity rose to 10 GW.", claim_id="b")[0]

    assert atomic_compatible(stated, reworded)


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

    "Costs" is also a relation word in this contract's table, so the breaker's
    ruling applies to it too: a run that is itself a relation word is not a
    subject, and the clause reports a derivation failure rather than deriving
    an entity from the verb. The refusal is unchanged; only its reason is
    sharper, and the accepted coverage loss is the direction that cannot settle
    a claim on a word that is not an entity.
    """
    revenue = _claim("Revenue rose 10 percent in 2024.", claim_id="claim-rev")
    costs = _claim("Costs rose 10 percent in 2024.", claim_id="claim-cost")

    (revenue_atom,) = extract_atoms(revenue)
    (costs_atom,) = extract_atoms(costs)

    assert revenue_atom.subject == "Revenue"
    assert costs_atom.subject == ""
    assert costs_atom.subject_state == "unresolved"
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


@pytest.mark.parametrize(
    ("left_text", "right_text"),
    [
        ("10 GW was doubled in 2024.", "10 GW was halved in 2024."),
        ("10 GW was added in 2024.", "10 GW was held in 2024."),
        ("10 GW was added in 2024.", "10 GW was cut in 2024."),
    ],
)
def test_the_auxiliary_never_steals_the_relation(
    left_text: str, right_text: str
) -> None:
    """The relation comes from the clause's own relation word, not its auxiliary.

    The reviewer's probes: with the value first, every predicate candidate
    follows it, so the relation was read from the auxiliary ``was`` — which
    made a doubling and a halving, a rise and a level, and a rise and an
    unnameable change all ``states_level`` and all compatible.
    """
    (left,) = extract_atoms(_claim(left_text, claim_id="claim-left"))
    (right,) = extract_atoms(_claim(right_text, claim_id="claim-right"))

    assert left.predicate != right.predicate
    assert not atomic_compatible(left, right)


def test_an_auxiliary_alone_states_no_relation() -> None:
    """Skip the auxiliary and, with no relation word left, refuse — never guess.

    ``10 GW was cut`` has no relation this contract can name: ``cut`` is not in
    the vocabulary, so the clause must come back unknown rather than defaulting
    to the level its auxiliary would have implied.
    """
    (unknown,) = extract_atoms(_claim("10 GW was cut in 2024.", claim_id="claim-cut"))
    (level,) = extract_atoms(
        _claim("10 GW was held in 2024.", claim_id="claim-held")
    )

    assert unknown.predicate == ""
    assert level.predicate == "states_level"
    assert not atomic_compatible(unknown, level)


@pytest.mark.asyncio
async def test_the_auxiliary_never_merges_two_value_fronted_relations() -> None:
    """The harm the probe measured: one canonical cluster, settled verified."""
    claims = [
        _claim("10 GW was doubled in 2024.", claim_id="claim-doubled"),
        _claim("10 GW was halved in 2024.", claim_id="claim-halved"),
    ]
    completer = ScriptedCompleter(outputs=[_pairs((1, 2))])

    consolidation = await consolidate_claims(completer, claims)

    assert len(consolidation.claims) == 2
    assert consolidation.diagnostics == [
        "equivalence_candidate_incompatible:1:2"
    ]


def test_two_value_fronted_levels_still_merge() -> None:
    """The positive control: skipping the auxiliary must not refuse real levels."""
    (left,) = extract_atoms(
        _claim(
            "10 GW was held in the 2024 interconnection queue.",
            claim_id="claim-left",
        )
    )
    (right,) = extract_atoms(
        _claim(
            "10 GW sat in the 2024 interconnection queue.",
            claim_id="claim-right",
        )
    )

    assert left.predicate == right.predicate == "states_level"
    assert atomic_compatible(left, right)


FULL_QUEUE_ENTITY = "California Independent System Operator interconnection queue"
LONG_ENTITY_CLAUSE = (
    "10 GW sat in the 2024 California Independent System Operator "
    "transmission queue"
)
FULL_TRANSMISSION_ENTITY = (
    "California Independent System Operator transmission queue"
)


@pytest.mark.parametrize(
    "other_side",
    [
        f"10 GW sat in the 2024 {FULL_QUEUE_ENTITY}",
        "The California Independent System Operator held 10 GW in 2024",
    ],
)
def test_a_long_trailing_entity_is_read_whole_or_not_at_all(
    other_side: str,
) -> None:
    """A truncated entity is more dangerous than a missing one.

    The reviewer's probes: the capture kept only the first four words after the
    preposition — the modifier side of the noun phrase — so an interconnection
    queue and a transmission queue both came back as ``California Independent
    System Operator`` and settled verified, and that truncated subject also
    matched the operator named on its own. Empty-vs-stated refuses; a plausible
    prefix does not.
    """
    (left,) = extract_atoms(_claim(other_side, claim_id="claim-left"))
    (right,) = extract_atoms(_claim(LONG_ENTITY_CLAUSE, claim_id="claim-right"))

    assert right.subject != "California Independent System Operator"
    assert right.subject in ("", FULL_TRANSMISSION_ENTITY)
    assert atomic_compatible(left, right) is False


def test_two_identical_full_entities_still_merge() -> None:
    """The control: reading the whole entity must not refuse one entity."""
    text = f"10 GW sat in the 2024 {FULL_QUEUE_ENTITY}"
    (left,) = extract_atoms(_claim(text, claim_id="claim-left"))
    (right,) = extract_atoms(
        _claim(
            f"10 GW was held in the 2024 {FULL_QUEUE_ENTITY}",
            claim_id="claim-right",
        )
    )

    assert left.subject == right.subject == FULL_QUEUE_ENTITY
    assert atomic_compatible(left, right)


def test_a_trailing_phrase_too_long_to_read_whole_is_never_a_prefix() -> None:
    """A capture this contract cannot list whole yields its digest, not a prefix.

    Round 5 discarded the phrase entirely, which refused two different over-cap
    entities correctly and two identical ones wrongly — the identical pair was
    then minted as two ledger rows under one cluster id. The breaker's ruling
    keeps the whole-or-empty principle for the *words* (no prefix ever stands
    for the entity) while taking the complete phrase whole as its digest.
    """
    (atom,) = extract_atoms(
        _claim(
            "10 GW sat in the 2024 one two three four five six seven eight "
            "nine ten eleven twelve thirteen queue",
            claim_id="claim-long",
        )
    )
    (same,) = extract_atoms(
        _claim(
            "10 GW sat in the 2024 one two three four five six seven eight "
            "nine ten eleven twelve thirteen queue",
            claim_id="claim-same",
        )
    )
    (other,) = extract_atoms(
        _claim(
            "10 GW sat in the 2024 one two three four five six seven eight "
            "nine ten eleven twelve thirteen backlog",
            claim_id="claim-other",
        )
    )

    assert atom.subject != ""
    assert atom.subject == same.subject
    assert atom.subject != other.subject
    assert not atom.subject.startswith("one two three")


def test_two_clauses_that_name_no_entity_still_merge() -> None:
    """The positive control: unknown on both sides is not a conflict."""
    left = AtomicProposition(text="Interconnection delays are growing.")
    right = AtomicProposition(text="Interconnection delays are increasing.")

    assert atomic_compatible(left, right)


# --------------------------------------------------------------------------
# Fix round 5: an entity a clause names is never deleted by its own wording
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left_text", "right_text"),
    [
        (
            "Projected wind capacity is 10 GW.",
            "Projected solar capacity is 10 GW.",
        ),
        (
            "Estimated wind capacity is 10 GW.",
            "Estimated solar capacity is 10 GW.",
        ),
        (
            "Reported wind capacity is 10 GW.",
            "Reported solar capacity is 10 GW.",
        ),
        (
            "Measured wind capacity is 10 GW.",
            "Measured solar capacity is 10 GW.",
        ),
        ("Projected capacity is 10 GW.", "Projected output is 10 GW."),
        (
            "The projected wind capacity is 10 GW.",
            "The projected solar capacity is 10 GW.",
        ),
        (
            "Projected wind capacity in 2024 is 10 GW.",
            "Projected solar capacity in 2024 is 10 GW.",
        ),
    ],
)
def test_a_relation_word_before_the_entity_never_deletes_it(
    left_text: str, right_text: str
) -> None:
    """A relation word that precedes its entity must not anchor the entity run.

    The reviewer's probes: with the auxiliaries gone from the relation table,
    ``_predicate_match`` returns the clause's own relation word — "Projected",
    whose start is at or before the entity — so the run before the anchor was
    empty and the clause reported no subject at all. Both sides came back ``''``,
    which the comparison reads as agreement, and this module's own running
    example ("wind capacity" vs "solar capacity") merged into one settled
    verified cluster with no diagnostic.
    """
    (left,) = extract_atoms(_claim(left_text, claim_id="claim-left"))
    (right,) = extract_atoms(_claim(right_text, claim_id="claim-right"))

    assert left.subject
    assert right.subject
    assert left.subject != right.subject
    assert not atomic_compatible(left, right)


def test_the_entity_is_read_from_the_gap_the_relation_leaves() -> None:
    """The rule stated directly: the entity sits between the relation and the value."""
    (atom,) = extract_atoms(_claim("Projected wind capacity is 10 GW."))

    assert atom.subject == "wind capacity"
    assert atom.subject_state == "derived"
    assert atom.predicate == "projected"


def test_the_pjm_trailing_control_is_unchanged() -> None:
    """The control the reviewer warned about: reading the gap must not perturb it.

    ``10 GW sat in PJM's 2024 interconnection queue`` names its entity after the
    verb, so it takes the trailing read and has to keep reading the whole
    entity. A naive version of the gap read came back as ``'PJM's'``.
    """
    (atom,) = extract_atoms(_claim("10 GW sat in PJM's 2024 interconnection queue"))

    assert atom.subject == "PJM's interconnection queue"


@pytest.mark.asyncio
async def test_a_relation_word_before_the_entity_never_merges_end_to_end() -> None:
    """The harm the probe measured: one canonical cluster, settled verified."""
    claims = [
        _claim("Projected wind capacity is 10 GW.", claim_id="claim-wind"),
        _claim("Projected solar capacity is 10 GW.", claim_id="claim-solar"),
    ]

    consolidation = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]), claims
    )

    assert len(consolidation.claims) == 2
    assert consolidation.diagnostics == [
        "equivalence_candidate_incompatible:1:2"
    ]


# --------------------------------------------------------------------------
# Precondition A (carried from Task 5's breaker): a run that IS a relation
# word or a light verb is not a subject
# --------------------------------------------------------------------------


def test_two_preceding_relation_words_are_not_a_subject() -> None:
    """The breaker probe: "Projected and reported …" read its subject as "Projected".

    Two relation words precede the entity, so ``_predicate_match`` selects the
    later one ("reported") while the run before it is the earlier one
    ("Projected") — a relation word, not an entity. Both sides then agreed on
    "Projected", and the wind and solar clauses became ONE canonical verified
    cluster with no diagnostic.
    """
    (wind,) = extract_atoms(
        _claim("Projected and reported wind capacity is 10 GW.")
    )
    (solar,) = extract_atoms(
        _claim("Projected and reported solar capacity is 10 GW.")
    )

    assert wind.subject == "wind capacity"
    assert solar.subject == "solar capacity"
    assert wind.subject_state == solar.subject_state == "derived"
    assert not atomic_compatible(wind, solar)


@pytest.mark.asyncio
async def test_two_preceding_relation_words_never_merge_end_to_end() -> None:
    """The harm the breaker measured: one canonical cluster, settled verified."""
    claims = [
        _claim(
            "Projected and reported wind capacity is 10 GW.",
            claim_id="claim-wind",
        ),
        _claim(
            "Projected and reported solar capacity is 10 GW.",
            claim_id="claim-solar",
        ),
    ]

    consolidation = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]), claims
    )

    assert len(consolidation.claims) == 2
    assert len(consolidation.clusters) == 2
    assert consolidation.diagnostics == [
        "equivalence_candidate_incompatible:1:2"
    ]


def test_two_multi_relation_clauses_naming_one_entity_still_merge() -> None:
    """The control: refusing a relation-word run may not refuse the same entity.

    Both clauses put two relation words before one entity, so both take the gap
    read and both find "wind capacity". Refusing the *pair* here would be the
    coverage loss the ruling accepts only where the entities genuinely differ.
    """
    (left,) = extract_atoms(
        _claim("Projected and reported wind capacity is 10 GW.")
    )
    (right,) = extract_atoms(
        _claim("Projected and reported wind capacity equals 10 GW.")
    )

    assert left.subject == right.subject == "wind capacity"
    assert left.subject_state == right.subject_state == "derived"
    assert atomic_compatible(left, right)


def test_a_subject_run_made_only_of_relation_words_is_unresolved() -> None:
    """Falling through the gap read with nothing behind it is a derivation failure.

    "Costs rose 10 percent in 2024" has an entity position whose only candidate
    word is the relation ``costs``, which this contract may not read as a
    subject: the run is unusable, the gap behind ``rose`` is empty, and the
    clause names an entity it could not derive. UNRESOLVED refuses against
    everything, which is the direction that cannot settle a claim on nothing.
    """
    (costs,) = extract_atoms(_claim("Costs rose 10 percent in 2024."))
    (revenue,) = extract_atoms(_claim("Revenue rose 10 percent in 2024."))

    assert revenue.subject == "Revenue"
    assert costs.subject == ""
    assert costs.subject_state == "unresolved"
    assert not atomic_compatible(revenue, costs)


def test_a_light_verb_never_erases_a_real_entity_word() -> None:
    """``Will County`` is an entity, not the modal ``will``.

    The reviewer's probe: the light-verb clearing dropped "Will"/"May" from the
    run and left nothing, so "Will County held 10 GW" and "May County held
    10 GW" both reported no subject and merged where the base refused. An entity
    word this contract gathers and then clears is a derivation failure —
    UNRESOLVED, which refuses — not a clause that names no entity.
    """
    (left,) = extract_atoms(_claim("Will County held 10 GW", claim_id="claim-will"))
    (right,) = extract_atoms(_claim("May County held 10 GW", claim_id="claim-may"))

    assert left.subject_state == "unresolved"
    assert right.subject_state == "unresolved"
    assert not atomic_compatible(left, right)


@pytest.mark.asyncio
async def test_a_light_verb_never_merges_two_counties_end_to_end() -> None:
    """The harm: a base-refused pair became one settled verified cluster."""
    claims = [
        _claim("Will County held 10 GW", claim_id="claim-will"),
        _claim("May County held 10 GW", claim_id="claim-may"),
    ]

    consolidation = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]), claims
    )

    assert len(consolidation.claims) == 2
    assert consolidation.diagnostics == [
        "equivalence_candidate_incompatible:1:2"
    ]


def test_two_clauses_that_name_no_entity_still_agree() -> None:
    """The round-3 control, through the real extractor rather than by hand."""
    (left,) = extract_atoms(_claim("10 GW was held in 2024.", claim_id="claim-a"))
    (right,) = extract_atoms(_claim("10 GW sat in 2024.", claim_id="claim-b"))

    assert left.subject_state == right.subject_state == "absent"
    assert atomic_compatible(left, right)


OVER_CAP_CALIFORNIA = (
    "10 GW was held by the California Independent System Operator "
    "interconnection queue for the second half of the year"
)
OVER_CAP_MIDCONTINENT = (
    "10 GW was held by the Midcontinent Independent System Operator "
    "interconnection queue for the second half of the year"
)


def test_two_over_cap_entities_never_agree_on_nothing() -> None:
    """A capture this contract cannot complete is UNRESOLVED, not "no entity".

    The reviewer's probe: both clauses carry a thirteen-word entity, so the
    whole-or-empty read returned ``''`` on both sides — and empty-vs-empty is
    the permissive case, so two different operators merged where the base
    refused on their four-word prefixes. Under the breaker's ruling the
    complete trailing phrase is no longer thrown away: its fixed-size digest is
    the subject discriminator, so the two operators are told apart by *what the
    clause says* rather than by a shared empty string.
    """
    (left,) = extract_atoms(_claim(OVER_CAP_CALIFORNIA, claim_id="claim-ca"))
    (right,) = extract_atoms(
        _claim(OVER_CAP_MIDCONTINENT, claim_id="claim-midcontinent")
    )

    assert left.subject_state == right.subject_state == "derived"
    assert left.subject != right.subject
    assert not atomic_compatible(left, right)


def test_two_identical_over_cap_entities_share_one_derived_identity() -> None:
    """The breaker probe: identical over-cap claims were one identity in two rows.

    Round 5 made an over-cap capture UNRESOLVED, which correctly refuses two
    *different* over-cap entities — but it also refused two identical ones, and
    the rejected pair was then minted as two singletons carrying the SAME
    cluster id (the assertion fingerprint ignores a subject nobody derived).
    Two ledger rows under one identity breaks the one-semantic-identity rule.
    The complete normalized trailing phrase is available before the cap check,
    so its digest is the discriminator: identical phrases agree, different ones
    still refuse.
    """
    (left,) = extract_atoms(_claim(OVER_CAP_CALIFORNIA, claim_id="claim-a"))
    (right,) = extract_atoms(_claim(OVER_CAP_CALIFORNIA, claim_id="claim-b"))

    assert left.subject_state == right.subject_state == "derived"
    assert left.subject != ""
    assert left.subject == right.subject
    assert atomic_compatible(left, right)


def test_the_over_cap_discriminator_is_a_fixed_size_digest() -> None:
    """Not a truncated prefix, and not unconditional UNRESOLVED.

    A four-word prefix of a long entity is nonempty, plausible, and identical
    for two different operators; UNRESOLVED refuses the identical case too. The
    discriminator is therefore the digest of the COMPLETE normalized phrase, so
    a shared opening cannot merge two entities and a shared phrase still can.
    """
    (california,) = extract_atoms(_claim(OVER_CAP_CALIFORNIA, claim_id="claim-ca"))
    (midcontinent,) = extract_atoms(
        _claim(OVER_CAP_MIDCONTINENT, claim_id="claim-midcontinent")
    )

    for atom in (california, midcontinent):
        assert atom.subject.startswith(OVER_CAP_SUBJECT_PREFIX)
        digest = atom.subject[len(OVER_CAP_SUBJECT_PREFIX):]
        assert len(digest) == OVER_CAP_SUBJECT_DIGEST_CHARS
        assert set(digest) <= set("0123456789abcdef")
    # The two entities share their opening words and differ only in the
    # operator's name, which is exactly what a prefix would have lost.
    assert california.subject != midcontinent.subject


@pytest.mark.asyncio
async def test_identical_over_cap_claims_publish_one_row_not_two() -> None:
    """The ledger harm: two canonical rows carrying one cluster id."""
    claims = [
        _claim(OVER_CAP_CALIFORNIA, claim_id="claim-a"),
        _claim(OVER_CAP_CALIFORNIA, claim_id="claim-b"),
    ]

    consolidation = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]), claims
    )

    assert len(consolidation.claims) == 1
    assert len(consolidation.clusters) == 1
    assert len({claim.claim_id for claim in consolidation.claims}) == 1


@pytest.mark.asyncio
async def test_identical_over_cap_claims_are_one_identity_without_a_proposal() -> None:
    """One assertion fingerprint may never mint two rows, proposal or not.

    The provider proposes duplicates; it is not the authority on identity. Two
    atoms that mint the SAME cluster id are one assertion by this contract's
    own fingerprint, so the ledger cannot publish them as two.
    """
    claims = [
        _claim(OVER_CAP_CALIFORNIA, claim_id="claim-a"),
        _claim(OVER_CAP_CALIFORNIA, claim_id="claim-b"),
    ]

    consolidation = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs()]), claims
    )

    assert len(consolidation.clusters) == 1
    assert len({cluster.cluster_id for cluster in consolidation.clusters}) == 1


@pytest.mark.asyncio
async def test_two_over_cap_entities_never_become_one_settled_claim() -> None:
    """The harm: two different operators merged into one canonical cluster."""
    claims = [
        _claim(OVER_CAP_CALIFORNIA, claim_id="claim-ca"),
        _claim(OVER_CAP_MIDCONTINENT, claim_id="claim-midcontinent"),
    ]

    consolidation = await consolidate_claims(
        ScriptedCompleter(outputs=[_pairs((1, 2))]), claims
    )

    assert len(consolidation.claims) == 2
    assert consolidation.diagnostics == [
        "equivalence_candidate_incompatible:1:2"
    ]


def test_a_bare_unit_noun_is_not_the_clauses_subject() -> None:
    """Shape (i): ``of capacity`` is the measured unit's own noun, not an entity."""
    (left,) = extract_atoms(
        _claim("10 GW of capacity was added to the Texas grid", claim_id="claim-tx")
    )
    (right,) = extract_atoms(
        _claim(
            "10 GW of capacity was added to the California grid",
            claim_id="claim-ca",
        )
    )

    assert left.subject == "Texas grid"
    assert right.subject == "California grid"
    assert not atomic_compatible(left, right)


def test_a_locative_entity_is_read_after_a_measured_value() -> None:
    """Shape (ii): both clauses named no subject, so two grids agreed."""
    (left,) = extract_atoms(
        _claim("10 GW was added to the Texas grid", claim_id="claim-tx")
    )
    (right,) = extract_atoms(
        _claim("10 GW was added to the California grid", claim_id="claim-ca")
    )

    assert left.subject == "Texas grid"
    assert right.subject == "California grid"
    assert not atomic_compatible(left, right)


def test_the_quantity_phrase_paraphrase_still_merges() -> None:
    """The coverage win: naming the unit's noun is wording, not a difference."""
    (left,) = extract_atoms(
        _claim("10 GW of capacity was added to the Texas grid", claim_id="claim-a")
    )
    (right,) = extract_atoms(
        _claim("10 GW was added to the Texas grid", claim_id="claim-b")
    )

    assert left.subject == right.subject == "Texas grid"
    assert atomic_compatible(left, right)


def test_a_quantity_phrase_never_overrides_a_clauses_own_subject() -> None:
    """The backfire control: the unit-noun rule may not touch a real subject.

    ``Capacity rose 10 percent`` states its subject before the measurement; only
    an ``of``-phrase *inside* a measured quantity is the unit's noun. Reading
    the two as one shape would merge capacity with output.
    """
    (left,) = extract_atoms(_claim("Capacity rose 10 percent in 2024"))
    (right,) = extract_atoms(_claim("Output rose 10 percent in 2024"))

    assert left.subject == "Capacity"
    assert right.subject == "Output"
    assert not atomic_compatible(left, right)


def test_a_qualified_quantity_phrase_still_discriminates() -> None:
    """``of wind capacity`` is not a bare unit noun, so it stays a readable entity."""
    (left,) = extract_atoms(
        _claim("10 GW of wind capacity was added in 2024", claim_id="claim-wind")
    )
    (right,) = extract_atoms(
        _claim("10 GW of solar capacity was added in 2024", claim_id="claim-solar")
    )

    assert left.subject == "wind capacity"
    assert right.subject == "solar capacity"
    assert not atomic_compatible(left, right)


@pytest.mark.parametrize(
    ("text", "captured"),
    [
        ("10 GW is expected to be added in 2024", "be added"),
        ("10 GW was added in 2024, according to PJM", "PJM"),
        ("10 GW was added, bringing the total to 12 GW", "GW"),
    ],
)
def test_a_to_phrase_never_captures_a_verb_a_level_or_an_attribution(
    text: str, captured: str
) -> None:
    """The reviewer's measurements: a ``to`` phrase is a locative only sometimes.

    "to be added" is a verb phrase, "according to PJM" is an attribution, and
    "to 12 GW" is a level the clause states rather than an entity it names.
    """
    (atom,) = extract_atoms(_claim(text))

    assert atom.subject != captured


def test_a_quantitys_noun_is_still_compared() -> None:
    """Narrowing the entity run may not drop the measurand a quantity names.

    Excluding the bare ``of``-noun from the entity run is what lets the locative
    entity be read; taken alone it also lost the difference between "10 GW of
    storage" and "10 GW of solar" on one grid, which the base refused. The noun
    is carried as the quantity's measurand and compared when both state one.
    """
    (left,) = extract_atoms(
        _claim("10 GW of storage was connected to the Texas grid")
    )
    (right,) = extract_atoms(
        _claim("10 GW of solar was connected to the Texas grid")
    )

    assert left.subject == right.subject == "Texas grid"
    assert left.quantity_noun == "storage"
    assert right.quantity_noun == "solar"
    assert not atomic_compatible(left, right)


def test_an_unnamed_measurand_is_not_a_different_one() -> None:
    """The control: naming the measurand is wording the other clause may omit."""
    (left,) = extract_atoms(
        _claim("10 GW of storage was connected to the Texas grid")
    )
    (right,) = extract_atoms(_claim("10 GW was connected to the Texas grid"))

    assert right.quantity_noun == ""
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


def test_an_all_unstamped_group_keeps_one_identity_whatever_the_order() -> None:
    """With no sequence to compare, the survivor is still not caller order.

    No production path mints an unstamped cluster — consolidation stamps every
    one it builds — so this is a latent determinism gap rather than a live
    defect: the fallback ordered its peers by the position the caller listed
    them in, so ``[a, b]`` kept ``a`` and ``[b, a]`` kept ``b``.
    """
    first = _stored_cluster("claim-a", 0)
    second = _stored_cluster("claim-b", 0)

    forward = oldest_first([first, second])
    backward = oldest_first([second, first])

    assert [cluster.created_seq for cluster in forward] == [0, 0]
    assert [cluster.cluster_id for cluster in forward] == [
        cluster.cluster_id for cluster in backward
    ]
    # The fold keeps the first of the ordered pair, so the identity it keeps is
    # the same one either way the caller listed them.
    assert (
        merge_claim_clusters(forward[0], forward[1]).cluster_id
        == merge_claim_clusters(backward[0], backward[1]).cluster_id
    )


def test_equal_sequences_never_flip_with_caller_order() -> None:
    """An equal non-zero sequence is not a licence to fall back to input order.

    ``oldest_first`` tie-broke on the cluster id only when ``created_seq`` was
    zero, so three clusters minted at one equal sequence produced six orderings
    — and six different survivors — by the order a caller listed them in. That
    is reachable through ``merge_claim_cluster_registry`` across runs, where the
    caller's order is the registry fold's.
    """
    clusters = [
        _stored_cluster("claim-a", 5),
        _stored_cluster("claim-b", 5),
        _stored_cluster("claim-c", 5),
    ]

    orderings = {
        tuple(cluster.cluster_id for cluster in oldest_first(list(order)))
        for order in permutations(clusters)
    }

    assert len(orderings) == 1
    (ordering,) = orderings
    assert ordering == tuple(sorted(ordering))
    assert [cluster.created_seq for cluster in oldest_first(list(clusters))] == [
        5,
        5,
        5,
    ]


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
        # The fixture's premise is a claim that passed the strict pair test;
        # ``Claim`` refuses the verified badge without it.
        "evidence_status": "verified_pair",
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


# --------------------------------------------------------------------------
# Attribution: a named issuer that reports is an attribution
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The live run's own claim prose (audit #2). Every one of these named
        # its issuer and was still recorded unattributed, so no plan target
        # that requires a source could be bound to it.
        (
            "EIA reported that generators in the United States added 10.4 GW "
            "of new utility-scale battery storage capacity in 2024.",
            "EIA",
        ),
        (
            "EIA stated that cumulative U.S. utility-scale battery storage "
            "capacity exceeded 26 GW in 2024.",
            "EIA",
        ),
        (
            "EIA forecast in its February 24, 2025 In-brief analysis that "
            "18.2 GW of utility-scale battery storage capacity would be added "
            "in 2025.",
            "EIA",
        ),
        (
            "EIA's August 20, 2025 In-brief analysis reported that battery "
            "storage accounted for 5.9 GW of additions in the first half of "
            "2025.",
            "EIA",
        ),
        (
            "EIA expects that developers will add 7.0 GW of battery storage "
            "capacity in Texas in 2025.",
            "EIA",
        ),
        ("EIA projected that 18.2 GW would be added in 2025.", "EIA"),
        ("EIA estimated that 10.3 GW was added in 2024.", "EIA"),
        ("EIA said the 2024 addition set a record.", "EIA"),
        ("FERC found that the queue grew in 2024.", "FERC"),
        (
            "Generators added 10.4 GW of battery storage capacity in 2024, "
            "EIA reported.",
            "EIA",
        ),
    ],
)
def test_a_named_issuer_that_reports_is_the_attribution(
    text: str, expected: str
) -> None:
    """The auditor's C11 contrast: "EIA reported that …" answers "according to EIA"."""
    atom = extract_text_atoms(text)[0]

    assert atom.attribution == expected


@pytest.mark.parametrize(
    "text",
    [
        # A bare pronoun or determiner names nobody: the claim states a fact
        # with no issuer, and this contract must not invent one.
        "It reported that generators added 10.4 GW of new capacity in 2024.",
        "They stated that the queue grew in 2024.",
        "The agency reported that generators added 10.4 GW in 2024.",
        "This analysis found that the queue grew in 2024.",
        "In 2024, generators added 10.4 GW of new capacity.",
    ],
)
def test_a_reporting_verb_with_no_named_issuer_attributes_nothing(
    text: str,
) -> None:
    (atom,) = extract_text_atoms(text)

    assert atom.attribution == ""


def test_a_clause_inherits_the_issuer_its_claim_names() -> None:
    """``according to its <document>`` refers to the claim's own named issuer."""
    claim = _claim(
        "EIA stated that utility-scale battery capacity grew in 2024. "
        "The 2024 addition reached 10.4 GW, according to its January 2025 "
        "Preliminary Monthly Electric Generator Inventory."
    )

    first, second = extract_atoms(claim)

    assert first.attribution == "EIA"
    assert second.attribution == "EIA"


def test_an_unnamed_issuers_own_document_attributes_nothing() -> None:
    """The control: "its" with no named issuer in the claim is not attribution."""
    claim = _claim(
        "The 2024 addition reached 10.4 GW, according to its January 2025 "
        "Preliminary Monthly Electric Generator Inventory."
    )

    (atom,) = extract_atoms(claim)

    assert atom.attribution == ""


def test_the_subject_verb_form_does_not_override_a_named_source() -> None:
    """A claim that opens with its source keeps that source."""
    claim = _claim(
        "According to Energy Global, EIA reported that generators added "
        "10.4 GW of new battery storage capacity in 2024."
    )

    (atom,) = extract_atoms(claim)

    assert atom.attribution == "Energy Global"


# --------------------------------------------------------------------------
# Geography: a place named without a preposition is still the place
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The run's own claim prose (audit #2, replay C8): the place is named
        # adjectivally, so no target that requires the geography could bind.
        (
            "EIA stated that cumulative U.S. utility-scale battery storage "
            "capacity exceeded 26 GW in 2024.",
            "United States",
        ),
        (
            "EIA reported that battery storage accounted for the "
            "second-largest share of U.S. capacity additions in the first "
            "half of 2025.",
            "United States",
        ),
        (
            "EIA forecast that 18.2 GW would be added to the U.S. grid in "
            "2025.",
            "United States",
        ),
        (
            "An EIA article stated that U.S. power providers added 10.3 GW of "
            "new battery storage capacity in 2024.",
            "United States",
        ),
        # The other spellings of the same place, and the ones a claim uses
        # when it introduces the place before naming it adjectivally.
        ("According to EIA, US battery capacity reached 26 GW in 2024.", "United States"),
        ("According to EIA, USA battery capacity reached 26 GW in 2024.", "United States"),
        ("According to EIA, American battery capacity reached 26 GW in 2024.", "United States"),
        ("According to EIA, United States battery capacity reached 26 GW in 2024.", "United States"),
        ("According to EIA, U.S. battery capacity reached 26 GW in 2024.", "United States"),
        ("According to EIA, the U.S. battery capacity reached 26 GW in 2024.", "United States"),
    ],
)
def test_a_place_named_without_a_preposition_is_the_geography(
    text: str, expected: str
) -> None:
    """``U.S. capacity additions`` names the same place as ``in the United States``.

    The atom already reads a prepositional locative; a clause that names the
    place adjectivally stated no geography at all, which is what left the
    run's own claims unable to bind a target that requires one.
    """
    atom = extract_text_atoms(text)[0]

    assert atom.geography == expected


@pytest.mark.parametrize(
    "text",
    [
        # A pronoun is not a country: "tell us about …" names no place.
        "EIA reported that the plan tells us about battery capacity in 2024.",
        # A currency code is not a country.
        "According to EIA, the contract was worth 26 million USD in 2024.",
        "According to EIA, the contract was worth US$26 million in 2024.",
        # Where a thing was made is not the place the clause's fact covers.
        "According to the agency, Canadian plants imported U.S.-made "
        "inverters in 2024.",
        # Another region's adjective is not the United States.
        "According to the agency, Latin American capacity reached 26 GW in 2024.",
        "According to the agency, South American capacity reached 26 GW in 2024.",
    ],
)
def test_an_alias_that_names_no_place_is_not_a_geography(text: str) -> None:
    """The negatives the alias list must not over-match."""
    atom = extract_text_atoms(text)[0]

    assert atom.geography == ""


def test_the_prepositional_place_still_wins_over_an_adjectival_one() -> None:
    """A clause about Canada that mentions U.S. firms stays about Canada."""
    atom = extract_text_atoms(
        "According to the agency, U.S. firms added 26 GW of capacity in Canada "
        "in 2024."
    )[0]

    assert atom.geography == "Canada"


def test_a_nations_own_reference_is_the_place_the_claim_names() -> None:
    """"The nation's fleet" is the country its claim names, and nobody else's."""
    claim = _claim(
        "EIA reported that U.S. battery capacity grew. The nation's storage "
        "fleet reached 26 GW in 2024."
    )

    named, anaphor = extract_atoms(claim)

    assert named.geography == "United States"
    assert anaphor.geography == "United States"


def test_a_nations_own_reference_with_no_named_country_stays_unnamed() -> None:
    """The control: "the nation" alone names no place this contract can read."""
    claim = _claim("The nation's storage fleet reached 26 GW in 2024.")

    (atom,) = extract_atoms(claim)

    assert atom.geography == ""


def test_the_two_spellings_of_one_place_state_one_geography() -> None:
    """So both answer the one dimension the plan stamps: "geography: United States"."""
    prepositional = _claim(
        "According to EIA, capacity reached 26 GW in the United States in 2024."
    )
    adjectival = _claim(
        "According to EIA, U.S. capacity reached 26 GW in 2024."
    )

    (prepositional_atom,) = extract_atoms(prepositional)
    (adjectival_atom,) = extract_atoms(adjectival)

    assert prepositional_atom.geography == adjectival_atom.geography
    for atom in (prepositional_atom, adjectival_atom):
        assert atom_answers_dimensions(
            atom, ["geography: United States"], question=_AUDIT_QUESTION
        )



# --------------------------------------------------------------------------
# Attribution: whose report the clause is, and whose it is not
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # A capitalised common noun is not an issuer (review F1).
        "Analysts reported that generators added 10.4 GW of storage in 2024.",
        "Nobody reported that generators added 10.4 GW of storage in 2024.",
        # The pronoun scopes the phrase even when a name follows it, so the
        # name inside it is not the clause's issuer.
        "Nobody at EIA reported that 10.4 GW was added in 2024.",
        "Industry analysts reported that generators added 10.4 GW in 2024.",
        "Last year developers reported 10.4 GW of additions in 2024.",
        "Grid operators in California said that 4 GW was added in 2024.",
        "Critics said that EIA overstated the 10.4 GW added in 2024.",
        "Earlier Reports said 10.4 GW was added in 2024.",
        # A trailing reporting verb may not cross a comma to the nearest name.
        (
            "Generators added 10,400 MW of battery storage capacity in the "
            "United States in 2024, the agency reported."
        ),
        "Fluence installed 2 GW in Germany in 2024, a U.S. company said.",
        # "reports" is a noun here: the clause reports a denial, not a report.
        (
            "EIA denied reports that generators added 10,400 MW of battery "
            "storage capacity in the United States in 2024."
        ),
    ],
)
def test_a_reporting_verb_names_no_issuer_when_none_is_named(
    text: str,
) -> None:
    """The name an attribution may be read from is not "any capital word".

    Every string here met a ``primary_attribution`` obligation at head, because
    the grammar credited a common noun, crossed a comma to reach the previous
    clause's place, or read the noun "reports" as a verb.
    """
    atom = extract_text_atoms(text)[0]

    assert atom.attribution == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The run's own forms, which the tightening may not lose.
        (
            "EIA reported that generators in the United States added 10.4 GW "
            "of new utility-scale battery storage capacity in 2024.",
            "EIA",
        ),
        (
            "EIA's August 20, 2025 In-brief analysis reported that battery "
            "storage accounted for the second-largest share of U.S. capacity "
            "additions in the first half of 2025.",
            "EIA",
        ),
        (
            "EIA forecast in its February 24, 2025 In-brief analysis that "
            "18.2 GW of utility-scale battery storage capacity would be added "
            "in 2025.",
            "EIA",
        ),
        # Acronyms, multi-token names, and a name that does not start the clause.
        ("FERC found that the queue grew in 2024.", "FERC"),
        ("Bloomberg NEF found that 10.4 GW was added in 2024.", "Bloomberg NEF"),
        (
            "Wood Mackenzie reported that 10.4 GW was added in 2024.",
            "Wood Mackenzie",
        ),
        ("However, EIA stated that 18.2 GW is expected in 2025.", "EIA"),
        # The trailing form credits the name it names, not the clause's subject.
        ("Developers expected 18.2 GW in 2025, EIA said.", "EIA"),
        (
            "Generators added 10.4 GW of battery storage capacity in 2024, "
            "EIA reported.",
            "EIA",
        ),
    ],
)
def test_a_named_issuer_still_attributes_after_the_grammar_tightening(
    text: str, expected: str
) -> None:
    """The positive half: acronyms, multi-token names, and trailing forms."""
    atom = extract_text_atoms(text)[0]

    assert atom.attribution == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A publisher's name is one token and not an acronym (re-review N2).
        ("Reuters reported that generators added 10,400 MW in 2024.", "Reuters"),
        # The run's own claim 11: the page is the publisher's, and "states that"
        # is a reported clause.
        (
            "OpenEI's page for the Form EIA-860 Instructions states that EIA "
            "requires certain developers of electric generating plants to "
            "submit Form EIA-860.",
            "OpenEI",
        ),
        # A brand spelling with an internal capital is a name however it is
        # cased, whatever follows its verb.
        ("OpenEI reported 10.4 GW of additions in 2024.", "OpenEI"),
        # A name whose verb states an object rather than a reported clause is
        # still a name, and a scope phrase in front of it is not part of it.
        ("Fluence reported 10.4 GW in 2024.", "Fluence"),
        ("Texas reported that 4 GW was added in 2024.", "Texas"),
        ("In 2024 Texas reported 4 GW of additions in 2024.", "Texas"),
    ],
)
def test_a_single_token_publisher_still_names_its_claim(
    text: str, expected: str
) -> None:
    """A one-token name is an issuer when nothing reads it as a common noun."""
    atom = extract_text_atoms(text)[0]

    assert atom.attribution == expected


@pytest.mark.parametrize(
    "text",
    [
        # A determiner or pronoun that opens a clause reports nothing (ND1).
        "Our analysis found that 10.4 GW of battery storage was added in the United States in 2024.",
        "Every study found that 10.4 GW was added in 2024.",
        "Several reported that 10.4 GW was added in 2024.",
        # A common noun that opens a clause is not a publisher.
        "Reports stated that 10.4 GW was added in 2024.",
        "Media reported that 10.4 GW was added in 2024.",
        "Press reported that 10.4 GW was added in 2024.",
        "Government reported that 10.4 GW was added in 2024.",
        "Study found that 10.4 GW was added in 2024.",
        "Studies found that 10.4 GW was added in 2024.",
        "Analysis found that 10.4 GW was added in 2024.",
        "Utilities reported that 10.4 GW was added in 2024.",
        # The shape of a noun phrase: a capitalised modifier in front of a
        # lowercase common-noun head names nobody, however the head is spelled.
        "Federal officials reported that 10.4 GW was added in 2024.",
        "State regulators said that 4 GW was added in 2024.",
        "Team members reported that 10.4 GW was added in 2024.",
        "Senior officials reported that 10.4 GW was added in 2024.",
        # The indefinite pronouns are the same closed class as "nobody".
        "None reported that 10.4 GW was added in 2024.",
        "Nothing reported that 10.4 GW was added in 2024.",
        "Everyone reported that 10.4 GW was added in 2024.",
        "Someone reported that 10.4 GW was added in 2024.",
        "Someone at EIA reported that 10.4 GW was added in 2024.",
    ],
)
def test_a_common_noun_that_opens_a_clause_names_no_issuer(text: str) -> None:
    """One title-case token is a name only when nothing reads it as a noun.

    A determiner ("Our", "Every", "Several") is a closed-class word, and a
    singular common noun ("Media", "Study", "Analysis") is a noun like the
    plurals the F1 negatives already cover. Crediting either one recorded a
    fabricated issuer for a claim that named none.
    """
    assert extract_text_atoms(text)[0].attribution == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The denial is the earlier assertion, not the name phrase (ND2).
        (
            "No one expected the growth, but EIA reported that 10.4 GW was "
            "added in 2024.",
            "EIA",
        ),
        (
            "None of the earlier forecasts matched, and EIA reported that "
            "10.4 GW was added in 2024.",
            "EIA",
        ),
        (
            "Nobody in the industry had forecast it, but Wood Mackenzie "
            "reported that U.S. storage grew 10.4 GW in 2024.",
            "Wood Mackenzie",
        ),
    ],
)
def test_an_earlier_denial_does_not_disqualify_a_later_issuer(
    text: str, expected: str
) -> None:
    """The nobody-pronoun scopes the phrase it belongs to, not the whole clause."""
    atom = extract_text_atoms(text)[0]

    assert atom.attribution == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A month is only a date when a date follows it (ND3).
        ("According to March Advisors, 10.4 GW was added in 2024.", "March Advisors"),
        (
            "May Advisors reported that 10.4 GW was added in the United "
            "States in 2024.",
            "May Advisors",
        ),
    ],
)
def test_a_publisher_whose_name_opens_with_a_month_is_still_a_name(
    text: str, expected: str
) -> None:
    """A lone month token may name a firm; "December 2024" is a date."""
    atom = extract_text_atoms(text)[0]

    assert atom.attribution == expected


def test_a_document_date_is_not_the_issuer_the_document_belongs_to() -> None:
    """The run's claim 3, verbatim: the inventory's date is not who stated it.

    The name run reached the verb through the source phrase and captured
    "December 2024 Preliminary Monthly Electric" — a date, not an issuer — so
    the run's third claim was attributed to a date phrase while the publisher
    the page belongs to is EIA. The clause's *geography* is deliberately not
    asserted here: the prepositional locative reads the document's own title
    ("Today in Energy") as a place, which is a separate, pre-existing capture
    that no binding depends on because the dimension check is presence-only.
    """
    (atom,) = extract_text_atoms(
        "An EIA Today in Energy article based on the December 2024 Preliminary "
        "Monthly Electric Generator Inventory stated that U.S. power providers "
        "added 10.3 GW of new battery storage capacity in 2024."
    )

    assert atom.attribution == "EIA"


def test_a_date_phrase_alone_names_no_issuer() -> None:
    """The control: the same inventory with no publisher named names nobody."""
    (atom,) = extract_text_atoms(
        "The December 2024 Preliminary Monthly Electric Generator Inventory "
        "stated that 10.3 GW was added in 2024."
    )

    assert atom.attribution == ""


def test_a_place_is_never_the_issuer_of_its_own_clause() -> None:
    """A locative names where the fact is, not who reported it."""
    (atom,) = extract_atoms(
        _claim("Generators added 4 GW of storage in California in 2024.")
    )

    assert atom.geography == "California"
    assert atom.attribution == ""


# --------------------------------------------------------------------------
# Attribution: a definition names the body whose convention it states
# --------------------------------------------------------------------------


def test_a_definitional_verb_attributes_the_body_that_states_the_convention(
) -> None:
    """The run's own claim 14: "EIA counts …" names EIA as its own subject.

    A definition states the convention a body counts by, and the body is the
    clause's grammatical subject in front of its verb — no reporting verb is
    involved, so the claim recorded no issuer and no methodology target could
    bind it (live cycle 08b9b469).
    """
    (atom,) = extract_text_atoms(
        "EIA counts battery storage projects larger than 1 MW in the electric "
        "power sector when reporting U.S. utility-scale battery storage "
        "capacity."
    )

    assert atom.attribution == "EIA"


def test_a_sentence_initial_participle_is_never_the_issuer() -> None:
    """The run's own claim 12: "Counting …, EIA projected …" is EIA's forecast.

    The phrase states how EIA counted, and the noun "projects" inside it was
    read as a reporting verb, so the clause was attributed to the method's own
    head. The issuer is the subject of the main clause after the comma.
    """
    (atom,) = extract_text_atoms(
        "Counting projects larger than 1 MW in the electric power sector, EIA "
        "projected that U.S. domestic storage capacity would rise from about "
        "28 GW at the end of Q1 2025 to 64.9 GW at the end of 2026."
    )

    assert atom.attribution == "EIA"


def test_a_place_that_counts_is_not_the_question_of_a_convention() -> None:
    """A definition belongs to a body, and a place is not one.

    "Texas counts the most battery additions" ranks Texas; it states no
    convention Texas applies, and a state that leads a count is not the issuer
    of the count.
    """
    (atom,) = extract_text_atoms("Texas counts the most battery additions in 2024.")

    assert atom.attribution == ""


def test_a_pronoun_subject_of_a_definitional_verb_attributes_nothing() -> None:
    """The determiner and pronoun guard holds for a definitional verb too."""
    (atom,) = extract_text_atoms("It counts projects larger than 1 MW.")

    assert atom.attribution == ""


@pytest.mark.parametrize(
    "text",
    [
        # The main clause names a common noun, so the only name-like tokens in
        # the clause are the phrase's own words — and the unit "MW" is one
        # (``_is_acronym``), which the subject-phrase fallback credited.
        (
            "Counting projects larger than 1 MW in the electric power sector, "
            "the agency projected that capacity would rise in 2025."
        ),
        (
            "Excluding projects under 1 MW, the agency reported that capacity "
            "rose in 2025."
        ),
    ],
)
def test_no_word_inside_a_participial_phrase_names_an_issuer(text: str) -> None:
    """The phrase states a method: neither its head nor a unit inside it is a name."""
    (atom,) = extract_text_atoms(text)

    assert atom.attribution == ""


def test_the_reporting_verb_form_still_attributes_the_same_issuer() -> None:
    """The control: reading a definitional verb may not change a reported clause."""
    (atom,) = extract_text_atoms(
        "EIA reported that generators added 10.4 GW of new battery storage "
        "capacity in the United States in 2024."
    )

    assert atom.attribution == "EIA"


# --------------------------------------------------------------------------
# Geography: an alias has to modify the measurand, not merely appear
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Another country's figure, with the alias in an adjunct (review F2).
        "Mexico added 300 MW of battery storage in 2024 using U.S. suppliers.",
        (
            "Canada, unlike its U.S. neighbour, added only 500 MW of battery "
            "storage in 2024."
        ),
        (
            "Germany's battery storage additions trailed U.S. levels in 2024, "
            "reaching 2,000 MW."
        ),
        "Pan-American capacity reached 26 GW in 2024.",
    ],
)
def test_an_alias_that_does_not_modify_the_measurand_is_not_the_geography(
    text: str,
) -> None:
    """An adjectival ``U.S.`` in an adjunct does not move the clause to the US.

    Each of these credited a foreign country's figure to the United States
    obligation that requires ``geography: United States``.
    """
    atom = extract_text_atoms(text)[0]

    assert atom.geography == ""


@pytest.mark.parametrize(
    "text",
    [
        # The same three adjunct clauses with the run's own issuer in front of
        # them: the issuer is the clause's source, never its subject, so the
        # subject of the *reported* clause is what decides (re-review F2).
        (
            "EIA reported that Mexico added 300 MW of battery storage in 2024 "
            "using U.S. suppliers."
        ),
        (
            "EIA reported that Canada, unlike its U.S. neighbour, added only "
            "500 MW of battery storage in 2024."
        ),
        (
            "EIA reported that Germany's battery storage additions trailed "
            "U.S. levels in 2024, reaching 2,000 MW."
        ),
    ],
)
def test_a_reported_clause_is_judged_by_its_own_subject(text: str) -> None:
    """The issuer in front of the verb is not the clause's subject.

    "EIA reported that Mexico added 300 MW … using U.S. suppliers" is a clause
    about Mexico: reading the alias made it the United States and met the
    ``geography: United States`` obligation with a foreign country's figure.
    """
    (atom,) = extract_text_atoms(text)

    assert atom.attribution == "EIA"
    assert atom.geography == ""


@pytest.mark.parametrize(
    "text",
    [
        # The reviewer's probe_regress strings (re-review N1). Each is a fact
        # about the United States with its issuer trailing, so the alias is the
        # clause's own subject and the unit that follows it names nobody.
        "U.S. utilities added 10.4 GW of battery storage in 2024, EIA reported.",
        (
            "The United States added 10.4 GW of battery storage in 2024, "
            "EIA reported."
        ),
        (
            "U.S. developers added 10.4 GW of battery storage in 2024, "
            "EIA reported."
        ),
        "U.S. capacity additions reached 10.4 GW in 2024, EIA reported.",
        (
            "In 2024 cumulative U.S. utility-scale battery storage capacity "
            "reached 26 GW."
        ),
        (
            "U.S. power providers added 10.3 GW of new battery storage "
            "capacity in 2024, EIA said."
        ),
    ],
)
def test_an_alias_the_clause_states_as_its_subject_is_the_geography(
    text: str,
) -> None:
    """A unit or a trailing issuer is not a competing subject.

    Reading the first capitalised token after the alias refused the clause's
    own place whenever that token was the unit ("10.4 GW") or the issuer, so
    clauses that were about the United States stated no geography at all and
    stopped binding the target that requires one.
    """
    (atom,) = extract_text_atoms(text)

    assert atom.geography == "United States"


# --------------------------------------------------------------------------
# Sentences: an abbreviation ends one only when the sentence really ends
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Capacity grew in the U.S. It fell in Canada.", 2),
        ("Built by Acme Co. The plant opened in 2025.", 2),
        ("The permit came from Main St. The county approved it.", 2),
        ("The reading was taken at 5 p.m. Demand fell after that.", 2),
        ("Storage rose, etc. Solar fell over the same period.", 2),
        # Mid-sentence, the same abbreviations are not sentence ends.
        ("Capacity in the U.S. utility-scale fleet grew in 2024.", 1),
        ("Acme Co. reported 10.4 GW of additions in 2024.", 1),
        ("Demand fell at 5 p.m. local time, the operator said.", 1),
        ("The figure is approximate, e.g. about 10.4 GW in 2024.", 1),
        # A sentence cannot be only a determiner and an initialism: the live
        # claim "The U.S. Energy Information Administration reported …" was cut
        # into a junk atom "The U.S" that merged claims about different facts.
        (
            "The U.S. Energy Information Administration reported that "
            "generators added 10.4 GW in 2024.",
            1,
        ),
        ("U.K. regulators approved the plan in 2024.", 1),
    ],
)
def test_an_abbreviation_ends_a_sentence_only_before_a_new_one(
    text: str, expected: int
) -> None:
    """The splitter may not fuse two sentences into one clause (review F3)."""
    assert len(_sentences(text)) == expected


@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        # The same quantity restated in parentheses in another prefix of the
        # same base unit is one measurement, and the first spelling stands.
        (
            "EIA reported that generators added 10.4 GW (10,400 MW) of new "
            "battery storage capacity in the United States in 2024.",
            "10.4",
            "GW",
        ),
        (
            "EIA reported that operators plan to add 19.6 GW (19,600 MW) of "
            "battery storage in the United States in 2025.",
            "19.6",
            "GW",
        ),
        # Two different measurements are still two, and state no value.
        ("The plant stores 10 GW (40 GWh) of capacity in 2024.", "", ""),
        ("Additions reached 18.2 GW in 2025, up from 10.3 GW in 2024.", "", ""),
        ("The fleet held 10.4 GW (26,000 MW) of battery storage in 2024.", "", ""),
    ],
)
def test_a_parenthetical_restatement_is_one_measurement(
    text: str, value: str, unit: str
) -> None:
    """Live defect: "10.4 GW (10,400 MW)" read as two figures, so none."""
    atoms = [atom for atom in extract_text_atoms(text) if atom.value or atom.unit]
    if not value:
        assert atoms == []
        return
    assert [(atom.value, atom.unit) for atom in atoms] == [(value, unit)]


def test_the_run_spelling_keeps_its_value_and_place_across_the_period() -> None:
    """The measured regression: "…in the U.S. Solar added 30,000 MW.".

    Fusing the two sentences made one clause with two measurements, so
    ``_value_and_unit`` read no value at all and the 2024 target lost its
    figure.
    """
    atoms = extract_text_atoms(
        "According to EIA, 10,400 MW of battery storage was added in 2024 in "
        "the U.S. Solar added 30,000 MW."
    )

    assert [atom.value for atom in atoms] == ["10,400", "30,000"]
    assert atoms[0].geography == "United States"


@pytest.mark.parametrize(
    "dimension",
    [
        "measure: stated grid-connection requirement of the classification",
        "measure: rating basis (AC or DC) of the reported capacity figures",
        "measure: inclusion rule and counting treatment for hybrid plants",
        "measure: inclusion or exclusion of behind-the-meter storage in the "
        "reported total",
        # A unit list names the units a convention chooses between, so it asks
        # what the convention is, not how much (integration review P2).
        "measure: rating basis of the reported figures (AC or DC MW)",
        "measure: whether capacity is reported in MW or MWh",
    ],
)
def test_a_qualitative_measure_never_demands_a_numeric_value(
    dimension: str,
) -> None:
    """Four of the run's eleven targets were unanswerable for this reason.

    Each asks what a rule, basis, or treatment *is*. Mapping ``measure:`` to
    the numeric ``value`` dimension made every one of them unmet by prose that
    answers them exactly, because such a clause states no number.
    """
    assert "value" not in checkable_dimensions(dimension)


@pytest.mark.parametrize(
    "dimension",
    [
        "measure: grid-scale battery power capacity added, in MW",
        "measure: projected battery storage capacity additions, in MW",
        "measure: minimum nameplate capacity threshold, in MW",
        # A unit asks for a number even beside a convention noun: the live
        # threshold target was read as qualitative and bound forecast levels.
        "measure: capacity threshold applied for the utility-scale "
        "classification, in megawatts",
        "measure: annual ridership",
        "value",
        "capacity",
    ],
)
def test_a_quantitative_measure_still_demands_a_numeric_value(
    dimension: str,
) -> None:
    """The control: a measure that names a quantity still needs the number."""
    assert checkable_dimensions(dimension) == ("value",)


_AUDIT_QUESTION = (
    "How much grid-scale battery storage capacity was added in the United "
    "States in 2024, and what do the latest forecasts project for 2025?"
)


def test_a_qualitative_obligation_is_answered_by_prose_that_states_it() -> None:
    (atom,) = extract_text_atoms(
        "According to EIA, behind-the-meter storage is excluded from the "
        "reported utility-scale capacity total in the United States in 2024."
    )

    assert atom_answers_dimensions(
        atom,
        [
            "measure: inclusion or exclusion of behind-the-meter storage in "
            "the reported total"
        ],
        question=_AUDIT_QUESTION,
    )


def test_a_qualitative_obligation_is_not_answered_by_a_number_alone() -> None:
    """The control: a bare quantity names no subject and settles no rule."""
    (atom,) = extract_text_atoms("10 GW was added in 2024.")

    assert not atom_answers_dimensions(
        atom,
        [
            "measure: inclusion or exclusion of behind-the-meter storage in "
            "the reported total"
        ],
        question=_AUDIT_QUESTION,
    )


def test_the_runs_four_unbindable_rule_targets_bind_an_ideal_claim() -> None:
    """C10's first half: the run's own targets, against claims that answer them.

    The targets are the run's, verbatim (audit C5); the claims are the plainest
    sentence that states each one. Before this change all four failed on
    ``value`` and no claim could ever have been bound to them.
    """
    targets = [
        (
            EvidenceTarget(
                target_id="topic-03-target-02",
                coverage_id="topic-03",
                question=(
                    "What grid-connection requirement does the classification "
                    "state?"
                ),
                required_dimensions=[
                    "measure: stated grid-connection requirement of the "
                    "classification",
                    "period: the classification in force for 2024 and 2025 "
                    "reporting",
                    "geography: United States",
                    "source: the publisher's definition or methodology page "
                    "for the classification",
                ],
                required=True,
                critical=True,
                support_policy="primary_attribution",
            ),
            "According to EIA, the utility-scale classification in the United "
            "States in 2024 requires a grid connection at the plant.",
        ),
        (
            EvidenceTarget(
                target_id="topic-04-target-01",
                coverage_id="topic-04",
                question="On which rating basis are the capacity figures stated?",
                required_dimensions=[
                    "measure: rating basis (AC or DC) of the reported capacity "
                    "figures",
                    "period: the 2024 addition and the 2025 forecast as "
                    "published",
                    "geography: United States",
                    "source: the publisher's methodology or convention "
                    "documentation for its capacity figures",
                ],
                required=True,
                critical=True,
                support_policy="primary_attribution",
            ),
            "According to EIA, the reported capacity figures in the United "
            "States for 2024 are stated on an AC rating basis.",
        ),
        (
            EvidenceTarget(
                target_id="topic-04-target-02",
                coverage_id="topic-04",
                question="How are hybrid co-located plants counted?",
                required_dimensions=[
                    "measure: inclusion rule and counting treatment for "
                    "hybrid/co-located battery plants",
                    "period: the 2024 addition and the 2025 forecast as "
                    "published",
                    "geography: United States",
                    "source: the publisher's methodology note describing "
                    "plant-level counting",
                ],
                required=True,
                critical=False,
                support_policy="primary_attribution",
            ),
            "According to EIA, hybrid co-located battery plants in the United "
            "States are counted at the plant level in the 2024 reported "
            "capacity.",
        ),
        (
            EvidenceTarget(
                target_id="topic-04-target-03",
                coverage_id="topic-04",
                question=(
                    "Is behind-the-meter storage inside the reported total?"
                ),
                required_dimensions=[
                    "measure: inclusion or exclusion of behind-the-meter "
                    "storage in the reported total",
                    "period: the 2024 addition and the 2025 forecast as "
                    "published",
                    "geography: United States",
                    "source: the publisher's definitions page or methodology "
                    "note",
                ],
                required=True,
                critical=False,
                support_policy="primary_attribution",
            ),
            "According to EIA, behind-the-meter storage is excluded from the "
            "reported utility-scale capacity total in the United States for "
            "2024.",
        ),
    ]

    for target, text in targets:
        atoms = extract_text_atoms(text, claim_id="ideal")
        assert any(
            atom_answers_target(atom, target, question=_AUDIT_QUESTION)
            for atom in atoms
        ), target.target_id


def _battery_binding_target(
    *,
    year: int = 2024,
    unit: str = "MW",
    publisher: str = "national generator-inventory dataset",
) -> EvidenceTarget:
    return EvidenceTarget(
        target_id="battery-additions",
        coverage_id="battery",
        question=(
            f"How much U.S. grid-scale battery storage capacity was added in "
            f"{year}, in {unit}?"
        ),
        required_dimensions=[
            f"measure: grid-scale battery storage capacity added, in {unit}",
            f"period: calendar year {year}",
            "geography: United States",
            f"source: {publisher}",
        ],
        required=True,
        critical=True,
        support_policy="primary_attribution",
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "EIA reported that generators in the United States added 10.4 GW "
            "of grid-scale battery storage capacity in 2024.",
            True,
        ),
        (
            "An EIA Today in Energy article based on the December 2024 "
            "Preliminary Monthly Electric Generator Inventory stated that "
            "U.S. power providers added 10.3 GW of new battery storage "
            "capacity in 2024.",
            True,
        ),
        (
            "In the United States, EIA reported that generators added 10.4 GW "
            "of grid-scale battery storage capacity in 2024.",
            True,
        ),
        (
            "In 2024, in the United States, EIA reported that generators added "
            "10.4 GW of grid-scale battery storage capacity.",
            True,
        ),
        (
            "EIA reported that developers commissioned 10.4 GW of grid-scale "
            "battery storage capacity in the United States in 2024.",
            True,
        ),
        (
            "EIA forecast that 18.2 GW of grid-scale battery storage capacity "
            "would be added in the United States in 2025.",
            False,
        ),
        (
            "EIA reported that generators in the United States added 10.4 GWh "
            "of grid-scale battery storage energy capacity in 2024.",
            False,
        ),
        (
            "EIA reported that developers in Texas added 7 GW of grid-scale "
            "battery storage capacity in 2024.",
            False,
        ),
        (
            "EIA reported that 64 GW of all generating capacity was added in "
            "the United States in 2024.",
            False,
        ),
        (
            "EIA reported that cumulative grid-scale battery storage capacity "
            "reached 26 GW in the United States in 2024.",
            False,
        ),
        (
            "The U.S. Energy Information Administration reported that "
            "generators added 10.4 GW (10,400 MW) of new battery storage "
            "capacity in the United States in 2024.",
            True,
        ),
    ],
)
def test_a_target_binds_only_its_own_measure_period_and_place(
    text: str, expected: bool
) -> None:
    target = _battery_binding_target()
    atoms = extract_text_atoms(text)
    assert (
        any(
            atom_answers_target(atom, target, question=target.question)
            for atom in atoms
        )
        is expected
    )


def test_a_government_figure_does_not_answer_an_independent_publisher_target() -> None:
    target = _battery_binding_target(
        publisher="non-governmental market-research publication distinct from the government series"
    )
    government = extract_text_atoms(
        "EIA reported that generators in the United States added 10.4 GW "
        "of grid-scale battery storage capacity in 2024."
    )
    independent = extract_text_atoms(
        "Wood Mackenzie reported that generators in the United States added "
        "10.4 GW of grid-scale battery storage capacity in 2024."
    )
    assert not any(
        atom_answers_target(atom, target, question=target.question)
        for atom in government
    )
    industry_target = _battery_binding_target(
        publisher=(
            "an industry or market-research forecast publication distinct "
            "from the government outlook"
        )
    )
    assert not any(
        atom_answers_target(atom, industry_target, question=industry_target.question)
        for atom in government
    )
    assert any(
        atom_answers_target(atom, target, question=target.question)
        for atom in independent
    )
    federal_target = _battery_binding_target(publisher="federal agency")
    federal = extract_text_atoms(
        "EPA reported that generators in the United States added 10.4 GW "
        "of grid-scale battery storage capacity in 2024."
    )
    assert any(
        atom_answers_target(atom, federal_target, question=federal_target.question)
        for atom in federal
    )
    assert not any(
        atom_answers_target(atom, target, question=target.question) for atom in federal
    )
    regulatory = extract_text_atoms(
        "FERC reported that generators in the United States added 10.4 GW "
        "of grid-scale battery storage capacity in 2024."
    )
    assert any(
        atom_answers_target(atom, federal_target, question=federal_target.question)
        for atom in regulatory
    )
    assert not any(
        atom_answers_target(atom, target, question=target.question)
        for atom in regulatory
    )


@pytest.mark.parametrize(
    ("publisher", "text", "binds"),
    [
        # An industry-class requirement rejects a government attribution but
        # does not demand a hard-coded list of trackers (integration review P2).
        (
            "the market monitor's latest published outlook",
            "Solar Energy Industries Association reported that generators in "
            "the United States added 10.4 GW of grid-scale battery storage "
            "capacity in 2024.",
            True,
        ),
        (
            "the market monitor's latest published outlook",
            "EIA reported that generators in the United States added 10.4 GW "
            "of grid-scale battery storage capacity in 2024.",
            False,
        ),
        # A requirement that names the agency in its own words is a government
        # requirement, even when it also says "industry".
        (
            "EIA's survey of the electric power industry",
            "EIA reported that generators in the United States added 10.4 GW "
            "of grid-scale battery storage capacity in 2024.",
            True,
        ),
        (
            "the Energy Information Administration's industry data",
            "EIA reported that generators in the United States added 10.4 GW "
            "of grid-scale battery storage capacity in 2024.",
            True,
        ),
    ],
)
def test_a_source_requirement_turns_on_the_publisher_class_it_names(
    publisher: str, text: str, binds: bool
) -> None:
    target = _battery_binding_target(publisher=publisher)
    assert (
        any(
            atom_answers_target(atom, target, question=target.question)
            for atom in extract_text_atoms(text)
        )
        is binds
    )


_LIVE_EVIDENCE_PERIOD = (
    "evidence period: the period the question names (2024, 2025); answer it "
    "as of 2025-12-31 and never substitute today's figures"
)
_LIVE_ANSWER_FORM = (
    "answer form: the specific fact asked for, with its value, unit, and the "
    "date the value applies to"
)


def _live_target(
    target_id: str,
    question: str,
    measure: str,
    period: str,
    source: str,
    support_policy: str,
) -> EvidenceTarget:
    return EvidenceTarget(
        target_id=target_id,
        coverage_id=target_id.rsplit("-target-", 1)[0],
        question=question,
        required_dimensions=[
            f"measure: {measure}",
            f"period: {period}",
            "geography: United States",
            f"source: {source}",
            _LIVE_ANSWER_FORM,
            _LIVE_EVIDENCE_PERIOD,
        ],
        required=True,
        critical=support_policy == "independent_pair",
        support_policy=support_policy,
    )


# The live plan's figure and methodology targets, verbatim from the traced run
# whose 10.4 GW and 19.6 GW claims bound every methodology target and the
# market monitor's outlook as well as their own figure (review rank 6).
_LIVE_TARGETS = (
    _live_target(
        "topic-01-target-01",
        "How much battery storage capacity, in megawatts, was added at grid "
        "scale in the United States in 2024?",
        "battery storage capacity added, in megawatts",
        "calendar year 2024",
        "the federal energy statistical agency's published capacity data and, "
        "independently, an industry energy-storage market tracker",
        "independent_pair",
    ),
    _live_target(
        "topic-02-target-01",
        "What 2025 addition of utility-scale battery storage capacity in the "
        "United States, in megawatts, does the federal energy statistical "
        "agency's most recently published outlook project?",
        "projected battery storage capacity additions, in megawatts",
        "forecast year 2025",
        "the forecasting agency's latest published outlook",
        "independent_pair",
    ),
    _live_target(
        "topic-02-target-02",
        "What 2025 addition of grid-scale battery storage capacity in the "
        "United States, in megawatts, does the industry energy-storage market "
        "monitor's most recently published outlook project?",
        "projected battery storage capacity additions, in megawatts",
        "forecast year 2025",
        "the market monitor's latest published outlook",
        "independent_pair",
    ),
    _live_target(
        "topic-03-target-01",
        "What capacity threshold, in megawatts, does the federal energy "
        "statistical agency use to classify battery storage as utility-scale "
        "in its United States capacity data?",
        "capacity threshold applied for the utility-scale classification, in "
        "megawatts",
        "the classification in force for the 2024 and 2025 data years",
        "the agency's published methodology documentation",
        "primary_attribution",
    ),
    _live_target(
        "topic-03-target-02",
        "Which facility types does the federal energy statistical agency "
        "include when it counts utility-scale battery storage capacity "
        "additions in the United States?",
        "facility types included in the utility-scale battery storage "
        "capacity count",
        "the 2024 and 2025 data years",
        "the agency's published methodology documentation",
        "primary_attribution",
    ),
    _live_target(
        "topic-03-target-03",
        "Which storage segment does the industry energy-storage market monitor "
        "count in its grid-scale United States additions figures?",
        "storage segment covered by the grid-scale additions figures",
        "the 2024 and 2025 data years",
        "the market monitor's published methodology documentation",
        "primary_attribution",
    ),
)
_LIVE_TARGET_IDS = frozenset(target.target_id for target in _LIVE_TARGETS)


@pytest.mark.parametrize(
    ("text", "binds", "refuses"),
    [
        # The agency's 2024 figure answers the 2024 figure target, whose pair
        # source names the agency, and no definitional target: it states no
        # threshold, facility type, or segment.
        (
            "The U.S. Energy Information Administration reported that "
            "generators added 10.4 GW (10,400 MW) of new battery storage "
            "capacity in the United States in 2024.",
            {"topic-01-target-01"},
            _LIVE_TARGET_IDS - {"topic-01-target-01"},
        ),
        # The agency's projection answers the agency outlook, not the market
        # monitor's outlook of the same measure.
        (
            "The U.S. Energy Information Administration projected that U.S. "
            "battery storage capacity growth could set a record in 2025, with "
            "operators reporting plans to add 19.6 GW (19,600 MW) of "
            "utility-scale battery storage capacity in the United States.",
            {"topic-02-target-01"},
            _LIVE_TARGET_IDS - {"topic-02-target-01"},
        ),
        # A cumulative level is no addition, and a forecast level names no
        # threshold: it was bound to two methodology targets live.
        (
            "EIA's Short Term Energy Outlook projected that U.S. utility-scale "
            "battery storage capacity will reach nearly 65 GW in 2025.",
            set(),
            _LIVE_TARGET_IDS,
        ),
        # The threshold target asks for a number in megawatts, and a clause
        # that states the threshold with its number answers it.
        (
            "According to EIA, battery storage projects larger than 1 MW in "
            "the electric power sector are counted in U.S. utility-scale "
            "battery storage capacity for 2024.",
            {"topic-03-target-01"},
            set(),
        ),
        # The pair source names the industry tracker too, so the tracker's own
        # 2024 figure answers its half; a 2024 figure answers no 2025 outlook.
        (
            "Wood Mackenzie reported that U.S. developers added 12,314 MW of "
            "battery storage capacity in the United States in 2024.",
            {"topic-01-target-01"},
            {"topic-02-target-01", "topic-02-target-02"},
        ),
    ],
)
def test_a_live_claim_binds_only_the_targets_it_answers(
    text: str, binds: set[str], refuses: set[str]
) -> None:
    atoms = extract_text_atoms(text)
    bound = {
        target.target_id
        for target in _LIVE_TARGETS
        if any(
            atom_answers_target(atom, target, question=target.question)
            for atom in atoms
        )
    }

    assert binds <= bound
    assert not bound & refuses


@pytest.mark.parametrize(
    "measure",
    [
        # The planner writes the measure detail freely, so a cue noun in a
        # qualifier may not constrain the clause: each of these wordings must
        # still accept the headline 10.4 GW claim (integration review P1).
        "battery storage capacity added at utility-scale facilities, in "
        "megawatts",
        "battery storage capacity added across all facility types, in "
        "megawatts",
        "battery storage capacity added on a nameplate basis, in megawatts",
        "battery storage capacity added in the utility-scale segment, in "
        "megawatts",
        "battery storage capacity added above the utility-scale threshold, in "
        "megawatts",
        "battery storage capacity added below the utility-scale threshold, in "
        "megawatts",
        "battery storage capacity added exceeding the utility-scale threshold, "
        "in megawatts",
    ],
)
def test_a_qualifier_word_in_a_measure_never_constrains_the_clause(
    measure: str,
) -> None:
    target = _live_target(
        "topic-01-target-01",
        "How much battery storage capacity, in megawatts, was added at grid "
        "scale in the United States in 2024?",
        measure,
        "calendar year 2024",
        "the federal energy statistical agency's published capacity data and, "
        "independently, an industry energy-storage market tracker",
        "independent_pair",
    )
    claim = (
        "The U.S. Energy Information Administration reported that generators "
        "added 10.4 GW (10,400 MW) of new battery storage capacity in the "
        "United States in 2024."
    )

    assert any(
        atom_answers_target(atom, target, question=target.question)
        for atom in extract_text_atoms(claim)
    )

@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        (
            "The Acme widget adoption rate in the United States was "
            "40 percent in 2024.",
            False,
        ),
        (
            "The Acme widget adoption rate increased via subsidies "
            "in the United States in 2024.",
            True,
        ),
    ],
)
def test_outcome_alone_cannot_answer_a_mechanism_obligation(
    claim: str, expected: bool
) -> None:
    dimension = "measure: the mechanism behind the change"
    assert (
        any(
            atom_answers_dimensions(
                atom, [dimension], question="Why did Acme widget adoption increase?"
            )
            for atom in extract_text_atoms(claim)
        )
        is expected
    )
