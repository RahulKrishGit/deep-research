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

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from deep_research.agents.identity import claim_cluster_id
from deep_research.utils.types import (
    AtomicProposition,
    Claim,
    ClaimCluster,
    EvidenceUnit,
    ResearchState,
)

__all__ = [
    "atomic_compatible",
    "claim_cluster_id",
    "cluster_for_atom",
    "extract_atoms",
    "merge_claim_clusters",
    "select_claim_batch",
    "select_claim_batch_indices",
    "target_order_for",
]


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


# The dimensions compared as written text. ``text`` is deliberately absent: it
# is the surface paraphrase, and comparing it would refuse the duplicate this
# contract exists to find.
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


def cluster_for_atom(
    atom: AtomicProposition,
    *,
    claim_id: str = "",
    status: str = "canonical",
) -> ClaimCluster:
    """Mint the cluster one atom anchors, with everything the atom carries."""
    members = set(atom.member_claim_ids)
    if claim_id:
        members.add(claim_id)
    elif atom.parent_claim_id:
        members.add(atom.parent_claim_id)
    return ClaimCluster(
        cluster_id=claim_cluster_id(atom),
        proposition=atom,
        evidence_ids=sorted(set(atom.evidence_ids)),
        member_claim_ids=sorted(members),
        target_ids=sorted(set(atom.target_ids)),
        status=status,  # type: ignore[arg-type]
    )


def merge_claim_clusters(
    existing: ClaimCluster, incoming: ClaimCluster
) -> ClaimCluster:
    """Fold ``incoming`` into ``existing``, keeping the oldest stable identity.

    ``existing`` is the older cluster by construction — consolidation walks
    its atoms in first-seen order — so its ``cluster_id`` is the survivor and
    ``incoming``'s becomes an alias. Its proposition is the survivor's too:
    the anchor is what the identity was minted from, and letting a later
    paraphrase rewrite it would drift the cluster away from its own id.

    ``evidence_ids``, ``member_claim_ids``, and ``target_ids`` are unioned and
    sorted, so the result is a deterministic function of the two inputs and
    two passes that saw the same atoms in a different order agree.
    """
    aliases = set(existing.cluster_aliases) | set(incoming.cluster_aliases)
    if incoming.cluster_id != existing.cluster_id:
        aliases.add(incoming.cluster_id)
    return existing.model_copy(
        update={
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
            "diagnostics": sorted(
                set(existing.diagnostics) | set(incoming.diagnostics)
            ),
        }
    )


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
_ATTRIBUTION = re.compile(
    r"\b(?:according to|published by|reported by|per)\s+(?P<attribution>"
    r"[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,4})"
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


def _observation_period(clause: str) -> str:
    match = _PERIOD_PATTERN.search(clause)
    if match is None:
        return ""
    return " ".join(match.group("period").split())


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


def extract_atoms(
    claim: Claim,
    evidence: tuple[EvidenceUnit, ...] | list[EvidenceUnit] = (),
) -> list[AtomicProposition]:
    """Reduce one claim's prose to one proposition per assertion it makes.

    Every qualifier the clause carries travels with it: the number, its unit,
    the observation period, a share's denominator, an attribution, a stated
    geography or population, a forecast status, and a negation. Every evidence
    id the claim's cited passages resolve to travels with it too, so an atom
    can be traced back to the read it came from, and the parent and member
    claim ids keep the original claim addressable from either side.
    """
    units = tuple(evidence)
    shared_evidence = _evidence_ids_for(claim, units)
    atoms: list[AtomicProposition] = []
    for clause in _split_clauses(claim.text):
        period = _observation_period(clause)
        value, unit = _value_and_unit(clause, period=period)
        forecast = _first_group(_FORECAST, clause)
        atoms.append(
            AtomicProposition(
                text=clause,
                value=value,
                unit=unit,
                observation_period=period,
                geography=_first_group(_GEOGRAPHY, clause),
                denominator=_first_group(_DENOMINATOR, clause),
                attribution=_first_group(_ATTRIBUTION, clause),
                forecast_status=forecast.casefold(),
                negated=_NEGATION.search(clause) is not None,
                parent_claim_id=claim.claim_id,
                member_claim_ids=[claim.claim_id],
                evidence_ids=list(shared_evidence),
                target_ids=list(claim.target_ids),
            )
        )
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
) -> list[int]:
    """Choose at most ``limit`` items, one per outstanding target first.

    ``obligations[i]`` is the set of targets item ``i`` discharges, and the
    returned indices are in the order they were chosen: the first pass walks
    ``target_order`` and takes the earliest unselected item for each target,
    and only then does a second pass fill the remaining slots from items that
    obligate nothing (or whose target never appears in the order).

    That ordering is the whole point. A rare-but-critical target's only claim
    is chosen in the first pass, while a second low-value claim about an
    already-served topic can only ever be an extra slot — so a batch can never
    spend its whole allowance on the first topic and leave a later one
    untouched. Everything not chosen is left where it was: the caller keeps it
    pending, and the next batch sees it again.
    """
    if limit < 1:
        raise ValueError("a claim batch must hold at least one claim")
    chosen: list[int] = []
    taken: set[int] = set()
    served: set[str] = set()
    for target_id in target_order:
        if len(chosen) >= limit:
            return chosen
        if target_id in served:
            continue
        for index, ids in enumerate(obligations):
            if index in taken or target_id not in ids:
                continue
            chosen.append(index)
            taken.add(index)
            served.add(target_id)
            break
    for index, _ in enumerate(obligations):
        if len(chosen) >= limit:
            break
        if index not in taken:
            chosen.append(index)
            taken.add(index)
    return chosen


def select_claim_batch(
    claims: Sequence[Claim],
    target_order: Sequence[str],
    limit: int,
) -> list[Claim]:
    """One outstanding obligation per target, then extra slots, then the rest pending.

    The ``Claim``-typed entry point to :func:`select_claim_batch_indices`. A
    caller that schedules claim *work* the same way — the Fact Checker
    schedules adjudicated and unadjudicated claims alike — passes the same
    obligation lists to the index form and keeps the items it did not get
    back.
    """
    picked = select_claim_batch_indices(
        [claim.target_ids for claim in claims], target_order, limit
    )
    return [claims[index] for index in picked]
