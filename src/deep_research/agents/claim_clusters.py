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
    """
    if a.negated != b.negated:
        return False
    if _canonical_number(a.value) != _canonical_number(b.value):
        return False
    if _canonical_unit(a.unit) != _canonical_unit(b.unit):
        return False
    for name in _COMPARED_DIMENSIONS:
        if _canonical(getattr(a, name)) != _canonical(getattr(b, name)):
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
    if claim is not None:
        verdict_evidence[claim.verdict] = _claim_citations(claim)
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
    *lowest*-priority answer, never a winning one: it sorts after every stamped
    cluster and is ordered among its peers by the identity it was minted from,
    which no caller can reorder. Consolidation mints a real sequence for every
    cluster it builds, so in production this fallback is never reached.
    """
    return [
        cluster
        for _, cluster in sorted(
            enumerate(clusters),
            key=lambda pair: (
                pair[1].created_seq == 0,
                pair[1].created_seq,
                # Among the unstamped, the registry position *is* the caller's
                # own order, so it would hand the identity to whichever came
                # first. The id is a stable tie-break instead.
                pair[1].cluster_id if pair[1].created_seq == 0 else "",
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
) -> bool:
    """Whether an *adjudicated* claim satisfies a target's support policy.

    Called after verification, never before: a policy constrains the evidence
    that exists, and a claim with no verdict has none. Every policy requires a
    verified claim — an ``insufficient_evidence`` one retrieved nothing
    independent, a ``contradicted`` one has evidence against it, and an
    ``unverified`` one has evidence that does not address it — and
    ``independent_pair`` additionally needs two supporting publishers, because
    one publisher is one source and a pair of passages from it is not a pair.
    """
    if verdict != "verified":
        return False
    if support_policy == "independent_pair":
        return supporting_publishers >= 2
    if support_policy in ("primary_attribution", "derivation"):
        return True
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
_GEOGRAPHY = re.compile(
    r"\b(?:in|across|within|for)\s+(?P<geography>"
    r"(?:the\s+)?[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,4})"
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

# Where an entity sits when the clause puts it after the value and the verb:
# "10 GW sat in the 2024 interconnection queue", "10 GW was held by the wind
# fleet". Read only when nothing precedes the anchor, so a clause that already
# named its subject is never overridden by a locative tail.
#
# The phrase is read to the clause's own boundary and kept whole, or not at all.
# A fixed-length prefix of a long entity is the dangerous shape: "California
# Independent System Operator" out of "… Operator interconnection queue" is
# nonempty, plausible, and silently identical to the opening words of a
# different entity — an interconnection queue and a transmission queue merged,
# and so did the operator named on its own. An empty subject REFUSES a merge
# against a stated one, so a capture this contract cannot show is the whole
# entity yields EMPTY rather than its first words.
_TRAILING_ENTITY_HEAD = re.compile(
    r"\b(?:by|in|at|on|within|across|for)\s+(?:the\s+)?"
    r"(?:(?:19|20)\d{2}\s+)?",
    re.IGNORECASE,
)
# Where the noun phrase stops: the clause's own punctuation. This is a real
# boundary, so it completes the capture rather than truncating it.
_TRAILING_ENTITY_BOUNDARY = re.compile(r"[,;:.!?()\[\]\u2013\u2014]")
MAX_TRAILING_ENTITY_WORDS = 12


def _split_clauses(text: str) -> list[str]:
    """Split a claim's prose into one clause per assertion it makes.

    Two shapes are split, and only two: a sentence or a semicolon-separated
    observation, and a causal assertion stated after its connective. Splitting
    on every "and" would cut a list of three minerals into three claims, which
    is the opposite of atomizing.
    """
    clauses: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for stated in re.split(r"\s*;\s*", sentence):
            clauses.extend(_CAUSAL_CONNECTIVE.split(stated))
    cleaned: list[str] = []
    for clause in clauses:
        stripped = clause.strip()
        if stripped.endswith("."):
            stripped = stripped[:-1].strip()
        if stripped:
            cleaned.append(stripped)
    return cleaned


def _value_and_unit(clause: str, *, period: str) -> tuple[str, str]:
    """The one number and unit this clause states, if it states exactly one.

    The observation period's own year is not a value: "the 2024 queue" states
    a period, not a measurement. A clause stating two measurements is left
    with none rather than with a guess, because the compatibility check treats
    an unstated dimension as unstated and refuses accordingly.
    """
    period_digits = set(re.findall(r"\d+", period))
    found: list[tuple[str, str]] = []
    for match in _VALUE_UNIT_PATTERN.finditer(clause):
        number = match.group("value")
        if number in period_digits:
            continue
        found.append((number, match.group("unit")))
    if len(found) != 1:
        return ("", "")
    value, unit = found[0]
    return (" ".join(value.split()), " ".join(unit.split()))


def _first_group(pattern: re.Pattern[str], clause: str) -> str:
    match = pattern.search(clause)
    if match is None:
        return ""
    return " ".join(match.group(1).split())


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


def _trailing_entity(
    clause: str, *, excluded: Sequence[tuple[int, int]]
) -> str:
    """The entity a clause names after its value and verb, or empty.

    "10 GW sat in the 2024 interconnection queue" and "10 GW was held by the
    wind fleet" both state their subject after the measurement. Reading it is
    what keeps an empty subject meaning "this contract could not derive it"
    rather than "there is nothing here to disagree about" — an underivable
    qualifier must not become an escape hatch for two different entities.

    The phrase runs to the clause's own boundary and is taken whole. A capture
    that would be cut short is no subject at all: a plausible prefix of a long
    entity matches entities it does not name, while an empty subject refuses the
    merge it would have wrongly allowed.
    """
    for head in _TRAILING_ENTITY_HEAD.finditer(clause):
        if any(start <= head.start() < end for start, end in excluded):
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
        if len(tokens) > MAX_TRAILING_ENTITY_WORDS:
            # Longer than this contract will read, so it cannot show the
            # capture is the whole entity. It states none.
            return ""
        words = [
            token.group(0)
            for token in tokens
            if token.group(0).casefold() not in _SUBJECT_STOPWORDS
        ]
        if words:
            return " ".join(words)
    return ""


def _subject(
    clause: str,
    *,
    anchor: re.Match[str] | None,
    excluded: Sequence[tuple[int, int]],
) -> str:
    """The entity the clause asserts about.

    Read as the run of words immediately before the clause's predicate — or,
    failing that, before its first stated value or period — that is not a
    stopword. A word inside an excluded span (the period, the value) ends the
    run rather than being read as the subject, so "the 2024 interconnection
    queue held 10 GW" yields "interconnection queue" and not the year.

    A clause that names its entity *after* the measurement — post-verbal or
    passive — has nothing before the anchor, and falls back to the trailing
    entity phrase rather than reporting an empty subject.
    """
    if anchor is not None:
        cutoff = anchor.start()
    elif excluded:
        cutoff = min(start for start, _ in excluded)
    else:
        cutoff = len(clause)

    def is_excluded(position: int) -> bool:
        return any(start <= position < end for start, end in excluded)

    words = [
        (match.start(), match.group(0))
        for match in _WORD_TOKEN.finditer(clause)
        if match.end() <= cutoff
    ]
    collected: list[str] = []
    for start, word in reversed(words):
        if is_excluded(start):
            break
        folded = word.casefold()
        # A light verb is noise: not an entity, and not the boundary of one
        # either — the determiner in front of the entity is. Anything gathered
        # to its right is on the verb phrase's side of it, not the entity's, so
        # "delays are growing" keeps "delays" and drops the participle.
        if folded in _LIGHT_VERBS:
            collected.clear()
            continue
        if folded in _SUBJECT_STOPWORDS:
            if collected:
                break
            continue
        collected.append(word)
        if len(collected) >= MAX_SUBJECT_WORDS:
            break
    if collected:
        return " ".join(reversed(collected))
    return _trailing_entity(clause, excluded=excluded)


def _of_phrase(pattern: re.Pattern[str], clause: str) -> str:
    """The noun phrase after "of", when the clause states one."""
    return _first_group(pattern, clause)


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
    atoms: list[AtomicProposition] = []
    for index, clause in enumerate(_split_clauses(text), start=1):
        period_match = _PERIOD_PATTERN.search(clause)
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
        atoms.append(
            AtomicProposition(
                text=clause,
                atom_id=(f"{claim_id}#{index}" if claim_id else ""),
                subject=_subject(
                    clause,
                    anchor=predicate_match or value_match or period_match,
                    excluded=excluded,
                ),
                predicate=_predicate(clause, anchor=measurement),
                value=value,
                unit=unit,
                observation_period=period,
                geography=_first_group(_GEOGRAPHY, clause),
                population="" if (share or measured) else phrase,
                denominator=phrase if share else "",
                attribution=_first_group(_ATTRIBUTION, clause),
                forecast_status=forecast.casefold(),
                negated=_NEGATION.search(clause) is not None,
                parent_claim_id=claim_id,
                member_claim_ids=[claim_id] if claim_id else [],
                evidence_ids=list(shared_evidence),
                target_ids=list(shared_targets),
            )
        )
    return atoms


def extract_atoms(
    claim: Claim,
    evidence: tuple[EvidenceUnit, ...] | list[EvidenceUnit] = (),
) -> list[AtomicProposition]:
    """Reduce one adjudicated claim to its atomic propositions."""
    return extract_text_atoms(
        claim.text,
        claim_id=claim.claim_id,
        evidence_ids=_evidence_ids_for(claim, tuple(evidence)),
        target_ids=claim.target_ids,
    )


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
    (("period", "year", "date", "vintage", "timeframe"), "observation_period"),
)

# The obligations the *contract* fixes are not prose dimensions. The evidence
# period is a currency requirement on the sourcing, discharged by the source
# assessment and its temporal status, and the answer form is a shape the reader
# report has to take. Neither is something a claim's clause spells, and reading
# either as one would strip every claim of every target.
_NON_PROSE_DIMENSION_PREFIXES = ("evidence period:", "answer form:")


def checkable_dimensions(required_dimension: str) -> tuple[str, ...]:
    """The atom dimensions one planned requirement can be checked against.

    A requirement this contract has no counterpart for maps to nothing, and
    :func:`atom_answers_dimensions` treats that as *unmet*: an atom is not
    shown to state a dimension it cannot state. That is the conservative
    direction Section 2.3 asks for — a target stays unattributed rather than
    being credited to prose that never met it.
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
    head = folded.split(":", 1)[0]
    for keywords, dimension in _DIMENSION_KINDS:
        if any(keyword in head for keyword in keywords):
            return (dimension,)
    if folded in _DIMENSION_NAMES:
        return (folded,)
    return ()


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
    stated = stated_dimensions(atom)
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
            ):
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


def _canonical_claim(cluster: ClaimCluster) -> Claim:
    """The one claim snapshot a cluster publishes.

    Built from the cluster rather than from one member, because the cluster is
    what survives a pass: its proposition supplies the atom-specific text and
    its persisted provenance supplies the citations, passages, consumed
    identities, and obligations of every member, including the ones a later
    pass never resubmitted. The verdict is the conservative resolution over
    every verdict recorded, so a disagreement cannot read as a settled fact.
    """
    verdict = resolved_verdict(cluster.verdicts)
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
