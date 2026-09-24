"""Atomic claim identity: one proposition, one cluster, one piece of work.

A research pass writes prose, and prose is a bad key. The ledger defect this
module removes is that the queue-growth, report-cutoff, and PJM-cycle
statements were repeated under different textual identities, so one fact
became several rows and a five-claim prefix starved every topic behind the
first one.

Three pure ideas fix that, and nothing here performs I/O, reads a clock, or
calls a provider on its own:

* **an atom** — :func:`extract_atoms` splits a claim's prose into one clause
  per assertion, keeping every qualifier and every evidence id it was given;
* **a compatibility check** — :func:`atomic_compatible` is *necessary, not
  sufficient*: it refuses any pair differing in a year, period, geography,
  population, unit, denominator, attribution, forecast status, or negation,
  and its refusals are the only thing standing between a paraphrase and a
  false merge. Unit equivalence therefore needs an explicit checked
  normalization, never text matching, and 10 GW is *not* 10000 MW here;
* **a stable cluster id** — minted once from the cluster's first canonical
  anchor and persisted, so refinement adds evidence without minting a second
  identity.

The provider's only job is the part local code cannot do: proposing which
atoms *might* state the same thing. Every proposal is validated here before
anything merges.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal, InvalidOperation

from pydantic import Field

from deep_research.agents.base import AgentCompleter
from deep_research.agents.identity import claim_cluster_id
from deep_research.agents.prompts import (
    CLAIM_EQUIVALENCE_INSTRUCTION,
    CLAIM_EQUIVALENCE_SYSTEM_PROMPT,
    render_structured_reply_format,
)
from deep_research.providers import ChatMessage, ProviderError
from deep_research.utils.types import (
    AtomicProposition,
    Claim,
    ClaimCluster,
    ContractModel,
    EvidencePassage,
    EvidenceTarget,
    EvidenceUnit,
    ResearchState,
    SubjectState,
    measure_head,
    names_a_definition,
    qualifier_matches_requirement,
)

__all__ = [
    "LEGACY_COVERAGE_DIMENSION",
    "MAX_EQUIVALENCE_ATOMS",
    "METADATA_DIMENSIONS",
    "AtomicPairDraft",
    "ClaimConsolidation",
    "ClaimEquivalenceDraft",
    "atom_answers_dimensions",
    "atom_answers_target",
    "atom_satisfies_policy",
    "atomic_compatible",
    "checkable_dimensions",
    "claim_cluster_id",
    "claim_meets_support_policy",
    "cluster_for_atom",
    "consolidate_claims",
    "critical_target_ids",
    "dimension_is_answered",
    "equivalence_messages",
    "equivalence_strength",
    "extract_atoms",
    "extract_text_atoms",
    "merge_claim_cluster_registry",
    "merge_claim_clusters",
    "metadata_dimension_asked_for",
    "oldest_first",
    "resolved_verdict",
    "resolved_verdict_and_status",
    "reverification_cache_key",
    "select_claim_batch",
    "select_claim_batch_indices",
    "stated_dimensions",
    "target_order_for",
]

# How many atoms one equivalence request may list, and how many pairs it is
# asked for. One bounded call, whatever the pass extracted.
MAX_EQUIVALENCE_ATOMS = 40
MAX_EQUIVALENCE_PAIRS = 40

# The obligation a plan written before the target inventory states: one per
# sub-topic, named by its coverage id. The dimension text is deliberately one
# this contract cannot check, because such a plan declared no dimensions to
# check — the claim is credited with the topic's one obligation without a
# dimension test the model never asked for.
LEGACY_COVERAGE_DIMENSION = "coverage: the plan predates evidence targets"

# Which agent's span records the consolidation request. The Fact Checker is
# the claim agent; consolidation is its second phase, not a seventh agent.
CONSOLIDATION_AGENT_NAME = "fact_checker"

# One example, because the empty-list case is the opposite end of a scale
# rather than a second shape, and the response contract already states it.
_CLAIM_EQUIVALENCE_REPLY_EXAMPLES = (
    (
        "Example input: two atomic claims that state one measured fact in "
        "different words.",
        '{"pairs":[{"left":1,"right":2}]}',
    ),
)


def _canonical(value: str) -> str:
    """Fold the formatting of one dimension, and nothing else."""
    return " ".join(value.split()).casefold()


def _canonical_number(value: str) -> str:
    """Fold how a number is *written* without changing which number it is.

    Thousands separators, a trailing ``.0``, and a leading zero are
    typography: "10,000", "10000.0" and "10000" are one number written three
    ways. Nothing is ever converted — a number the document states is compared
    to the same number, never to an equivalent one.
    """
    digits = value.replace(",", "").replace("_", "").strip()
    if not digits:
        return ""
    try:
        number = Decimal(digits)
    except InvalidOperation:
        return _canonical(value)
    if number == number.to_integral_value():
        return str(number.quantize(Decimal(1)))
    return format(number.normalize(), "f")


# The unit spellings this contract recognizes, mapped to the case-folded form
# they are all written as. Only *spelling* is normalized here: a word maps to
# the symbol it is written as, and no unit ever maps onto another unit.
# Converting 10 GW to 10000 MW is a measurement the document did not state, so
# it is exactly what this boundary must not do.
_UNIT_CANONICAL_FORMS = {
    "percent": "%",
    "per cent": "%",
    "pct": "%",
    "percentage point": "pp",
    "percentage points": "pp",
    "gigawatt": "gw",
    "gigawatts": "gw",
    "megawatt": "mw",
    "megawatts": "mw",
    "kilowatt": "kw",
    "kilowatts": "kw",
    "terawatt": "tw",
    "terawatts": "tw",
    "gigawatt hour": "gwh",
    "gigawatt hours": "gwh",
    "megawatt hour": "mwh",
    "megawatt hours": "mwh",
    "kilowatt hour": "kwh",
    "kilowatt hours": "kwh",
    "terawatt hour": "twh",
    "terawatt hours": "twh",
    "tonne": "t",
    "tonnes": "t",
    "tons": "t",
    "thousand": "k",
    "million": "m",
    "billion": "bn",
    "trillion": "tn",
}


def _canonical_unit(unit: str) -> str:
    """Return the case-folded spelling ``unit`` is written as.

    Deliberately *not* a conversion table. Two units that differ in scale stay
    different, because this check exists to refuse a merge it cannot prove,
    and proving 10 GW equals 10000 MW needs arithmetic the document never
    showed.
    """
    folded = _canonical(unit)
    return _UNIT_CANONICAL_FORMS.get(folded, folded)


# ``subject`` and ``predicate`` are compared like every other dimension here:
# two clauses that state no subject agree — both are unknown — while a clause
# that names one and a clause that names another disagree, and so do a clause
# that names one and a clause that names none. An underivable qualifier is not
# an escape hatch: if one side could not derive a subject and the other names a
# different one, the merge is unsound and the pair is refused.
_COMPARED_DIMENSIONS = (
    "subject",
    "predicate",
    "observation_period",
    "geography",
    "population",
    "denominator",
    "attribution",
    "forecast_status",
    "comparator",
    "change_kind",
)

# The two dimensions that describe *how* a clause is written rather than what
# it measures. A pair that states neither is not thereby one assertion, which
# is what ``equivalence_strength`` reports as ``uncertain``.
_NON_CHECKABLE_DIMENSIONS = frozenset({"subject", "predicate"})

# Every dimension an atom can be asked to state, by the name a plan or a
# question uses for it.
_DIMENSION_NAMES = frozenset(
    {
        *_COMPARED_DIMENSIONS,
        "value",
        "unit",
        "negated",
        "assertion",
        "publication_date",
        "data_period",
        "forecast_horizon",
        "effective_date",
        "retrieval_date",
        "generation_date",
    }
)

# The dimension names that are fields of ``AtomicProposition`` itself, which is
# what a prompt can print.
_RENDERED_DIMENSIONS = frozenset(
    {
        *_COMPARED_DIMENSIONS,
        "value",
        "unit",
    }
)

_NUMBER_TOKEN = re.compile(r"(?<![\w.])(?P<number>\d[\d,]*(?:\.\d+)?)")
_FOLLOWER_TOKEN = re.compile(r"\s*(?P<word>[A-Za-z%][A-Za-z%/]*)")


def _stated_numbers(proposition: AtomicProposition) -> tuple[str, ...]:
    """Every number the proposition writes, canonicalized, as a sorted set.

    Taken from the clause *and* the stated value, because either may carry a
    number the other does not.
    """
    written = " ".join((proposition.value, proposition.text))
    return tuple(
        sorted(
            {
                _canonical_number(match.group("number"))
                for match in _NUMBER_TOKEN.finditer(written)
            }
        )
    )


def _accounted_numbers(proposition: AtomicProposition) -> set[str]:
    """The numbers in the clause that one of its own dimensions explains.

    A number is accounted for when it *is* the stated value with the stated
    unit beside it, or when it is part of the stated observation period.
    Anything else — a second figure, a scale word this contract has no unit
    for, a number whose neighbour contradicts the stated unit — is residue.
    """
    accounted: set[str] = set()
    value = _canonical_number(proposition.value)
    unit = _canonical_unit(proposition.unit)
    period_parts = {
        _canonical_number(part)
        for part in re.findall(r"\d+", proposition.observation_period)
    }
    for match in _NUMBER_TOKEN.finditer(proposition.text):
        number = _canonical_number(match.group("number"))
        follower = _FOLLOWER_TOKEN.match(proposition.text, match.end())
        word = follower.group("word") if follower is not None else ""
        if value and number == value and _canonical_unit(word) == unit:
            accounted.add(number)
        elif number in period_parts:
            accounted.add(number)
    return accounted


def _unaccounted_numbers(proposition: AtomicProposition) -> set[str]:
    """The numbers this contract cannot show belong to a stated dimension."""
    return set(_stated_numbers(proposition)) - _accounted_numbers(proposition)


def atomic_compatible(
    a: AtomicProposition, b: AtomicProposition
) -> bool:
    """True only when nothing this contract can check keeps ``a`` and ``b`` apart.

    **Necessary, not sufficient.** A ``True`` is permission to consider the two
    the same assertion, never proof that they are: the caller still has to
    have a reason to merge, and that reason is the provider's proposal.

    Every refusal is one of two kinds. A *dimension* refusal is a stated
    difference — a year, an observation period, a geography, a population, a
    capacity or percentage unit, a percentage denominator, an attribution, a
    forecast status, or a negation. A *residue* refusal is the absence of
    proof: when the clause writes a number no stated dimension accounts for,
    this contract cannot show the two clauses assert the same thing, so only a
    formatting-identical text may merge.

    A *derivation* refusal comes first, because it is about the derivation
    rather than about what the clauses say: a clause whose entity position this
    contract could not resolve (see ``AtomicProposition.subject_state``) is
    refused against everything, including another clause it could not resolve.
    Two underivable entities are not two mentions of one entity, and reading
    the two empty subjects as agreement settled claims that named different
    operators, counties, and grids.
    """
    if (
        a.subject_state == _SUBJECT_UNRESOLVED
        or b.subject_state == _SUBJECT_UNRESOLVED
    ):
        return False
    if a.negated != b.negated:
        return False
    if _canonical_number(a.value) != _canonical_number(b.value):
        return False
    if _canonical_unit(a.unit) != _canonical_unit(b.unit):
        return False
    for name in _COMPARED_DIMENSIONS:
        if _canonical(getattr(a, name)) != _canonical(getattr(b, name)):
            return False
    # The measurand a quantity names is compared when both state one. "10 GW of
    # storage" is not "10 GW of solar", and dropping the quantity's noun from
    # the entity run (so the locative entity can be read) may not lose that
    # difference. A one-sided measurand is not a difference: the same assertion
    # is written with the noun named and left implied.
    if (
        _canonical(a.quantity_noun)
        and _canonical(b.quantity_noun)
        and _canonical(a.quantity_noun) != _canonical(b.quantity_noun)
    ):
        return False
    if _stated_numbers(a) != _stated_numbers(b):
        return False
    if _unaccounted_numbers(a) or _unaccounted_numbers(b):
        return _canonical(a.text) == _canonical(b.text)
    return True


def _claim_citations(claim: Claim) -> list[str]:
    """Every citation one adjudicated claim carries, sorted and distinct."""
    return sorted(
        set(claim.source_urls)
        | {passage.source_url for passage in claim.verification_evidence}
    )


def cluster_for_atom(
    atom: AtomicProposition,
    *,
    claim_id: str = "",
    claim: Claim | None = None,
    status: str = "canonical",
    created_seq: int = 0,
) -> ClaimCluster:
    """Mint the cluster one atom anchors, with everything the atom carries.

    ``claim`` supplies the provenance the cluster persists — its citations,
    its passages, its verdict with the evidence that recorded it, and what it
    consumed. A later pass reconstructs all of that from the cluster, so only
    the run that produced the claim has to hand it over.
    """
    members = set(atom.member_claim_ids)
    if claim_id:
        members.add(claim_id)
    elif atom.parent_claim_id:
        members.add(atom.parent_claim_id)
    verdict_evidence: dict[str, list[str]] = {}
    verdict_status: dict[str, str] = {}
    if claim is not None:
        verdict_evidence[claim.verdict] = _claim_citations(claim)
        if claim.evidence_status:
            verdict_status[claim.verdict] = claim.evidence_status
    return ClaimCluster(
        cluster_id=claim_cluster_id(atom),
        proposition=atom,
        created_seq=created_seq,
        evidence_ids=sorted(set(atom.evidence_ids)),
        member_claim_ids=sorted(members),
        target_ids=sorted(set(atom.target_ids)),
        source_urls=(
            list(claim.source_urls) if claim is not None else []
        ),
        verification_evidence=(
            list(claim.verification_evidence) if claim is not None else []
        ),
        verdicts=sorted(verdict_evidence),  # type: ignore[arg-type]
        verdict_evidence=verdict_evidence,
        verdict_evidence_status=verdict_status,
        confidence=claim.confidence if claim is not None else None,
        insufficient_reason=(
            claim.insufficient_reason if claim is not None else None
        ),
        consumed_finding_fingerprints=(
            list(claim.consumed_finding_fingerprints)
            if claim is not None
            else []
        ),
        consumed_coverage_ids=(
            list(claim.consumed_coverage_ids) if claim is not None else []
        ),
        status=status,  # type: ignore[arg-type]
    )


def _older_seq(first: int, second: int) -> int:
    """The surviving creation sequence of a merge.

    Zero means "nobody stamped this cluster", which is not a claim to being the
    oldest. Taking a plain minimum would let such a cluster drag a stamped one
    to zero and, with two of them, hand the identity to whichever the caller
    listed first — so an unstamped sequence is ignored while a stamped one
    exists, and is only the answer when neither side has one.
    """
    stamped = [value for value in (first, second) if value > 0]
    return min(stamped) if stamped else 0


def merge_claim_clusters(
    existing: ClaimCluster, incoming: ClaimCluster
) -> ClaimCluster:
    """Fold ``incoming`` into ``existing``, keeping the oldest stable identity.

    ``existing`` is the older cluster by construction — the caller folds in
    oldest-first order — so its ``cluster_id`` is the survivor and
    ``incoming``'s becomes an alias. Its proposition is the survivor's too:
    the anchor is what the identity was minted from, and letting a later
    paraphrase rewrite it would drift the cluster away from its own id.

    Every unioned field is sorted or first-seen ordered, so the result is a
    deterministic function of the two inputs and two passes that saw the same
    atoms in a different order agree.
    """
    aliases = (
        set(existing.cluster_aliases) | set(incoming.cluster_aliases)
    ) - {existing.cluster_id}
    if incoming.cluster_id != existing.cluster_id:
        aliases.add(incoming.cluster_id)
    confidences = [
        value
        for value in (existing.confidence, incoming.confidence)
        if value is not None
    ]
    return existing.model_copy(
        update={
            "created_seq": _older_seq(
                existing.created_seq, incoming.created_seq
            ),
            "evidence_ids": sorted(
                set(existing.evidence_ids) | set(incoming.evidence_ids)
            ),
            "member_claim_ids": sorted(
                set(existing.member_claim_ids) | set(incoming.member_claim_ids)
            ),
            "target_ids": sorted(
                set(existing.target_ids) | set(incoming.target_ids)
            ),
            "cluster_aliases": sorted(aliases),
            "source_urls": sorted(
                set(existing.source_urls) | set(incoming.source_urls)
            ),
            "verification_evidence": _union_passages(
                existing.verification_evidence, incoming.verification_evidence
            ),
            "verdicts": sorted(set(existing.verdicts) | set(incoming.verdicts)),
            "verdict_evidence": _union_verdict_evidence(
                existing.verdict_evidence, incoming.verdict_evidence
            ),
            "verdict_evidence_status": _union_verdict_status(
                existing.verdict_evidence_status,
                incoming.verdict_evidence_status,
            ),
            "confidence": min(confidences) if confidences else None,
            "insufficient_reason": (
                existing.insufficient_reason or incoming.insufficient_reason
            ),
            "consumed_finding_fingerprints": _union(
                [
                    *existing.consumed_finding_fingerprints,
                    *incoming.consumed_finding_fingerprints,
                ]
            ),
            "consumed_coverage_ids": _union(
                [
                    *existing.consumed_coverage_ids,
                    *incoming.consumed_coverage_ids,
                ]
            ),
            "diagnostics": sorted(
                set(existing.diagnostics) | set(incoming.diagnostics)
            ),
        }
    )


def _union_verdict_status(
    first: Mapping[str, str],
    second: Mapping[str, str],
) -> dict[str, str]:
    """Per-verdict evidence badges, unioned under every verdict either recorded.

    A verdict keeps the strongest badge recorded for it: ``verified_pair`` is
    the only badge that may accompany ``verified``, and a member that recorded
    a weaker badge for the same verdict cannot take it away.
    """
    union: dict[str, str] = {}
    for source in (first, second):
        for verdict, status in source.items():
            current = union.get(verdict)
            if current is None or (
                status == "verified_pair" and current != "verified_pair"
            ):
                union[verdict] = status
    return union


def _union_verdict_evidence(
    first: Mapping[str, Sequence[str]],
    second: Mapping[str, Sequence[str]],
) -> dict[str, list[str]]:
    """Per-verdict citation sets, unioned under every verdict either recorded."""
    union: dict[str, list[str]] = {}
    for source in (first, second):
        for verdict, urls in source.items():
            union[verdict] = sorted({*union.get(verdict, []), *urls})
    return union


def oldest_first(clusters: Sequence[ClaimCluster]) -> list[ClaimCluster]:
    """Stored clusters in the order they were first minted.

    ``created_seq`` is the authority, so the surviving identity does not depend
    on the order a caller lists its clusters in. An unstamped sequence (zero —
    a cluster built by hand, or written before the field existed) is the
    *lowest*-priority answer, never a winning one. The cluster id is the
    tie-break for peers at one sequence, stamped or not: two clusters minted in
    the same pass share a sequence, and ordering them by registry position would
    hand the identity to whichever the caller happened to list first. Consolidation
    mints a real sequence for every cluster it builds, so in production the
    unstamped fallback is never reached.
    """
    return [
        cluster
        for _, cluster in sorted(
            enumerate(clusters),
            key=lambda pair: (
                pair[1].created_seq == 0,
                pair[1].created_seq,
                pair[1].cluster_id,
                pair[0],
            ),
        )
    ]


def merge_claim_cluster_registry(
    previous: Mapping[str, ClaimCluster],
    current: Mapping[str, ClaimCluster],
) -> dict[str, ClaimCluster]:
    """Fold cluster snapshots in, keyed by the identity they were minted from.

    A cluster *grows* — evidence, members, verdicts and citations join it as
    later passes recognise their own work — so two records for one identity
    are unioned rather than treated as a conflict. What is refused is a record
    that does not describe the identity it is stored under: a key that is not
    its own ``cluster_id``, or a proposition that does not mint that id, is a
    forged or re-anchored cluster, and storing it would let a row claim an
    identity it does not have.
    """
    merged: dict[str, ClaimCluster] = {}
    for source in (previous, current):
        for key, cluster in source.items():
            if key != cluster.cluster_id:
                raise ValueError(
                    f"cluster registry key {key!r} is not the cluster's own "
                    f"id {cluster.cluster_id!r}"
                )
            if claim_cluster_id(cluster.proposition) != cluster.cluster_id:
                raise ValueError(
                    f"cluster {cluster.cluster_id!r} was re-anchored on a "
                    "proposition that does not mint it"
                )
            stored = _resolve_stored_cluster(cluster.cluster_id, merged.values())
            if stored is None:
                merged[cluster.cluster_id] = cluster
                continue
            ordered = oldest_first([stored, cluster])
            survivor = merge_claim_clusters(ordered[0], ordered[1])
            if survivor.cluster_id != stored.cluster_id:
                merged.pop(stored.cluster_id, None)
            merged[survivor.cluster_id] = survivor
    return merged


def _union_passages(
    first: Sequence[EvidencePassage], second: Sequence[EvidencePassage]
) -> list[EvidencePassage]:
    """Both passage lists, first-seen order, without an exact duplicate."""
    union: list[EvidencePassage] = []
    seen: set[tuple[str, str, str, str]] = set()
    for passage in (*first, *second):
        key = (
            passage.source_url,
            passage.locator,
            passage.excerpt,
            passage.stance,
        )
        if key in seen:
            continue
        seen.add(key)
        union.append(passage)
    return union


# The order a disagreement resolves in. A contradiction is the strongest
# negative evidence there is, so it survives every other verdict; `verified`
# survives only when nothing disagrees with it. This is the plan's "zero false
# settled claims" bar expressed as a total order over the verdict vocabulary.
_VERDICT_PRECEDENCE: tuple[str, ...] = (
    "contradicted",
    "unverified",
    "insufficient_evidence",
    "verified",
)


def resolved_verdict(verdicts: Sequence[str]) -> str:
    """The one verdict a cluster may publish, given every verdict in it.

    A cluster whose members disagree never resolves to ``verified``: it takes
    the strongest disagreement, so a contradiction is never laundered into a
    settled fact.
    """
    for verdict in _VERDICT_PRECEDENCE:
        if verdict in verdicts:
            return verdict
    return "insufficient_evidence"


def _verdict_disagreements(
    cluster: ClaimCluster,
) -> list[str]:
    """One diagnostic per verdict in a disagreeing cluster, with ITS evidence.

    Each verdict is reported beside the citations that actually recorded it,
    so the diagnostic says which sources contradicted the assertion and which
    supported it rather than repeating one unioned set for both.
    """
    if len(cluster.verdicts) < 2:
        return []
    return [
        f"cluster_verdict_disagreement:{verdict}:"
        f"{','.join(cluster.verdict_evidence.get(verdict, []))}"
        for verdict in cluster.verdicts
    ]


def claim_meets_support_policy(
    *,
    support_policy: str,
    verdict: str,
    supporting_publishers: int,
    evidence_status: str | None = None,
) -> bool:
    """Whether an *adjudicated* claim satisfies a target's support policy.

    Called after verification, never before: a policy constrains the evidence
    that exists, and a claim with no verdict has none.

    ``independent_pair`` is the strict badge and nothing less: it needs the
    ``verified`` verdict the pair test writes, plus two supporting publishers,
    because one publisher is one source and a pair of passages from it is not
    a pair. A ``contradicted`` claim has evidence against it and an
    ``unverified`` one has evidence that does not address it, so neither
    answers a target under any policy.

    The weaker policies take that strict pair too, and additionally take a
    faithful scoped attribution: ``insufficient_evidence`` beside
    ``source_supported``, which is what one complete, in-scope support from a
    single publisher is — a primary report can support "report X estimates Y"
    without another publisher reproducing X's measurement. That is the only
    path the weakening opens, and it still needs the badge *and* at least one
    supporting publisher behind the claim; an ``insufficient_evidence`` claim
    judged anything else was never found source-supported. ``evidence_status``
    defaults to the conservative ``None``, so a caller that does not state the
    claim's own badge cannot open the weaker path by omission.
    """
    if verdict == "verified":
        if support_policy == "independent_pair":
            return supporting_publishers >= 2
        return support_policy in ("primary_attribution", "derivation")
    if (
        verdict == "insufficient_evidence"
        and evidence_status == "source_supported"
    ):
        return (
            support_policy in ("primary_attribution", "derivation")
            and supporting_publishers >= 1
        )
    return False


# How a document joins the two ends of a period it states. An abbreviated
# second end is a spelling local code would have to guess at, so only a fully
# written one is read, exactly as the temporal contract reads one.
_YEAR = r"(?:19|20)\d{2}"
_PERIOD_PATTERN = re.compile(
    rf"(?<![\d-])(?P<period>{_YEAR}(?:\s*(?:-|\u2013|\u2014|/|to|through)"
    rf"\s*{_YEAR})?)(?![\d-])"
)

# The units a clause may attach to a number. Longest first, so "percentage
# points" is never read as "points" and "million" never as "m".
_UNIT_FORMS = (
    "percentage points",
    "percentage point",
    "gigawatt hours",
    "megawatt hours",
    "kilowatt hours",
    "terawatt hours",
    "gigawatt hour",
    "megawatt hour",
    "kilowatt hour",
    "terawatt hour",
    "gigawatts",
    "megawatts",
    "kilowatts",
    "terawatts",
    "gigawatt",
    "megawatt",
    "kilowatt",
    "terawatt",
    "trillion",
    "billion",
    "million",
    "thousand",
    "percent",
    "tonnes",
    "tonne",
    "tons",
    "GWh",
    "MWh",
    "kWh",
    "TWh",
    "GW",
    "MW",
    "kW",
    "TW",
    "%",
)
_UNIT_ALTERNATION = "|".join(re.escape(form) for form in _UNIT_FORMS)
_VALUE_UNIT_PATTERN = re.compile(
    r"(?<![\w.])(?P<value>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>" + _UNIT_ALTERNATION + r")(?![A-Za-z%])",
    re.IGNORECASE,
)

_CAUSAL_CONNECTIVE = re.compile(
    r"\s*(?:,?\s*(?:because|due to|as a result of|which is why|so that|"
    r"therefore|thus)\s+)\s*",
    re.IGNORECASE,
)
# A contrasting measured quantity is a different assertion, not a second
# reading of the same one: "15 GW added in 2025, against 24 GW planned in
# 2026" must retain the first actual even when the second comparison differs.
_MEASURED_CONTRAST = re.compile(
    r",\s*(?=against\s+(?:the\s+)?\d[\d,.]*\s*(?:GW|MW|GWh|MWh)\b)",
    re.IGNORECASE,
)

# A sentence ends at a period, a question mark, or an exclamation mark, and
# the whitespace after it is where the split falls. What precedes that
# whitespace decides whether it really is an end; see :func:`_sentences`.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# An honorific never ends a sentence, whatever follows it: "Dr. Smith reported"
# is one sentence.
_HONORIFIC_END = re.compile(r"(?:Mr|Mrs|Ms|Dr|Prof)\.?$")

# Every other abbreviation is ambiguous, and which reading is right is decided
# by what comes next: a dotted initialism ("U.S.", "U.K.", "E.U.") or a common
# abbreviation ("Inc.", "No.", "e.g.", "p.m.") ends a sentence only when a new
# one starts after it. Suppressing the split unconditionally — the first cut of
# this rule — fused "Capacity grew in the U.S. It fell in Canada." into one
# clause with two measurements, so the clause read no value at all and the 2024
# target lost its figure.
_ABBREVIATED_END = re.compile(
    r"(?:"
    r"(?:[A-Za-z]\.){2,}"
    r"|Inc|Ltd|Co|Corp|St|No|Fig|vs|etc|approx"
    r")\.?$"
)
_NEW_SENTENCE = re.compile(r"^[A-Z]")
# A sentence is never only a determiner and a dotted initialism. "The U.S.
# Energy Information Administration reported …" is one sentence whose subject
# opens with "U.S.", and cutting it there left a junk atom "The U.S" that
# became the packet's cluster identity and merged claims about three different
# facts into one (live cycle 08b9b469).
_INITIALISM_ONLY = re.compile(
    r"^\s*(?:(?:the|a|an)\s+)?(?:[A-Za-z]\.){2,}$", re.IGNORECASE
)

# The scale of each SI-prefixed power and energy unit, as (base, exponent).
# Read only to recognize a document restating one quantity in parentheses
# ("10.4 GW (10,400 MW)"); the atom keeps the first spelling as written, so no
# converted value ever reaches a comparison (see :func:`_canonical_unit`).
_SI_UNIT_SCALE = {
    "kw": ("w", 3),
    "mw": ("w", 6),
    "gw": ("w", 9),
    "tw": ("w", 12),
    "kwh": ("wh", 3),
    "mwh": ("wh", 6),
    "gwh": ("wh", 9),
    "twh": ("wh", 12),
}
# A restatement may round its converted figure, never change it.
_RESTATEMENT_TOLERANCE = Decimal("0.005")
_OPENS_PARENTHESIS = re.compile(r"\s*\(\s*")
_CLOSES_PARENTHESIS = re.compile(r"\s*\)")
_NEGATION = re.compile(
    r"\b(?:not|no|never|without|nor|neither|isn't|aren't|wasn't|weren't|"
    r"doesn't|don't|didn't|cannot|can't|fails? to|failed to)\b",
    re.IGNORECASE,
)
_DENOMINATOR = re.compile(
    r"\b(?:of|out of|among|across)\s+(?P<denominator>"
    r"(?:all\s+|the\s+)?[a-z][a-z-]*(?:\s+[a-z][a-z-]*){0,3})"
    r"(?=[.,;]|\s+(?:was|were|is|are|in|during|for|reported|grew|fell)\b|$)",
    re.IGNORECASE,
)
# The attribution phrase is matched case-insensitively — "According to …" opens
# a sentence and is the most common spelling of it — while the name it
# attributes stays a proper noun, because that is what makes it a name.
_ATTRIBUTION = re.compile(
    r"(?i:\b(?:according to|published by|reported by|per)\s+)"
    r"(?P<attribution>[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,4})"
)

# A named issuer in front of its own reporting verb attributes the clause just
# as "according to X" does, and the run's live claims used this form for every
# figure they carried: "EIA reported that generators … added 10.4 GW" was
# recorded unattributed while "According to EIA, generators … added 10.4 GW"
# attributed to EIA, so the fact checker bound no claim to any target (audit
# #2, replay C11). The vocabulary is explicit and bounded, like every other
# marker table here: these are the verbs that report a fact their subject
# states, and a name in front of one of them is that fact's issuer.
_REPORTING_VERBS = (
    "reported",
    "reports",
    "stated",
    "states",
    "said",
    "says",
    "estimated",
    "estimates",
    "forecast",
    "forecasts",
    "expects",
    "expect",
    "expected",
    "projected",
    "projects",
    "found",
    "finds",
    "indicated",
    "indicates",
    "noted",
    "notes",
    "announced",
    "announces",
)
_REPORTING_VERB_ALTERNATION = "|".join(_REPORTING_VERBS)

# The name run in front of a reporting verb. Bounded at five tokens, the same
# bound the prepositional form uses, so a sentence-initial determiner or
# pronoun cannot sweep a whole clause into the attribution. A token after the
# first may carry digits and commas, which is what lets an issuer's own
# document phrase reach its verb: "EIA's August 20, 2025 update stated …".
_NAME_RUN = r"[A-Z][\w&.'-]*(?:\s+[A-Z0-9][\w&.,'-]*){0,4}"

# The forms whose *noun* reading is common enough that nothing may stand
# between the name and them. "EIA denied reports that …" is a denial with no
# issuer in it; reading the noun "reports" as a verb attributed the denial to
# EIA and met a primary_attribution obligation with it.
_NOUN_AMBIGUOUS_VERBS = frozenset(
    {"reports", "estimates", "forecasts", "notes", "finds"}
)

# Between the name and its verb may stand the document the issuer published —
# "EIA's August 20, 2025 In-brief analysis reported that …", "EIA forecast in
# its February 24, 2025 analysis that …". The run is bounded and may not cross
# a comma or a "that", so it can never bridge two clauses to reach an
# unrelated verb: "… in the United States in 2024, the agency reported" used to
# credit the United States.
_SUBJECT_VERB_ATTRIBUTION = re.compile(
    r"(?P<attribution>" + _NAME_RUN + r")"
    r"(?:\s+(?!that\b)[\w.&'/-]+){0,6}?"
    r"\s+(?i:(?P<verb>" + _REPORTING_VERB_ALTERNATION + r"))\b"
)

# Every reporting verb on its own, for the subject phrase whose verb stands too
# far off for the run above to reach: "An EIA Today in Energy article based on
# the December 2024 Preliminary Monthly Electric Generator Inventory stated
# that …" puts fourteen words between the publisher and "stated".
_REPORTING_VERB_IN_CLAUSE = re.compile(
    r"(?i:\b(?P<verb>" + _REPORTING_VERB_ALTERNATION + r")\b)"
)

# A definition states the convention a body counts by, and that body is the
# clause's own grammatical subject — no reporting verb stands between them. The
# run's fourteenth claim stated its issuer exactly this way, "EIA counts battery
# storage projects larger than 1 MW in the electric power sector when reporting
# U.S. utility-scale battery storage capacity", and was recorded unattributed,
# so no methodology target could bind the convention the figures rest on (live
# cycle 08b9b469). The vocabulary is explicit and bounded like the reporting
# table above: these are the verbs whose subject states what a count includes,
# excludes, or treats alike — never a verb that ranks or quantifies one.
_DEFINITIONAL_VERBS = (
    "counts",
    "count",
    "defines",
    "define",
    "classifies",
    "classify",
    "includes",
    "include",
    "excludes",
    "exclude",
    "treats",
    "treat",
    "considers",
    "consider",
    "categorises",
    "categorizes",
    "categorise",
    "categorize",
    "tracks",
    "track",
    "measures",
    "measure",
)
_DEFINITIONAL_VERB_ALTERNATION = "|".join(_DEFINITIONAL_VERBS)

# The same name run and the same bounded filler the reporting form uses, so a
# clause's definition is read exactly as its report is.
_SUBJECT_DEFINITIONAL_ATTRIBUTION = re.compile(
    r"(?P<attribution>" + _NAME_RUN + r")"
    r"(?:\s+(?!that\b)[\w.&'/-]+){0,6}?"
    r"\s+(?i:(?P<verb>" + _DEFINITIONAL_VERB_ALTERNATION + r"))\b"
)

# What a definitional verb has to state: the convention itself. A clause that
# ends at the verb states none, and a copula or auxiliary directly after it
# reads the verb as a noun rather than as a definition — "the EIA counts were
# revised in February" reports nothing — exactly as the reported-clause test
# refuses "the EIA forecast that was published in February".
_NO_DEFINITION_AFTER_VERB = re.compile(
    r"(?i:\s*(?:was|were|is|are|been|being|has|have|had|can|could|will|would|"
    r"should|may|might|must)\b)"
)

# The head of a sentence-initial participial phrase. A quote or a bracket may
# open the clause, and the head is one ``-ing`` word however it is followed.
_PARTICIPIAL_HEAD = re.compile(r"(?i:^[\s\"'(\[]*[A-Z][\w'-]*ing\b)")

# What a reporting verb has to introduce to be a reporting verb here: a
# reported clause ("… reported that capacity grew"), the end of the clause
# ("…, EIA reported."), or a stated object ("EIA said the addition set a
# record"). The "that" is refused when a copula follows it, because "the EIA
# forecast that was published in February" reports nothing.
_REPORTED_THAT = re.compile(
    r"(?i:\s+that\s+)"
    r"(?!(?:was|were|is|are|been|being|has|have|had|can|could|will|would|"
    r"should|may|might|must)\b)"
)
_REPORTED_CLAUSE_WINDOW = 120

# What a reporting verb may state as its object: "EIA said the 2024 addition
# set a record", "EIA expects 18.2 GW of storage to be added in 2025". A
# determiner, a possessive, a pronoun, or a number opens a stated object; a
# copula or auxiliary does not, which is what keeps a noun use of the verb
# ("the EIA forecast was published in February") out of the vocabulary.
_REPORTED_OBJECT = re.compile(
    r"(?i:\s*(?:the|a|an|this|these|those|its|their|his|her|our|my|it|they|"
    r"he|she|we|you|no|another|some|many|most|all)\b|\s*\d)"
)

# The name positions that name nobody: a determiner or pronoun in front of a
# reporting verb states that *someone* reported something, never who. The list
# is explicit and holds whole captured names only, so "The Federal Energy
# Regulatory Commission reported …" keeps its name.
_NAME_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "this",
        "that",
        "these",
        "those",
        "it",
        "they",
        "he",
        "she",
        "we",
        "you",
        "i",
        "there",
        "in",
        "on",
        "at",
        "by",
        "for",
        "of",
        "to",
        "from",
        "with",
        "as",
        "but",
        "and",
        "or",
        "if",
        "when",
        "while",
        "after",
        "before",
        "since",
        "during",
        "according",
        "new",
        "both",
        "some",
        "many",
        "most",
        "all",
        "no",
        "not",
        "other",
        "another",
        # The possessives and quantifiers, which are the same closed class as
        # the determiners above: "Our analysis found that …" and "Every study
        # found that …" report nothing, and no name opens with "our" (re-review
        # ND1).
        "our",
        "your",
        "his",
        "her",
        "my",
        "its",
        "their",
        "every",
        "each",
        "few",
        "several",
        "either",
        "neither",
        "any",
        # The indefinite pronouns, the same closed class as the determiners
        # above: "None reported that …", "Everyone reported that …" and
        # "Someone reported that …" state that an unnamed someone did
        # (re-review ND1).
        "none",
        "nothing",
        "everyone",
        "everybody",
        "someone",
        "somebody",
        "something",
        "anyone",
        "anybody",
        "anything",
        "everything",
    }
)

# "according to its <document>": the document is the claim's own issuer's, so
# the clause is attributed to the issuer the *claim* names — and to nobody when
# the claim names nobody.
_REFERENCES_ITS_OWN_DOCUMENT = re.compile(r"(?i:\baccording to\s+its\b|\bin its\b)")

_GEOGRAPHY = re.compile(
    r"\b(?:in|across|within|for)\s+(?P<geography>"
    r"(?:the\s+)?[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,4})"
)

# The place a clause names without a preposition: "U.S. capacity additions",
# "the U.S. grid", "American plants". The run's own claims used this spelling
# for every figure they carried ("cumulative U.S. utility-scale battery storage
# capacity", "share of U.S. capacity additions"), so with only the prepositional
# form read they stated no geography at all and no target requiring one could
# bind (audit #2, replay C8).
#
# The alias list is closed and each entry is one spelling of one country:
#
# * "U.S.", "US", "USA", "United States" — the country's own name and its
#   initialism. Case matters: the lowercase pronoun ("tell us about …") is
#   never read as the country, and a following word character or a currency
#   sign refuses "USD" and "US$26 million" (a currency code names no place).
# * "American" — the standard adjectival form, accepted only unqualified:
#   "Latin American", "South American", "North American" and "Central
#   American" name other regions that share the adjective.
#
# The trailing lookahead also refuses the hyphenated compound ("U.S.-made"):
# where a thing was made is not the place a clause's fact covers, and reading
# it as one told a claim about Canada that it was about the United States.
_UNITED_STATES_ALIASES = frozenset(
    {"american", "u.s", "u.s.a", "united states", "us", "usa"}
)
# The same spellings with their periods dropped, which is how a captured name
# that lost its sentence-final period is compared ("U.S").
_UNITED_STATES_FOLDED = frozenset(
    alias.replace(".", "") for alias in _UNITED_STATES_ALIASES
)
_UNITED_STATES = re.compile(
    r"(?<![A-Za-z0-9-])"
    r"(?<!Latin\s)(?<!South\s)(?<!North\s)(?<!Central\s)"
    r"(?P<geography>U\.S\.A\.?|U\.S\.|USA|US|United\s+States|American)"
    r"(?![\w$-])"
)

# One capitalised word, for finding the name a clause opens with.
_NAME_TOKEN = re.compile(r"\b[A-Z][\w&.'-]*")

# The nouns the alias has to reach to be the clause's own place. "U.S. capacity
# additions" is about the United States; "U.S. suppliers" and "U.S. levels" are
# adjuncts inside a clause about Mexico or Germany, and reading them as the
# clause's geography credited a foreign country's figure to the United States
# obligation (review F2). One noun within three tokens of the alias.
_UNITED_STATES_NOUNS = (
    "addition",
    "capacity",
    "fleet",
    "grid",
    "market",
    "storage",
    "utility-scale",
)
_UNITED_STATES_WINDOW = 3
_UNITED_STATES_NOUN_PATTERN = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(noun) + r"s?" for noun in _UNITED_STATES_NOUNS)
    + r")(?!\w)"
)

# The same forms folded, for asking whether a token is a unit rather than a
# name: "U.S. utilities added 10.4 GW of storage in 2024, EIA reported" names
# nobody else, and reading "GW" as a competing subject refused the clause's own
# place and left it with no geography at all (re-review N1).
_UNIT_TOKEN_FORMS = frozenset(form.casefold() for form in _UNIT_FORMS)

# The determiners that can precede a name without being part of it. Deliberately
# narrower than ``_NAME_STOPWORDS``: "New York Times reported …" keeps its "New".
_LEADING_ARTICLES = frozenset(
    {"a", "an", "the", "this", "these", "those", "its", "their"}
)

# The scoping words that can open a clause in front of a name: "In 2024 Texas
# reported …", "Earlier this year EIA stated …". A name keeps its first token
# unless that token scopes the sentence rather than naming the issuer.
_NAME_SCOPE_TOKENS = frozenset(
    {"in", "on", "at", "by", "for", "during", "since", "after", "before", "as"}
)

# The adjectives that can open a common-noun phrase but never an issuer's name:
# "Earlier Reports said …", "Previous Reports found …". An acronym, a
# multi-token proper name, or a name that does not open the clause is what is
# left, and each is an issuer the plan may rely on.
_NAME_MODIFIER_TOKENS = frozenset(
    {
        "earlier",
        "former",
        "last",
        "later",
        "latter",
        "next",
        "other",
        "previous",
        "recent",
        "same",
        "such",
    }
)

# The nouns a sentence about people in general opens with. One title-case token
# that opens a clause is a common noun by default — "Analysts reported …",
# "Grid operators … said", "Media reported …", "Study found …" — and only a
# token that is not one of these is read as a publisher's name: "Reuters
# reported that …" names Reuters, "Reports stated that …" names nobody (review
# F1, re-reviews N2 and ND1).
#
# Every word here is a common noun that takes a determiner ("the media", "the
# study", "the government") or a plural of one, which is what tells it from a
# publisher's name; the list is the same kind of bounded vocabulary as the
# marker tables above, and it is the only signal the text itself offers. A
# single token that is not here and reports something is credited — recall is
# traded for the F1 refusal, deliberately.
_SINGLE_TOKEN_NON_ISSUERS = frozenset(
    {
        "agencies",
        "agency",
        "analyses",
        "analysis",
        "analysts",
        "authorities",
        "authority",
        "authors",
        "body",
        "bureau",
        "bureaus",
        "buyers",
        "commentators",
        "commission",
        "commissions",
        "companies",
        "company",
        "critics",
        "customers",
        "data",
        "department",
        "departments",
        "developers",
        "engineers",
        "evidence",
        "expert",
        "experts",
        "figures",
        "firms",
        "government",
        "grid",
        "groups",
        "industry",
        "investors",
        "journal",
        "journals",
        "lawmakers",
        "market",
        "markets",
        "media",
        "model",
        "models",
        "nobody",
        "number",
        "numbers",
        "observers",
        "office",
        "offices",
        "officials",
        "operators",
        "owners",
        "panel",
        "panels",
        "paper",
        "papers",
        "plants",
        "press",
        "projections",
        "providers",
        "regulators",
        "report",
        "reporters",
        "reports",
        "research",
        "researchers",
        "review",
        "reviews",
        "sales",
        "sources",
        "staff",
        "statistic",
        "statistics",
        "studies",
        "study",
        "suppliers",
        "survey",
        "surveys",
        "teams",
        "utilities",
        "utility",
        "vendors",
        "workers",
    }
)

# A clause whose subject is nobody reports nothing: "Nobody at EIA reported that
# …" states that no one at EIA said it, so no name inside that phrase is the
# clause's issuer (review F1). The pronoun scopes the phrase it belongs to, not
# the whole clause: in "No one expected the growth, but EIA reported that …" the
# denial is an earlier assertion and EIA is still the issuer, so the guard fires
# only where the pronoun stands immediately in front of the name, with nothing
# between them but one preposition (re-review ND2).
_NO_ISSUER_LEAD = re.compile(
    r"(?i)\b(?:nobody|no one|none|nothing|everyone|everybody|someone|somebody|"
    r"anyone|anybody|everything|something|anything)\b"
    r"(?:\s+(?:at|of|in|from|for|with|on|by|among|within|inside))?\s*$"
)

# The months and the years that end a name. A document phrase states its date
# and then its publisher — "the December 2024 Preliminary Monthly Electric
# Generator Inventory" — and a run that continued through the date attributed
# the run's own third claim to "December 2024 Preliminary Monthly Electric"
# instead of to the EIA page it belongs to (re-review N2).
_MONTH_TOKENS = frozenset(
    {
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
    }
)


def _is_month(token: str) -> bool:
    """Whether one token is a month name."""
    return token.strip(".,;:").casefold() in _MONTH_TOKENS


def _names_a_date(token: str) -> bool:
    """Whether one token is a date rather than a name: a month, or a year."""
    folded = token.strip(".,;:")
    return _is_month(folded) or (folded.isdigit() and len(folded) == 4)


def _opens_with_a_date(tokens: Sequence[str]) -> bool:
    """Whether a name opens with a date rather than naming someone.

    A month *and* the date it belongs to — "December 2024 Preliminary Monthly
    Electric Generator Inventory" — is a document's date, and a bare year is
    one too. A lone month token may still open a publisher's name ("March
    Advisors", "May Advisors"), so a month is refused only where a date follows
    it (re-review ND3).
    """
    first = tokens[0].strip(".,;:")
    if first.isdigit() and len(first) == 4:
        return True
    return (
        _is_month(first)
        and len(tokens) > 1
        and tokens[1].strip(".,;:")[:1].isdigit()
    )

# A clause that points at its country without naming it. Only a claim that
# names a place can resolve one: "the nation's fleet" in a claim about no
# country in particular stays unnamed rather than guessed.
_NATION_ANAPHOR = re.compile(
    r"(?i:\bthe\s+(?:nation|country)'?s?\b|\bnationwide\b)"
)

_FORECAST = re.compile(
    r"\b(?P<forecast>projected|projection|forecast|forecasted|expected|"
    r"anticipated|estimated|observed|measured|actual)\b",
    re.IGNORECASE,
)

# What a clause DOES to its subject, as the relation a reader would name.
#
# The surface verb is not comparable — one assertion is "held", another says
# "sat", a third "reported", and all three state the same level — so the field
# holds the relation class and two clauses that state the same thing about
# their subject share one. The classes are deliberately NARROW: a level and a
# delta are different facts ("hit 10 GW" is not "rose 10 GW"), a doubling is
# not a rise, and a halving is not a fall. Where two relations cannot be shown
# to be the same, they are different classes and the pair is refused — the
# coverage loss is the accepted direction and a false settled claim is not.
#
# An auxiliary or light verb is NOT a relation and is deliberately absent from
# this table. When the value comes first — "10 GW was doubled in 2024" — every
# relation word follows it, so the nearest candidate before the value was
# ``was``, and reading it made a doubling, a halving, a rise and a level all
# one ``states_level`` assertion. A clause whose only verb is one of those
# states no relation this contract can name: the relation is UNKNOWN, and an
# unknown relation is refused against a named one rather than defaulted to a
# level.
_PREDICATE_RELATIONS: dict[str, str] = {
    # The value IS the level: "the queue held 10 GW", "the survey reported 40".
    "held": "states_level",
    "holds": "states_level",
    "hold": "states_level",
    "carried": "states_level",
    "carries": "states_level",
    "sat": "states_level",
    "sits": "states_level",
    "sit": "states_level",
    "stood": "states_level",
    "stands": "states_level",
    "remained": "states_level",
    "stayed": "states_level",
    "reported": "states_level",
    "reports": "states_level",
    "showed": "states_level",
    "shows": "states_level",
    "found": "states_level",
    "measured": "states_level",
    "measures": "states_level",
    "indicated": "states_level",
    "indicates": "states_level",
    "total": "states_level",
    "totals": "states_level",
    "equalled": "states_level",
    "equaled": "states_level",
    "equals": "states_level",
    "cost": "states_level",
    "costs": "states_level",
    "accounts for": "states_level",
    "represents": "states_level",
    # The value is a LEVEL THE SUBJECT REACHED, not a change it underwent.
    "hit": "reaches_level",
    "hits": "reaches_level",
    "reached": "reaches_level",
    "reaches": "reaches_level",
    "peaked": "reaches_level",
    "peaks": "reaches_level",
    "touched": "reaches_level",
    "touches": "reaches_level",
    # The value is a DELTA: how much the subject moved.
    "rose": "increases_by",
    "rises": "increases_by",
    "grew": "increases_by",
    "grows": "increases_by",
    "increased": "increases_by",
    "increases": "increases_by",
    "climbed": "increases_by",
    "climbs": "increases_by",
    "added": "increases_by",
    "adds": "increases_by",
    "gained": "increases_by",
    "gains": "increases_by",
    "fell": "decreases_by",
    "falls": "decreases_by",
    "dropped": "decreases_by",
    "drops": "decreases_by",
    "decreased": "decreases_by",
    "decreases": "decreases_by",
    "declined": "decreases_by",
    "declines": "decreases_by",
    "lost": "decreases_by",
    "loses": "decreases_by",
    # A multiplicative change is neither a rise nor a fall.
    "doubled": "doubled",
    "doubles": "doubled",
    "tripled": "tripled",
    "triples": "tripled",
    "halved": "halved",
    "halves": "halved",
    # Everything else keeps its own relation.
    "withheld": "withholds",
    "withholds": "withholds",
    "estimated": "projected",
    "estimates": "projected",
    "projected": "projected",
    "projects": "projected",
    "expected": "projected",
    "requires": "requires",
    "required": "requires",
    "connects": "connects",
    "connected": "connects",
    "uses": "uses",
    "used": "uses",
    "approved": "approved",
    "approves": "approved",
}

_PREDICATE = re.compile(
    r"\b(?P<predicate>"
    + "|".join(
        re.escape(verb)
        for verb in sorted(_PREDICATE_RELATIONS, key=len, reverse=True)
    )
    + r")\b",
    re.IGNORECASE,
)

# Words that carry no identity: determiners, prepositions, conjunctions, and
# pronouns. The subject is the run of words before the predicate that survives
# them.
_SUBJECT_STOPWORDS = frozenset(
    {
        "a", "an", "the", "this", "that", "these", "those", "of", "in", "on",
        "at", "by", "for", "from", "to", "with", "and", "or", "but", "as",
        "during", "since", "over", "under", "between", "across", "within",
        "its", "their", "his", "her", "our", "your", "it", "they", "we",
        "according", "reported", "published",
    }
)
# Verbs that assert nothing about their subject on their own. They are skipped
# in the entity run rather than read as part of an entity or as its boundary:
# once the relation is read from the clause's real relation word, the auxiliary
# that used to anchor the run falls inside it, and "10 GW of capacity was added"
# would otherwise name its subject "was capacity".
_LIGHT_VERBS = frozenset(
    {
        "am", "is", "are", "was", "were", "be", "been", "being",
        "has", "have", "had", "having",
        "do", "does", "did", "done",
        "will", "would", "shall", "should", "can", "could", "may", "might",
        "must",
    }
)
_WORD_TOKEN = re.compile(r"[A-Za-z][\w'-]*")
MAX_SUBJECT_WORDS = 6

# The relation vocabulary, as the set a subject run may never read an entity
# from. It is the table's own keys, so a relation this contract learns is a
# relation here too and the two cannot drift.
_RELATION_WORDS = frozenset(_PREDICATE_RELATIONS)

# The three outcomes of a subject derivation. ``derived`` means an entity was
# read and is compared; ``absent`` means the clause names none, so two such
# clauses may still agree; ``unresolved`` means an entity position is there and
# the derivation failed, which refuses. The distinction exists because an empty
# subject that means "I could not tell" was being read as "the two agree".
_SUBJECT_DERIVED: SubjectState = "derived"
_SUBJECT_ABSENT: SubjectState = "absent"
_SUBJECT_UNRESOLVED: SubjectState = "unresolved"

# Where an entity sits when the clause puts it after the value and the verb:
# "10 GW sat in the 2024 interconnection queue", "10 GW was held by the wind
# fleet", "10 GW was added to the Texas grid". Read only when nothing precedes
# the anchor, so a clause that already named its subject is never overridden by
# a locative tail.
#
# The phrase is read to the clause's own boundary and kept whole, or not at all.
# A fixed-length prefix of a long entity is the dangerous shape: "California
# Independent System Operator" out of "… Operator interconnection queue" is
# nonempty, plausible, and silently identical to the opening words of a
# different entity — an interconnection queue and a transmission queue merged,
# and so did the operator named on its own. A capture this contract cannot show
# is the whole entity is therefore UNRESOLVED, which refuses, rather than empty,
# which two clauses could agree on.
_TRAILING_ENTITY_HEAD = re.compile(
    r"\b(?:by|in|at|on|within|across|for|to|into|onto|throughout)\s+"
    r"(?:the\s+)?(?:(?:19|20)\d{2}\s+)?",
    re.IGNORECASE,
)
# Words that turn a following "to" into an idiom rather than a locative:
# "according to PJM", "up to 10 GW", "prior to 2024", "due to delays",
# "next to the queue".
_TO_HEAD_GUARD_WORDS = frozenset({"according", "up", "prior", "due", "next"})
# Where the noun phrase stops: the clause's own punctuation. This is a real
# boundary, so it completes the capture rather than truncating it.
_TRAILING_ENTITY_BOUNDARY = re.compile(r"[,;:.!?()\[\]\u2013\u2014]")
MAX_TRAILING_ENTITY_WORDS = 12

# What a capture longer than ``MAX_TRAILING_ENTITY_WORDS`` is reduced to. The
# whole-or-empty rule refused two *different* over-cap entities correctly, but
# it also refused two identical ones — and the rejected pair was then minted as
# two ledger rows sharing one cluster id, because the assertion fingerprint
# ignores a subject nobody derived. The complete normalized phrase IS available
# before the cap check, so it is digested rather than discarded.
#
# A digest, not a prefix: a four-word prefix of "California Independent System
# Operator interconnection queue" is nonempty, plausible, and identical to the
# opening words of a different operator's queue, which is the false merge the
# whole-or-empty rule existed to prevent. 128 bits of SHA-256 keeps collisions
# out of practical reach while the subject stays a fixed-size token that only
# ever compares for equality.
OVER_CAP_SUBJECT_PREFIX = "over-cap:"
OVER_CAP_SUBJECT_DIGEST_CHARS = 32


def _sentences(text: str) -> list[str]:
    """Split prose at its sentence ends, and inside an abbreviation only when one ends.

    A period after a dotted initialism ("U.S.", "U.K.", "e.g.") or a common
    abbreviation ("Inc.", "St.", "No.") is a sentence end only when a new
    sentence follows it. Splitting there unconditionally cut a claim in half
    and the half that carried the place, the figure, or the issuer was the half
    left behind — the run's "cumulative U.S. utility-scale battery storage
    capacity" became "cumulative U" beside "S. utility-scale …" — while never
    splitting there fused two real sentences into one clause, which reads no
    value at all from either of them ("Capacity grew in the U.S. It fell in
    Canada."). What follows the period decides.
    """
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        preceding = text[: match.start()]
        if _INITIALISM_ONLY.match(text[start : match.start()]):
            continue
        if _HONORIFIC_END.search(preceding):
            continue
        if _ABBREVIATED_END.search(preceding) and not _NEW_SENTENCE.match(
            text[match.end() :]
        ):
            continue
        sentences.append(text[start : match.start()])
        start = match.end()
    sentences.append(text[start:])
    return sentences


def _split_clauses(text: str) -> list[str]:
    """Split a claim's prose into one clause per assertion it makes.

    Two shapes are split, and only two: a sentence or a semicolon-separated
    observation, and a causal assertion stated after its connective. Splitting
    on every "and" would cut a list of three minerals into three claims, which
    is the opposite of atomizing.
    """
    clauses: list[str] = []
    for sentence in _sentences(text):
        for stated in re.split(r"\s*;\s*", sentence):
            for contrasted in _MEASURED_CONTRAST.split(stated):
                clauses.extend(_CAUSAL_CONNECTIVE.split(contrasted))
    cleaned: list[str] = []
    for clause in clauses:
        stripped = clause.strip()
        if stripped.endswith("."):
            stripped = stripped[:-1].strip()
        if stripped:
            cleaned.append(stripped)
    return cleaned


def _restates(
    clause: str, previous: re.Match[str], current: re.Match[str]
) -> bool:
    """True when ``current`` is ``previous`` restated in parentheses.

    Two shapes count. "X u1 (Y u2)" with both units on one SI base (watts or
    watt-hours) and the same quantity after scaling is a rescaled restatement.
    "N% (Y u)" — a share immediately followed by its own absolute figure in
    parentheses, such as "growing by 47% (14 GW)" — is a restatement too: the
    parenthetical spells the share's own quantity, not a second measurement,
    and a percentage has no SI base to scale against so no magnitude check
    applies to it. Two quantities in different bases ("10 GW (40 GWh)"),
    different values ("10.4 GW (26,000 MW)"), or any other arrangement
    ("18.2 GW, up from 10.3 GW") stay two.
    """
    if not _OPENS_PARENTHESIS.fullmatch(clause[previous.end() : current.start()]):
        return False
    if not _CLOSES_PARENTHESIS.match(clause[current.end() :]):
        return False
    previous_unit = _canonical_unit(previous.group("unit"))
    current_unit = _canonical_unit(current.group("unit"))
    if previous_unit in ("%", "pp") and current_unit not in ("%", "pp"):
        return True
    first = _SI_UNIT_SCALE.get(previous_unit)
    second = _SI_UNIT_SCALE.get(current_unit)
    if first is None or second is None or first[0] != second[0]:
        return False
    try:
        left = Decimal(previous.group("value").replace(",", "")).scaleb(first[1])
        right = Decimal(current.group("value").replace(",", "")).scaleb(second[1])
    except InvalidOperation:
        return False
    if left == 0:
        return right == 0
    return abs(left - right) <= abs(left) * _RESTATEMENT_TOLERANCE


def _value_and_unit(clause: str, *, period: str) -> tuple[str, str]:
    """The one number and unit this clause states, if it states exactly one.

    The observation period's own year is not a value: "the 2024 queue" states
    a period, not a measurement. A clause stating two measurements is left
    with none rather than with a guess, because the compatibility check treats
    an unstated dimension as unstated and refuses accordingly. A parenthetical
    restatement of the same quantity is one measurement, not two: the live
    claims "added 10.4 GW (10,400 MW)" and "add 19.6 GW (19,600 MW)" read no
    value at all, so neither could answer the target it was written for. A
    share restated as its own absolute figure in parentheses — "grew by 47%
    (14 GW)" — keeps that absolute figure rather than the share, because the
    unit a target asks for is the checkable one.
    """
    period_digits = set(re.findall(r"\d+", period))
    found: list[re.Match[str]] = []
    for match in _VALUE_UNIT_PATTERN.finditer(clause):
        if match.group("value") in period_digits:
            continue
        if found and _restates(clause, found[-1], match):
            if _canonical_unit(found[-1].group("unit")) in ("%", "pp"):
                found[-1] = match
            continue
        found.append(match)
    if len(found) != 1:
        return ("", "")
    value, unit = found[0].group("value"), found[0].group("unit")
    return (" ".join(value.split()), " ".join(unit.split()))


def _first_group(pattern: re.Pattern[str], clause: str) -> str:
    match = pattern.search(clause)
    if match is None:
        return ""
    return " ".join(match.group(1).split())


def _possessive_owner(token: str) -> str | None:
    """The owner named by a possessive token, or ``None`` for any other token."""
    folded = token.casefold()
    for suffix in ("'s", "\u2019s"):
        if folded.endswith(suffix):
            return token[: -len(suffix)]
    return None


def _is_acronym(name: str) -> bool:
    """True when a name is written as an acronym: every letter is upper case.

    "EIA reported …" is an issuer; "Analysts reported …" is a common noun at
    the head of a sentence, and the two differ in nothing but case.
    """
    letters = [character for character in name if character.isalpha()]
    return bool(letters) and all(character.isupper() for character in letters)


def _is_united_states(name: str) -> bool:
    """True when a name is one of the United States' own spellings.

    Compared on the folded spelling with its periods dropped, so the trailing
    period a sentence-final capture loses ("…, a U.S. company said." yields
    "U.S") still reads as the country rather than as an issuer.
    """
    folded = _canonical(name).replace(".", "").strip(" ,;:")
    return folded in _UNITED_STATES_FOLDED


def _clean_issuer(
    candidate: str,
    *,
    claim_places: frozenset[str] = frozenset(),
) -> str:
    """The issuer one captured name phrase names, or "" when it names none.

    A possessive ends the name: "EIA's August 20, 2025 In-brief analysis
    reported …" names EIA, and the document behind the possessive is what
    reported, not a second issuer — so the phrase is cut at it, and "EIA's
    analysis reported" and "EIA reported" fold to one issuer rather than two.
    A bare determiner or pronoun names nobody at all, and neither does a place:
    a locative says where a fact is, not who reported it, so "in California
    said" and "the U.S. … reported" name no issuer. A date names nobody either:
    "the December 2024 Preliminary Monthly Electric Generator Inventory stated
    …" is a document, and the run's third claim was attributed to that date
    phrase until the name stopped at it.
    """
    cleaned = " ".join(candidate.split()).strip(" ,.;:")
    tokens = cleaned.split()
    # A leading article or scoping phrase is not part of the name: "An EIA
    # article stated …", "In 2024 Texas reported …" and "The Federal Energy
    # Regulatory Commission found …" each name one issuer, and the article or
    # year left in place made the name disagree with itself between spellings
    # ("An EIA" versus "EIA", "In 2024 Texas" versus "Texas").
    while len(tokens) > 1 and (
        tokens[0].casefold() in _LEADING_ARTICLES
        or tokens[0].casefold() in _NAME_SCOPE_TOKENS
        or re.fullmatch(r"[\d,.]+", tokens[0]) is not None
    ):
        tokens.pop(0)
    cleaned = " ".join(tokens)
    tokens = cleaned.split()
    for index, token in enumerate(tokens):
        owner = _possessive_owner(token)
        if owner is not None:
            cleaned = " ".join([*tokens[:index], owner]).strip()
            break
    if not cleaned or cleaned.casefold() in _NAME_STOPWORDS:
        return ""
    if _opens_with_a_date(cleaned.split()):
        return ""
    if _is_united_states(cleaned):
        return ""
    if (
        " " not in cleaned
        and claim_places
        and cleaned.casefold() in claim_places
    ):
        return ""
    return cleaned


def _introduces_a_reported_clause(clause: str, *, verb_end: int) -> bool:
    """Whether what follows a reporting verb is the fact it reports.

    Four shapes, and only four: a reported clause ("… reported that capacity
    grew"), the end of the clause ("…, EIA reported."), a stated object ("EIA
    said the 2024 addition set a record", "EIA expects 18.2 GW …"), and a
    trailing punctuation mark. A copula or auxiliary directly after the verb
    is refused, because "the EIA forecast that was published in February"
    reports nothing.
    """
    rest = clause[verb_end:]
    if not rest.strip():
        return True
    if rest.lstrip()[:1] in (",", ".", ";", ":"):
        return True
    if _REPORTED_THAT.search(rest[: _REPORTED_CLAUSE_WINDOW]) is not None:
        return True
    return _REPORTED_OBJECT.match(rest) is not None


def _has_internal_capital(name: str) -> bool:
    """True for a brand spelling: a capital anywhere after the first letter.

    "OpenEI", "YouTube", "GitHub" are names however they are cased, while
    "Analysts" and "Utilities" are common nouns that happen to open a sentence.
    """
    return any(character.isupper() for character in name[1:])


def _single_token_names_an_issuer(name: str) -> bool:
    """Whether one title-case token that opens a clause names an issuer.

    Two ways to earn it, and a common noun earns neither. A token with an
    internal capital is a brand spelling and not a common noun at all
    ("OpenEI reported 10.4 GW …"). Any token that is not one of the nouns a
    sentence about people in general opens with is a name: "Reuters reported
    that …", "Fluence reported 10.4 GW", "Texas reported that …", where
    "Reports stated that …", "Media reported …" and "Study found …" name
    nobody (review F1, re-reviews N2 and ND1).
    """
    return name.casefold() not in _SINGLE_TOKEN_NON_ISSUERS


def _names_its_own_subject(
    captured: str,
    *,
    name: str,
    clause: str,
    name_end: int,
    verb_start: int,
) -> bool:
    """Whether one captured token is the clause's own subject, not its modifier.

    "Reuters reported that …" puts its verb straight behind the name, and so
    does "Fluence reported 10.4 GW". "Federal officials reported that …",
    "State regulators said …" and "Team members found …" put a lowercase
    common-noun head between the two: that capitalised word modifies the head,
    the subject is the whole noun phrase, and nobody in it is a publisher
    (re-review ND1). A possessive capture is a name by construction —
    "OpenEI's page … states that …" — and a brand spelling is one too.
    """
    if _has_internal_capital(name):
        return True
    if _possessive_owner(captured) is not None:
        return True
    return clause[name_end:verb_start].strip() == ""


def _accepted_issuer(
    candidate: str,
    *,
    clause: str,
    start: int,
    name_end: int,
    verb_start: int,
    claim_places: frozenset[str] = frozenset(),
) -> str:
    """The issuer one captured name names, or "" when the grammar refuses it."""
    if _NO_ISSUER_LEAD.search(clause[:start]):
        # "Nobody at EIA reported that …" reports nothing, and the name inside
        # that phrase is not the clause's issuer (review F1).
        return ""
    name = _clean_issuer(candidate, claim_places=claim_places)
    if not name:
        return ""
    if name.split()[0].casefold() in _NAME_MODIFIER_TOKENS:
        return ""
    opens_the_clause = clause[:start].strip(" \t\"'([") == ""
    if opens_the_clause and " " not in name and not _is_acronym(name):
        if not _names_its_own_subject(
            candidate,
            name=name,
            clause=clause,
            name_end=name_end,
            verb_start=verb_start,
        ):
            return ""
        if not _single_token_names_an_issuer(name):
            return ""
    return name


def _issuer_in_subject_phrase(
    clause: str,
    *,
    claim_places: frozenset[str] = frozenset(),
) -> str:
    """The issuer a subject phrase names when its verb stands too far off.

    "An EIA Today in Energy article based on the December 2024 Preliminary
    Monthly Electric Generator Inventory stated that …" puts fourteen words
    between the publisher and its verb, past the bound that keeps the name run
    from bridging clauses, and the date phrase inside it is no issuer. The name
    the subject phrase itself opens with is the publisher the document belongs
    to, and only a name that is unmistakably a name is read this way — an
    acronym ("EIA") or a brand spelling ("OpenEI"). "The December 2024
    Preliminary Monthly Electric Generator Inventory stated that …" therefore
    names nobody, which is the reading this fallback has to keep.

    A sentence-initial participial phrase is skipped whole: it states a method,
    so neither its head nor the unit "MW" inside it is a name, and reading
    inside it credited "MW" as the publisher of "Counting projects larger than
    1 MW …, the agency projected that …".
    """
    phrase_end = _participial_phrase_end(clause)
    for verb in _REPORTING_VERB_IN_CLAUSE.finditer(clause):
        if verb.group("verb").casefold() in _NOUN_AMBIGUOUS_VERBS:
            # "EIA denied reports that …" reads as a denial, not as EIA's report,
            # and the fallback cannot ask for the empty filler the main scan
            # asks for — it exists precisely because the filler is long.
            continue
        if _REPORTED_THAT.match(clause[verb.end("verb") :]) is None:
            continue
        for named in _NAME_TOKEN.finditer(clause[: verb.start("verb")]):
            if named.start() < phrase_end:
                continue
            token = named.group(0)
            name = _accepted_issuer(
                token,
                clause=clause,
                start=named.start(),
                name_end=named.end(),
                verb_start=verb.start("verb"),
                claim_places=claim_places,
            )
            if name and (_is_acronym(name) or _has_internal_capital(name)):
                return name
        return ""
    return ""


def _participial_phrase_end(clause: str) -> int:
    """Where a sentence-initial participial phrase ends, or 0 when none opens.

    "Counting projects larger than 1 MW in the electric power sector, EIA
    projected that …" states how EIA counted, and the noun "projects" inside
    the phrase is spelled exactly as the reporting verb, so the phrase's own
    head was read as the issuer and the run's twelfth claim was attributed to
    "Counting". A clause that opens with an ``-ing`` form and continues past a
    comma states a method before it states its fact, and nothing inside that
    phrase — its head, its nouns, or the unit "MW" it names — is an issuer: the
    issuer is the subject of the main clause the comma introduces.
    """
    head = _PARTICIPIAL_HEAD.match(clause)
    if head is None:
        return 0
    comma = clause.find(",", head.end())
    return comma + 1 if comma >= 0 else 0


def _opens_a_participial_phrase(clause: str, *, start: int) -> bool:
    """Whether a captured name stands inside a sentence-initial participial phrase."""
    end = _participial_phrase_end(clause)
    return 0 < end and start < end


def _introduces_a_definition(clause: str, *, verb_end: int) -> bool:
    """Whether what follows a definitional verb is the convention it states.

    "EIA counts battery storage projects larger than 1 MW …" states the rule
    EIA applies, and "EIA tracks large-scale battery storage resources …" the
    segment it keeps its own count of. A clause that ends at the verb states
    none, and a copula or auxiliary directly after it reads the verb as a noun
    ("the EIA counts were revised in February"), so neither is a definition.
    """
    rest = clause[verb_end:]
    if not rest.strip():
        return False
    return _NO_DEFINITION_AFTER_VERB.match(rest) is None


def _names_a_body(name: str, *, captured: str) -> bool:
    """Whether a name is unmistakably a body's, rather than a place or a noun.

    A definition belongs to the body that applies it, and the text has to show
    the subject is that body's name: an acronym ("EIA counts …"), a brand
    spelling ("OpenEI counts …"), a possessive ("EIA's methodology counts …"),
    or a multi-token name ("Wood Mackenzie counts …"). A lone title-case word
    is a place or a common noun — "Texas counts the most battery additions in
    2024" ranks the state and names no issuer — so it is refused here, on the
    same recall-for-refusal trade ``_SINGLE_TOKEN_NON_ISSUERS`` makes.
    """
    return (
        _is_acronym(name)
        or _has_internal_capital(name)
        or " " in name
        or _possessive_owner(captured) is not None
    )


def _subject_definitional_attribution(
    clause: str,
    *,
    claim_places: frozenset[str] = frozenset(),
) -> str:
    """The issuer a clause names as the subject of its own definitional verb.

    Every refusal the reporting form applies applies here: a determiner or
    pronoun names nobody, an earlier denial disqualifies the phrase it scopes,
    a date names nobody, and a place names nobody. Two more are its own — a
    definition has to state the convention it applies
    (:func:`_introduces_a_definition`), and its subject has to be unmistakably
    a body's name (:func:`_names_a_body`) — and a sentence-initial participial
    phrase is a method rather than an issuer for both forms
    (:func:`_opens_a_participial_phrase`).
    """
    for match in _SUBJECT_DEFINITIONAL_ATTRIBUTION.finditer(clause):
        captured = match.group("attribution")
        if _opens_a_participial_phrase(clause, start=match.start()):
            continue
        if not _introduces_a_definition(clause, verb_end=match.end("verb")):
            continue
        name = _accepted_issuer(
            captured,
            clause=clause,
            start=match.start("attribution"),
            name_end=match.end("attribution"),
            verb_start=match.start("verb"),
            claim_places=claim_places,
        )
        if name and _names_a_body(name, captured=captured):
            return name
    return ""


def _subject_verb_attribution(
    clause: str,
    *,
    claim_places: frozenset[str] = frozenset(),
) -> str:
    """The issuer named in front of its own reporting verb, if the clause has one.

    The scan is left to right, so the outermost named subject of the clause
    wins: in "Wood Mackenzie's analysis of the EIA forecast said …" the issuer
    is Wood Mackenzie, not the document it examined. Five refusals keep a
    common noun out of the attribution:

    * a single title-case token that opens the clause is a common noun —
      "Analysts reported", "Grid operators … said" — while an acronym ("EIA
      reported"), a brand spelling ("OpenEI reported") and a multi-token name
      ("Wood Mackenzie reported") are issuers wherever they stand;
    * a name that is a place names no issuer, however it is written;
    * the noun-ambiguous forms take no filler, so "EIA denied reports that …"
      stays a denial rather than becoming EIA's report;
    * a date names nobody, so a document phrase names its publisher and not the
      month it was published in;
    * a sentence-initial participial phrase is a method, so "Counting projects
      larger than 1 MW …, EIA projected …" belongs to the main clause's
      subject and not to "Counting" (:func:`_opens_a_participial_phrase`).

    A verb whose clause carries no report at all — "the EIA forecast that was
    published in February" — is refused by :func:`_introduces_a_reported_clause`,
    and a publisher whose verb stands beyond the name run's bound is still read
    from the subject phrase it opens (:func:`_issuer_in_subject_phrase`).

    A clause that states a convention instead of a report names its issuer the
    same way — "EIA counts battery storage projects larger than 1 MW …" — and
    is read by :func:`_subject_definitional_attribution` when no reporting verb
    in the clause has credited anyone.
    """
    for match in _SUBJECT_VERB_ATTRIBUTION.finditer(clause):
        captured = match.group("attribution")
        if _opens_a_participial_phrase(clause, start=match.start()):
            continue
        verb = match.group("verb").casefold()
        if verb in _NOUN_AMBIGUOUS_VERBS and clause[
            match.end("attribution") : match.start("verb")
        ].strip():
            continue
        if not _introduces_a_reported_clause(
            clause, verb_end=match.end("verb")
        ):
            continue
        name = _accepted_issuer(
            captured,
            clause=clause,
            start=match.start("attribution"),
            name_end=match.end("attribution"),
            verb_start=match.start("verb"),
            claim_places=claim_places,
        )
        if name:
            return name
    named = _subject_definitional_attribution(clause, claim_places=claim_places)
    if named:
        return named
    return _issuer_in_subject_phrase(clause, claim_places=claim_places)


def _attribution_for(
    clause: str,
    *,
    claim_issuer: str,
    claim_places: frozenset[str] = frozenset(),
) -> str:
    """The issuer one clause attributes its fact to, or "".

    Three spellings, in precedence order: the prepositional form this contract
    has always read, the named issuer in front of its reporting verb, and the
    claim's own issuer when the clause sends the reader to that issuer's
    document ("according to its January 2025 inventory"). A clause with none of
    them — a bare "it reported", a sentence with no named source — stays
    unattributed, because an attribution this contract cannot name is one it
    must not invent.
    """
    named = _clean_issuer(
        _first_group(_ATTRIBUTION, clause), claim_places=claim_places
    )
    if named:
        return named
    named = _subject_verb_attribution(clause, claim_places=claim_places)
    if named:
        return named
    if claim_issuer and _REFERENCES_ITS_OWN_DOCUMENT.search(clause):
        return claim_issuer
    return ""


def _claim_issuer(text: str, *, claim_places: frozenset[str] = frozenset()) -> str:
    """The first issuer a claim names anywhere, for its own-document clauses."""
    for clause in _split_clauses(text):
        named = _clean_issuer(
            _first_group(_ATTRIBUTION, clause), claim_places=claim_places
        )
        if not named:
            named = _subject_verb_attribution(
                clause, claim_places=claim_places
            )
        if named:
            return named
    return ""


def _canonical_place(name: str) -> str:
    """The geography a place name states, in the spelling the contract uses.

    Only the United States' own spellings are folded, and only onto the one
    spelling the frozen answer contract carries ("United States"): a clause
    that writes "U.S." and a clause that writes "in the United States" name
    one place, so the two may still merge and both match the plan's own
    "geography: United States". Every other place is kept exactly as the
    clause wrote it — this normalizes spelling, never geography.
    """
    cleaned = " ".join(name.split())
    folded = _canonical(cleaned).strip(" .")
    for suffix in ("'s", "s'", "'"):
        if folded.endswith(suffix):
            folded = folded[: -len(suffix)].strip(" .")
            break
    if folded.startswith("the "):
        folded = folded[4:]
    if folded in _UNITED_STATES_ALIASES:
        return "United States"
    return cleaned


def _reported_clause_start(clause: str) -> int:
    """Where the fact a reporting verb reports begins, or 0 when none does.

    "EIA reported that Mexico added 300 MW …" states a fact about Mexico: the
    name in front of the verb is where the clause got the fact, not what the
    clause is about, so the subject a competing name has to match starts after
    the "that" (re-review F2).
    """
    for match in _SUBJECT_VERB_ATTRIBUTION.finditer(clause):
        reports = _REPORTED_THAT.match(clause[match.end("verb") :])
        if reports is not None:
            return match.end("verb") + reports.end()
    return 0


def _clause_owner(clause: str, *, issuer: str) -> str:
    """The name that owns the fact a clause states, or "" when nobody does.

    The alias itself names no owner — it is the place being judged — so the
    clause is read with its alias spellings removed. A unit ("10.4 GW"), a
    date, a determiner or a modifier is no name either, and the issuer is the
    clause's source rather than its subject: "EIA reported that Mexico added
    300 MW" is about Mexico, while "U.S. utilities added 10.4 GW …, EIA
    reported" is about the United States and names nobody else (re-review F2
    residual and N1).
    """
    text = _UNITED_STATES.sub(" ", clause)
    issuer_tokens = {token.casefold() for token in issuer.split()}
    for named in _NAME_TOKEN.finditer(text, _reported_clause_start(text)):
        token = named.group(0)
        owner = _canonical(_possessive_owner(token) or token).strip(" .,;:")
        if owner in issuer_tokens:
            continue
        if (
            owner in _NAME_STOPWORDS
            or owner in _NAME_MODIFIER_TOKENS
            or owner in _LEADING_ARTICLES
            or owner in _NAME_SCOPE_TOKENS
            or owner in _UNIT_TOKEN_FORMS
            or _names_a_date(owner)
        ):
            continue
        return owner
    return ""


def _alias_names_the_clause(clause: str, *, issuer: str) -> bool:
    """Whether an adjectival ``U.S.`` is the clause's own place.

    Two ways to be, and one refusal the reviewer measured. The alias *is* the
    clause's place when it modifies the measurand or market the clause is about
    — "U.S. capacity additions", "the U.S. grid", "U.S. utility-scale storage"
    — or when the clause's fact names nobody else as its owner: "U.S. power
    providers added 10.3 GW", "U.S. utilities added 10.4 GW …, EIA reported",
    "In 2024 cumulative U.S. utility-scale battery storage capacity reached
    26 GW". It is an adjunct, and not the clause's place, when another name
    owns the fact: "Mexico added 300 MW … using U.S. suppliers", "Canada,
    unlike its U.S. neighbour, …", "Germany's … additions trailed U.S. levels",
    and each of those with the run's own issuer in front of it — "EIA reported
    that Mexico added …" is a clause about Mexico, because an issuer in front
    of a verb says where the clause got the fact, not what it is about. Those
    clauses are about Mexico, Canada and Germany, and reading the alias made
    each of them the United States.
    """
    match = _UNITED_STATES.search(clause)
    if match is None:
        return False
    following = clause[match.end() :].split()[:_UNITED_STATES_WINDOW]
    if _UNITED_STATES_NOUN_PATTERN.search(" ".join(following).casefold()):
        return True
    return _clause_owner(clause, issuer=issuer) == ""


def _geography_for(
    clause: str,
    *,
    claim_geography: str,
    issuer: str = "",
) -> str:
    """The place one clause states, or "".

    Three spellings, in precedence order. The prepositional locative wins
    whenever the clause states one — "U.S. firms added capacity in Canada" is
    about Canada — then the adjectival or possessive mention of the United
    States, but only where it names the clause's own place (see
    :func:`_alias_names_the_clause`), then the claim's own place when the
    clause points at it without naming it ("the nation's fleet" in a claim
    that names one country). A clause with none of them states no geography,
    and this contract does not invent one.
    """
    fact_start = _reported_clause_start(clause)
    fact = clause[fact_start:]
    named = _first_group(_GEOGRAPHY, fact)
    if named:
        return _canonical_place(named)
    if _alias_names_the_clause(fact, issuer=issuer):
        return "United States"
    # A comma-bounded locative before the issuer qualifies its reported fact;
    # a place-like word in a source title does not. A date may precede it.
    prefix = clause[:fact_start].strip()
    normalized_prefix = prefix[:1].lower() + prefix[1:]
    for leading in _GEOGRAPHY.finditer(normalized_prefix):
        if prefix[leading.end():].lstrip().startswith(","):
            return _canonical_place(leading.group("geography"))
    if claim_geography and _NATION_ANAPHOR.search(fact):
        return claim_geography
    return ""


def _claim_places(text: str) -> frozenset[str]:
    """The places a claim's own locatives name, casefolded.

    Used to keep a place from being read as an issuer: "Grid operators in
    California said …" names California as a place in the same clause, so
    California is not who reported it.
    """
    places: set[str] = set()
    for clause in _split_clauses(text):
        for match in _GEOGRAPHY.finditer(clause):
            places.add(match.group("geography").casefold())
    return frozenset(places)


def _claim_geography(text: str) -> str:
    """The first place a claim names anywhere, for its own-nation clauses."""
    for clause in _split_clauses(text):
        place = _first_group(_GEOGRAPHY, clause)
        if not place:
            place = _first_group(_UNITED_STATES, clause)
        if place:
            return _canonical_place(place)
    return ""


def _predicate_match(
    clause: str, *, anchor: re.Match[str] | None
) -> re.Match[str] | None:
    """The clause's relation word, read as the one nearest its measurement.

    A sentence-initial noun can be spelled like a relation — "Costs rose 10
    percent" matches ``costs`` before it matches ``rose`` — so the relation is
    the last candidate before the clause's value or period. Without one, the
    first candidate stands.
    """
    matches = list(_PREDICATE.finditer(clause))
    if not matches:
        return None
    if anchor is None:
        return matches[-1]
    before = [match for match in matches if match.start() < anchor.start()]
    return before[-1] if before else matches[0]


def _predicate(
    clause: str, *, anchor: re.Match[str] | None
) -> str:
    """The relation class the clause states, from the table above."""
    match = _predicate_match(clause, anchor=anchor)
    if match is None:
        return ""
    return _PREDICATE_RELATIONS[match.group("predicate").casefold()]


# The bound or approximation a clause may put on its value, longest phrase
# first so "no more than" is read as one bound rather than as "more than".
_COMPARATOR_PHRASES: tuple[tuple[str, str], ...] = (
    ("no more than", "at_most"),
    ("no less than", "at_least"),
    ("not more than", "at_most"),
    ("not less than", "at_least"),
    ("more than", "more_than"),
    ("greater than", "more_than"),
    ("higher than", "more_than"),
    ("less than", "less_than"),
    ("fewer than", "less_than"),
    ("lower than", "less_than"),
    ("at least", "at_least"),
    ("at most", "at_most"),
    ("up to", "at_most"),
    ("over", "more_than"),
    ("above", "more_than"),
    ("under", "less_than"),
    ("below", "less_than"),
    ("exceeding", "more_than"),
    ("nearly", "nearly"),
    ("almost", "nearly"),
    ("roughly", "approximately"),
    ("approximately", "approximately"),
    ("about", "approximately"),
    ("around", "approximately"),
    ("exactly", "exactly"),
)

# The relation classes whose stated value is a change of the subject rather
# than a level it reached.
_DELTA_RELATIONS = frozenset(
    {"increases_by", "decreases_by", "doubled", "tripled", "halved"}
)
_LEVEL_RELATIONS = frozenset({"reaches_level", "states_level"})


def _word_before(clause: str, *, anchor: re.Match[str] | None) -> str:
    """The clause's own words immediately before its measurement, folded.

    Read from position, not from anywhere in the clause: "held more than 10 GW"
    states a bound, while "under the new rule, capacity reached 10 GW" states
    none, and only where the phrase sits tells the two apart.
    """
    if anchor is None:
        return ""
    return " ".join(clause[: anchor.start()].split()).casefold()


def _comparator(clause: str, *, anchor: re.Match[str] | None) -> str:
    """The bound or approximation this clause puts on its value, or empty."""
    before = _word_before(clause, anchor=anchor)
    if not before:
        return ""
    for phrase, name in _COMPARATOR_PHRASES:
        if before.endswith(phrase) or before.endswith(f" {phrase}"):
            return name
    return ""


def _change_kind(
    clause: str, *, anchor: re.Match[str] | None, predicate: str
) -> str:
    """Whether the value is a level the subject reached or a change it gained.

    "rose to 10 GW" and "rose by 10 GW" write the same verb and the same
    number; the preposition decides which assertion is being made. A bound can
    sit between the two — "rose to over 10 GW" — so the bound is stripped first
    before the preposition is read. A clause that writes no preposition is read
    through its relation class, which already says whether the value is a delta
    ("grew 30 percent") or a level ("held 10 GW").
    """
    before = _word_before(clause, anchor=anchor)
    comparator_name = _comparator(clause, anchor=anchor)
    if comparator_name:
        for phrase, name in _COMPARATOR_PHRASES:
            if name == comparator_name and before.endswith(phrase):
                before = before[: -len(phrase)].rstrip()
                break
    if before.endswith(" by"):
        return "delta"
    if before.endswith(" to") or before.endswith(" at"):
        return "level"
    if predicate in _DELTA_RELATIONS:
        return "delta"
    if predicate in _LEVEL_RELATIONS:
        return "level"
    return ""


def _over_cap_subject(phrase: str) -> str:
    """The fixed-size identity of an entity phrase this contract cannot list whole.

    The phrase is available before the cap check, so it is digested rather than
    discarded: two clauses that write the same complete entity agree, and two
    that differ anywhere in it do not. A prefix would do neither — the opening
    words of two different operators' queues are identical — and an empty
    subject would do worse, because two clauses that agree on nothing are the
    permissive case this contract compares as agreement.
    """
    digest = hashlib.sha256(_canonical(phrase).encode("utf-8")).hexdigest()
    return OVER_CAP_SUBJECT_PREFIX + digest[:OVER_CAP_SUBJECT_DIGEST_CHARS]


def _trailing_entity(
    clause: str, *, excluded: Sequence[tuple[int, int]]
) -> tuple[str, SubjectState]:
    """The entity a clause names after its value and verb, and how it was read.

    "10 GW sat in the 2024 interconnection queue" and "10 GW was held by the
    wind fleet" both state their subject after the measurement. Reading it is
    what keeps an empty subject meaning "this contract could not derive it"
    rather than "there is nothing here to disagree about" — an underivable
    qualifier must not become an escape hatch for two different entities.

    The phrase runs to the clause's own boundary and is taken whole, as its
    words when this contract can list them and as their digest when it cannot.
    A clause whose entity phrase is longer than ``MAX_TRAILING_ENTITY_WORDS`` is
    therefore still compared by what it names, and a clause whose entity is
    *present* but unreadable is ``unresolved`` rather than ``absent``: two
    clauses that both name an entity this contract could not read are not two
    clauses that name the same entity.

    Not every locative head introduces an entity, so a head is skipped when the
    phrase behind it is a verb phrase ("to be added"), an attribution
    ("according to PJM"), a level the clause reached ("to 12 GW"), or another
    preposition's object rather than a noun phrase of its own.
    """
    for head in _TRAILING_ENTITY_HEAD.finditer(clause):
        if any(start <= head.start() < end for start, end in excluded):
            continue
        if _is_guarded_head(clause, head):
            continue
        tail = clause[head.end():]
        boundary = _TRAILING_ENTITY_BOUNDARY.search(tail)
        phrase = tail[: boundary.start()] if boundary is not None else tail
        tokens = list(_WORD_TOKEN.finditer(phrase))
        if not tokens:
            continue
        if any(
            start <= head.end() + tokens[0].start() < end
            for start, end in excluded
        ):
            continue
        if tokens[0].group(0).casefold() in _SUBJECT_STOPWORDS:
            # A noun phrase this head introduces does not begin with a
            # connective or a preposition: "in 2024, according to PJM" names an
            # attribution, not an entity that starts with "according".
            continue
        if len(tokens) > MAX_TRAILING_ENTITY_WORDS:
            # Longer than this contract will read, so it cannot show the
            # capture is the whole entity as a list of words — but the phrase
            # itself is right here and is taken whole, as its digest. Discarding
            # it made an identical entity indistinguishable from a different
            # one: both came back empty and the refused pair was minted as two
            # ledger rows sharing one cluster id.
            return _over_cap_subject(phrase), _SUBJECT_DERIVED
        words = [
            token.group(0)
            for token in tokens
            if token.group(0).casefold() not in _SUBJECT_STOPWORDS
        ]
        if words:
            return " ".join(words), _SUBJECT_DERIVED
    return "", _SUBJECT_ABSENT


def _is_guarded_head(clause: str, head: re.Match[str]) -> bool:
    """True when this locative head does not introduce an entity phrase.

    Only the ``to`` head needs this: ``by``/``in``/``on`` and their peers take a
    noun phrase, while ``to`` introduces a level ("rose to 10 GW"), an
    infinitive ("expected to be added"), an attribution ("according to PJM"),
    or an idiom ("up to", "prior to", "due to", "next to") at least as often as
    it introduces a place.
    """
    if not head.group(0).casefold().startswith("to"):
        return False
    before = _WORD_TOKEN.findall(clause[: head.start()])
    if before and before[-1].casefold() in _TO_HEAD_GUARD_WORDS:
        return True
    rest = clause[head.end():].lstrip()
    if not rest or rest[0].isdigit():
        return True
    token = _WORD_TOKEN.match(rest)
    if token is None or token.group(0).casefold() in _LIGHT_VERBS:
        return True
    return False


_COMPARATOR_WORDS = frozenset(
    word for phrase, _ in _COMPARATOR_PHRASES for word in phrase.split()
)
"""Every word a comparator phrase is built from, as subject stop words.

Derived from the phrase table so the two cannot drift: a comparator added
there stops a subject run here without a second edit.
"""


def _stops_subject_run(word: str) -> bool:
    """True when this word ends a subject run instead of joining it.

    ``_SUBJECT_STOPWORDS`` covers the connectives and prepositions; the
    comparator phrases are multi-word ("at least", "up to", "no less than"),
    and a run read backwards meets their last word first, so ``"Output was at
    least 10 GW"`` yielded a subject ending in ``least``. Every bounded clause
    therefore carried a corrupted subject, and ``"up to 10 GW"`` against
    ``"at least 10 GW"`` was refused by the *subject* comparison rather than by
    the qualifier dimension the comparator belongs to: right answer, accidental
    reason, and the same corruption for every other bounded phrasing. The
    table's own words are stop words too, so a comparator added later cannot be
    forgotten here.
    """
    folded = word.casefold()
    return folded in _SUBJECT_STOPWORDS or folded in _COMPARATOR_WORDS


def _run_before(
    clause: str,
    *,
    cutoff: int,
    floor: int,
    excluded: Sequence[tuple[int, int]],
) -> tuple[str, bool]:
    """The entity words in ``clause[floor:cutoff]``, and whether words were erased.

    Read right-to-left from the anchor, as the subject run always has been: a
    stopword ends the run once something has been gathered, and a word inside an
    excluded span (the period, the value, a quantity phrase's unit noun) ends it
    too. A light verb clears whatever was gathered to its right — those words
    sit on the verb phrase's side of it — and the erased flag records that real
    words were dropped. A run that is empty *because* a light verb ate it is a
    derivation failure, not a clause that names no entity: "Will County" is not
    the modal "will".

    **A run that is itself a relation word is not a subject either.** With two
    relation words before the entity ("Projected and reported wind capacity is
    10 GW") the selected relation is the later one and the run before it is the
    earlier one — a verb, not an entity — so both the wind and the solar clause
    came back as "Projected" and merged into one settled fact. A relation word
    therefore never joins the run: it ends a run that already gathered real
    entity words, and a run that found nothing but relation words is unusable
    and erases like a light verb, so the caller falls through to the gap the
    relation leaves and reports a derivation failure if that is empty too. The
    entity is never DERIVED from a relation word.
    """

    def is_excluded(position: int) -> bool:
        return any(start <= position < end for start, end in excluded)

    words = [
        (match.start(), match.group(0))
        for match in _WORD_TOKEN.finditer(clause)
        if floor <= match.start() and match.end() <= cutoff
    ]
    collected: list[str] = []
    erased = False
    for start, word in reversed(words):
        if is_excluded(start):
            break
        folded = word.casefold()
        if folded in _LIGHT_VERBS:
            if collected:
                erased = True
            collected.clear()
            continue
        if folded in _RELATION_WORDS:
            if collected:
                break
            erased = True
            continue
        if _stops_subject_run(folded):
            if collected:
                break
            continue
        collected.append(word)
        if len(collected) >= MAX_SUBJECT_WORDS:
            break
    if collected:
        return " ".join(reversed(collected)), erased
    return "", erased


def _entity_after_relation(
    clause: str,
    *,
    start: int,
    end: int,
    excluded: Sequence[tuple[int, int]],
) -> str:
    """The entity a relation word leaves between itself and the measurement.

    "Projected wind capacity is 10 GW" names its entity *after* the relation, so
    the run before the relation word is empty and there is nothing to fall back
    to but the trailing phrase — which is what made two different entities agree
    on an empty subject. The phrase is read forward from the relation word and
    stops at the first word that cannot be part of a noun phrase, so a locative
    that follows it ("… capacity in PJM is 10 GW") does not become the entity.
    """

    def is_excluded(position: int) -> bool:
        return any(left <= position < right for left, right in excluded)

    collected: list[str] = []
    for match in _WORD_TOKEN.finditer(clause, start, end):
        if is_excluded(match.start()):
            break
        folded = match.group(0).casefold()
        if folded in _LIGHT_VERBS:
            break
        if _stops_subject_run(folded):
            if collected:
                break
            continue
        collected.append(match.group(0))
        if len(collected) >= MAX_SUBJECT_WORDS:
            break
    return " ".join(collected)


def _subject(
    clause: str,
    *,
    anchor: re.Match[str] | None,
    measurement: re.Match[str] | None,
    excluded: Sequence[tuple[int, int]],
    unit_noun_excluded: bool = False,
) -> tuple[str, SubjectState]:
    """The entity the clause asserts about, and how it was derived.

    Read as the run of words immediately before the clause's predicate — or,
    failing that, before its first stated value or period — that is not a
    stopword. A word inside an excluded span (the period, the value) ends the
    run rather than being read as the subject, so "the 2024 interconnection
    queue held 10 GW" yields "interconnection queue" and not the year.

    A clause that names its entity *after* the measurement — post-verbal or
    passive — has nothing before the anchor, and falls back to the trailing
    entity phrase rather than reporting an empty subject.

    Three outcomes, and the difference between the last two is the point:

    * ``derived`` — an entity was read, and it is compared as it always was;
    * ``absent`` — the clause names no entity at all, so two such clauses may
      still agree (the round-3 control: "10 GW was held in 2024" and "10 GW sat
      in 2024" state one fact about no particular entity);
    * ``unresolved`` — an entity position exists and the derivation failed.
      That refuses, because it is the absence of evidence rather than evidence
      that the two clauses name the same thing.
    """
    if anchor is not None:
        cutoff = anchor.start()
    elif excluded:
        cutoff = min(start for start, _ in excluded)
    else:
        cutoff = len(clause)

    text, erased = _run_before(
        clause, cutoff=cutoff, floor=0, excluded=excluded
    )
    if text:
        return text, _SUBJECT_DERIVED

    # A relation word can precede the entity it relates. The run *before* the
    # word is then empty even though the clause names an entity, so the entity
    # is read from the gap the relation word leaves before the measurement.
    if (
        measurement is not None
        and anchor is not None
        and anchor.end() <= measurement.start()
    ):
        text = _entity_after_relation(
            clause,
            start=anchor.end(),
            end=measurement.start(),
            excluded=excluded,
        )
        if text:
            return text, _SUBJECT_DERIVED

    text, trailing_state = _trailing_entity(clause, excluded=excluded)
    if text:
        return text, _SUBJECT_DERIVED
    if trailing_state == _SUBJECT_UNRESOLVED:
        return "", _SUBJECT_UNRESOLVED
    if erased or unit_noun_excluded:
        # Words were gathered and cleared, or the only candidate this contract
        # found was the measured unit's own noun. Either way an entity position
        # was there and this contract could not read it.
        return "", _SUBJECT_UNRESOLVED
    return "", _SUBJECT_ABSENT


def _of_phrase(pattern: re.Pattern[str], clause: str) -> str:
    """The noun phrase after "of", when the clause states one."""
    return _first_group(pattern, clause)


def _unit_noun_span(
    clause: str,
    *,
    value_match: re.Match[str] | None,
    measured: bool,
) -> tuple[int, int] | None:
    """The span of a bare unit noun in the ``of``-phrase of a measured quantity.

    "10 GW of capacity" is one quantity phrase whose ``of`` names the unit's own
    noun, not an entity: a measured quantity is already fully qualified by its
    number and unit, so the noun behind it says what is being measured rather
    than what the clause asserts about. Only a single bare noun immediately
    after the value qualifies — "10 GW of *wind* capacity" and "10 GW of
    capacity *and output*" are qualified phrases that do discriminate, and a
    clause's own grammatical subject ("Capacity rose 10 percent") is not an
    ``of``-phrase at all and is never touched by this.
    """
    if not measured or value_match is None:
        return None
    match = _DENOMINATOR.search(clause)
    if match is None:
        return None
    if clause[value_match.end(): match.start()].strip():
        return None
    if len(_WORD_TOKEN.findall(match.group("denominator"))) != 1:
        return None
    return match.span("denominator")


def _evidence_ids_for(
    claim: Claim, evidence: tuple[EvidenceUnit, ...]
) -> list[str]:
    """The evidence units the claim's own cited passages resolve to.

    Resolved by exact (url, locator, excerpt), so an id is only ever attached
    to the passage it actually is. A passage with no matching unit contributes
    no id rather than an invented one.
    """
    by_passage = {
        (unit.source_url, unit.locator, unit.excerpt): unit.evidence_id
        for unit in evidence
    }
    found: list[str] = []
    for passage in claim.verification_evidence:
        evidence_id = by_passage.get(
            (passage.source_url, passage.locator, passage.excerpt)
        )
        if evidence_id and evidence_id not in found:
            found.append(evidence_id)
    return found


def extract_text_atoms(
    text: str,
    *,
    claim_id: str = "",
    evidence_ids: Sequence[str] = (),
    target_ids: Sequence[str] = (),
) -> list[AtomicProposition]:
    """Reduce one claim's prose to one proposition per assertion it makes.

    Every qualifier the clause carries is populated: the subject and the
    relation, the number and its unit, the observation period, a share's
    denominator or a count's population, an attribution, a stated geography, a
    forecast status, and a negation. Every evidence id the claim's cited
    passages resolve to travels with it too, so an atom can be traced back to
    the read it came from, and the parent and member claim ids keep the
    original claim addressable from either side.

    Each atom also gets a stable ``atom_id``: a compound claim becomes several
    rows, and two rows with the same text and the same id are exactly the
    ledger duplication this contract removes.
    """
    shared_evidence = list(evidence_ids)
    shared_targets = list(target_ids)
    claim_places = _claim_places(text)
    issuer = _claim_issuer(text, claim_places=claim_places)
    claim_geography = _claim_geography(text)
    atoms: list[AtomicProposition] = []
    for index, clause in enumerate(_split_clauses(text), start=1):
        # The issuer's release date precedes its reported assertion. Once a
        # reporting verb introduces "that", the observed period belongs to
        # the assertion, never to the preceding publication date.
        period_match = _PERIOD_PATTERN.search(
            clause, _reported_clause_start(clause)
        ) or _PERIOD_PATTERN.search(clause)
        period = (
            " ".join(period_match.group("period").split())
            if period_match is not None
            else ""
        )
        value, unit = _value_and_unit(clause, period=period)
        value_match = _VALUE_UNIT_PATTERN.search(clause)
        measurement = value_match or period_match
        predicate_match = _predicate_match(clause, anchor=measurement)
        present = [
            match
            for match in (predicate_match, value_match, period_match)
            if match is not None
        ]
        excluded = [match.span() for match in present]
        forecast = _first_group(_FORECAST, clause)
        # "of X" means two different things depending on what is asserted. For
        # a share it is the base the percentage is taken of. For a plain count
        # it is the population that was counted. For a measurement it is
        # neither — "10 GW of capacity" is one quantity phrase, and reading
        # "capacity" as a qualifier there would refuse a paraphrase that
        # simply did not repeat the unit's noun.
        share = _canonical_unit(unit) in ("%", "pp")
        phrase = _of_phrase(_DENOMINATOR, clause)
        measured = bool(unit) and not share
        # In "10 GW of capacity was added to the Texas grid", the only words the
        # pre-anchor run can gather are the measured unit's own noun, so both
        # grids came back as "capacity" and agreed. The bare unit noun of a
        # measured quantity is therefore not an entity: its span is excluded, so
        # the run breaks and the clause falls through to the entity it actually
        # names — and when there is none, the exclusion is remembered so the
        # clause reports UNRESOLVED rather than a clause that names nothing.
        unit_noun_span = _unit_noun_span(
            clause, value_match=value_match, measured=measured
        )
        if unit_noun_span is not None:
            excluded.append(unit_noun_span)
        subject, subject_state = _subject(
            clause,
            anchor=predicate_match or value_match or period_match,
            measurement=value_match or period_match,
            excluded=excluded,
            unit_noun_excluded=unit_noun_span is not None,
        )
        atoms.append(
            AtomicProposition(
                text=clause,
                atom_id=(f"{claim_id}#{index}" if claim_id else ""),
                subject=subject,
                subject_state=subject_state,
                predicate=_predicate(clause, anchor=measurement),
                comparator=_comparator(clause, anchor=measurement),
                change_kind=_change_kind(
                    clause,
                    anchor=measurement,
                    predicate=_predicate(clause, anchor=measurement),
                ),
                value=value,
                unit=unit,
                observation_period=period,
                geography=_geography_for(
                    clause,
                    claim_geography=claim_geography,
                    issuer=issuer,
                ),
                quantity_noun=(
                    " ".join(
                        clause[unit_noun_span[0]: unit_noun_span[1]].split()
                    )
                    if unit_noun_span is not None
                    else ""
                ),
                population="" if (share or measured) else phrase,
                denominator=phrase if share else "",
                attribution=_attribution_for(
                    clause,
                    claim_issuer=issuer,
                    claim_places=claim_places,
                ),
                forecast_status=forecast.casefold(),
                negated=_NEGATION.search(clause) is not None,
                parent_claim_id=claim_id,
                member_claim_ids=[claim_id] if claim_id else [],
                evidence_ids=list(shared_evidence),
                target_ids=list(shared_targets),
            )
        )
    return atoms


# States and grid operators a document may scope a figure to instead of the
# nation as a whole. A masthead's "U.S." reads as the country; it does not
# read as one of these, so a clause or a supporting sentence naming one is
# never handed the title's geography, however the title itself is worded.
_OTHER_PLACE_NAMES = frozenset(
    {
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
        "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
        "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
        "maine", "maryland", "massachusetts", "michigan", "minnesota",
        "mississippi", "missouri", "montana", "nebraska", "nevada",
        "new hampshire", "new jersey", "new mexico", "new york",
        "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
        "pennsylvania", "rhode island", "south carolina", "south dakota",
        "tennessee", "texas", "utah", "vermont", "virginia", "washington",
        "west virginia", "wisconsin", "wyoming",
        "ercot", "caiso", "pjm", "miso", "spp", "nyiso", "iso-ne",
    }
)
_OTHER_PLACE_PATTERN = re.compile(
    r"\b(?:"
    + "|".join(
        re.escape(name)
        for name in sorted(_OTHER_PLACE_NAMES, key=len, reverse=True)
    )
    + r")\b",
    re.IGNORECASE,
)
_GLOBAL_SCOPE = re.compile(r"\b(?:global(?:ly)?|world(?:wide)?)\b", re.IGNORECASE)


def _names_other_place(clause: str) -> bool:
    """True when the fact a clause states names a place the nation is not.

    Read only after the clause's own reporting verb — the same restriction
    :func:`_geography_for` applies to its own locative search — so a preamble
    such as "EIA's ... Today in Energy analysis states that" is never read as
    a locative itself; it is the issuer's own section title, not a place the
    fact was scoped to.
    """
    fact = clause[_reported_clause_start(clause):]
    return (
        _OTHER_PLACE_PATTERN.search(fact) is not None
        or _GLOBAL_SCOPE.search(fact) is not None
        or _GEOGRAPHY.search(fact) is not None
    )


def source_title_geography(
    atom: AtomicProposition, source_title: str, supporting_text: str
) -> str:
    """Read a measured fact's geographic context from its supporting source.

    An issuer named ``U.S. ...`` is not the geography of every measurement it
    publishes. A headline saying ``U.S. battery capacity`` does scope a
    measurement that the same source text actually states — but only when
    neither the claim's own clause nor the specific supporting sentence that
    states this value and unit names anywhere else: a state, an ISO/RTO, a
    city-like locative, or a global/worldwide scope. "14%, or 3.4 GW, in
    California" on a "New U.S. electric generating capacity" masthead is
    California's number, not the nation's, however the headline reads.
    """
    if atom.geography or not atom.value or not atom.unit:
        return ""
    value = _canonical_number(atom.value)
    unit = _canonical_unit(atom.unit)
    stated_sentence = ""
    for sentence in _sentences(supporting_text):
        if any(
            _canonical_number(match.group("value")) == value
            and _canonical_unit(match.group("unit")) == unit
            for match in _VALUE_UNIT_PATTERN.finditer(sentence)
        ):
            stated_sentence = sentence
            break
    if not stated_sentence:
        return ""
    if _names_other_place(stated_sentence) or _names_other_place(atom.text):
        return ""
    for match in _UNITED_STATES.finditer(source_title):
        following = source_title[match.end():].split()[:4]
        if re.search(r"\bcapacit(?:y|ies)\b", " ".join(following), re.I):
            return "United States"
    return ""


def extract_atoms(
    claim: Claim,
    evidence: tuple[EvidenceUnit, ...] | list[EvidenceUnit] = (),
) -> list[AtomicProposition]:
    atoms = extract_text_atoms(
        claim.text,
        claim_id=claim.claim_id,
        evidence_ids=_evidence_ids_for(claim, tuple(evidence)),
        target_ids=claim.target_ids,
    )
    for index, atom in enumerate(atoms):
        if atom.geography:
            continue
        for passage in claim.verification_evidence:
            if passage.stance != "supports":
                continue
            place = source_title_geography(
                atom, passage.source_title, passage.excerpt
            )
            if place:
                atoms[index] = atom.model_copy(update={"geography": place})
                break
    return atoms


def target_order_for(state: ResearchState) -> list[str]:
    """The frozen order of obligations one run schedules claims against.

    Section 2.3 freezes the original inventory and lets a later reviewed pass
    only *add* to it, so this reads the union of ``initial_target_ids`` and
    ``expanded_target_ids`` rather than recomputing a denominator from whatever
    the plan happens to say now. A plan written before the target inventory
    existed carries no ids at all, and falls back to its own sub-topic
    coverage ids — the same ids such a plan's claims record consuming — so a
    legacy snapshot still schedules one obligation per topic instead of
    starving every topic behind the first.
    """
    order: list[str] = []
    for target_id in (*state.initial_target_ids, *state.expanded_target_ids):
        if target_id and target_id not in order:
            order.append(target_id)
    if order:
        return order
    for topic in state.sub_topics:
        for target in topic.evidence_targets:
            if target.target_id not in order:
                order.append(target.target_id)
    if order:
        return order
    return [
        topic.coverage_id
        for topic in state.sub_topics
        if topic.coverage_id and topic.coverage_id not in order
    ]


def select_claim_batch_indices(
    obligations: Sequence[Sequence[str]],
    target_order: Sequence[str],
    limit: int,
    *,
    cursor: int = 0,
    priority: Sequence[str] = (),
) -> tuple[list[int], int]:
    """Choose at most ``limit`` items and the cursor the next batch starts at.

    ``obligations[i]`` is the set of targets item ``i`` discharges. Three
    passes, in order:

    1. every target in ``priority`` — the critical and still-unanswered ones —
       takes its earliest unselected item;
    2. ``target_order`` is walked from ``cursor``, wrapping, taking the
       earliest unselected item for each target it has not served yet;
    3. any remaining slot is filled from items that obligate nothing.

    The cursor is what stops a finite pass from starving a late target. A pass
    always starts its second pass where the previous batch stopped, so thirty
    slots handed out five at a time reach a target that only ever sits last,
    which restarting at the first target every time never would. Everything not
    chosen stays where it is: the caller keeps it pending and the next batch
    sees it again.
    """
    if limit < 1:
        raise ValueError("a claim batch must hold at least one claim")
    chosen: list[int] = []
    taken: set[int] = set()
    served: set[str] = set()

    def take(target_id: str) -> bool:
        for index, ids in enumerate(obligations):
            if index in taken or target_id not in ids:
                continue
            chosen.append(index)
            taken.add(index)
            served.add(target_id)
            return True
        return False

    for target_id in priority:
        if len(chosen) >= limit:
            return chosen, cursor
        take(target_id)

    order = list(target_order)
    start = cursor % len(order) if order else 0
    served_position: int | None = None
    for offset in range(len(order)):
        if len(chosen) >= limit:
            break
        position = (start + offset) % len(order)
        target_id = order[position]
        if target_id in served:
            continue
        if take(target_id):
            served_position = position
    next_cursor = (
        (served_position + 1) % len(order)
        if served_position is not None and order
        else start
    )

    for index, _ in enumerate(obligations):
        if len(chosen) >= limit:
            break
        if index not in taken:
            chosen.append(index)
            taken.add(index)
    return chosen, next_cursor


def select_claim_batch(
    claims: Sequence[Claim],
    target_order: Sequence[str],
    limit: int,
    *,
    priority: Sequence[str] = (),
) -> list[Claim]:
    """One outstanding obligation per target, then extra slots, then the rest pending.

    The ``Claim``-typed entry point to :func:`select_claim_batch_indices`, for
    a caller scheduling one independent batch. A caller scheduling a *sequence*
    of batches keeps the cursor the index form returns and passes it back, so
    no target can be starved by a finite pass.
    """
    picked, _ = select_claim_batch_indices(
        [claim.target_ids for claim in claims],
        target_order,
        limit,
        priority=priority,
    )
    return [claims[index] for index in picked]


def critical_target_ids(state: ResearchState) -> list[str]:
    """The targets the plan marked critical, in plan order.

    A critical obligation is one the question cannot be answered without, so
    the scheduler serves it ahead of the rotation. A plan written before the
    target inventory carries no critical flag at all, and contributes none.
    """
    critical: list[str] = []
    for topic in state.sub_topics:
        for target in topic.evidence_targets:
            if target.critical and target.target_id not in critical:
                critical.append(target.target_id)
    return critical


def stated_dimensions(proposition: AtomicProposition) -> frozenset[str]:
    """The checkable dimensions this proposition actually states.

    An assertion that states none of them can be *compatible* with another and
    still not be provably the same claim: "delays are growing" and "delays are
    increasing" agree on every dimension because neither states one. That is
    what ``uncertain`` records.

    A stated observation period is also this atom's data period — the period
    the assertion's data cover is exactly what it states — which is what makes
    a data-period obligation answerable from prose, and only when the question
    asks for it.
    """
    stated = {
        name
        for name in _COMPARED_DIMENSIONS
        if name not in _NON_CHECKABLE_DIMENSIONS
        if _canonical(getattr(proposition, name))
    }
    if _canonical_number(proposition.value):
        stated.add("value")
    if _canonical_unit(proposition.unit):
        stated.add("unit")
    if proposition.negated:
        stated.add("negated")
    if _canonical(proposition.observation_period):
        stated.add("data_period")
    return frozenset(stated)


def equivalence_strength(
    a: AtomicProposition, b: AtomicProposition
) -> str:
    """How far this contract can go towards calling ``a`` and ``b`` one claim.

    ``incompatible`` is a refusal. ``identical`` means the two agree on every
    checkable dimension and at least one of them is stated, so they are one
    assertion and merging them cannot lose evidence. ``uncertain`` means they
    do not contradict each other but this contract cannot prove they are the
    same — a diagnostic, never a hard failure, and never a silent merge.
    """
    if not atomic_compatible(a, b):
        return "incompatible"
    return "identical" if stated_dimensions(a) else "uncertain"


# How a planned obligation's ``required_dimensions`` phrase maps onto the
# dimensions an atom can be checked for. The Planner writes them either as a
# bare dimension name or as "<kind>: <detail>", so the kind is read as the
# dimension it names. Longest-first, so "percentage points" is never read as
# "points" and "geography" never as "graph".
# The dimension a period requirement is checked against. Named because
# :func:`_definitional_measure` waives exactly this one for a target that asks
# what a convention is: a definition states the rule and no observation.
_PERIOD_DIMENSION = "observation_period"
_DIMENSION_KINDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("measure", "value", "quantity", "amount", "number", "count", "cost",
      "capacity", "level", "rate", "total", "size", "price", "volume"),
     "value"),
    (("denominator", "base"), "denominator"),
    (("geography", "location", "region", "jurisdiction", "country", "area",
      "place", "market"), "geography"),
    (("population", "cohort", "sample", "subject"), "population"),
    (("source", "attribution", "issuer", "publisher", "authority", "origin"),
     "attribution"),
    (("forecast", "projection", "projected", "outlook", "horizon"),
     "forecast_status"),
    (("period", "year", "date", "vintage", "timeframe"), _PERIOD_DIMENSION),
)

# The obligations the *contract* fixes are not prose dimensions. The evidence
# period is a currency requirement on the sourcing, discharged by the source
# assessment and its temporal status, and the answer form is a shape the reader
# report has to take. Neither is something a claim's clause spells, and reading
# either as one would strip every claim of every target.
_NON_PROSE_DIMENSION_PREFIXES = ("evidence period:", "answer form:")


# The obligation a plan states with a quantity kind is checked against the
# numeric ``value`` dimension — unless the requirement's own detail asks for a
# fact. "measure: grid-scale battery power capacity added, in MW" asks how
# much, and only a number answers it; "measure: rating basis (AC or DC)",
# "measure: inclusion rule and counting treatment" and "measure: stated
# grid-connection requirement" ask what a convention IS, and a clause that
# states it without a number answers it exactly. Reading every ``measure:`` as
# numeric made four of the live plan's eleven targets unanswerable by any claim
# (audit #3, replay C10).
#
# Three explicit vocabularies decide it, in this order:
#
# * a *unit* means the requirement asks for a number, whatever convention noun
#   sits beside it: "capacity threshold applied for the utility-scale
#   classification, in megawatts" asks how many megawatts, and reading it as
#   qualitative let a forecast level with no threshold in it answer the live
#   threshold target (review rank 6);
# * a *convention* noun means the requirement asks what a rule, basis, or
#   treatment is, whatever else the detail says ("… of the reported capacity
#   figures" is about the basis, not the capacity);
# * otherwise the requirement asks for a quantity when its detail writes a
#   number or names a countable noun.
#
# A requirement written as a bare dimension name ("value", "capacity") is the
# plan's own vocabulary for the quantity itself and is never re-read as
# qualitative.
_QUANTITY_DIMENSION = "value"
# The dimension a qualitative measure obligation is checked against: the clause
# must state what it is about. It is deliberately weak — judging whether prose
# answers "what is the rating basis" is the adjudicator's work, not this
# contract's — and it still refuses a clause that names no subject at all.
_QUALITATIVE_DIMENSION = "assertion"
_CONVENTION_NOUNS = (
    "basis",
    "rule",
    "rules",
    "requirement",
    "requirements",
    "treatment",
    "definition",
    "definitions",
    "inclusion",
    "exclusion",
    "convention",
    "conventions",
    "methodology",
    "method",
    "policy",
    "classification",
    "scope",
    "criterion",
    "criteria",
    "mechanism",
    "condition",
    "treatment of",
)
_COUNTABLE_NOUNS = (
    "number",
    "count",
    "amount",
    "quantity",
    "total",
    "sum",
    "average",
    "mean",
    "median",
    "share",
    "percentage",
    "percent",
    "rate",
    "ratio",
    "index",
    "score",
    "price",
    "cost",
    "spending",
    "revenue",
    "budget",
    "funding",
    "investment",
    "production",
    "output",
    "generation",
    "consumption",
    "emissions",
    "mileage",
    "distance",
    "duration",
    "weight",
    "volume",
    "capacity",
    "level",
    "value",
    "threshold",
    "population",
    "headcount",
    "ridership",
)
_CONVENTION_NOUN_PATTERN = re.compile(
    r"(?<!\w)(?:" + "|".join(_CONVENTION_NOUNS) + r")(?!\w)"
)
_COUNTABLE_NOUN_PATTERN = re.compile(
    r"(?<!\w)(?:" + "|".join(_COUNTABLE_NOUNS) + r")(?!\w)"
)
# The unit forms, minus the bare sign, which needs its own test because "40%"
# leaves no letter boundary in front of it.
_UNIT_FORM_ALTERNATION = "|".join(
    re.escape(form) for form in _UNIT_FORMS if form != "%"
)
_UNIT_WORD_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:" + _UNIT_FORM_ALTERNATION + r")(?![a-z0-9])",
    re.IGNORECASE,
)
# Two unit spellings joined by a connector name the units a convention chooses
# between ("MW or MWh", "AC or DC kW"), so the requirement asks *which* unit the
# figures use, not how many. Read as a quantity ask it demanded a number that no
# clause about the convention states (integration review P2).
_UNIT_LIST_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:" + _UNIT_FORM_ALTERNATION + r")(?![a-z0-9])"
    r"\s*(?:,|or|/)\s*(?:the\s+)?"
    r"(?:" + _UNIT_FORM_ALTERNATION + r")(?![a-z0-9])",
    re.IGNORECASE,
)
# An amount asked for: "in megawatts", "in the reported megawatts", "how many
# megawatts", "the amount of capacity". A unit the detail merely mentions is no
# amount ask, so a convention noun beside one stays qualitative.
_AMOUNT_ASKED_PATTERN = re.compile(
    r"\bin\s+(?:the\s+)?(?:[a-z-]+\s+){0,2}?(?:" + _UNIT_FORM_ALTERNATION + r"|%)"
    r"(?![a-z0-9])"
    r"|\bhow\s+(?:many|much)\b|\bamounts?\b",
    re.IGNORECASE,
)


def _demands_a_quantity(detail: str) -> bool:
    """Whether a measure requirement's own detail asks for a number.

    The countable-noun fallback reads the measure's *head* only. A cue noun in
    a qualifier says what the count covers, not that a number is asked for:
    "facility types included in the utility-scale battery storage capacity
    count" names the types a count includes, and reading "capacity" and
    "count" out of its qualifier made it a numeric obligation that no answer
    stating those types could meet (review rank 2). A unit or a number still
    decides the whole detail, so "capacity threshold applied …, in megawatts"
    remains a quantity ask.
    """
    folded = _canonical(detail)
    if _UNIT_LIST_PATTERN.search(folded):
        return False
    if _CONVENTION_NOUN_PATTERN.search(folded):
        return _AMOUNT_ASKED_PATTERN.search(folded) is not None
    if "%" in folded or _UNIT_WORD_PATTERN.search(folded):
        return True
    if _NUMBER_TOKEN.search(folded):
        return True
    return _COUNTABLE_NOUN_PATTERN.search(measure_head(folded)) is not None


def checkable_dimensions(required_dimension: str) -> tuple[str, ...]:
    """The atom dimensions one planned requirement can be checked against.

    A requirement this contract has no counterpart for maps to nothing, and
    :func:`atom_answers_dimensions` treats that as *unmet*: an atom is not
    shown to state a dimension it cannot state. That is the conservative
    direction Section 2.3 asks for — a target stays unattributed rather than
    being credited to prose that never met it.

    A quantity requirement whose detail asks a *qualitative* question maps to
    the assertion dimension instead of the numeric value, so a clause that
    states the fact answers it; see :data:`_CONVENTION_NOUNS`.
    """
    folded = _canonical(required_dimension)
    if not folded or folded.startswith(_NON_PROSE_DIMENSION_PREFIXES):
        return ()
    found: list[str] = []
    for name in sorted(METADATA_DIMENSIONS, key=len, reverse=True):
        if name.replace("_", " ") in folded and name not in found:
            found.append(name)
    if found:
        return tuple(found)
    head, _, detail = folded.partition(":")
    for keywords, dimension in _DIMENSION_KINDS:
        if any(keyword in head for keyword in keywords):
            if (
                dimension == _QUANTITY_DIMENSION
                and detail.strip()
                and not _demands_a_quantity(detail)
            ):
                return (_QUALITATIVE_DIMENSION,)
            return (dimension,)
    if folded in _DIMENSION_NAMES:
        return (folded,)
    return ()


def _dimension_states(atom: AtomicProposition) -> frozenset[str]:
    """The dimensions an atom states, for the *coverage* check.

    :func:`stated_dimensions` is the merge-facing view and deliberately leaves
    ``subject`` and ``predicate`` out: two clauses that state neither are not
    thereby one assertion, and no merge may rest on that. Coverage asks a
    different question — does this clause assert anything about a named subject
    at all — so the qualitative assertion dimension is added here, where it can
    only ever credit an atom for a qualitative obligation. Widening the
    merge-facing view instead would make almost every compatible pair
    "identical", which is the refusal that view exists to keep.
    """
    stated = set(stated_dimensions(atom))
    if _canonical(atom.subject):
        stated.add(_QUALITATIVE_DIMENSION)
    return frozenset(stated)


def _definitional_measure(required_dimensions: Sequence[str]) -> bool:
    """Whether the target's own measures ask what a convention is.

    A classification or methodology target states the years its rule is in
    force rather than an observation its evidence has to carry: "the
    classification in force for the 2024 and 2025 data years" is about the
    rule, and "EIA counts battery storage projects larger than 1 MW …" states
    it with no period at all. Read as a period the claim has to state, such a
    requirement left the live classification target unanswerable (review
    rank 2). A quantity measure is untouched, so a figure still has to carry
    the period it is asked for.
    """
    for required in required_dimensions:
        folded = _canonical(required)
        if not folded.startswith("measure:"):
            continue
        if names_a_definition(folded.partition(":")[2].strip()):
            return True
    return False


def atom_answers_dimensions(
    atom: AtomicProposition,
    required_dimensions: Sequence[str],
    *,
    question: str,
) -> bool:
    """True when the atom states every required dimension this contract checks.

    Section 2.3: a target is answered only when its reader statement satisfies
    its required dimensions. The contract's own currency and answer-form
    obligations are not prose dimensions and are skipped; a required dimension
    the atom cannot state at all is *not* satisfied. A metadata dimension
    counts only when the question itself asks for that metadata, so a
    publication date can never stand in for a deployment mechanism.
    """
    stated = _dimension_states(atom)
    definitional = _definitional_measure(required_dimensions)
    for required in required_dimensions:
        folded = _canonical(required)
        if folded == _canonical(LEGACY_COVERAGE_DIMENSION):
            continue
        if folded.startswith(_NON_PROSE_DIMENSION_PREFIXES):
            continue
        dimensions = checkable_dimensions(required)
        if not dimensions:
            return False
        for dimension in dimensions:
            if not dimension_is_answered(
                question=question,
                dimension=dimension,
                stated_dimensions=stated,
            ) and not (definitional and dimension == _PERIOD_DIMENSION):
                return False
            if not qualifier_matches_requirement(atom, required, question=question):
                return False
    return True


def atom_satisfies_policy(atom: AtomicProposition, support_policy: str) -> bool:
    """True when the atom could carry this target's support policy.

    ``independent_pair`` needs two independent passages, which only exist after
    verification, so an atom can neither earn nor fail it here.
    ``primary_attribution`` needs the issuing body named — a claim that
    attributes nothing can never become precise primary attribution.
    ``derivation`` needs a quantity to derive from.
    """
    if support_policy == "primary_attribution":
        return bool(atom.attribution)
    if support_policy == "derivation":
        return bool(atom.value)
    return True


def atom_answers_target(
    atom: AtomicProposition, target: EvidenceTarget, *, question: str
) -> bool:
    """True when this atom could answer this target's obligation.

    Both halves have to hold: the atom states every required dimension this
    contract can check, and it could carry the target's support policy.
    """
    return atom_answers_dimensions(
        atom, target.required_dimensions, question=question
    ) and atom_satisfies_policy(atom, target.support_policy)


def equivalence_messages(
    atoms: Sequence[AtomicProposition],
) -> list[ChatMessage]:
    """Build the one bounded request that proposes duplicate pairs.

    Bounded in the atoms it lists: a pass with more atoms than the ceiling
    still makes exactly one call, over the first ``MAX_EQUIVALENCE_ATOMS`` of
    them in the order the claims were adjudicated. Nothing is dropped from the
    ledger by that bound — the atoms left out simply stay separate claims,
    which is the direction that cannot lose evidence.
    """
    listed = atoms[:MAX_EQUIVALENCE_ATOMS]
    lines: list[str] = []
    for position, atom in enumerate(listed, start=1):
        # The dimension names that are fields of the proposition itself. A
        # derived name (``data_period``, which an atom states through its
        # observation period) is reported by the field it comes from.
        stated = ", ".join(
            f"{name}={getattr(atom, name)}"
            for name in sorted(stated_dimensions(atom))
            if name in _RENDERED_DIMENSIONS
        )
        suffix = f" ({stated})" if stated else ""
        lines.append(f"{position}. {atom.text}{suffix}")
    inventory = "\n".join(lines) or "(no atomic claims)"
    sections = [
        f"# Atomic claims\n{inventory}",
        f"# Response contract\n{CLAIM_EQUIVALENCE_INSTRUCTION}",
        (
            "# Reply format\n"
            f"{render_structured_reply_format(_CLAIM_EQUIVALENCE_REPLY_EXAMPLES)}"
        ),
    ]
    return [
        ChatMessage(role="developer", content=CLAIM_EQUIVALENCE_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


class AtomicPairDraft(ContractModel):
    """One provider-proposed duplicate pair, as two positions in the list."""

    left: int
    right: int


class ClaimEquivalenceDraft(ContractModel):
    """The provider-facing shape of one equivalence proposal.

    No ``Field`` constraints: this is converted to a strict JSON schema, and
    a model that returns nothing usable must produce local diagnostics rather
    than a validation failure that discards the whole consolidation.
    """

    pairs: list[AtomicPairDraft]


class ClaimConsolidation(ContractModel):
    """Canonical claims, the clusters behind them, and the aliases between ids."""

    claims: list[Claim] = Field(default_factory=list)
    clusters: list[ClaimCluster] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)
    """Absorbed cluster id -> the surviving cluster id it resolves to."""
    diagnostics: list[str] = Field(default_factory=list)
    """Provider-independent reasons a proposal did not become a merge."""
    provider_failed: bool = False


def _accepted_pairs(
    proposal: ClaimEquivalenceDraft,
    atoms: Sequence[AtomicProposition],
    diagnostics: list[str],
) -> list[tuple[int, int]]:
    """The proposed pairs local validation did not refuse.

    The provider answers with the numbers the prompt showed it, which start at
    one; they are converted to positions here, once, so the prompt and this
    validator cannot disagree about where the list starts. A pair is refused
    when it names an atom outside the list, when it pairs an atom with itself,
    and when :func:`atomic_compatible` says the two differ in a qualifier. Each
    refusal is recorded — in the provider's own numbering, so the diagnostic
    names what it actually said — and the claims behind it publish separately.
    """
    accepted: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for pair in proposal.pairs:
        left, right = pair.left, pair.right
        if (
            not 1 <= left <= len(atoms)
            or not 1 <= right <= len(atoms)
            or left == right
        ):
            diagnostics.append(
                f"equivalence_candidate_out_of_range:{left}:{right}"
            )
            continue
        key = (min(left, right) - 1, max(left, right) - 1)
        if key in seen:
            continue
        seen.add(key)
        if not atomic_compatible(atoms[key[0]], atoms[key[1]]):
            diagnostics.append(
                f"equivalence_candidate_incompatible:{key[0] + 1}:{key[1] + 1}"
            )
            continue
        accepted.append(key)
    return accepted


def _one_row_per_identity(
    atoms: Sequence[AtomicProposition],
    groups: Sequence[Sequence[int]],
) -> list[list[int]]:
    """Fold together the atoms that mint one cluster id.

    The provider proposes which atoms *might* state one fact; it is not the
    authority on identity. ``claim_cluster_id`` is a function of the assertion
    alone, so two atoms that mint the same id ARE one assertion by this
    contract's own fingerprint, and publishing them as two rows under one id
    breaks the one-known-duplicate and one-semantic-identity rules the ledger is
    read for. The fold only ever adds an edge local code can already prove.
    """
    first_for: dict[str, int] = {}
    identical: list[tuple[int, int]] = []
    for index, atom in enumerate(atoms):
        first = first_for.setdefault(claim_cluster_id(atom), index)
        if first != index:
            identical.append((first, index))
    if not identical:
        return [list(group) for group in groups]
    edges = [
        (group[0], member)
        for group in groups
        for member in group[1:]
    ]
    return _grouped(len(atoms), [*edges, *identical])


def _grouped(count: int, pairs: Sequence[tuple[int, int]]) -> list[list[int]]:
    """Transitive closure of the accepted pairs, in first-seen order.

    An accepted pair is a validated equivalence, so if A is one fact with B
    and B is one fact with C then A, B and C are one fact; every edge was
    checked before it was used.
    """
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left, right in pairs:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)
    groups: dict[int, list[int]] = {}
    for index in range(count):
        groups.setdefault(find(index), []).append(index)
    return [groups[root] for root in sorted(groups)]


def _resolve_stored_cluster(
    cluster_id: str, existing: Sequence[ClaimCluster]
) -> ClaimCluster | None:
    """The stored cluster ``cluster_id`` resolves to, following its aliases."""
    for cluster in existing:
        if cluster.cluster_id == cluster_id or cluster_id in cluster.cluster_aliases:
            return cluster
    return None


def _union(values: Sequence[str]) -> list[str]:
    """First-seen order, without duplicates."""
    union: list[str] = []
    for value in values:
        if value and value not in union:
            union.append(value)
    return union


def resolved_verdict_and_status(cluster: ClaimCluster) -> tuple[str, str | None]:
    """The verdict and badge one cluster may publish, resolved conservatively.

    The verdict is the strongest disagreement among the members, and the badge
    is the one that verdict recorded — so a contradicted member never publishes
    as the verified pair a sibling recorded. A ``verified`` resolution whose no
    member recorded the strict badge degrades to ``unverified``: ``Claim``
    refuses the combination outright, and a settled fact with no pair behind it
    is exactly what the conservative direction must not invent.
    """
    verdict = resolved_verdict(cluster.verdicts)
    status = cluster.verdict_evidence_status.get(verdict)
    if verdict == "verified" and status != "verified_pair":
        return "unverified", None
    return verdict, status


def _canonical_claim(cluster: ClaimCluster) -> Claim:
    """The one claim snapshot a cluster publishes.

    Built from the cluster rather than from one member, because the cluster is
    what survives a pass: its proposition supplies the atom-specific text and
    its persisted provenance supplies the citations, passages, consumed
    identities, and obligations of every member, including the ones a later
    pass never resubmitted. The verdict is the conservative resolution over
    every verdict recorded, so a disagreement cannot read as a settled fact.
    """
    verdict, status = resolved_verdict_and_status(cluster)
    return Claim(
        claim_id=cluster.cluster_id,
        text=cluster.proposition.text,
        source_urls=list(cluster.source_urls),
        verdict=verdict,  # type: ignore[arg-type]
        confidence=cluster.confidence if cluster.confidence is not None else 0.0,
        evidence=[
            passage.excerpt
            for passage in cluster.verification_evidence
            if passage.stance == "supports"
        ],
        contradictions=[
            passage.excerpt
            for passage in cluster.verification_evidence
            if passage.stance == "contradicts"
        ],
        verification_evidence=list(cluster.verification_evidence),
        insufficient_reason=(
            cluster.insufficient_reason
            if verdict == "insufficient_evidence"
            else None
        ),
        evidence_status=status,  # type: ignore[arg-type]
        consumed_finding_fingerprints=list(
            cluster.consumed_finding_fingerprints
        ),
        consumed_coverage_ids=list(cluster.consumed_coverage_ids),
        target_ids=list(cluster.target_ids),
        cluster_id=cluster.cluster_id,
        cluster_aliases=list(cluster.cluster_aliases),
    )


async def consolidate_claims(
    provider: AgentCompleter,
    drafts: Sequence[Claim],
    existing: Sequence[ClaimCluster] = (),
    evidence: Sequence[EvidenceUnit] = (),
) -> ClaimConsolidation:
    """Reduce adjudicated claims to one canonical snapshot per asserted fact.

    The provider proposes which atoms might state the same thing — that is the
    judgement local code cannot make — and every proposal is validated here
    before anything merges. A refused or uncertain proposal is a diagnostic,
    never a failure: the claims behind it either publish separately (a
    refusal) or publish once through a conservative representative carrying
    the union of their provenance (uncertainty).

    The candidate list is the stored clusters' own propositions followed by
    this pass's atoms, so a refinement that submits only the NEW claim can
    still be recognised as the same fact: the merge keeps the stored cluster's
    identity and reconstructs its citations from the cluster itself.
    """
    claims = list(drafts)
    atoms: list[AtomicProposition] = []
    owners: list[int | None] = []
    stored_for: list[ClaimCluster | None] = []
    for cluster in existing:
        atoms.append(cluster.proposition)
        owners.append(None)
        stored_for.append(cluster)
    for position, claim in enumerate(claims):
        for atom in extract_atoms(claim, evidence):
            atoms.append(atom)
            owners.append(position)
            stored_for.append(None)

    diagnostics: list[str] = []
    provider_failed = False
    groups = [[index] for index in range(len(atoms))]
    if len(atoms) > 1:
        try:
            proposal = await provider.complete_structured(
                equivalence_messages(atoms),
                ClaimEquivalenceDraft,
                agent_name=CONSOLIDATION_AGENT_NAME,
            )
        except ProviderError:
            provider_failed = True
            diagnostics.append("equivalence_provider_failed")
        else:
            groups = _grouped(
                len(atoms), _accepted_pairs(proposal, atoms, diagnostics)
            )

    # Identity is local, and it is not the provider's to grant: two atoms that
    # mint one cluster id are one assertion whether or not the provider noticed.
    groups = _one_row_per_identity(atoms, groups)

    clusters: list[ClaimCluster] = []
    aliases: dict[str, str] = {}
    canonical: list[Claim] = []
    # Every atom gets a real, monotonic creation sequence, so a member cluster
    # is never minted with the "nobody stamped this" default and the folded
    # survivor's sequence is the minimum of the sequences that actually exist.
    base_seq = max(
        (cluster.created_seq for cluster in existing), default=0
    ) + 1
    for position, group in enumerate(groups):
        anchor_index = group[0]
        anchor_atom = atoms[anchor_index]
        anchor_claim = (
            claims[owners[anchor_index]]
            if owners[anchor_index] is not None
            else None
        )
        cluster = cluster_for_atom(
            anchor_atom,
            claim=anchor_claim,
            created_seq=base_seq + anchor_index,
        )
        for index in group[1:]:
            if equivalence_strength(anchor_atom, atoms[index]) == "uncertain":
                cluster = cluster.model_copy(
                    update={
                        "status": "duplicate_representative",
                        "diagnostics": sorted(
                            {
                                *cluster.diagnostics,
                                "equivalence_candidate_uncertain:"
                                f"{group[0] + 1}:{index + 1}",
                            }
                        ),
                    }
                )
            member_claim = (
                claims[owners[index]] if owners[index] is not None else None
            )
            cluster = merge_claim_clusters(
                cluster,
                cluster_for_atom(
                    atoms[index],
                    claim=member_claim,
                    created_seq=base_seq + index,
                ),
            )
        # Every stored cluster in this group is older than anything this pass
        # built, and among themselves the oldest is the one whose identity
        # survives. Folding them in oldest-first — rather than in the order
        # they happened to arrive — is what makes that true.
        stored = [
            stored_for[index]
            for index in group
            if stored_for[index] is not None
        ]
        for source in reversed(oldest_first(stored)):
            cluster = merge_claim_clusters(source, cluster)
        if len(cluster.verdicts) > 1:
            cluster = cluster.model_copy(
                update={
                    "status": "contested",
                    "diagnostics": sorted(
                        {
                            *cluster.diagnostics,
                            *_verdict_disagreements(cluster),
                        }
                    ),
                }
            )
        for alias in cluster.cluster_aliases:
            aliases[alias] = cluster.cluster_id
        clusters.append(cluster)
        if not cluster.source_urls:
            diagnostics.append(
                f"cluster_without_citations:{cluster.cluster_id}"
            )
            continue
        canonical.append(_canonical_claim(cluster))

    return ClaimConsolidation(
        claims=canonical,
        clusters=clusters,
        aliases=aliases,
        diagnostics=sorted(set(diagnostics)),
        provider_failed=provider_failed,
    )


def reverification_cache_key(
    *,
    proposition: str,
    evidence_content: Sequence[str],
    assessment_revision: str,
    temporal_scope: str,
    prompt_version: str,
    critique: str = "",
) -> str:
    """The cache key one claim's verdict is stored and reused under.

    Every component is a reason a stored verdict might no longer be the
    verdict this pass would reach: the proposition itself, the content of the
    evidence it was judged against, Task 4's ``assessment_revision`` (the
    recorded evidence that the source's body, metadata, or dates changed), the
    temporal scope the judgement was made in, and the verification
    prompt/schema version it was made under.

    ``critique`` is accepted and deliberately *not* part of the digest. A
    critique that only rewords the request — "re-verify this claim", "please
    double-check" — asks for the same judgement of the same evidence, and
    letting its wording change the key would make every restatement of the
    same request buy a fresh paid verification.
    """
    parts = [
        _canonical(proposition),
        *(_canonical(text) for text in evidence_content),
        _canonical(assessment_revision),
        _canonical(temporal_scope),
        _canonical(prompt_version),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


# The dimensions that describe the record rather than the world. Section 2.3:
# publication date, data period, forecast horizon, effective policy date,
# retrieval date, and generation date are six different facts, and none of
# them is evidence about a substantive dimension. Metadata is context unless
# the question asks for metadata.
METADATA_DIMENSIONS = frozenset(
    {
        "publication_date",
        "data_period",
        "forecast_horizon",
        "effective_date",
        "retrieval_date",
        "generation_date",
    }
)

# How a question asks for each metadata dimension, as (temporal intent, the
# dimension's own words). Both have to be present. A question that merely
# *names* the dimension's subject — "Who published the report?" — is not asking
# when it was published, and a substring test on "publish" alone read it as
# one. Deliberately a small explicit marker list rather than a similarity
# score: this decides whether a date may be used as an ANSWER, and a wrong yes
# here lets a publication date stand in for a mechanism.
_METADATA_QUESTION_MARKERS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "publication_date": (
        ("when", "what date", "which date", "date of", "publication date",
         "release date", "how recent", "how old", "as of what date"),
        ("publish", "publication", "release", "released", "issued", "issue"),
    ),
    "data_period": (
        ("when", "what period", "which period", "what date", "date of",
         "data period", "as of", "timeframe", "time frame", "how recent"),
        ("data", "period", "cover", "covers", "vintage"),
    ),
    "forecast_horizon": (
        ("when", "what period", "which period", "what year", "how far ahead",
         "forecast horizon", "projection horizon", "horizon", "date of"),
        ("forecast", "projection", "projected", "outlook", "horizon"),
    ),
    "effective_date": (
        ("when", "what date", "which date", "date of", "effective date",
         "as of what date"),
        ("effective", "effect", "in force", "govern", "governs", "appl"),
    ),
    "retrieval_date": (
        ("when", "what date", "which date", "date of", "retrieval date"),
        ("retriev", "read", "fetch", "accessed"),
    ),
    "generation_date": (
        ("when", "what date", "which date", "date of", "generation date"),
        ("generat", "produced", "written", "created"),
    ),
}


def metadata_dimension_asked_for(question: str, dimension: str) -> bool:
    """True when the question asks for this metadata dimension *as a date*.

    Naming the dimension's subject is not enough: "Who published the report?"
    asks who, not when, and a question that only mentions a publisher must not
    be answered with the publication date.
    """
    intent, words = _METADATA_QUESTION_MARKERS.get(dimension, ((), ()))
    if not intent:
        return False
    folded = _canonical(question)
    return any(cue in folded for cue in intent) and any(
        word in folded for word in words
    )


def dimension_is_answered(
    *,
    question: str,
    dimension: str,
    stated_dimensions: Collection[str],
) -> bool:
    """True when the evidence answers ``dimension`` for ``question``.

    Two rules, and both are needed. Evidence answers a dimension only by
    stating it — a publication date cannot stand in for a deployment
    mechanism, however recent the report. And a *metadata* dimension is
    answered only when the question asks for it: a report's publication date
    is context for a question about grid costs, and is the answer to a
    question about when the report was published.
    """
    stated = {_canonical(name) for name in stated_dimensions}
    if dimension not in stated:
        return False
    if dimension in METADATA_DIMENSIONS:
        return metadata_dimension_asked_for(question, dimension)
    return True
