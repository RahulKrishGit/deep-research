# Source Evaluator And Fact Checker Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the two evidence-quality agents that sit between the Researcher and the Synthesizer — `SourceEvaluatorAgent`, which groups `state.raw_findings` by source URL and writes a `ScoredSource` (or an explicit low-confidence flag) for every source, and `FactCheckerAgent`, which extracts the major factual claims and assigns each one a `verified` / `unverified` / `contradicted` / `insufficient_evidence` verdict backed by independent sources.

**Architecture:** Both agents follow the `planner.py` / `researcher.py` template exactly: a module of pure helpers and prompt-facing draft models, then a `BaseAgent` subclass that overrides `run` to add progress events. The division of labour is the same one the Researcher already uses — the model judges what needs judgement, and this project computes what is countable. `sources.py` (new) owns URL normalization, grouping, and the deterministic corroboration score. `source_evaluator.py` runs **no ReAct loop**: grouping, corroboration, and the memory reputation lookup are deterministic, and a single structured-output call supplies authority/recency/relevance/rationale, which local code clamps, blends with any prior reputation, and folds into `overall_score`. `fact_checker.py` runs **one bounded ReAct loop per claim** (mirroring `ResearcherAgent`'s loop-per-sub-topic), then one structured verification call over that loop's real tool payloads; verdicts are normalized locally so a contradiction can never be reported as `verified` and a claim with no independent source can never be reported as anything but `insufficient_evidence`.

**Tech Stack:** Python 3.11+, Pydantic 2, OpenAI Python SDK (through the existing `OpenAIChatProvider`), LangSmith SDK 0.10+ (through the existing `Tracker`), pytest, pytest-asyncio (strict mode — every async test needs `@pytest.mark.asyncio`), Ruff

## Global Constraints

- Preserve `requires-python = ">=3.11"`. No new runtime dependencies: this plan adds zero entries to `pyproject.toml`.
- No LangGraph, FastAPI, or Streamlit import anywhere in `src/deep_research/agents/`. Both agents are plain async library code.
- **No report synthesis and no Critic routing.** Spec 10 owns those. This plan writes `evaluated_sources` and `verified_claims` into `ResearchStateUpdate` and stops there.
- **No external fact database integration.** Cross-referencing uses only the existing `web_search`, `web_scraper`, `document_reader`, and `query_memory` tools.
- Never send a domain type to the provider. `ScoredSource` and `Claim` declare `Field(min_length=1)` and `UnitScore` constraints that render as `minLength` / `minimum` / `maximum`, which strict structured outputs reject. Ask for the constraint-free `*Draft` mirrors and validate locally, exactly as `ResearchPlanDraft` and `SubTopicFindingsDraft` already do.
- Recorded `ResearchError.details` and `ResearchEvent.metadata` carry counts, identifiers, and enumerated reasons only — never `str(exception)` and never raw provider text.
- Tool failures are observations; they never stop a loop. Only provider failures are non-recoverable.
- Every model is a `ContractModel` subclass (`extra="forbid"`, `str_strip_whitespace=True`, `validate_default=True`).
- All scores are `UnitScore` — a float in `[0.0, 1.0]`, inclusive. This is the project's existing scale (`Finding.confidence`, `SourceReputation.reputation_score`, `MemorySnapshot.known_source_reputations`); do not introduce a 0-100 scale.
- The four verdict strings are exactly `ClaimVerdict = Literal["verified", "unverified", "contradicted", "insufficient_evidence"]` from `deep_research.utils.types`. Never spell `insufficient_evidence` any other way.
- Ruff `select = ["E", "F", "I"]`, line length 88. Imports must be isort-ordered.
- No test may make a real OpenAI, Tavily, LangSmith, or HTTP network call. Every test constructs `Tracker(LangSmithRuntimeConfig(tracing_enabled=False, ...))` via the existing `tracker` fixture in `tests/test_agents/conftest.py`, and uses `tests/agent_fakes.py` / `tests/research_fakes.py` doubles.
- `tests/test_imports.py::test_agent_submodule_public_names_all_reach_all` walks a hard-coded submodule list and asserts every public module-level name is in `deep_research.agents.__all__`. Every new module and every new public constant, function, and class in this plan must be added to both.

## Decisions And Assumptions

Recorded here because the spec does not settle them.

1. **Score scale is `UnitScore` (0.0-1.0).** `ScoredSource` already exists in `src/deep_research/utils/types.py:90-98` with all five score fields typed `UnitScore`, and `SourceReputation.reputation_score` uses the same scale, so reputation blends into authority without a conversion. A 0-100 scale would need one.
2. **`ScoredSource` gains `low_confidence: bool = False`.** The spec's acceptance criterion is "a score **or explicit low-confidence flag**", and the Synthesizer/Critic must not have to re-derive the evaluator's threshold. Defaulted, so every existing construction site keeps working. Task 1.
3. **`ScoredSource` and `Claim` already exist** and are already wired into `ResearchState.evaluated_sources` / `verified_claims`, `ResearchStateUpdate`, and `_APPEND_STATE_FIELDS`. This plan adds one field and nothing else to the state contract.
4. **Corroboration is computed, not asked for.** A model cannot count how many *other* domains covered the same sub-topic; local code can. `corroboration_score` is the fraction of a source's sub-topics that at least one finding from a *different* domain also covers. Authority, recency, and relevance stay with the model.
5. **Recency is judged from content, not from `Finding.extracted_at`.** `extracted_at` is when *we* pulled the page, not when the source was published, so it carries no recency signal. The prompt asks the model to judge recency from the source's own dating.
6. **`overall_score` is computed locally** as `0.35*authority + 0.15*recency + 0.30*relevance + 0.20*corroboration`. Weights sum to exactly 1.0, so the result is in `[0, 1]` by construction given clamped inputs. Asking the model for an overall score too would let it contradict its own dimensions.
7. **Prior reputation blends into `authority_score`, not into `overall_score`.** `authority = 0.6*model_authority + 0.4*prior` when a prior exists. This keeps `overall_score` a pure function of the four recorded dimensions, so a `ScoredSource` record is internally checkable.
8. **The Source Evaluator runs no ReAct loop.** Its only external read is a per-URL reputation lookup, which is an exact-id `get_source_reputation(url)` call, not a semantic `query_memory` search. Handing that to a model would make "every source gets scored" non-deterministic and would make "reputation lookup failed, continue with direct scoring" impossible to express. It still subclasses `BaseAgent` and implements all four hooks; it overrides `run` the way `ResearcherAgent` already does.
9. **Reputation arrives through an injected `ReputationSource` protocol**, satisfied structurally by the existing `deep_research.memory.long_term.LongTermMemory`. `state.memory_context.known_source_reputations` is used as a zero-I/O seed; a live lookup overrides it. **Unknown:** who populates `memory_context` is spec 11's (orchestration) business and is not settled anywhere in this repo yet — this plan reads it defensively and never requires it.
10. **A search hit counts as retrieved evidence.** `web_search` returns a title and a content snippet per result, which is real retrieved text. Requiring a full scrape before a source counts as "independent" would push almost every claim to `insufficient_evidence`. Trade-off accepted and pinned by a test.
11. **A claim whose model verdict is `verified` but which carries contradictions is downgraded to `contradicted`.** Local, deterministic, and testable; the spec's contradiction handling requirement has no other enforcement point.
12. **Sources past `max_sources` and sources the model omitted still get a record** — a `low_confidence=True` fallback with an enumerated reason — because the acceptance criterion is "*every* source used by findings".

## Design Trade-Offs

- **New prompt text lives in `prompts.py`, message assembly stays in the agent module.** `prompts.py` is the project's pure-rendering boundary. `planner.py` and `researcher.py` keep their own prompt constants for historical reasons; those are **not** being moved (YAGNI, and moving them would churn two passing test modules). New constants and the three new pure renderers go to `prompts.py`; `scoring_messages` / `claim_extraction_messages` / `claim_verification_messages` stay beside the agents that own them, mirroring `researcher.extraction_messages`.
- **`fact_checker.py` imports `merge_react_runs` and `render_evidence` from `researcher.py`.** Both are already public, already exported, and already generic despite their home module. Re-implementing them would be a DRY violation; relocating them would churn `researcher.py` and its tests for no behavioural gain.
- **`SourceEvaluationTask` and `ClaimTask` extend `AgentTask`.** Carrying the groups (or the claim) on the task is what lets `finalize(task, run)` know what it is finalizing without the agent holding mutable state across await points — the same reason `SubTopicTask` exists.
- **`score_sources` and `verify_claim` return `(records, errors, provider_failed)` triples**, and `finalize` is a thin adapter over them. The `finalize` hook signature has nowhere to return recoverable errors; `ResearcherAgent.extract_findings` already solved this the same way.
- **The Fact Checker gets no `save_to_memory`.** It reads evidence; it does not write findings. Spec 09's scope lists no memory write.

## File Structure

- Modify `src/deep_research/utils/types.py:90-98` — add `low_confidence: bool = False` to `ScoredSource`.
- Modify `tests/test_types.py` — pin the new field's default and bounds.
- Create `src/deep_research/agents/sources.py` — `SourceGroup`, `normalize_source_url`, `source_domain`, `group_findings_by_url`, `corroboration_score`. Pure, no I/O, no provider.
- Create `tests/test_agents/test_sources.py`.
- Modify `src/deep_research/agents/prompts.py` — seven new prompt constants and three new pure renderers.
- Modify `tests/test_agents/test_prompts.py`.
- Create `src/deep_research/agents/source_evaluator.py` — draft models, scoring maths, fallbacks, events, errors, `SourceEvaluatorAgent`.
- Create `tests/test_agents/test_source_evaluator.py`.
- Create `src/deep_research/agents/fact_checker.py` — claim drafts, extraction, evidence/independence helpers, verdict resolution, events, errors, `FactCheckerAgent`.
- Create `tests/test_agents/test_fact_checker.py`.
- Modify `tests/research_fakes.py` — `FakeReputationSource`, `fact_checker_tools`.
- Create `tests/test_agents/test_evidence_quality_seam.py` — Researcher output feeds Source Evaluator feeds Fact Checker.
- Modify `src/deep_research/agents/__init__.py` — public exports for all three new modules.
- Modify `tests/test_imports.py` — extend the import list, the submodule list, and the identity test.
- Modify `README.md` — document both agents, their events, and the phase line.

---

### Task 1: The Explicit Low-Confidence Flag On `ScoredSource`

**Files:**
- Modify: `src/deep_research/utils/types.py:90-98`
- Modify: `tests/test_types.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ScoredSource.low_confidence: bool = False`. Every later task sets it explicitly; it defaults to `False` so existing construction sites in `tests/test_state.py` keep validating.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_types.py`:

```python
def test_scored_source_defaults_to_not_low_confidence() -> None:
    source = ScoredSource(
        url="https://example.org/a",
        title="A",
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        corroboration_score=0.5,
        overall_score=0.76,
        rationale="Peer-reviewed and corroborated.",
    )

    assert source.low_confidence is False


def test_scored_source_records_an_explicit_low_confidence_flag() -> None:
    source = ScoredSource(
        url="https://example.org/b",
        title="B",
        authority_score=0.1,
        recency_score=0.0,
        relevance_score=0.2,
        corroboration_score=0.0,
        overall_score=0.095,
        rationale="Anonymous blog with no corroboration.",
        low_confidence=True,
    )

    assert source.low_confidence is True
```

If `ScoredSource` is not already imported in `tests/test_types.py`, add it to the existing `from deep_research.utils.types import (...)` block.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_types.py -k low_confidence -v`
Expected: FAIL with `ValidationError: Extra inputs are not permitted [type=extra_forbidden ... low_confidence]` on the second test, and `AttributeError`/`ValidationError` on the first.

- [ ] **Step 3: Write minimal implementation**

In `src/deep_research/utils/types.py`, replace the `ScoredSource` body:

```python
class ScoredSource(ContractModel):
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authority_score: UnitScore
    recency_score: UnitScore
    relevance_score: UnitScore
    corroboration_score: UnitScore
    overall_score: UnitScore
    rationale: str = Field(min_length=1)
    low_confidence: bool = False
    """True when this source must not be leaned on without corroboration.

    Set explicitly by ``SourceEvaluatorAgent`` — either because
    ``overall_score`` fell under its threshold, or because the source could
    not be scored by the model at all. Downstream agents read this flag
    rather than re-deriving the evaluator's threshold.
    """
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_types.py tests/test_state.py -v`
Expected: PASS (all of them — the default keeps every existing `ScoredSource` construction valid)

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/utils/types.py tests/test_types.py
git commit -m "feat: flag low-confidence sources on ScoredSource"
```

---

### Task 2: URL Grouping And The Deterministic Corroboration Score

**Files:**
- Create: `src/deep_research/agents/sources.py`
- Create: `tests/test_agents/test_sources.py`

**Interfaces:**
- Consumes: `deep_research.utils.types.ContractModel`, `Finding`.
- Produces, all importable from `deep_research.agents.sources`:
  - `normalize_source_url(url: str) -> str`
  - `source_domain(url: str) -> str`
  - `SourceGroup(ContractModel)` with fields `url: str`, `domain: str`, `title: str`, `sub_topics: list[str]`, `findings: list[Finding]`
  - `group_findings_by_url(findings: Sequence[Finding]) -> list[SourceGroup]`
  - `corroboration_score(group: SourceGroup, groups: Sequence[SourceGroup]) -> float`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agents/test_sources.py`:

```python
"""Tests for URL normalization, source grouping, and corroboration."""

from __future__ import annotations

import pytest

from deep_research.agents.sources import (
    SourceGroup,
    corroboration_score,
    group_findings_by_url,
    normalize_source_url,
    source_domain,
)
from deep_research.utils.types import Finding

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _finding(
    url: str,
    *,
    sub_topic: str = "Alpha",
    title: str = "QEC 2025",
    content: str = "Logical error rates fell below break-even.",
) -> Finding:
    return Finding(
        content=content,
        source_url=url,
        source_title=title,
        extracted_at=EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.ORG/a/", "https://example.org/a"),
        ("https://www.example.org/a", "https://example.org/a"),
        ("https://example.org:443/a", "https://example.org/a"),
        ("http://example.org:80/a", "http://example.org/a"),
        ("https://example.org/a#section", "https://example.org/a"),
        ("https://example.org/a?q=1", "https://example.org/a?q=1"),
        ("  https://example.org/  ", "https://example.org"),
        ("not a url at all", "not a url at all"),
    ],
)
def test_url_normalization_is_canonical_and_total(raw: str, expected: str) -> None:
    assert normalize_source_url(raw) == expected


def test_source_domain_strips_scheme_port_and_www() -> None:
    assert source_domain("https://www.Example.ORG:443/a") == "example.org"
    assert source_domain("opaque source") == "opaque source"


def test_findings_group_by_normalized_url_in_first_seen_order() -> None:
    groups = group_findings_by_url(
        [
            _finding("https://example.org/a", sub_topic="Alpha"),
            _finding("https://other.test/b", sub_topic="Beta"),
            _finding("https://WWW.example.org/a/", sub_topic="Beta"),
        ]
    )

    assert [group.url for group in groups] == [
        "https://example.org/a",
        "https://other.test/b",
    ]
    assert groups[0].domain == "example.org"
    assert groups[0].sub_topics == ["Alpha", "Beta"]
    assert len(groups[0].findings) == 2


def test_group_title_is_the_first_non_blank_source_title() -> None:
    groups = group_findings_by_url(
        [
            _finding("https://example.org/a", title="  "),
            _finding("https://example.org/a", title="Real Title"),
        ]
    )

    assert groups[0].title == "Real Title"


def test_group_title_falls_back_to_the_url() -> None:
    groups = group_findings_by_url([_finding("https://example.org/a", title=" ")])

    assert groups[0].title == "https://example.org/a"


def test_corroboration_is_the_fraction_of_sub_topics_other_domains_cover() -> None:
    groups = group_findings_by_url(
        [
            _finding("https://example.org/a", sub_topic="Alpha"),
            _finding("https://example.org/a", sub_topic="Beta"),
            _finding("https://other.test/b", sub_topic="Alpha"),
        ]
    )

    assert corroboration_score(groups[0], groups) == pytest.approx(0.5)
    assert corroboration_score(groups[1], groups) == pytest.approx(1.0)


def test_the_same_domain_never_corroborates_itself() -> None:
    groups = group_findings_by_url(
        [
            _finding("https://example.org/a", sub_topic="Alpha"),
            _finding("https://example.org/b", sub_topic="Alpha"),
        ]
    )

    assert corroboration_score(groups[0], groups) == pytest.approx(0.0)


def test_corroboration_of_a_group_with_no_sub_topics_is_zero() -> None:
    empty = SourceGroup(
        url="https://example.org/a",
        domain="example.org",
        title="A",
    )

    assert corroboration_score(empty, [empty]) == pytest.approx(0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.agents.sources'`

- [ ] **Step 3: Write minimal implementation**

Create `src/deep_research/agents/sources.py`:

```python
"""Source identity, grouping, and corroboration — pure, offline helpers.

``Finding.source_url`` is whatever a model reported, so two findings can
name the same page three different ways. Everything downstream keys on
``normalize_source_url``'s output instead, which is the canonical URL that
lands in ``ScoredSource.url``.

Nothing here performs I/O, reads a clock, or calls a provider, so grouping
and corroboration are deterministic functions of ``state.raw_findings``.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field

from deep_research.utils.types import ContractModel, Finding

_DEFAULT_PORTS = {"http": "80", "https": "443"}


def normalize_source_url(url: str) -> str:
    """Return a canonical form of ``url``, or the collapsed input verbatim.

    Total by design: a model may report a source that is not a URL at all
    (a book title, a file name). Those are returned whitespace-collapsed
    rather than rejected, so no finding is ever dropped for having an
    unusual source.
    """
    collapsed = " ".join(url.split())
    parts = urlsplit(collapsed)
    if not parts.scheme or not parts.hostname:
        return collapsed

    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    port = parts.port
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def source_domain(url: str) -> str:
    """Return the registrable-ish host for ``url``, or the normalized input.

    Not a public-suffix parse: ``a.example.co.uk`` and ``b.example.co.uk``
    are treated as different domains. That is deliberately conservative —
    it can only *under*-count corroboration, never invent it.
    """
    normalized = normalize_source_url(url)
    parts = urlsplit(normalized)
    return parts.hostname or normalized


class SourceGroup(ContractModel):
    """Every finding this research pass drew from one canonical source."""

    url: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    title: str = Field(min_length=1)
    sub_topics: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)


def group_findings_by_url(findings: Sequence[Finding]) -> list[SourceGroup]:
    """Fold findings into one group per canonical URL, first-seen order.

    ``title`` is the first non-blank ``source_title`` in the group, falling
    back to the URL: ``SourceGroup.title`` and ``ScoredSource.title`` both
    require a non-blank string, and a model that returned a blank title
    must not be able to fail validation for the whole run.
    """
    grouped: dict[str, SourceGroup] = {}
    for finding in findings:
        url = normalize_source_url(finding.source_url)
        group = grouped.get(url)
        if group is None:
            group = SourceGroup(url=url, domain=source_domain(url), title=url)
            grouped[url] = group
        if group.title == url and finding.source_title.strip():
            group.title = finding.source_title.strip()
        if finding.related_sub_topic not in group.sub_topics:
            group.sub_topics.append(finding.related_sub_topic)
        group.findings.append(finding)
    return list(grouped.values())


def corroboration_score(
    group: SourceGroup,
    groups: Sequence[SourceGroup],
) -> float:
    """Fraction of ``group``'s sub-topics another domain also covered.

    In ``[0.0, 1.0]`` by construction, and ``0.0`` for a group covering no
    sub-topic at all. A second page on the same domain is not corroboration.
    """
    if not group.sub_topics:
        return 0.0
    covered = sum(
        1
        for sub_topic in group.sub_topics
        if any(
            other.domain != group.domain and sub_topic in other.sub_topics
            for other in groups
        )
    )
    return covered / len(group.sub_topics)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/test_sources.py -v && ruff check src/deep_research/agents/sources.py tests/test_agents/test_sources.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/sources.py tests/test_agents/test_sources.py
git commit -m "feat: group findings by canonical source url"
```

---

### Task 3: Source Scoring And Claim Prompt Templates

**Files:**
- Modify: `src/deep_research/agents/prompts.py` (constants after `REACT_RESPONSE_CONTRACT`, renderers after `render_memory_guidance`)
- Modify: `tests/test_agents/test_prompts.py` (append)

**Interfaces:**
- Consumes: `SourceGroup` from `deep_research.agents.sources` (Task 2); `summarize_text` from `deep_research.agents.steps`; `Finding`, `ScoredSource` from `deep_research.utils.types`.
- Produces, all importable from `deep_research.agents.prompts`:
  - `SOURCE_EVALUATOR_SYSTEM_PROMPT: str`
  - `SOURCE_SCORING_INSTRUCTION: str`
  - `FACT_CHECKER_SYSTEM_PROMPT: str`
  - `CLAIM_EXTRACTION_SYSTEM_PROMPT: str`
  - `CLAIM_EXTRACTION_INSTRUCTION: str`
  - `CLAIM_VERIFICATION_SYSTEM_PROMPT: str`
  - `CLAIM_VERIFICATION_INSTRUCTION: str`
  - `render_source_dossier(group: SourceGroup, *, index: int, corroboration: float, reputation: float | None, excerpt_chars: int = 400) -> str`
  - `render_finding_digest(findings: Sequence[Finding], *, limit: int = 200) -> str`
  - `render_source_quality(sources: Sequence[ScoredSource]) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents/test_prompts.py`. Add the nine new names to the existing `from deep_research.agents.prompts import (...)` block, and add `from deep_research.agents.sources import SourceGroup` plus `Finding` and `ScoredSource` to the `deep_research.utils.types` import.

```python
PROMPT_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _prompt_finding(
    *,
    content: str = "Logical error rates fell below break-even.",
    url: str = "https://example.org/a",
    sub_topic: str = "Alpha",
) -> Finding:
    return Finding(
        content=content,
        source_url=url,
        source_title="QEC 2025",
        extracted_at=PROMPT_EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
    )


def test_source_dossier_renders_every_scoring_input() -> None:
    group = SourceGroup(
        url="https://example.org/a",
        domain="example.org",
        title="QEC 2025",
        sub_topics=["Alpha"],
        findings=[_prompt_finding()],
    )

    rendered = render_source_dossier(
        group, index=2, corroboration=0.5, reputation=0.9
    )

    assert "Source 2: https://example.org/a" in rendered
    assert "Title: QEC 2025" in rendered
    assert "Cited for: Alpha" in rendered
    assert "Corroboration (computed): 0.50" in rendered
    assert "Known reputation: 0.90" in rendered
    assert "Logical error rates fell below break-even." in rendered


def test_source_dossier_says_so_when_no_reputation_is_known() -> None:
    group = SourceGroup(
        url="https://example.org/a", domain="example.org", title="A"
    )

    rendered = render_source_dossier(
        group, index=1, corroboration=0.0, reputation=None
    )

    assert "Known reputation: none on record" in rendered
    assert "(no findings)" in rendered


def test_source_dossier_clamps_long_finding_text() -> None:
    group = SourceGroup(
        url="https://example.org/a",
        domain="example.org",
        title="A",
        sub_topics=["Alpha"],
        findings=[_prompt_finding(content="x" * 500)],
    )

    rendered = render_source_dossier(
        group, index=1, corroboration=0.0, reputation=None, excerpt_chars=50
    )

    assert "x" * 500 not in rendered
    assert "..." in rendered


def test_finding_digest_numbers_findings_and_names_their_sources() -> None:
    rendered = render_finding_digest(
        [
            _prompt_finding(sub_topic="Alpha"),
            _prompt_finding(content="Second claim.", sub_topic="Beta"),
        ]
    )

    assert "1. [Alpha] Logical error rates fell below break-even." in rendered
    assert "(https://example.org/a)" in rendered
    assert "2. [Beta] Second claim." in rendered


def test_finding_digest_handles_an_empty_list() -> None:
    assert render_finding_digest([]) == "(no findings)"


def test_source_quality_marks_low_confidence_sources() -> None:
    rendered = render_source_quality(
        [
            ScoredSource(
                url="https://example.org/a",
                title="A",
                authority_score=0.9,
                recency_score=0.8,
                relevance_score=0.9,
                corroboration_score=1.0,
                overall_score=0.9,
                rationale="Strong.",
            ),
            ScoredSource(
                url="https://weak.test/b",
                title="B",
                authority_score=0.1,
                recency_score=0.1,
                relevance_score=0.1,
                corroboration_score=0.0,
                overall_score=0.08,
                rationale="Weak.",
                low_confidence=True,
            ),
        ]
    )

    assert "https://example.org/a: 0.90" in rendered
    assert "https://weak.test/b: 0.08 (LOW CONFIDENCE)" in rendered


def test_source_quality_handles_an_empty_list() -> None:
    assert render_source_quality([]) == "(no sources scored)"


def test_new_prompt_constants_state_their_contracts() -> None:
    # The scoring call must never be asked for a combined score: this
    # project computes overall_score from the four recorded dimensions.
    assert "overall" not in SOURCE_SCORING_INSTRUCTION
    assert "authority" in SOURCE_SCORING_INSTRUCTION
    assert "between 0 and 1" in SOURCE_SCORING_INSTRUCTION
    assert "exact url" in SOURCE_EVALUATOR_SYSTEM_PROMPT
    assert "independent" in FACT_CHECKER_SYSTEM_PROMPT
    assert "retrieved findings" in CLAIM_EXTRACTION_SYSTEM_PROMPT
    assert "empty list" in CLAIM_EXTRACTION_INSTRUCTION
    assert "invent" in CLAIM_VERIFICATION_SYSTEM_PROMPT
    for verdict in ("verified", "unverified", "contradicted",
                    "insufficient_evidence"):
        assert verdict in CLAIM_VERIFICATION_INSTRUCTION
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_prompts.py -v`
Expected: FAIL with `ImportError: cannot import name 'SOURCE_EVALUATOR_SYSTEM_PROMPT' from 'deep_research.agents.prompts'`

- [ ] **Step 3: Write minimal implementation**

In `src/deep_research/agents/prompts.py`, extend the imports:

```python
from deep_research.agents.sources import SourceGroup
from deep_research.utils.types import (
    ContractModel,
    Finding,
    MemorySnapshot,
    ScoredSource,
)
```

Append these constants after `REACT_RESPONSE_CONTRACT`:

```python
SOURCE_EVALUATOR_SYSTEM_PROMPT = (
    "You are the source evaluator of a multi-agent research system. You "
    "judge how much each source behind the collected findings can be "
    "trusted.\n"
    "You are shown one dossier per source: its URL, its title, the "
    "sub-topics it was cited for, an excerpt of every finding drawn from "
    "it, a corroboration score this system already computed, and any "
    "reputation previous sessions recorded for it.\n"
    "Score only what the dossier supports. Do not assume a publisher you "
    "were not told about, and never invent a source that is not listed. "
    "Return one score object per listed source, using the exact url string "
    "from its dossier."
)

SOURCE_SCORING_INSTRUCTION = (
    "For each listed source return authority, recency, and relevance as "
    "numbers between 0 and 1, plus a one- or two-sentence rationale.\n"
    "authority: how much the publisher's identity, expertise, and "
    "editorial process justify trust. Peer-reviewed venues, standards "
    "bodies, and primary institutional publications score high; anonymous "
    "posts, content farms, and vendor marketing score low.\n"
    "recency: how current the source's own content is for this question, "
    "judged from the dates, versions, and events its excerpts mention — "
    "not from when this system retrieved it. Use 0.5 when the excerpts "
    "carry no dating signal at all.\n"
    "relevance: how directly the excerpts answer the sub-topics the source "
    "was cited for, rather than merely mentioning them.\n"
    "Corroboration is computed for you and is not yours to return. Neither "
    "is the combined score.\n"
    "rationale: name the concrete signals you used. Never restate the "
    "numbers alone."
)

FACT_CHECKER_SYSTEM_PROMPT = (
    "You are the fact checker of a multi-agent research system. You verify "
    "exactly one claim at a time against sources independent of the ones "
    "that made it.\n"
    "Use web_search to find sources that could confirm or refute the "
    "claim, web_scraper to read a promising page, document_reader for PDFs "
    "and data files, and query_memory to recall what previous sessions "
    "established.\n"
    "A page from the claim's own publisher is not independent "
    "corroboration; look for a different organisation. Actively look for "
    "evidence that the claim is wrong, not only evidence that it is "
    "right.\n"
    "Finish once you have retrieved enough independent material to judge "
    "the claim, or once no further source is worth retrieving."
)

CLAIM_EXTRACTION_SYSTEM_PROMPT = (
    "You extract the major factual claims from a completed research pass. "
    "A claim is a specific, checkable statement of fact — a number, a "
    "date, an attribution, a causal assertion — not a summary, an opinion, "
    "or a restatement of the research question.\n"
    "Every claim must come from the retrieved findings you are shown, and "
    "every source URL you attach must be one of the URLs listed with those "
    "findings. Return an empty list rather than inventing a claim or a URL."
)

CLAIM_EXTRACTION_INSTRUCTION = (
    "Return the most load-bearing factual claims in the findings — the "
    "ones a reader would most want checked before trusting the report.\n"
    "Write each claim as one self-contained sentence that can be checked "
    "without reading the rest of the findings. Merge findings that state "
    "the same fact into a single claim carrying every source URL that "
    "stated it.\n"
    "Prefer claims drawn from sources marked LOW CONFIDENCE: those are the "
    "ones most in need of independent checking.\n"
    "Attach at least one source URL to every claim, copied exactly from "
    "the findings. Return an empty list when the findings support no "
    "checkable claim."
)

CLAIM_VERIFICATION_SYSTEM_PROMPT = (
    "You judge one claim against the evidence a verification loop actually "
    "retrieved. Report only what that evidence states.\n"
    "If the evidence does not settle the claim, say so. Never invent "
    "confidence, and never treat the claim's own sources as confirmation "
    "of themselves."
)

CLAIM_VERIFICATION_INSTRUCTION = (
    "Return one verdict for the claim, chosen from exactly these "
    "strings:\n"
    "verified: independent retrieved evidence states the claim.\n"
    "unverified: independent evidence was retrieved but none of it "
    "addresses the claim either way.\n"
    "contradicted: independent retrieved evidence states something "
    "incompatible with the claim.\n"
    "insufficient_evidence: nothing independent was retrieved, or what was "
    "retrieved is too thin to judge.\n"
    "Also return confidence as a number between 0 and 1, an evidence list "
    "quoting or closely paraphrasing the independent passages supporting "
    "your verdict, and a contradictions list holding every independent "
    "passage that conflicts with the claim. Leave a list empty rather than "
    "filling it with restatements of the claim."
)
```

Append these renderers after `render_memory_guidance`:

```python
def render_source_dossier(
    group: SourceGroup,
    *,
    index: int,
    corroboration: float,
    reputation: float | None,
    excerpt_chars: int = 400,
) -> str:
    """Render everything the model may use to score one source.

    Written as an explicit loop rather than a comprehension: each finding
    excerpt has to be clamped before interpolation, and Python 3.11
    f-strings cannot hold a multi-line call expression.
    """
    lines = [
        f"Source {index}: {group.url}",
        f"Title: {group.title}",
        f"Cited for: {', '.join(group.sub_topics) or 'no sub-topic'}",
        f"Findings drawn from it: {len(group.findings)}",
        f"Corroboration (computed): {corroboration:.2f}",
    ]
    if reputation is None:
        lines.append("Known reputation: none on record")
    else:
        lines.append(f"Known reputation: {reputation:.2f}")
    lines.append("Excerpts:")
    if not group.findings:
        lines.append("- (no findings)")
    for finding in group.findings:
        lines.append(f"- {summarize_text(finding.content, limit=excerpt_chars)}")
    return "\n".join(lines)


def render_finding_digest(
    findings: Sequence[Finding],
    *,
    limit: int = 200,
) -> str:
    """Render findings as one numbered, sub-topic-tagged line each."""
    lines: list[str] = []
    for position, finding in enumerate(findings, start=1):
        content = summarize_text(finding.content, limit=limit)
        lines.append(
            f"{position}. [{finding.related_sub_topic}] {content} "
            f"({finding.source_url})"
        )
    return "\n".join(lines) or "(no findings)"


def render_source_quality(sources: Sequence[ScoredSource]) -> str:
    """Render scored sources so weak ones are visible in a prompt."""
    lines: list[str] = []
    for source in sources:
        flag = " (LOW CONFIDENCE)" if source.low_confidence else ""
        lines.append(f"- {source.url}: {source.overall_score:.2f}{flag}")
    return "\n".join(lines) or "(no sources scored)"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/test_prompts.py -v && ruff check src/deep_research/agents/prompts.py tests/test_agents/test_prompts.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/prompts.py tests/test_agents/test_prompts.py
git commit -m "feat: add source scoring and claim prompt templates"
```

---

### Task 4: Source Scoring Maths, Draft Contracts, And Fallback Records

**Files:**
- Create: `src/deep_research/agents/source_evaluator.py` (pure module contents only — the agent class arrives in Task 5)
- Create: `tests/test_agents/test_source_evaluator.py`

**Interfaces:**
- Consumes: `SourceGroup`, `normalize_source_url` from `deep_research.agents.sources`; `summarize_text` from `deep_research.agents.steps`; `ScoredSource`, `ContractModel` from `deep_research.utils.types`.
- Produces, all importable from `deep_research.agents.source_evaluator`:
  - `SOURCE_EVALUATOR_NAME: str = "source_evaluator"`
  - `LOW_CONFIDENCE_THRESHOLD: float = 0.4`
  - `AUTHORITY_WEIGHT = 0.35`, `RECENCY_WEIGHT = 0.15`, `RELEVANCE_WEIGHT = 0.30`, `CORROBORATION_WEIGHT = 0.20`, `REPUTATION_BLEND = 0.4`
  - `DEFAULT_MAX_SOURCES: int = 12`, `DEFAULT_EXCERPT_CHARS: int = 400`
  - `FALLBACK_REASONS: dict[str, str]`
  - `SourceScoreDraft(ContractModel)` — `url: str`, `authority_score: float`, `recency_score: float`, `relevance_score: float`, `rationale: str`
  - `SourceScoresDraft(ContractModel)` — `sources: list[SourceScoreDraft]`
  - `EvaluatedSources(ContractModel)` — `sources: list[ScoredSource]`
  - `clamp_unit(value: float) -> float`
  - `blend_authority(model_authority: float, reputation: float | None) -> float`
  - `overall_score(*, authority: float, recency: float, relevance: float, corroboration: float) -> float`
  - `build_rationale(model_rationale: str, *, corroboration: float, reputation: float | None, sub_topics: Sequence[str]) -> str`
  - `build_scored_source(group: SourceGroup, draft: SourceScoreDraft, *, corroboration: float, reputation: float | None) -> ScoredSource`
  - `fallback_scored_source(group: SourceGroup, *, corroboration: float, reputation: float | None, reason: str) -> ScoredSource`
  - `average_score(sources: Sequence[ScoredSource]) -> float`
  - `low_confidence_count(sources: Sequence[ScoredSource]) -> int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agents/test_source_evaluator.py`:

```python
"""Tests for the Source Evaluator's scoring maths and record building."""

from __future__ import annotations

import pytest

from deep_research.agents.source_evaluator import (
    AUTHORITY_WEIGHT,
    CORROBORATION_WEIGHT,
    LOW_CONFIDENCE_THRESHOLD,
    RECENCY_WEIGHT,
    RELEVANCE_WEIGHT,
    REPUTATION_BLEND,
    SourceScoreDraft,
    average_score,
    blend_authority,
    build_rationale,
    build_scored_source,
    clamp_unit,
    fallback_scored_source,
    low_confidence_count,
    overall_score,
)
from deep_research.agents.sources import SourceGroup
from deep_research.utils.types import Finding, ScoredSource

EVAL_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _group(
    *,
    url: str = "https://example.org/a",
    sub_topics: list[str] | None = None,
) -> SourceGroup:
    topics = sub_topics if sub_topics is not None else ["Alpha"]
    return SourceGroup(
        url=url,
        domain="example.org",
        title="QEC 2025",
        sub_topics=topics,
        findings=[
            Finding(
                content="Logical error rates fell below break-even.",
                source_url=url,
                source_title="QEC 2025",
                extracted_at=EVAL_EXTRACTED_AT,
                confidence=0.8,
                related_sub_topic=topic,
            )
            for topic in topics
        ],
    )


def _draft(
    *,
    url: str = "https://example.org/a",
    authority: float = 0.8,
    recency: float = 0.6,
    relevance: float = 0.9,
    rationale: str = "Peer-reviewed venue with dated results.",
) -> SourceScoreDraft:
    return SourceScoreDraft(
        url=url,
        authority_score=authority,
        recency_score=recency,
        relevance_score=relevance,
        rationale=rationale,
    )


def test_the_weights_are_a_convex_combination() -> None:
    total = (
        AUTHORITY_WEIGHT
        + RECENCY_WEIGHT
        + RELEVANCE_WEIGHT
        + CORROBORATION_WEIGHT
    )

    assert total == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(-3.0, 0.0), (0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (7.5, 1.0)],
)
def test_clamp_unit_pins_every_value_into_the_unit_interval(
    raw: float, expected: float
) -> None:
    assert clamp_unit(raw) == pytest.approx(expected)


def test_overall_score_is_the_weighted_mean_of_the_four_dimensions() -> None:
    score = overall_score(
        authority=0.8, recency=0.6, relevance=0.9, corroboration=0.5
    )

    assert score == pytest.approx(
        0.35 * 0.8 + 0.15 * 0.6 + 0.30 * 0.9 + 0.20 * 0.5
    )


def test_overall_score_stays_in_bounds_for_extreme_inputs() -> None:
    assert overall_score(
        authority=1.0, recency=1.0, relevance=1.0, corroboration=1.0
    ) == pytest.approx(1.0)
    assert overall_score(
        authority=0.0, recency=0.0, relevance=0.0, corroboration=0.0
    ) == pytest.approx(0.0)


def test_authority_ignores_reputation_when_none_is_known() -> None:
    assert blend_authority(0.8, None) == pytest.approx(0.8)


def test_authority_blends_a_known_reputation() -> None:
    blended = blend_authority(0.8, 0.3)

    assert blended == pytest.approx(
        (1 - REPUTATION_BLEND) * 0.8 + REPUTATION_BLEND * 0.3
    )
    assert blended < 0.8


def test_rationale_always_records_corroboration_and_reputation() -> None:
    rationale = build_rationale(
        "Peer-reviewed.",
        corroboration=0.5,
        reputation=0.9,
        sub_topics=["Alpha", "Beta"],
    )

    assert rationale.startswith("Peer-reviewed.")
    assert "Cited for: Alpha, Beta." in rationale
    assert "Corroboration 0.50" in rationale
    assert "Prior reputation 0.90" in rationale


def test_rationale_is_never_blank_when_the_model_returned_nothing() -> None:
    rationale = build_rationale(
        "   ", corroboration=0.0, reputation=None, sub_topics=[]
    )

    assert rationale.strip()
    assert "no sub-topic" in rationale
    assert "No prior reputation on record." in rationale


def test_a_scored_source_clamps_out_of_range_model_scores() -> None:
    source = build_scored_source(
        _group(),
        _draft(authority=9.0, recency=-2.0, relevance=0.9),
        corroboration=0.5,
        reputation=None,
    )

    assert isinstance(source, ScoredSource)
    assert source.authority_score == pytest.approx(1.0)
    assert source.recency_score == pytest.approx(0.0)
    assert source.overall_score == pytest.approx(
        overall_score(
            authority=1.0, recency=0.0, relevance=0.9, corroboration=0.5
        )
    )
    assert source.low_confidence is False


def test_a_weak_source_is_flagged_low_confidence() -> None:
    source = build_scored_source(
        _group(),
        _draft(authority=0.1, recency=0.1, relevance=0.1),
        corroboration=0.0,
        reputation=None,
    )

    assert source.overall_score < LOW_CONFIDENCE_THRESHOLD
    assert source.low_confidence is True


def test_a_fallback_record_is_conservative_and_always_low_confidence() -> None:
    source = fallback_scored_source(
        _group(), corroboration=0.5, reputation=0.9, reason="model_unavailable"
    )

    assert source.url == "https://example.org/a"
    assert source.title == "QEC 2025"
    assert source.recency_score == pytest.approx(0.0)
    assert source.relevance_score == pytest.approx(0.0)
    assert source.corroboration_score == pytest.approx(0.5)
    assert source.authority_score == pytest.approx(blend_authority(0.0, 0.9))
    assert source.low_confidence is True
    assert "could not be reached" in source.rationale


def test_a_fallback_record_rejects_an_unenumerated_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        fallback_scored_source(
            _group(), corroboration=0.0, reputation=None, reason="because"
        )


def test_observability_aggregates_are_finite_for_an_empty_run() -> None:
    assert average_score([]) == pytest.approx(0.0)
    assert low_confidence_count([]) == 0


def test_observability_aggregates_summarize_scored_sources() -> None:
    strong = build_scored_source(
        _group(), _draft(), corroboration=1.0, reputation=None
    )
    weak = fallback_scored_source(
        _group(url="https://weak.test/b"),
        corroboration=0.0,
        reputation=None,
        reason="not_scored_by_model",
    )

    assert low_confidence_count([strong, weak]) == 1
    assert average_score([strong, weak]) == pytest.approx(
        round((strong.overall_score + weak.overall_score) / 2, 4)
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_source_evaluator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.agents.source_evaluator'`

- [ ] **Step 3: Write minimal implementation**

Create `src/deep_research/agents/source_evaluator.py`:

```python
"""The Source Evaluator: score every source the findings actually used.

Like the Planner and the Researcher, this module never sends a domain type
to the provider. ``SourceScoreDraft`` mirrors the model-judged part of
``ScoredSource`` with plain field types so it survives strict JSON schema
conversion, and this module stamps the parts the model must not be trusted
to supply: the canonical URL, the computed corroboration score, the
weighted overall score, and the low-confidence flag.

Score convention: every score is a ``UnitScore`` in ``[0.0, 1.0]``, higher
is better, matching ``Finding.confidence`` and
``SourceReputation.reputation_score``. ``overall_score`` is a convex
combination of the four recorded dimensions, so a ``ScoredSource`` record
can always be re-checked against its own fields.
"""

from __future__ import annotations

from collections.abc import Sequence

from deep_research.agents.sources import SourceGroup
from deep_research.agents.steps import summarize_text
from deep_research.utils.types import ContractModel, ScoredSource

SOURCE_EVALUATOR_NAME = "source_evaluator"

# Weights form a convex combination: overall_score is in [0, 1] whenever
# its four inputs are. Authority and relevance dominate because a source
# that is neither authoritative nor on-topic is not rescued by being new.
AUTHORITY_WEIGHT = 0.35
RECENCY_WEIGHT = 0.15
RELEVANCE_WEIGHT = 0.30
CORROBORATION_WEIGHT = 0.20

# How much a reputation recalled from long-term memory moves the model's
# authority judgement. Blended into authority rather than into the overall
# score so that overall_score stays a pure function of the four recorded
# dimensions.
REPUTATION_BLEND = 0.4

LOW_CONFIDENCE_THRESHOLD = 0.4
DEFAULT_MAX_SOURCES = 12
DEFAULT_EXCERPT_CHARS = 400
_RATIONALE_CHARS = 400

# Enumerated, project-generated reasons a source was recorded without a
# model judgement. Never provider text: these strings reach prompts,
# ResearchError.details, and user-facing rationales.
FALLBACK_REASONS = {
    "model_unavailable": "The scoring model could not be reached.",
    "not_scored_by_model": (
        "The scoring model returned no score for this source."
    ),
    "over_source_cap": "This source fell past this run's scoring cap.",
}


class SourceScoreDraft(ContractModel):
    """One model-judged source score, before domain validation.

    Declares no ``Field`` constraints for the same reason as
    ``planner.SubTopicDraft``: it is converted to a strict OpenAI JSON
    schema. ``corroboration_score``, ``overall_score``, and
    ``low_confidence`` are deliberately absent — this project computes
    those, not the model.
    """

    url: str
    authority_score: float
    recency_score: float
    relevance_score: float
    rationale: str


class SourceScoresDraft(ContractModel):
    """The provider-facing scoring schema for one evaluation pass."""

    sources: list[SourceScoreDraft]


class EvaluatedSources(ContractModel):
    """The validated scores ``SourceEvaluatorAgent`` produces.

    Never sent to the provider — ``SourceScoresDraft`` is. Do not route
    this agent through ``complete_output``.
    """

    sources: list[ScoredSource] = []


def clamp_unit(value: float) -> float:
    """Pin a model-supplied number into ``[0.0, 1.0]``.

    ``ScoredSource`` fields are ``UnitScore`` and would raise on an
    out-of-range value. A model that returns 9.0 for authority is making a
    formatting mistake, not invalidating the whole run.
    """
    return min(1.0, max(0.0, float(value)))


def blend_authority(model_authority: float, reputation: float | None) -> float:
    """Fold a remembered reputation into the model's authority judgement."""
    authority = clamp_unit(model_authority)
    if reputation is None:
        return authority
    prior = clamp_unit(reputation)
    return clamp_unit(
        (1.0 - REPUTATION_BLEND) * authority + REPUTATION_BLEND * prior
    )


def overall_score(
    *,
    authority: float,
    recency: float,
    relevance: float,
    corroboration: float,
) -> float:
    """Combine the four recorded dimensions into one ``UnitScore``."""
    return clamp_unit(
        AUTHORITY_WEIGHT * clamp_unit(authority)
        + RECENCY_WEIGHT * clamp_unit(recency)
        + RELEVANCE_WEIGHT * clamp_unit(relevance)
        + CORROBORATION_WEIGHT * clamp_unit(corroboration)
    )


def build_rationale(
    model_rationale: str,
    *,
    corroboration: float,
    reputation: float | None,
    sub_topics: Sequence[str],
) -> str:
    """Extend the model's rationale with the facts this project computed.

    Always returns a non-blank string: ``ScoredSource.rationale`` requires
    one, and a model that returned a blank rationale must not be able to
    fail validation for a whole source.
    """
    parts: list[str] = []
    text = " ".join(model_rationale.split())
    if text:
        parts.append(summarize_text(text, limit=_RATIONALE_CHARS))
    parts.append(f"Cited for: {', '.join(sub_topics) or 'no sub-topic'}.")
    parts.append(
        f"Corroboration {corroboration:.2f} across independent domains."
    )
    if reputation is None:
        parts.append("No prior reputation on record.")
    else:
        parts.append(
            f"Prior reputation {reputation:.2f} blended into authority."
        )
    return " ".join(parts)


def build_scored_source(
    group: SourceGroup,
    draft: SourceScoreDraft,
    *,
    corroboration: float,
    reputation: float | None,
) -> ScoredSource:
    """Stamp one model score into a validated ``ScoredSource`` record."""
    authority = blend_authority(draft.authority_score, reputation)
    recency = clamp_unit(draft.recency_score)
    relevance = clamp_unit(draft.relevance_score)
    corroboration = clamp_unit(corroboration)
    overall = overall_score(
        authority=authority,
        recency=recency,
        relevance=relevance,
        corroboration=corroboration,
    )
    return ScoredSource(
        url=group.url,
        title=group.title,
        authority_score=authority,
        recency_score=recency,
        relevance_score=relevance,
        corroboration_score=corroboration,
        overall_score=overall,
        rationale=build_rationale(
            draft.rationale,
            corroboration=corroboration,
            reputation=reputation,
            sub_topics=group.sub_topics,
        ),
        low_confidence=overall < LOW_CONFIDENCE_THRESHOLD,
    )


def fallback_scored_source(
    group: SourceGroup,
    *,
    corroboration: float,
    reputation: float | None,
    reason: str,
) -> ScoredSource:
    """Record a source that could not be scored by the model.

    The three model-judged dimensions floor at 0.0 rather than being
    guessed at, corroboration is kept because it was computed locally, and
    ``low_confidence`` is always ``True``. This is what makes "every source
    used by findings gets a score or an explicit low-confidence flag" hold
    even when the provider is down.
    """
    explanation = FALLBACK_REASONS.get(reason)
    if explanation is None:
        raise ValueError(f"unknown fallback reason: {reason}")
    authority = blend_authority(0.0, reputation)
    corroboration = clamp_unit(corroboration)
    overall = overall_score(
        authority=authority,
        recency=0.0,
        relevance=0.0,
        corroboration=corroboration,
    )
    rationale = build_rationale(
        explanation,
        corroboration=corroboration,
        reputation=reputation,
        sub_topics=group.sub_topics,
    )
    return ScoredSource(
        url=group.url,
        title=group.title,
        authority_score=authority,
        recency_score=0.0,
        relevance_score=0.0,
        corroboration_score=corroboration,
        overall_score=overall,
        rationale=rationale,
        low_confidence=True,
    )


def average_score(sources: Sequence[ScoredSource]) -> float:
    """Mean ``overall_score``, rounded, and ``0.0`` for an empty run.

    Rounded and zero-guarded because this value lands in
    ``ResearchEvent.metadata``, which rejects non-finite JSON numbers.
    """
    if not sources:
        return 0.0
    total = sum(source.overall_score for source in sources)
    return round(total / len(sources), 4)


def low_confidence_count(sources: Sequence[ScoredSource]) -> int:
    """How many scored sources carry the explicit low-confidence flag."""
    return sum(1 for source in sources if source.low_confidence)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/test_source_evaluator.py -v && ruff check src/deep_research/agents/source_evaluator.py tests/test_agents/test_source_evaluator.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/source_evaluator.py \
  tests/test_agents/test_source_evaluator.py
git commit -m "feat: add source scoring maths and fallback records"
```

---

### Task 5: `SourceEvaluatorAgent` — Task Building, Reputation Lookup, Scoring

**Files:**
- Modify: `src/deep_research/agents/source_evaluator.py` (append)
- Modify: `tests/research_fakes.py` (append `FakeReputationSource`)
- Modify: `tests/test_agents/test_source_evaluator.py` (append)

**Interfaces:**
- Consumes: everything Task 4 produced; `AgentRun`, `BaseAgent`, `StructuredCompleter` from `deep_research.agents.base`; `agent_error` from `deep_research.agents.errors`; `AgentTask`, `SOURCE_EVALUATOR_SYSTEM_PROMPT`, `SOURCE_SCORING_INSTRUCTION`, `render_source_dossier` from `deep_research.agents.prompts`; `ReActRun` from `deep_research.agents.steps`; `group_findings_by_url`, `corroboration_score`, `normalize_source_url` from `deep_research.agents.sources`; `SourceReputation` from `deep_research.memory.entries`; `ChatMessage`, `OpenAIProviderError` from `deep_research.providers`.
- Produces:
  - `ReputationSource(Protocol)` with `async get_source_reputation(self, url: str) -> SourceReputation | None`
  - `SourceEvaluationTask(AgentTask)` — `groups: list[SourceGroup]`, `corroborations: dict[str, float]`, `reputations: dict[str, float]`
  - `scoring_messages(task: SourceEvaluationTask, *, excerpt_chars: int) -> list[ChatMessage]`
  - `reputation_lookup_error(*, failures: int, sources: int) -> ResearchError`
  - `scoring_provider_error(error: Exception, *, sources: int) -> ResearchError`
  - `no_sources_error() -> ResearchError`
  - `SourceEvaluatorAgent(BaseAgent[EvaluatedSources])` with `name = "source_evaluator"`, `allowed_tools = ()`, constructor `(*, provider, tracker, scratchpad, tools=(), config=None, reputation=None, max_sources=DEFAULT_MAX_SOURCES, excerpt_chars=DEFAULT_EXCERPT_CHARS)`, and methods `build_task(state) -> SourceEvaluationTask`, `async lookup_reputations(task) -> tuple[SourceEvaluationTask, list[ResearchError], int]`, `async score_sources(task) -> tuple[list[ScoredSource], list[ResearchError], bool]`, `async finalize(task, run)`, `state_update(result, run)`
- Task 6 adds `run` and the event builders on top of this class.

- [ ] **Step 1: Write the failing tests**

Append `FakeReputationSource` to `tests/research_fakes.py` (and add `SourceReputation` to its imports from `deep_research.memory.entries`):

```python
class FakeReputationSource:
    """Serve remembered source reputations without a vector store.

    ``error`` makes every lookup raise, which is how the "reputation
    lookup failed, keep scoring directly" path is exercised.
    """

    def __init__(
        self,
        *,
        reputations: Mapping[str, float] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.reputations = dict(reputations or {})
        self.error = error
        self.queried: list[str] = []

    async def get_source_reputation(self, url: str) -> SourceReputation | None:
        self.queried.append(url)
        if self.error is not None:
            raise self.error
        score = self.reputations.get(url)
        if score is None:
            return None
        return SourceReputation(
            url=url,
            title=url,
            reputation_score=score,
            observations=3,
            notes="",
        )
```

Append to `tests/test_agents/test_source_evaluator.py` (extend the existing import block with the new names, and add the imports the tests below need):

```python
def _evaluator(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    reputation: object | None = None,
    max_sources: int = 12,
) -> SourceEvaluatorAgent:
    return SourceEvaluatorAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1",
            agent_name="source_evaluator",
            max_entries=20,
        ),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
        reputation=reputation,
        max_sources=max_sources,
    )


def _eval_state(findings: list[Finding], **overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "How mature is quantum error correction?",
        "raw_findings": findings,
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def _eval_finding(url: str, sub_topic: str = "Alpha") -> Finding:
    return Finding(
        content="Logical error rates fell below break-even.",
        source_url=url,
        source_title="QEC 2025",
        extracted_at=EVAL_EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
    )


def test_build_task_groups_findings_and_seeds_remembered_reputations(
    tracker: Tracker,
) -> None:
    agent = _evaluator(tracker, ScriptedCompleter())
    state = _eval_state(
        [
            _eval_finding("https://example.org/a", "Alpha"),
            _eval_finding("https://other.test/b", "Alpha"),
        ],
        memory_context=MemorySnapshot(
            known_source_reputations={"https://example.org/a": 0.9}
        ),
    )

    task = agent.build_task(state)

    assert isinstance(task, SourceEvaluationTask)
    assert [group.url for group in task.groups] == [
        "https://example.org/a",
        "https://other.test/b",
    ]
    assert task.corroborations["https://example.org/a"] == pytest.approx(1.0)
    assert task.reputations == {"https://example.org/a": 0.9}
    assert "How mature is quantum error correction?" in task.instruction


@pytest.mark.asyncio
async def test_reputation_lookup_overrides_the_seed_and_is_recorded(
    tracker: Tracker,
) -> None:
    memory = FakeReputationSource(reputations={"https://example.org/a": 0.2})
    agent = _evaluator(tracker, ScriptedCompleter(), reputation=memory)
    state = _eval_state(
        [_eval_finding("https://example.org/a")],
        memory_context=MemorySnapshot(
            known_source_reputations={"https://example.org/a": 0.9}
        ),
    )

    task, errors, hits = await agent.lookup_reputations(agent.build_task(state))

    assert memory.queried == ["https://example.org/a"]
    assert task.reputations == {"https://example.org/a": 0.2}
    assert hits == 1
    assert errors == []


@pytest.mark.asyncio
async def test_a_failed_reputation_lookup_keeps_direct_scoring(
    tracker: Tracker,
) -> None:
    memory = FakeReputationSource(error=RuntimeError("chroma is down"))
    agent = _evaluator(tracker, ScriptedCompleter(), reputation=memory)
    state = _eval_state([_eval_finding("https://example.org/a")])

    task, errors, hits = await agent.lookup_reputations(agent.build_task(state))

    assert task.reputations == {}
    assert hits == 0
    assert errors[0].error_type == "source_evaluator_reputation_unavailable"
    assert errors[0].recoverable is True
    assert errors[0].details["failures"] == 1
    assert "chroma is down" not in str(errors[0].details)


@pytest.mark.asyncio
async def test_scoring_stamps_computed_fields_onto_model_scores(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        outputs=[
            SourceScoresDraft(
                sources=[
                    SourceScoreDraft(
                        url="https://example.org/a",
                        authority_score=0.9,
                        recency_score=0.8,
                        relevance_score=0.9,
                        rationale="Peer-reviewed.",
                    )
                ]
            )
        ]
    )
    agent = _evaluator(tracker, completer)
    state = _eval_state(
        [
            _eval_finding("https://example.org/a", "Alpha"),
            _eval_finding("https://other.test/b", "Alpha"),
        ]
    )
    task, _, _ = await agent.lookup_reputations(agent.build_task(state))

    sources, errors, provider_failed = await agent.score_sources(task)

    assert provider_failed is False
    assert errors == []
    assert [source.url for source in sources] == [
        "https://example.org/a",
        "https://other.test/b",
    ]
    scored = sources[0]
    assert scored.corroboration_score == pytest.approx(1.0)
    assert scored.low_confidence is False
    # other.test was never scored by the model, so it still gets a record.
    assert sources[1].low_confidence is True
    assert "returned no score" in sources[1].rationale


@pytest.mark.asyncio
async def test_every_source_still_gets_a_record_when_the_provider_fails(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])
    agent = _evaluator(tracker, completer)
    state = _eval_state([_eval_finding("https://example.org/a")])
    task, _, _ = await agent.lookup_reputations(agent.build_task(state))

    sources, errors, provider_failed = await agent.score_sources(task)

    assert provider_failed is True
    assert [source.url for source in sources] == ["https://example.org/a"]
    assert sources[0].low_confidence is True
    assert "could not be reached" in sources[0].rationale
    assert errors[0].error_type == "source_evaluator_scoring_provider_error"
    assert errors[0].recoverable is False
    assert errors[0].details["exception_type"] == "ProviderTimeoutError"


@pytest.mark.asyncio
async def test_sources_past_the_cap_are_recorded_not_dropped(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        outputs=[
            SourceScoresDraft(
                sources=[
                    SourceScoreDraft(
                        url="https://example.org/a",
                        authority_score=0.9,
                        recency_score=0.9,
                        relevance_score=0.9,
                        rationale="Strong.",
                    )
                ]
            )
        ]
    )
    agent = _evaluator(tracker, completer, max_sources=1)
    state = _eval_state(
        [
            _eval_finding("https://example.org/a"),
            _eval_finding("https://other.test/b"),
        ]
    )
    task, _, _ = await agent.lookup_reputations(agent.build_task(state))

    sources, _, _ = await agent.score_sources(task)

    assert [source.url for source in sources] == [
        "https://example.org/a",
        "https://other.test/b",
    ]
    assert sources[1].low_confidence is True
    assert "past this run's scoring cap" in sources[1].rationale
    # Only the sources under the cap reached the prompt.
    scoring_call = completer.calls[-1]
    assert "https://other.test/b" not in scoring_call[2][1].content


@pytest.mark.asyncio
async def test_scoring_makes_no_provider_call_without_findings(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _evaluator(tracker, completer)
    task, _, _ = await agent.lookup_reputations(agent.build_task(_eval_state([])))

    sources, errors, provider_failed = await agent.score_sources(task)

    assert sources == []
    assert errors == []
    assert provider_failed is False
    assert completer.calls == []


def test_state_update_carries_scored_sources_and_errors(
    tracker: Tracker,
) -> None:
    agent = _evaluator(tracker, ScriptedCompleter())
    scored = build_scored_source(
        _group(), _draft(), corroboration=1.0, reputation=None
    )
    run = ReActRun(agent_name="source_evaluator", stop_reason="finished")

    update = agent.state_update(EvaluatedSources(sources=[scored]), run)

    assert update["evaluated_sources"] == [scored]
    assert update["errors"] == []
```

Imports the appended tests need, merged into the module's existing blocks:

```python
from deep_research.agents.source_evaluator import (
    EvaluatedSources,
    SourceEvaluationTask,
    SourceEvaluatorAgent,
    SourceScoresDraft,
)
from deep_research.agents.steps import ReActRun
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ProviderTimeoutError
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import MemorySnapshot, ResearchState
from tests.agent_fakes import ScriptedCompleter
from tests.research_fakes import FakeReputationSource
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_source_evaluator.py -v`
Expected: FAIL with `ImportError: cannot import name 'SourceEvaluatorAgent' from 'deep_research.agents.source_evaluator'`

- [ ] **Step 3: Write minimal implementation**

Extend `src/deep_research/agents/source_evaluator.py`'s imports:

```python
from collections.abc import Sequence
from typing import Protocol

from pydantic import Field

from deep_research.agents.base import BaseAgent, StructuredCompleter
from deep_research.agents.errors import AgentConfigurationError, agent_error
from deep_research.agents.prompts import (
    SOURCE_EVALUATOR_SYSTEM_PROMPT,
    SOURCE_SCORING_INSTRUCTION,
    AgentTask,
    render_source_dossier,
)
from deep_research.agents.sources import (
    SourceGroup,
    corroboration_score,
    group_findings_by_url,
    normalize_source_url,
)
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.memory.entries import SourceReputation
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, OpenAIProviderError
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    ContractModel,
    ResearchError,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
)
```

Append to the module:

```python
class ReputationSource(Protocol):
    """The one long-term-memory capability this agent needs.

    ``deep_research.memory.long_term.LongTermMemory`` satisfies it
    structurally. Keeping the protocol to a single method keeps test
    doubles small and keeps this agent out of the vector-store's
    construction path.
    """

    async def get_source_reputation(self, url: str) -> SourceReputation | None:
        """Return the stored reputation for one source, if any."""
        raise NotImplementedError


class SourceEvaluationTask(AgentTask):
    """An ``AgentTask`` bound to the sources one evaluation pass scores.

    Carrying the groups on the task is what lets ``finalize(task, run)``
    score without the agent holding mutable state across await points —
    the same reason ``researcher.SubTopicTask`` exists.
    """

    groups: list[SourceGroup] = Field(default_factory=list)
    corroborations: dict[str, float] = Field(default_factory=dict)
    reputations: dict[str, float] = Field(default_factory=dict)


def scoring_messages(
    task: SourceEvaluationTask,
    *,
    excerpt_chars: int,
) -> list[ChatMessage]:
    """Build the messages that request one structured scoring draft."""
    dossiers = [
        render_source_dossier(
            group,
            index=index,
            corroboration=task.corroborations.get(group.url, 0.0),
            reputation=task.reputations.get(group.url),
            excerpt_chars=excerpt_chars,
        )
        for index, group in enumerate(task.groups, start=1)
    ]
    sections = [f"## Research question\n{task.instruction}"]
    if task.guidance.strip():
        sections.append(f"## Context\n{task.guidance}")
    sections.append("## Sources\n" + "\n\n".join(dossiers))
    sections.append(f"## Scoring contract\n{SOURCE_SCORING_INSTRUCTION}")
    return [
        ChatMessage(role="developer", content=SOURCE_EVALUATOR_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def reputation_lookup_error(*, failures: int, sources: int) -> ResearchError:
    """Warn that remembered reputations could not be read.

    Recoverable: scoring continues from the dossiers alone, which is the
    spec's "continue with direct scoring" path. Carries counts only —
    never the backend's exception text.
    """
    return agent_error(
        agent_name=SOURCE_EVALUATOR_NAME,
        error_type="source_evaluator_reputation_unavailable",
        message=(
            "Remembered source reputations could not be read; sources were "
            "scored directly instead."
        ),
        details={"failures": failures, "sources": sources},
    )


def scoring_provider_error(error: Exception, *, sources: int) -> ResearchError:
    """Record that the scoring call could not reach the provider.

    Non-recoverable, mirroring ``researcher.extraction_provider_error``:
    every source still gets a low-confidence fallback record, but no
    model judgement exists for this pass.
    """
    return agent_error(
        agent_name=SOURCE_EVALUATOR_NAME,
        error_type="source_evaluator_scoring_provider_error",
        message=(
            "The model provider failed while sources were scored; every "
            "source was recorded as low confidence instead."
        ),
        recoverable=False,
        details={
            "exception_type": type(error).__name__,
            "sources": sources,
        },
    )


def no_sources_error() -> ResearchError:
    """Warn that there was nothing to evaluate at all."""
    return agent_error(
        agent_name=SOURCE_EVALUATOR_NAME,
        error_type="source_evaluator_no_sources",
        message="No findings were available to evaluate.",
    )


class SourceEvaluatorAgent(BaseAgent[EvaluatedSources]):
    """Score every source behind ``state.raw_findings``.

    Runs no ReAct loop: grouping and corroboration are deterministic, and
    the reputation read is an exact-id memory lookup rather than a search.
    ``run`` is overridden for the same reason ``ResearcherAgent`` overrides
    it — the shared single-loop ``BaseAgent.run`` cannot express this
    agent's shape.
    """

    name = SOURCE_EVALUATOR_NAME
    description = "Score the sources behind the collected findings."
    allowed_tools: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        provider: StructuredCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        reputation: ReputationSource | None = None,
        max_sources: int = DEFAULT_MAX_SOURCES,
        excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
        )
        if max_sources < 1:
            raise ValueError("max_sources must be at least 1")
        if excerpt_chars < 1:
            raise ValueError("excerpt_chars must be at least 1")
        self._reputation = reputation
        self._max_sources = max_sources
        self._excerpt_chars = excerpt_chars

    @property
    def output_schema(self) -> type[EvaluatedSources]:
        """The validated scores. Never sent to the provider.

        ``score_sources`` asks for ``SourceScoresDraft`` instead, because
        ``ScoredSource`` carries ``Field`` and ``UnitScore`` constraints
        that do not survive strict JSON schema conversion. Do not route
        this agent through ``complete_output``.
        """
        return EvaluatedSources

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return SOURCE_EVALUATOR_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> SourceEvaluationTask:
        """Group findings, compute corroboration, seed remembered scores."""
        groups = group_findings_by_url(state.raw_findings)
        corroborations = {
            group.url: corroboration_score(group, groups) for group in groups
        }
        seeded = {
            normalize_source_url(url): float(score)
            for url, score in state.memory_context.known_source_reputations.items()
        }
        reputations = {
            group.url: seeded[group.url]
            for group in groups
            if group.url in seeded
        }
        return SourceEvaluationTask(
            instruction=state.original_question,
            groups=groups,
            corroborations=corroborations,
            reputations=reputations,
        )

    async def lookup_reputations(
        self,
        task: SourceEvaluationTask,
    ) -> tuple[SourceEvaluationTask, list[ResearchError], int]:
        """Refresh remembered reputations, tolerating a failing backend.

        A live lookup wins over the ``memory_context`` seed. Any failure
        leaves the seed in place, records one recoverable error for the
        whole pass, and lets scoring continue — the spec's "continue with
        direct scoring" requirement.
        """
        if self._reputation is None or not task.groups:
            return task, [], 0

        reputations = dict(task.reputations)
        failures = 0
        hits = 0
        for group in task.groups:
            try:
                record = await self._reputation.get_source_reputation(group.url)
            except Exception:
                # Deliberately broad: a memory backend can raise anything,
                # and no backend failure is worth failing the pass over.
                failures += 1
                continue
            if record is not None:
                reputations[group.url] = clamp_unit(record.reputation_score)
                hits += 1
        errors = (
            [reputation_lookup_error(failures=failures, sources=len(task.groups))]
            if failures
            else []
        )
        return (
            task.model_copy(update={"reputations": reputations}),
            errors,
            hits,
        )

    async def score_sources(
        self,
        task: SourceEvaluationTask,
    ) -> tuple[list[ScoredSource], list[ResearchError], bool]:
        """Score every grouped source, in ``task.groups`` order.

        The third element, ``provider_failed``, is ``True`` only when the
        scoring call itself could not reach the provider. Even then every
        source gets a record: the acceptance criterion is "*every* source
        used by findings", and a silent gap is worse than a flagged
        low-confidence row.
        """
        if not task.groups:
            return [], [], False

        scored_groups = task.groups[: self._max_sources]
        capped_groups = task.groups[self._max_sources :]
        capped = {group.url for group in capped_groups}
        request = task.model_copy(update={"groups": scored_groups})

        drafts: dict[str, SourceScoreDraft] = {}
        errors: list[ResearchError] = []
        provider_failed = False
        try:
            response = await self.provider.complete_structured(
                scoring_messages(request, excerpt_chars=self._excerpt_chars),
                SourceScoresDraft,
                agent_name=self.name,
            )
        except OpenAIProviderError as error:
            provider_failed = True
            errors.append(
                scoring_provider_error(error, sources=len(scored_groups))
            )
        else:
            for draft in response.sources:
                url = normalize_source_url(draft.url)
                # First score for a URL wins; a model that repeats itself
                # must not be able to overwrite its own earlier judgement.
                drafts.setdefault(url, draft)

        sources: list[ScoredSource] = []
        for group in task.groups:
            corroboration = task.corroborations.get(group.url, 0.0)
            reputation = task.reputations.get(group.url)
            draft = drafts.get(group.url)
            if draft is not None:
                sources.append(
                    build_scored_source(
                        group,
                        draft,
                        corroboration=corroboration,
                        reputation=reputation,
                    )
                )
                continue
            if group.url in capped:
                reason = "over_source_cap"
            elif provider_failed:
                reason = "model_unavailable"
            else:
                reason = "not_scored_by_model"
            sources.append(
                fallback_scored_source(
                    group,
                    corroboration=corroboration,
                    reputation=reputation,
                    reason=reason,
                )
            )
        return sources, errors, provider_failed

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> EvaluatedSources | None:
        """Adapt ``score_sources`` to the ``BaseAgent`` hook.

        ``run`` calls ``score_sources`` directly so it can keep the errors
        this hook signature has nowhere to return.
        """
        del run
        if not isinstance(task, SourceEvaluationTask):
            raise AgentConfigurationError(
                "SourceEvaluatorAgent.finalize requires a SourceEvaluationTask"
            )
        sources, _, _ = await self.score_sources(task)
        return EvaluatedSources(sources=sources)

    def state_update(
        self,
        result: EvaluatedSources | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """Scored sources and errors only. ``run`` adds the events."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["evaluated_sources"] = list(result.sources)
        return update
```

`summarize_text` is imported for the event builders Task 6 adds; if Ruff flags it as unused at this point, add it in Task 6 instead.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/test_source_evaluator.py -v && ruff check src/deep_research/agents/source_evaluator.py tests/research_fakes.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/source_evaluator.py \
  tests/test_agents/test_source_evaluator.py tests/research_fakes.py
git commit -m "feat: score sources with memory-backed reputation fallback"
```

---

### Task 6: Source Evaluator Observability And `run`

**Files:**
- Modify: `src/deep_research/agents/source_evaluator.py` (event builders before the class; `run` at the end of the class)
- Modify: `tests/test_agents/test_source_evaluator.py` (append)

**Interfaces:**
- Consumes: everything Tasks 4-5 produced; `agent_event` from `deep_research.agents.events`; `AgentRun` from `deep_research.agents.base`; `ResearchEvent` from `deep_research.utils.types`.
- Produces:
  - `evaluation_started_event(*, finding_count: int, source_count: int) -> ResearchEvent` — type `source_evaluator.evaluation.started`
  - `evaluation_completed_event(sources: Sequence[ScoredSource], *, reputation_hits: int, reputation_failures: int) -> ResearchEvent` — type `source_evaluator.evaluation.completed`, metadata `source_count`, `average_score`, `low_confidence_count`, `reputation_hits`, `reputation_failures`
  - `SourceEvaluatorAgent.run(state) -> AgentRun[EvaluatedSources]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents/test_source_evaluator.py`:

```python
def _scoring_response() -> SourceScoresDraft:
    return SourceScoresDraft(
        sources=[
            SourceScoreDraft(
                url="https://example.org/a",
                authority_score=0.9,
                recency_score=0.8,
                relevance_score=0.9,
                rationale="Peer-reviewed.",
            ),
            SourceScoreDraft(
                url="https://other.test/b",
                authority_score=0.1,
                recency_score=0.1,
                relevance_score=0.1,
                rationale="Anonymous blog.",
            ),
        ]
    )


def test_the_completed_event_reports_the_three_required_counts() -> None:
    strong = build_scored_source(
        _group(), _draft(), corroboration=1.0, reputation=None
    )
    weak = fallback_scored_source(
        _group(url="https://weak.test/b"),
        corroboration=0.0,
        reputation=None,
        reason="not_scored_by_model",
    )

    event = evaluation_completed_event(
        [strong, weak], reputation_hits=1, reputation_failures=0
    )

    assert event.event_type == "source_evaluator.evaluation.completed"
    assert event.source == "agent.source_evaluator"
    assert event.metadata["source_count"] == 2
    assert event.metadata["low_confidence_count"] == 1
    assert event.metadata["average_score"] == pytest.approx(
        average_score([strong, weak])
    )
    assert event.metadata["reputation_hits"] == 1
    assert event.metadata["reputation_failures"] == 0


def test_the_completed_event_is_finite_for_an_empty_run() -> None:
    event = evaluation_completed_event(
        [], reputation_hits=0, reputation_failures=0
    )

    assert event.metadata["source_count"] == 0
    assert event.metadata["average_score"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_a_full_run_writes_sources_events_and_span_outputs(
    tracker: Tracker,
) -> None:
    memory = FakeReputationSource(reputations={"https://example.org/a": 0.95})
    agent = _evaluator(
        tracker, ScriptedCompleter(outputs=[_scoring_response()]),
        reputation=memory,
    )
    state = _eval_state(
        [
            _eval_finding("https://example.org/a", "Alpha"),
            _eval_finding("https://other.test/b", "Alpha"),
        ]
    )

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.agent_name == "source_evaluator"
    assert outcome.react.stop_reason == "finished"
    assert outcome.result is not None
    assert len(outcome.result.sources) == 2

    merged = merge_research_state(state, outcome.state_update)
    assert [source.url for source in merged.evaluated_sources] == [
        "https://example.org/a",
        "https://other.test/b",
    ]
    assert merged.evaluated_sources[1].low_confidence is True

    event_types = [event.event_type for event in outcome.state_update["events"]]
    assert event_types == [
        "source_evaluator.evaluation.started",
        "source_evaluator.evaluation.completed",
    ]
    completed = outcome.state_update["events"][1]
    assert completed.metadata["source_count"] == 2
    assert completed.metadata["low_confidence_count"] == 1
    assert completed.metadata["reputation_hits"] == 1


@pytest.mark.asyncio
async def test_a_run_without_findings_records_a_recoverable_error(
    tracker: Tracker,
) -> None:
    agent = _evaluator(tracker, ScriptedCompleter())
    state = _eval_state([])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert outcome.result.sources == []
    assert outcome.errors[0].error_type == "source_evaluator_no_sources"
    assert outcome.errors[0].recoverable is True
    started = outcome.state_update["events"][0]
    assert started.metadata["finding_count"] == 0
    assert started.metadata["source_count"] == 0


@pytest.mark.asyncio
async def test_a_reputation_failure_is_visible_in_state_and_events(
    tracker: Tracker,
) -> None:
    memory = FakeReputationSource(error=RuntimeError("chroma is down"))
    agent = _evaluator(
        tracker,
        ScriptedCompleter(outputs=[_scoring_response()]),
        reputation=memory,
    )
    state = _eval_state([_eval_finding("https://example.org/a")])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert len(outcome.result.sources) == 1
    types = {error.error_type for error in outcome.errors}
    assert "source_evaluator_reputation_unavailable" in types
    completed = outcome.state_update["events"][-1]
    assert completed.metadata["reputation_failures"] == 1
```

Add `evaluation_completed_event` and `average_score` to the module's existing `source_evaluator` import block, and `merge_research_state` to the `deep_research.utils.types` import.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_source_evaluator.py -v`
Expected: FAIL with `ImportError: cannot import name 'evaluation_completed_event'`

- [ ] **Step 3: Write minimal implementation**

Add `from deep_research.agents.base import AgentRun, BaseAgent, StructuredCompleter` (extend the existing `base` import) and `from deep_research.agents.events import agent_event`, plus `ResearchEvent` in the `utils.types` import.

Insert the event builders just before `class SourceEvaluatorAgent`:

```python
def evaluation_started_event(
    *,
    finding_count: int,
    source_count: int,
) -> ResearchEvent:
    """Announce that evaluation began, before any provider call."""
    return agent_event(
        agent_name=SOURCE_EVALUATOR_NAME,
        event_type="source_evaluator.evaluation.started",
        message="Source evaluation started.",
        metadata={
            "finding_count": finding_count,
            "source_count": source_count,
        },
    )


def evaluation_completed_event(
    sources: Sequence[ScoredSource],
    *,
    reputation_hits: int,
    reputation_failures: int,
) -> ResearchEvent:
    """Report the counts the spec requires of this agent.

    ``average_score`` is rounded and zero-guarded by ``average_score`` so
    this metadata is always a finite JSON number, which
    ``ResearchEvent.metadata`` requires.
    """
    return agent_event(
        agent_name=SOURCE_EVALUATOR_NAME,
        event_type="source_evaluator.evaluation.completed",
        message="Source evaluation complete.",
        metadata={
            "source_count": len(sources),
            "average_score": average_score(sources),
            "low_confidence_count": low_confidence_count(sources),
            "reputation_hits": reputation_hits,
            "reputation_failures": reputation_failures,
        },
    )
```

Append `run` to `SourceEvaluatorAgent`:

```python
    async def run(self, state: ResearchState) -> AgentRun[EvaluatedSources]:
        """Group, look up reputations, score, and report the counts.

        No ReAct loop runs, so the returned ``ReActRun`` is a synthetic
        record with zero iterations and zero tool calls. ``stop_reason`` is
        ``"provider_error"`` only when the scoring call itself failed, so a
        caller reading ``react.succeeded`` learns the same thing it would
        from any other agent.
        """
        task = self.build_task(state)
        events: list[ResearchEvent] = [
            evaluation_started_event(
                finding_count=len(state.raw_findings),
                source_count=len(task.groups),
            )
        ]
        errors: list[ResearchError] = []

        async with self.tracker.agent_span(self.name) as span:
            task, lookup_errors, hits = await self.lookup_reputations(task)
            errors.extend(lookup_errors)
            sources, scoring_errors, provider_failed = await self.score_sources(
                task
            )
            errors.extend(scoring_errors)
            if not task.groups:
                errors.append(no_sources_error())
            failures = sum(
                int(error.details.get("failures", 0) or 0)
                for error in lookup_errors
            )
            events.append(
                evaluation_completed_event(
                    sources,
                    reputation_hits=hits,
                    reputation_failures=failures,
                )
            )
            span.set_outputs(
                {
                    "agent_name": self.name,
                    "source_count": len(sources),
                    "average_score": average_score(sources),
                    "low_confidence_count": low_confidence_count(sources),
                    "reputation_hits": hits,
                    "reputation_failures": failures,
                }
            )

        react = ReActRun(
            agent_name=self.name,
            stop_reason="provider_error" if provider_failed else "finished",
            errors=errors,
        )
        result = EvaluatedSources(sources=sources)
        return AgentRun(
            agent_name=self.name,
            result=result,
            react=react,
            errors=errors,
            state_update={
                **self.state_update(result, react),
                "events": events,
            },
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/ -v && ruff check src/deep_research/agents/`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/source_evaluator.py \
  tests/test_agents/test_source_evaluator.py
git commit -m "feat: emit source evaluation counts and run the pass"
```

---

### Task 7: Claim Extraction Contracts And Validation

**Files:**
- Create: `src/deep_research/agents/fact_checker.py` (pure module contents only — the agent class arrives in Task 9)
- Create: `tests/test_agents/test_fact_checker.py`

**Interfaces:**
- Consumes: `AgentTask`, `CLAIM_EXTRACTION_INSTRUCTION`, `CLAIM_EXTRACTION_SYSTEM_PROMPT`, `render_finding_digest`, `render_source_quality` from `deep_research.agents.prompts`; `normalize_source_url`, `source_domain` from `deep_research.agents.sources`; `agent_error` from `deep_research.agents.errors`; `ChatMessage` from `deep_research.providers`; `ContractModel`, `ResearchError`, `ResearchState` from `deep_research.utils.types`.
- Produces, all importable from `deep_research.agents.fact_checker`:
  - `FACT_CHECKER_NAME: str = "fact_checker"`
  - `DEFAULT_MAX_CLAIMS: int = 5`, `DEFAULT_FINDING_DIGEST: int = 40`, `FACT_CHECK_EVIDENCE_CHARS: int = 4000`
  - `ClaimDraft(ContractModel)` — `text: str`, `source_urls: list[str]`
  - `ClaimsDraft(ContractModel)` — `claims: list[ClaimDraft]`
  - `ClaimTask(AgentTask)` — `claim: ClaimDraft`, `claimed_domains: list[str]`
  - `VerifiedClaims(ContractModel)` — `claims: list[Claim]`
  - `known_source_urls(state: ResearchState) -> list[str]`
  - `build_claim_drafts(draft: ClaimsDraft, *, known_urls: Sequence[str]) -> tuple[list[ClaimDraft], list[str]]`
  - `claim_extraction_messages(state: ResearchState, *, max_findings: int) -> list[ChatMessage]`
  - `claim_extraction_provider_error(error: Exception) -> ResearchError`
  - `invalid_claim_error(rejected: Sequence[str]) -> ResearchError`
  - `no_findings_to_check_error() -> ResearchError`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agents/test_fact_checker.py`:

```python
"""Tests for the Fact Checker's claim extraction and verification."""

from __future__ import annotations

import pytest

from deep_research.agents.fact_checker import (
    ClaimDraft,
    ClaimsDraft,
    build_claim_drafts,
    claim_extraction_messages,
    known_source_urls,
)
from deep_research.utils.types import (
    Finding,
    MemorySnapshot,
    ResearchState,
    ScoredSource,
)

CHECK_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _check_finding(
    url: str = "https://example.org/a",
    *,
    content: str = "Logical error rates fell below break-even in 2025.",
    sub_topic: str = "Alpha",
) -> Finding:
    return Finding(
        content=content,
        source_url=url,
        source_title="QEC 2025",
        extracted_at=CHECK_EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
    )


def _scored(url: str, *, low: bool = False) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="QEC 2025",
        authority_score=0.2 if low else 0.9,
        recency_score=0.2 if low else 0.9,
        relevance_score=0.2 if low else 0.9,
        corroboration_score=0.0 if low else 1.0,
        overall_score=0.16 if low else 0.9,
        rationale="Because.",
        low_confidence=low,
    )


def _check_state(
    findings: list[Finding] | None = None,
    sources: list[ScoredSource] | None = None,
) -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        raw_findings=findings or [],
        evaluated_sources=sources or [],
        memory_context=MemorySnapshot(),
    )


def test_known_source_urls_are_canonical_and_deduplicated() -> None:
    state = _check_state(
        [
            _check_finding("https://example.org/a"),
            _check_finding("https://WWW.example.org/a/"),
            _check_finding("https://other.test/b"),
        ]
    )

    assert known_source_urls(state) == [
        "https://example.org/a",
        "https://other.test/b",
    ]


def test_claim_drafts_keep_only_urls_the_findings_actually_used() -> None:
    draft = ClaimsDraft(
        claims=[
            ClaimDraft(
                text="Logical error rates fell below break-even in 2025.",
                source_urls=[
                    "https://WWW.example.org/a/",
                    "https://invented.test/x",
                ],
            )
        ]
    )

    claims, rejected = build_claim_drafts(
        draft, known_urls=["https://example.org/a"]
    )

    assert rejected == []
    assert claims[0].source_urls == ["https://example.org/a"]


def test_a_claim_with_no_known_source_is_rejected_with_a_safe_reason() -> None:
    draft = ClaimsDraft(
        claims=[
            ClaimDraft(text="Invented.", source_urls=["https://invented.test/x"]),
            ClaimDraft(text="Real.", source_urls=["https://example.org/a"]),
        ]
    )

    claims, rejected = build_claim_drafts(
        draft, known_urls=["https://example.org/a"]
    )

    assert [claim.text for claim in claims] == ["Real."]
    assert rejected == ["claim 1: no source url from the collected findings"]
    assert "invented.test" not in " ".join(rejected)


def test_a_blank_claim_is_rejected() -> None:
    draft = ClaimsDraft(
        claims=[ClaimDraft(text="   ", source_urls=["https://example.org/a"])]
    )

    claims, rejected = build_claim_drafts(
        draft, known_urls=["https://example.org/a"]
    )

    assert claims == []
    assert rejected == ["claim 1: blank claim text"]


def test_extraction_messages_show_findings_and_source_quality() -> None:
    state = _check_state(
        [_check_finding()],
        [_scored("https://example.org/a", low=True)],
    )

    messages = claim_extraction_messages(state, max_findings=10)

    assert messages[0].role == "developer"
    body = messages[1].content
    assert "How mature is quantum error correction?" in body
    assert "1. [Alpha] Logical error rates fell below break-even" in body
    assert "https://example.org/a: 0.16 (LOW CONFIDENCE)" in body
    assert "Return an empty list" in body


def test_extraction_messages_cap_the_number_of_findings_rendered() -> None:
    findings = [
        _check_finding(content=f"Fact number {index}.")
        for index in range(1, 6)
    ]

    messages = claim_extraction_messages(_check_state(findings), max_findings=2)

    body = messages[1].content
    assert "Fact number 1." in body
    assert "Fact number 3." not in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_fact_checker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.agents.fact_checker'`

- [ ] **Step 3: Write minimal implementation**

Create `src/deep_research/agents/fact_checker.py`:

```python
"""The Fact Checker: extract major claims and verify them independently.

The provider is asked for ``ClaimsDraft`` and ``ClaimVerdictDraft``, never
for ``Claim`` itself: ``Claim`` declares ``Field(min_length=1)`` and
``UnitScore`` constraints that strict structured outputs reject. Local code
stamps the parts the model must not be trusted with — which source URLs
actually exist, which retrieved domains are independent of the claim, and
the final verdict when the model's answer conflicts with the evidence it
reported.

Verdict convention: the four ``ClaimVerdict`` strings, and only those.
``insufficient_evidence`` is the honest default whenever nothing
independent was retrieved, and is never a way of expressing "probably
true".
"""

from __future__ import annotations

from collections.abc import Sequence

from deep_research.agents.errors import agent_error
from deep_research.agents.prompts import (
    CLAIM_EXTRACTION_INSTRUCTION,
    CLAIM_EXTRACTION_SYSTEM_PROMPT,
    AgentTask,
    render_finding_digest,
    render_source_quality,
)
from deep_research.agents.sources import normalize_source_url, source_domain
from deep_research.providers import ChatMessage
from deep_research.utils.types import (
    Claim,
    ContractModel,
    ResearchError,
    ResearchState,
)

FACT_CHECKER_NAME = "fact_checker"
DEFAULT_MAX_CLAIMS = 5
DEFAULT_FINDING_DIGEST = 40
# Named distinctly from researcher.DEFAULT_EVIDENCE_CHARS: both are
# re-exported from deep_research.agents, so the names must not collide.
FACT_CHECK_EVIDENCE_CHARS = 4000


class ClaimDraft(ContractModel):
    """One model-extracted claim, before domain validation.

    Declares no ``Field`` constraints for the same reason as
    ``planner.SubTopicDraft``: it is converted to a strict OpenAI JSON
    schema. ``verdict``, ``confidence``, ``evidence``, and
    ``contradictions`` are deliberately absent — extraction proposes
    claims, verification judges them.
    """

    text: str
    source_urls: list[str]


class ClaimsDraft(ContractModel):
    """The provider-facing claim-extraction schema."""

    claims: list[ClaimDraft]


class ClaimTask(AgentTask):
    """An ``AgentTask`` bound to the claim its loop verifies.

    Carrying the claim on the task is what lets ``finalize(task, run)``
    know which claim it is finalizing without the agent holding mutable
    state across await points.
    """

    claim: ClaimDraft
    claimed_domains: list[str] = []


class VerifiedClaims(ContractModel):
    """The validated claims ``FactCheckerAgent`` produces.

    Never sent to the provider — ``ClaimsDraft`` and ``ClaimVerdictDraft``
    are. Do not route this agent through ``complete_output``.
    """

    claims: list[Claim] = []


def known_source_urls(state: ResearchState) -> list[str]:
    """Every canonical source URL the findings actually used, in order."""
    seen: list[str] = []
    for finding in state.raw_findings:
        url = normalize_source_url(finding.source_url)
        if url not in seen:
            seen.append(url)
    return seen


def build_claim_drafts(
    draft: ClaimsDraft,
    *,
    known_urls: Sequence[str],
) -> tuple[list[ClaimDraft], list[str]]:
    """Keep the claims whose sources exist, naming the ones dropped.

    A model that attaches a URL nobody retrieved has invented a citation,
    which is exactly the failure this project refuses to pass downstream.
    Rejection reasons are generated here and never copied from provider
    output, so they are safe to record in ``ResearchError.details``.
    """
    allowed = set(known_urls)
    claims: list[ClaimDraft] = []
    rejected: list[str] = []
    for index, item in enumerate(draft.claims, start=1):
        text = " ".join(item.text.split())
        if not text:
            rejected.append(f"claim {index}: blank claim text")
            continue
        urls: list[str] = []
        for raw in item.source_urls:
            url = normalize_source_url(raw)
            if url in allowed and url not in urls:
                urls.append(url)
        if not urls:
            rejected.append(
                f"claim {index}: no source url from the collected findings"
            )
            continue
        claims.append(ClaimDraft(text=text, source_urls=urls))
    return claims, rejected


def claim_extraction_messages(
    state: ResearchState,
    *,
    max_findings: int,
) -> list[ChatMessage]:
    """Build the messages that request one structured claim draft."""
    findings = list(state.raw_findings)[:max_findings]
    sections = [
        f"## Research question\n{state.original_question}",
        f"## Retrieved findings\n{render_finding_digest(findings)}",
        (
            "## Source quality\n"
            f"{render_source_quality(state.evaluated_sources)}"
        ),
        f"## Response contract\n{CLAIM_EXTRACTION_INSTRUCTION}",
    ]
    return [
        ChatMessage(role="developer", content=CLAIM_EXTRACTION_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def claim_extraction_provider_error(error: Exception) -> ResearchError:
    """Record that claim extraction could not reach the provider.

    Non-recoverable: with no claims there is nothing to verify, so the
    pass ends. ``details`` carries ``exception_type`` only, matching the
    redaction discipline in ``react.py`` and ``researcher.py``.
    """
    return agent_error(
        agent_name=FACT_CHECKER_NAME,
        error_type="fact_checker_extraction_provider_error",
        message=(
            "The model provider failed while claims were extracted; no "
            "claim was verified."
        ),
        recoverable=False,
        details={"exception_type": type(error).__name__},
    )


def invalid_claim_error(rejected: Sequence[str]) -> ResearchError:
    """Warn that some extracted claims were malformed and were dropped."""
    return agent_error(
        agent_name=FACT_CHECKER_NAME,
        error_type="fact_checker_invalid_claim",
        message="Some extracted claims were malformed and were dropped.",
        details={"rejected": list(rejected)},
    )


def no_findings_to_check_error() -> ResearchError:
    """Warn that there was nothing to fact check at all."""
    return agent_error(
        agent_name=FACT_CHECKER_NAME,
        error_type="fact_checker_no_findings",
        message="No findings were available to extract claims from.",
    )
```

`source_domain` is imported for Task 8; if Ruff flags it as unused at this point, add it in Task 8 instead.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/test_fact_checker.py -v && ruff check src/deep_research/agents/fact_checker.py tests/test_agents/test_fact_checker.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/fact_checker.py \
  tests/test_agents/test_fact_checker.py
git commit -m "feat: extract checkable claims from research findings"
```

---

### Task 8: Independence, Verdict Resolution, And Claim Records

**Files:**
- Modify: `src/deep_research/agents/fact_checker.py` (append)
- Modify: `tests/test_agents/test_fact_checker.py` (append)

**Interfaces:**
- Consumes: Task 7's contracts; `ReActRun` from `deep_research.agents.steps`; `render_evidence` from `deep_research.agents.researcher`; `CLAIM_VERIFICATION_INSTRUCTION`, `CLAIM_VERIFICATION_SYSTEM_PROMPT` from `deep_research.agents.prompts`; `Claim`, `ClaimVerdict` from `deep_research.utils.types`.
- Produces, all importable from `deep_research.agents.fact_checker`:
  - `VERDICT_VALUES: tuple[ClaimVerdict, ...]` — `("verified", "unverified", "contradicted", "insufficient_evidence")`
  - `INSUFFICIENT_REASONS: dict[str, str]` — keys `no_independent_source`, `loop_failed`, `provider_unavailable`, `unrecognized_verdict`
  - `ClaimVerdictDraft(ContractModel)` — `verdict: str`, `confidence: float`, `evidence: list[str]`, `contradictions: list[str]`
  - `retrieved_source_urls(run: ReActRun) -> list[str]`
  - `independent_domains(urls: Sequence[str], *, claimed_domains: Sequence[str]) -> list[str]`
  - `claimed_domains_for(source_urls: Sequence[str]) -> list[str]`
  - `normalize_verdict(raw: str) -> ClaimVerdict`
  - `resolve_verdict(draft: ClaimVerdictDraft, *, independent: Sequence[str]) -> tuple[ClaimVerdict, float]`
  - `build_claim(claim: ClaimDraft, draft: ClaimVerdictDraft, *, independent: Sequence[str]) -> Claim`
  - `insufficient_claim(claim: ClaimDraft, *, reason: str) -> Claim`
  - `claim_verification_messages(task: ClaimTask, run: ReActRun, *, evidence_chars: int, independent: Sequence[str]) -> list[ChatMessage]`
  - `verdict_counts(claims: Sequence[Claim]) -> dict[str, int]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents/test_fact_checker.py`:

```python
def _tool_step(
    iteration: int,
    tool_name: str,
    data: object,
    *,
    success: bool = True,
) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        thought=f"Call {tool_name}.",
        action="use_tool",
        tool_name=tool_name,
        observation=ReActObservation(
            tool_name=tool_name,
            success=success,
            summary=f"{tool_name} ran",
        ),
        tool_result=(
            ToolResult(
                tool_name=tool_name, success=True, data=data, latency_ms=1.0
            )
            if success
            else ToolResult(
                tool_name=tool_name,
                success=False,
                error={"type": "TimeoutError", "message": "upstream timed out"},
                latency_ms=1.0,
            )
        ),
    )


def _verdict_draft(
    *,
    verdict: str = "verified",
    confidence: float = 0.9,
    evidence: list[str] | None = None,
    contradictions: list[str] | None = None,
) -> ClaimVerdictDraft:
    return ClaimVerdictDraft(
        verdict=verdict,
        confidence=confidence,
        evidence=evidence if evidence is not None else ["A third party agrees."],
        contradictions=contradictions or [],
    )


def _claim_draft(
    *, urls: list[str] | None = None
) -> ClaimDraft:
    return ClaimDraft(
        text="Logical error rates fell below break-even in 2025.",
        source_urls=urls or ["https://example.org/a"],
    )


def test_verdict_values_match_the_shared_claim_verdict_type() -> None:
    assert set(VERDICT_VALUES) == set(get_args(ClaimVerdict))


def test_retrieved_urls_are_pulled_from_every_evidence_carrying_tool() -> None:
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            ),
            _tool_step(
                2,
                "web_scraper",
                {"url": "https://WWW.third.test/x/", "text": "Body."},
            ),
            _tool_step(
                3,
                "document_reader",
                {"source": "https://fourth.test/d.csv", "chunks": ["a"]},
            ),
            _tool_step(
                4,
                "query_memory",
                {"matches": [{"metadata": {"source_url": "https://fifth.test/m"}}]},
            ),
        ],
        iterations=4,
        tool_calls=4,
    )

    assert retrieved_source_urls(run) == [
        "https://third.test/x",
        "https://fourth.test/d.csv",
        "https://fifth.test/m",
    ]


def test_empty_and_failed_tool_payloads_yield_no_urls() -> None:
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(1, "web_search", {"results": []}),
            _tool_step(2, "web_scraper", {"url": "https://a.test/x", "text": " "}),
            _tool_step(3, "document_reader", {"source": "https://b.test/d", "chunks": []}),
            _tool_step(4, "web_search", None, success=False),
        ],
        iterations=4,
        tool_calls=4,
    )

    assert retrieved_source_urls(run) == []


def test_the_claims_own_domains_are_never_independent() -> None:
    assert claimed_domains_for(["https://www.example.org/a"]) == ["example.org"]
    assert independent_domains(
        ["https://example.org/other", "https://third.test/x"],
        claimed_domains=["example.org"],
    ) == ["third.test"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("verified", "verified"),
        ("  CONTRADICTED ", "contradicted"),
        ("insufficient evidence", "insufficient_evidence"),
        ("insufficient-evidence", "insufficient_evidence"),
        ("probably true", "insufficient_evidence"),
        ("", "insufficient_evidence"),
    ],
)
def test_verdict_normalization_is_total(raw: str, expected: str) -> None:
    assert normalize_verdict(raw) == expected


def test_a_claim_with_no_independent_source_is_insufficient() -> None:
    verdict, confidence = resolve_verdict(_verdict_draft(), independent=[])

    assert verdict == "insufficient_evidence"
    assert confidence == pytest.approx(0.0)


def test_reported_contradictions_downgrade_a_verified_verdict() -> None:
    verdict, confidence = resolve_verdict(
        _verdict_draft(
            verdict="verified", contradictions=["A regulator disputes it."]
        ),
        independent=["third.test"],
    )

    assert verdict == "contradicted"
    assert confidence == pytest.approx(0.9)


def test_confidence_is_clamped_and_zeroed_for_insufficient_evidence() -> None:
    verdict, confidence = resolve_verdict(
        _verdict_draft(verdict="insufficient_evidence", confidence=0.8),
        independent=["third.test"],
    )
    assert verdict == "insufficient_evidence"
    assert confidence == pytest.approx(0.0)

    _, high = resolve_verdict(
        _verdict_draft(confidence=4.0), independent=["third.test"]
    )
    assert high == pytest.approx(1.0)


def test_a_built_claim_keeps_its_own_sources_and_the_models_evidence() -> None:
    claim = build_claim(
        _claim_draft(),
        _verdict_draft(evidence=["Third party agrees."]),
        independent=["third.test"],
    )

    assert isinstance(claim, Claim)
    assert claim.source_urls == ["https://example.org/a"]
    assert claim.verdict == "verified"
    assert claim.evidence == ["Third party agrees."]
    assert claim.contradictions == []


def test_an_insufficient_claim_names_its_reason_and_invents_no_confidence() -> None:
    claim = insufficient_claim(_claim_draft(), reason="loop_failed")

    assert claim.verdict == "insufficient_evidence"
    assert claim.confidence == pytest.approx(0.0)
    assert claim.evidence == []
    assert claim.contradictions == []
    assert claim.source_urls == ["https://example.org/a"]


def test_an_insufficient_claim_rejects_an_unenumerated_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        insufficient_claim(_claim_draft(), reason="because")


def test_verification_messages_carry_the_claim_and_its_evidence() -> None:
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            )
        ],
        iterations=1,
        tool_calls=1,
    )
    task = ClaimTask(
        instruction="Verify one claim.",
        claim=_claim_draft(),
        claimed_domains=["example.org"],
    )

    messages = claim_verification_messages(
        task, run, evidence_chars=500, independent=["third.test"]
    )

    body = messages[1].content
    assert "Logical error rates fell below break-even in 2025." in body
    assert "https://example.org/a" in body
    assert "third.test" in body
    assert "insufficient_evidence" in body


def test_verdict_counts_cover_every_verdict_value() -> None:
    claims = [
        build_claim(
            _claim_draft(), _verdict_draft(), independent=["third.test"]
        ),
        insufficient_claim(_claim_draft(), reason="no_independent_source"),
    ]

    counts = verdict_counts(claims)

    assert counts == {
        "verified": 1,
        "unverified": 0,
        "contradicted": 0,
        "insufficient_evidence": 1,
    }
```

Extend the module's imports with:

```python
from typing import get_args

from deep_research.agents.fact_checker import (
    VERDICT_VALUES,
    ClaimTask,
    ClaimVerdictDraft,
    build_claim,
    claim_verification_messages,
    claimed_domains_for,
    independent_domains,
    insufficient_claim,
    normalize_verdict,
    resolve_verdict,
    retrieved_source_urls,
    verdict_counts,
)
from deep_research.agents.steps import ReActObservation, ReActRun, ReActStep
from deep_research.tools.base import ToolResult
from deep_research.utils.types import Claim, ClaimVerdict
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_fact_checker.py -v`
Expected: FAIL with `ImportError: cannot import name 'VERDICT_VALUES' from 'deep_research.agents.fact_checker'`

- [ ] **Step 3: Write minimal implementation**

Extend `fact_checker.py`'s imports with `CLAIM_VERIFICATION_INSTRUCTION`, `CLAIM_VERIFICATION_SYSTEM_PROMPT` (from `prompts`), `from deep_research.agents.researcher import render_evidence`, `from deep_research.agents.steps import ReActRun`, and `ClaimVerdict` in the `utils.types` import.

Append to the module:

```python
# The verdict vocabulary, in report order. Pinned against ClaimVerdict by
# test_verdict_values_match_the_shared_claim_verdict_type so the two can
# never drift.
VERDICT_VALUES: tuple[ClaimVerdict, ...] = (
    "verified",
    "unverified",
    "contradicted",
    "insufficient_evidence",
)

# Enumerated, project-generated reasons a claim could not be judged.
# Never provider text: these reach ResearchEvent.metadata.
INSUFFICIENT_REASONS = {
    "no_independent_source": (
        "No source independent of the claim's own publisher was retrieved."
    ),
    "loop_failed": "The verification loop stopped on a provider failure.",
    "provider_unavailable": (
        "The model provider failed while the verdict was requested."
    ),
    "unrecognized_verdict": "The model returned no usable verdict.",
}

# Read tools that can carry evidence, mapped to the payload key holding the
# source identifier. save_to_memory is absent by construction: this agent
# never writes.
_EVIDENCE_URL_KEYS = {
    "web_search": "results",
    "web_scraper": "url",
    "document_reader": "source",
    "query_memory": "matches",
}


class ClaimVerdictDraft(ContractModel):
    """One model verdict for one claim, before domain validation.

    ``verdict`` is a plain ``str`` rather than a ``Literal``: a value the
    model invents must become ``insufficient_evidence`` locally, not a
    validation failure that discards the whole verification pass.
    """

    verdict: str
    confidence: float
    evidence: list[str]
    contradictions: list[str]


def _search_urls(data: dict[str, object]) -> list[str]:
    results = data.get("results")
    if not isinstance(results, list):
        return []
    urls: list[str] = []
    for item in results:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            urls.append(str(item["url"]))
    return urls


def _memory_urls(data: dict[str, object]) -> list[str]:
    matches = data.get("matches")
    if not isinstance(matches, list):
        return []
    urls: list[str] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        candidate = match.get("source_url")
        if not isinstance(candidate, str):
            metadata = match.get("metadata")
            candidate = (
                metadata.get("source_url") if isinstance(metadata, dict) else None
            )
        if isinstance(candidate, str) and candidate.strip():
            urls.append(candidate)
    return urls


def retrieved_source_urls(run: ReActRun) -> list[str]:
    """Canonical URLs the loop actually retrieved content from, in order.

    A tool can return ``success=True`` with nothing usable inside it — a
    search with no hits, an empty scrape, a memory miss — so a payload is
    only counted when it actually carries content. This doubles as the
    "did we retrieve anything at all" predicate: an empty list means the
    verification call must not be made.
    """
    found: list[str] = []
    for step in run.steps:
        result = step.tool_result
        if result is None or not result.success:
            continue
        data = result.data
        if not isinstance(data, dict):
            continue
        if result.tool_name not in _EVIDENCE_URL_KEYS:
            continue
        if result.tool_name == "web_search":
            candidates = _search_urls(data)
        elif result.tool_name == "query_memory":
            candidates = _memory_urls(data)
        elif result.tool_name == "web_scraper":
            url = data.get("url")
            text = data.get("text")
            candidates = (
                [url]
                if isinstance(url, str) and isinstance(text, str) and text.strip()
                else []
            )
        else:
            source = data.get("source")
            candidates = (
                [source]
                if isinstance(source, str) and data.get("chunks")
                else []
            )
        for candidate in candidates:
            url = normalize_source_url(candidate)
            if url and url not in found:
                found.append(url)
    return found


def claimed_domains_for(source_urls: Sequence[str]) -> list[str]:
    """The distinct domains a claim's own sources live on."""
    domains: list[str] = []
    for url in source_urls:
        domain = source_domain(url).casefold()
        if domain not in domains:
            domains.append(domain)
    return domains


def independent_domains(
    urls: Sequence[str],
    *,
    claimed_domains: Sequence[str],
) -> list[str]:
    """Distinct retrieved domains that are not the claim's own.

    A second page from the publisher that made the claim is not
    corroboration, which is the whole point of cross-referencing.
    """
    claimed = {domain.casefold() for domain in claimed_domains}
    found: list[str] = []
    for url in urls:
        domain = source_domain(url).casefold()
        if domain in claimed or domain in found:
            continue
        found.append(domain)
    return found


def normalize_verdict(raw: str) -> ClaimVerdict:
    """Map model text onto a ``ClaimVerdict``, defaulting to the honest one.

    Anything unrecognised becomes ``insufficient_evidence``: a verdict the
    system cannot interpret is not evidence of anything.
    """
    candidate = " ".join(raw.split()).casefold().replace("-", "_")
    candidate = candidate.replace(" ", "_")
    if candidate in VERDICT_VALUES:
        return candidate  # type: ignore[return-value]
    return "insufficient_evidence"


def _clamp_confidence(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def resolve_verdict(
    draft: ClaimVerdictDraft,
    *,
    independent: Sequence[str],
) -> tuple[ClaimVerdict, float]:
    """Decide the recorded verdict from the model's answer and the evidence.

    Three local rules override the model, in this order:
    nothing independent was retrieved -> ``insufficient_evidence``;
    the model itself reported contradictions -> ``contradicted``, whatever
    it called the verdict; ``insufficient_evidence`` carries no confidence.
    """
    if not independent:
        return "insufficient_evidence", 0.0
    verdict = normalize_verdict(draft.verdict)
    confidence = _clamp_confidence(draft.confidence)
    if draft.contradictions:
        return "contradicted", confidence
    if verdict == "insufficient_evidence":
        return "insufficient_evidence", 0.0
    return verdict, confidence


def build_claim(
    claim: ClaimDraft,
    draft: ClaimVerdictDraft,
    *,
    independent: Sequence[str],
) -> Claim:
    """Stamp one model verdict into a validated ``Claim`` record."""
    verdict, confidence = resolve_verdict(draft, independent=independent)
    return Claim(
        text=claim.text,
        source_urls=list(claim.source_urls),
        verdict=verdict,
        confidence=confidence,
        evidence=list(draft.evidence),
        contradictions=list(draft.contradictions),
    )


def insufficient_claim(claim: ClaimDraft, *, reason: str) -> Claim:
    """Record a claim that could not be judged, with no invented confidence.

    ``reason`` is one of ``INSUFFICIENT_REASONS``; it travels in the
    claim's event metadata rather than on the record, because ``Claim``
    has no field for it and this project does not widen a shared contract
    for one agent's bookkeeping.
    """
    if reason not in INSUFFICIENT_REASONS:
        raise ValueError(f"unknown insufficient-evidence reason: {reason}")
    return Claim(
        text=claim.text,
        source_urls=list(claim.source_urls),
        verdict="insufficient_evidence",
        confidence=0.0,
        evidence=[],
        contradictions=[],
    )


def claim_verification_messages(
    task: ClaimTask,
    run: ReActRun,
    *,
    evidence_chars: int,
    independent: Sequence[str],
) -> list[ChatMessage]:
    """Build the messages that judge one claim from one finished loop."""
    claimed = "\n".join(f"- {url}" for url in task.claim.source_urls)
    domains = ", ".join(independent) or "(none)"
    sections = [
        f"## Claim\n{task.claim.text}",
        f"## Sources that made the claim\n{claimed}",
        f"## Independent domains retrieved\n{domains}",
        (
            "## Retrieved evidence\n"
            f"{render_evidence(run, limit=evidence_chars)}"
        ),
        f"## Response contract\n{CLAIM_VERIFICATION_INSTRUCTION}",
    ]
    return [
        ChatMessage(
            role="developer", content=CLAIM_VERIFICATION_SYSTEM_PROMPT
        ),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def verdict_counts(claims: Sequence[Claim]) -> dict[str, int]:
    """Count every verdict value, including the ones that did not occur.

    Zero-filled so a consumer reading the event stream never has to guess
    whether a missing key means zero or means the agent forgot.
    """
    counts = {verdict: 0 for verdict in VERDICT_VALUES}
    for claim in claims:
        counts[claim.verdict] += 1
    return counts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/test_fact_checker.py -v && ruff check src/deep_research/agents/fact_checker.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/fact_checker.py \
  tests/test_agents/test_fact_checker.py
git commit -m "feat: resolve claim verdicts against independent evidence"
```

---

### Task 9: `FactCheckerAgent` — Extraction, Per-Claim Loop, Verification

**Files:**
- Modify: `src/deep_research/agents/fact_checker.py` (append)
- Modify: `tests/research_fakes.py` (append `fact_checker_tools`)
- Modify: `tests/test_agents/test_fact_checker.py` (append)

**Interfaces:**
- Consumes: everything Tasks 7-8 produced; `AgentRun`, `BaseAgent`, `StructuredCompleter` from `deep_research.agents.base`; `render_react_messages` from `deep_research.agents.prompts`; `run_react_loop` from `deep_research.agents.react`; `ReActDecision`, `ReActStep` from `deep_research.agents.steps`; `FACT_CHECKER_SYSTEM_PROMPT` from `deep_research.agents.prompts`.
- Produces:
  - `claim_verification_provider_error(error: Exception) -> ResearchError`
  - `FactCheckerAgent(BaseAgent[VerifiedClaims])` with `name = "fact_checker"`, `allowed_tools = ("web_search", "web_scraper", "document_reader", "query_memory")`, constructor `(*, provider, tracker, scratchpad, tools=(), config=None, max_claims=DEFAULT_MAX_CLAIMS, finding_digest=DEFAULT_FINDING_DIGEST, evidence_chars=FACT_CHECK_EVIDENCE_CHARS)`, and methods `build_task(state) -> AgentTask`, `claim_task(base, claim) -> ClaimTask`, `async extract_claims(state) -> tuple[list[ClaimDraft], list[ResearchError], bool]`, `async verify_claim(task, run) -> tuple[Claim, str | None, list[ResearchError], bool]`, `async finalize(task, run)`, `state_update(result, run)`
- Task 10 adds `run` and the event builders on top of this class.

The `verify_claim` return is `(claim, reason, errors, provider_failed)`: `reason` is an `INSUFFICIENT_REASONS` key when the claim could not be judged and `None` otherwise, and it is what Task 10's per-claim event records.

- [ ] **Step 1: Write the failing tests**

Append `fact_checker_tools` to `tests/research_fakes.py`:

```python
def fact_checker_tools(
    tracker: Tracker,
    *,
    search: FakeSearchClient | None = None,
    memory: FakeMemory | None = None,
    http: httpx.AsyncClient | None = None,
) -> list[BaseTool]:
    """Build the four tools ``FactCheckerAgent`` declares, all offline.

    No ``save_to_memory``: the Fact Checker reads evidence and never
    writes findings.
    """
    client = http or page_client()
    return [
        WebSearchTool(
            tracker,
            client=search or FakeSearchClient([search_response()]),
        ),
        WebScraperTool(tracker, client=client),
        DocumentReaderTool(tracker, client=client),
        QueryMemoryTool(tracker, memory or FakeMemory()),
    ]
```

Append to `tests/test_agents/test_fact_checker.py`:

```python
def _checker(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    tools: list[object] | None = None,
    max_claims: int = 5,
) -> FactCheckerAgent:
    return FactCheckerAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="fact_checker", max_entries=20
        ),
        tools=tools if tools is not None else fact_checker_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=3),
        max_claims=max_claims,
    )


@pytest.mark.asyncio
async def test_extraction_keeps_only_claims_backed_by_real_sources(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(
                        text="Break-even was crossed in 2025.",
                        source_urls=["https://example.org/a"],
                    ),
                    ClaimDraft(
                        text="Invented.",
                        source_urls=["https://invented.test/x"],
                    ),
                ]
            )
        ]
    )
    agent = _checker(tracker, completer)
    state = _check_state([_check_finding("https://example.org/a")])

    claims, errors, provider_failed = await agent.extract_claims(state)

    assert provider_failed is False
    assert [claim.text for claim in claims] == [
        "Break-even was crossed in 2025."
    ]
    assert errors[0].error_type == "fact_checker_invalid_claim"
    assert errors[0].recoverable is True


@pytest.mark.asyncio
async def test_extraction_makes_no_provider_call_without_findings(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _checker(tracker, completer)

    claims, errors, provider_failed = await agent.extract_claims(_check_state([]))

    assert claims == []
    assert provider_failed is False
    assert errors[0].error_type == "fact_checker_no_findings"
    assert completer.calls == []


@pytest.mark.asyncio
async def test_extraction_provider_failure_is_non_recoverable(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])
    agent = _checker(tracker, completer)
    state = _check_state([_check_finding("https://example.org/a")])

    claims, errors, provider_failed = await agent.extract_claims(state)

    assert claims == []
    assert provider_failed is True
    assert errors[0].error_type == "fact_checker_extraction_provider_error"
    assert errors[0].recoverable is False
    assert errors[0].details["exception_type"] == "ProviderTimeoutError"


@pytest.mark.asyncio
async def test_a_claim_with_only_its_own_domain_retrieved_is_insufficient(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_scraper",
                {"url": "https://example.org/other", "text": "Same publisher."},
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, errors, provider_failed = await agent.verify_claim(task, run)

    assert claim.verdict == "insufficient_evidence"
    assert claim.confidence == pytest.approx(0.0)
    assert reason == "no_independent_source"
    assert errors == []
    assert provider_failed is False
    assert completer.calls == []


@pytest.mark.asyncio
async def test_a_loop_that_died_to_the_provider_is_insufficient(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="provider_error",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, _, _ = await agent.verify_claim(task, run)

    assert claim.verdict == "insufficient_evidence"
    assert reason == "loop_failed"
    assert completer.calls == []


@pytest.mark.asyncio
async def test_every_tool_call_failing_is_insufficient_not_unverified(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(1, "web_search", None, success=False),
            _tool_step(2, "web_scraper", None, success=False),
        ],
        iterations=2,
        tool_calls=2,
    )

    claim, reason, _, _ = await agent.verify_claim(task, run)

    assert claim.verdict == "insufficient_evidence"
    assert reason == "no_independent_source"
    assert completer.calls == []


@pytest.mark.asyncio
async def test_independent_evidence_produces_a_model_verdict(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(outputs=[_verdict_draft(verdict="verified")])
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, errors, provider_failed = await agent.verify_claim(task, run)

    assert claim.verdict == "verified"
    assert claim.confidence == pytest.approx(0.9)
    assert reason is None
    assert errors == []
    assert provider_failed is False


@pytest.mark.asyncio
async def test_a_contradiction_survives_a_verified_model_answer(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        outputs=[
            _verdict_draft(
                verdict="verified",
                contradictions=["A regulator published the opposite figure."],
            )
        ]
    )
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, _, _ = await agent.verify_claim(task, run)

    assert claim.verdict == "contradicted"
    assert claim.contradictions == [
        "A regulator published the opposite figure."
    ]
    assert reason is None


@pytest.mark.asyncio
async def test_a_verification_provider_failure_is_insufficient_not_invented(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, errors, provider_failed = await agent.verify_claim(task, run)

    assert claim.verdict == "insufficient_evidence"
    assert claim.confidence == pytest.approx(0.0)
    assert reason == "provider_unavailable"
    assert provider_failed is True
    assert errors[0].error_type == "fact_checker_verification_provider_error"
    assert errors[0].recoverable is False


def test_state_update_carries_verified_claims_and_errors(
    tracker: Tracker,
) -> None:
    agent = _checker(tracker, ScriptedCompleter())
    claim = insufficient_claim(_claim_draft(), reason="no_independent_source")
    run = ReActRun(agent_name="fact_checker", stop_reason="finished")

    update = agent.state_update(VerifiedClaims(claims=[claim]), run)

    assert update["verified_claims"] == [claim]
    assert update["errors"] == []
```

Extend the module's imports with `FactCheckerAgent`, `VerifiedClaims` from `deep_research.agents.fact_checker`; `AgentTask` from `deep_research.agents.prompts`; `ScratchpadMemory`, `Tracker`, `ProviderTimeoutError`, `AgentRuntimeConfig`; and `ScriptedCompleter`, `fact_checker_tools` from the test fakes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_fact_checker.py -v`
Expected: FAIL with `ImportError: cannot import name 'FactCheckerAgent'`

- [ ] **Step 3: Write minimal implementation**

Extend `fact_checker.py`'s imports with `AgentConfigurationError` (from `errors`), `AgentRun`, `BaseAgent`, `StructuredCompleter` (from `base`), `FACT_CHECKER_SYSTEM_PROMPT`, `render_react_messages` (from `prompts`), `run_react_loop` (from `react`), `ReActDecision`, `ReActStep`, `summarize_text` (from `steps`), `ScratchpadMemory`, `Tracker`, `OpenAIProviderError`, `BaseTool`, `AgentRuntimeConfig`, and `ResearchStateUpdate`.

Append to the module:

```python
def claim_verification_provider_error(error: Exception) -> ResearchError:
    """Record that one claim's verdict call could not reach the provider.

    Non-recoverable, and the claim is recorded as
    ``insufficient_evidence``: an outage is not evidence.
    """
    return agent_error(
        agent_name=FACT_CHECKER_NAME,
        error_type="fact_checker_verification_provider_error",
        message=(
            "The model provider failed while a claim's verdict was "
            "requested; the claim was recorded as insufficient evidence."
        ),
        recoverable=False,
        details={"exception_type": type(error).__name__},
    )


class FactCheckerAgent(BaseAgent[VerifiedClaims]):
    """Extract the major claims and verify each against independent sources.

    ``run`` is overridden because the spec requires a loop *per claim*,
    which the single-loop ``BaseAgent.run`` cannot express. Everything
    below ``run`` — bounds, tracing, tool execution, scratchpad writes —
    is still the shared runtime's.
    """

    name = FACT_CHECKER_NAME
    description = "Verify the major factual claims against independent sources."
    allowed_tools = (
        "web_search",
        "web_scraper",
        "document_reader",
        "query_memory",
    )

    def __init__(
        self,
        *,
        provider: StructuredCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        max_claims: int = DEFAULT_MAX_CLAIMS,
        finding_digest: int = DEFAULT_FINDING_DIGEST,
        evidence_chars: int = FACT_CHECK_EVIDENCE_CHARS,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
        )
        if max_claims < 1:
            raise ValueError("max_claims must be at least 1")
        if finding_digest < 1:
            raise ValueError("finding_digest must be at least 1")
        if evidence_chars < 1:
            raise ValueError("evidence_chars must be at least 1")
        self._max_claims = max_claims
        self._finding_digest = finding_digest
        self._evidence_chars = evidence_chars

    @property
    def output_schema(self) -> type[VerifiedClaims]:
        """The validated claims. Never sent to the provider.

        ``extract_claims`` asks for ``ClaimsDraft`` and ``verify_claim``
        asks for ``ClaimVerdictDraft``, because ``Claim`` carries ``Field``
        and ``UnitScore`` constraints that do not survive strict JSON
        schema conversion. Do not route this agent through
        ``complete_output``.
        """
        return VerifiedClaims

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return FACT_CHECKER_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> AgentTask:
        """Describe the run as a whole. ``claim_task`` narrows it."""
        return AgentTask(instruction=state.original_question)

    def claim_task(self, base: AgentTask, claim: ClaimDraft) -> ClaimTask:
        """Narrow the run-level task down to one claim's loop."""
        claimed = claimed_domains_for(claim.source_urls)
        sources = "\n".join(f"- {url}" for url in claim.source_urls)
        guidance = "\n".join(
            [
                "The claim was made by these sources:",
                sources,
                (
                    "Do not treat another page from "
                    f"{', '.join(claimed)} as independent corroboration."
                ),
            ]
        )
        sections = [
            section
            for section in (base.guidance.strip(), guidance)
            if section
        ]
        return ClaimTask(
            instruction=(
                f'Verify this claim against independent sources: "'
                f'{claim.text}"'
            ),
            guidance="\n\n".join(sections),
            claim=claim,
            claimed_domains=claimed,
        )

    async def extract_claims(
        self,
        state: ResearchState,
    ) -> tuple[list[ClaimDraft], list[ResearchError], bool]:
        """Turn the finished research pass into checkable claim drafts.

        Makes no provider call when there are no findings, so the
        extraction step can never invent a claim out of nothing.
        """
        if not state.raw_findings:
            return [], [no_findings_to_check_error()], False

        try:
            response = await self.provider.complete_structured(
                claim_extraction_messages(
                    state, max_findings=self._finding_digest
                ),
                ClaimsDraft,
                agent_name=self.name,
            )
        except OpenAIProviderError as error:
            return [], [claim_extraction_provider_error(error)], True

        claims, rejected = build_claim_drafts(
            response, known_urls=known_source_urls(state)
        )
        errors = [invalid_claim_error(rejected)] if rejected else []
        return claims[: self._max_claims], errors, False

    async def verify_claim(
        self,
        task: ClaimTask,
        run: ReActRun,
    ) -> tuple[Claim, str | None, list[ResearchError], bool]:
        """Judge one claim from one finished loop.

        Returns ``(claim, reason, errors, provider_failed)``. ``reason`` is
        an ``INSUFFICIENT_REASONS`` key when the claim could not be judged
        and ``None`` otherwise. No provider call is made when the loop
        failed or retrieved nothing independent, so a verdict can never be
        invented over an empty evidence section.
        """
        if not run.succeeded:
            return insufficient_claim(task.claim, reason="loop_failed"), (
                "loop_failed"
            ), [], False

        independent = independent_domains(
            retrieved_source_urls(run), claimed_domains=task.claimed_domains
        )
        if not independent:
            return (
                insufficient_claim(task.claim, reason="no_independent_source"),
                "no_independent_source",
                [],
                False,
            )

        try:
            draft = await self.provider.complete_structured(
                claim_verification_messages(
                    task,
                    run,
                    evidence_chars=self._evidence_chars,
                    independent=independent,
                ),
                ClaimVerdictDraft,
                agent_name=self.name,
            )
        except OpenAIProviderError as error:
            return (
                insufficient_claim(task.claim, reason="provider_unavailable"),
                "provider_unavailable",
                [claim_verification_provider_error(error)],
                True,
            )

        return build_claim(task.claim, draft, independent=independent), None, [], False

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> VerifiedClaims | None:
        """Adapt ``verify_claim`` to the ``BaseAgent`` hook.

        ``run`` calls ``verify_claim`` directly so it can keep the reason
        and the errors this hook signature has nowhere to return.
        """
        if not isinstance(task, ClaimTask):
            raise AgentConfigurationError(
                "FactCheckerAgent.finalize requires a ClaimTask"
            )
        claim, _, _, _ = await self.verify_claim(task, run)
        return VerifiedClaims(claims=[claim])

    def state_update(
        self,
        result: VerifiedClaims | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """Verified claims and errors only. ``run`` adds the events."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["verified_claims"] = list(result.claims)
        return update
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/test_fact_checker.py -v && ruff check src/deep_research/agents/fact_checker.py tests/research_fakes.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/fact_checker.py \
  tests/test_agents/test_fact_checker.py tests/research_fakes.py
git commit -m "feat: verify claims with a bounded loop per claim"
```

---

### Task 10: Fact Checker Observability And `run`

**Files:**
- Modify: `src/deep_research/agents/fact_checker.py` (event builders before the class; `_check_claim` and `run` at the end of the class)
- Modify: `tests/test_agents/test_fact_checker.py` (append)

**Interfaces:**
- Consumes: everything Tasks 7-9 produced; `agent_event` from `deep_research.agents.events`; `merge_react_runs` from `deep_research.agents.researcher`; `ResearchEvent` from `deep_research.utils.types`.
- Produces:
  - `claims_extracted_event(*, claim_count: int, findings_considered: int, sources_considered: int) -> ResearchEvent` — type `fact_checker.claims.extracted`
  - `claim_checked_event(claim: Claim, run: ReActRun, *, index: int, independent_sources: int, reason: str | None) -> ResearchEvent` — type `fact_checker.claim.checked`, metadata includes `tool_calls`
  - `fact_check_completed_event(claims: Sequence[Claim], *, tool_calls: int) -> ResearchEvent` — type `fact_checker.fact_check.completed`, metadata includes `claim_count`, one key per verdict, `contradiction_count`, `tool_calls`
  - `FactCheckerAgent._check_claim(task) -> ReActRun` (private)
  - `FactCheckerAgent.run(state) -> AgentRun[VerifiedClaims]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents/test_fact_checker.py`:

```python
def _check_decisions() -> list[object]:
    return [
        use_tool(
            "Look for an independent source.",
            "web_search",
            '{"query": "break-even 2025"}',
        ),
        finish("I have independent material.", "Checked."),
    ]


def test_the_per_claim_event_reports_tool_calls_and_the_verdict() -> None:
    claim = insufficient_claim(_claim_draft(), reason="no_independent_source")
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        iterations=2,
        tool_calls=2,
    )

    event = claim_checked_event(
        claim, run, index=1, independent_sources=0,
        reason="no_independent_source",
    )

    assert event.event_type == "fact_checker.claim.checked"
    assert event.source == "agent.fact_checker"
    assert event.metadata["verdict"] == "insufficient_evidence"
    assert event.metadata["tool_calls"] == 2
    assert event.metadata["independent_sources"] == 0
    assert event.metadata["reason"] == "no_independent_source"
    assert event.metadata["contradictions"] == 0


def test_the_completed_event_reports_every_verdict_count() -> None:
    verified = build_claim(
        _claim_draft(), _verdict_draft(), independent=["third.test"]
    )
    contradicted = build_claim(
        _claim_draft(),
        _verdict_draft(contradictions=["Disputed."]),
        independent=["third.test"],
    )

    event = fact_check_completed_event(
        [verified, contradicted], tool_calls=5
    )

    assert event.event_type == "fact_checker.fact_check.completed"
    assert event.metadata["claim_count"] == 2
    assert event.metadata["verified"] == 1
    assert event.metadata["contradicted"] == 1
    assert event.metadata["unverified"] == 0
    assert event.metadata["insufficient_evidence"] == 0
    assert event.metadata["contradiction_count"] == 1
    assert event.metadata["tool_calls"] == 5


@pytest.mark.asyncio
async def test_a_full_run_verifies_each_claim_and_reports_the_counts(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=list(_check_decisions()),
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(
                        text="Break-even was crossed in 2025.",
                        source_urls=["https://example.org/a"],
                    )
                ]
            ),
            _verdict_draft(verdict="verified"),
        ],
    )
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [search_response(url="https://third.test/x")]
            ),
        ),
    )
    state = _check_state(
        [_check_finding("https://example.org/a")],
        [_scored("https://example.org/a")],
    )

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.agent_name == "fact_checker"
    assert outcome.result is not None
    assert [claim.verdict for claim in outcome.result.claims] == ["verified"]

    merged = merge_research_state(state, outcome.state_update)
    assert len(merged.verified_claims) == 1

    events = outcome.state_update["events"]
    types = [event.event_type for event in events]
    assert types[0] == "fact_checker.claims.extracted"
    assert "fact_checker.claim.checked" in types
    assert types[-1] == "fact_checker.fact_check.completed"
    completed = events[-1]
    assert completed.metadata["claim_count"] == 1
    assert completed.metadata["verified"] == 1
    assert completed.metadata["contradiction_count"] == 0
    checked = next(
        event for event in events
        if event.event_type == "fact_checker.claim.checked"
    )
    assert checked.metadata["tool_calls"] >= 1


@pytest.mark.asyncio
async def test_a_run_without_findings_verifies_nothing_and_says_so(
    tracker: Tracker,
) -> None:
    agent = _checker(tracker, ScriptedCompleter())
    state = _check_state([])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert outcome.result.claims == []
    assert outcome.errors[0].error_type == "fact_checker_no_findings"
    completed = outcome.state_update["events"][-1]
    assert completed.metadata["claim_count"] == 0
    assert completed.metadata["insufficient_evidence"] == 0


@pytest.mark.asyncio
async def test_a_verification_provider_failure_stops_further_claims(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=list(_check_decisions()),
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(text="First.", source_urls=["https://example.org/a"]),
                    ClaimDraft(text="Second.", source_urls=["https://example.org/a"]),
                ]
            ),
            ProviderTimeoutError("timed out"),
        ],
    )
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [search_response(url="https://third.test/x")]
            ),
        ),
    )
    state = _check_state([_check_finding("https://example.org/a")])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert [claim.verdict for claim in outcome.result.claims] == [
        "insufficient_evidence"
    ]
    assert outcome.react.stop_reason == "provider_error"
    types = {error.error_type for error in outcome.errors}
    assert "fact_checker_verification_provider_error" in types
```

Extend the module's imports with `claim_checked_event`, `fact_check_completed_event` from `deep_research.agents.fact_checker`; `merge_research_state` from `deep_research.utils.types`; and `finish`, `use_tool` from `tests.agent_fakes`, `FakeSearchClient`, `search_response` from `tests.research_fakes`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_fact_checker.py -v`
Expected: FAIL with `ImportError: cannot import name 'claim_checked_event'`

- [ ] **Step 3: Write minimal implementation**

Extend `fact_checker.py`'s imports with `from deep_research.agents.events import agent_event`, `from deep_research.agents.researcher import merge_react_runs, render_evidence`, and `ResearchEvent` in the `utils.types` import.

Insert the event builders just before `class FactCheckerAgent`:

```python
def claims_extracted_event(
    *,
    claim_count: int,
    findings_considered: int,
    sources_considered: int,
) -> ResearchEvent:
    """Report how many checkable claims the findings yielded."""
    return agent_event(
        agent_name=FACT_CHECKER_NAME,
        event_type="fact_checker.claims.extracted",
        message="Claim extraction complete.",
        metadata={
            "claim_count": claim_count,
            "findings_considered": findings_considered,
            "sources_considered": sources_considered,
        },
    )


def claim_checked_event(
    claim: Claim,
    run: ReActRun,
    *,
    index: int,
    independent_sources: int,
    reason: str | None,
) -> ResearchEvent:
    """Report one claim's verdict and what it cost to reach it.

    ``tool_calls`` here is the spec's "tool calls per claim". ``reason`` is
    an ``INSUFFICIENT_REASONS`` key or ``None``; it is never provider
    text. The claim itself is summarized, never pasted, for the same
    reason.
    """
    return agent_event(
        agent_name=FACT_CHECKER_NAME,
        event_type="fact_checker.claim.checked",
        message=f"Claim {index} checked.",
        metadata={
            "claim": summarize_text(claim.text),
            "index": index,
            "verdict": claim.verdict,
            "confidence": round(claim.confidence, 4),
            "contradictions": len(claim.contradictions),
            "independent_sources": independent_sources,
            "tool_calls": run.tool_calls,
            "iterations": run.iterations,
            "stop_reason": run.stop_reason,
            "reason": reason,
        },
    )


def fact_check_completed_event(
    claims: Sequence[Claim],
    *,
    tool_calls: int,
) -> ResearchEvent:
    """Report the whole fact-checking pass.

    Verdict counts are zero-filled by ``verdict_counts``, so a consumer
    never has to guess whether a missing key means zero.
    """
    counts = verdict_counts(claims)
    contradiction_count = sum(1 for claim in claims if claim.contradictions)
    return agent_event(
        agent_name=FACT_CHECKER_NAME,
        event_type="fact_checker.fact_check.completed",
        message="Fact checking complete.",
        metadata={
            "claim_count": len(claims),
            **counts,
            "contradiction_count": contradiction_count,
            "tool_calls": tool_calls,
        },
    )
```

Append to `FactCheckerAgent`:

```python
    async def _check_claim(self, task: ClaimTask) -> ReActRun:
        """Run one bounded ReAct loop inside the caller's agent span.

        The scratchpad is cleared first: notes about the previous claim are
        noise in this one's prompt. Context that genuinely carries over
        travels in ``task.guidance`` instead.
        """
        self.scratchpad.clear()
        toolset = self.toolset

        async def decide(
            iteration: int,
            steps: Sequence[ReActStep],
        ) -> ReActDecision:
            del steps
            return await self.provider.complete_structured(
                render_react_messages(
                    system_prompt=self.system_prompt(task),
                    task=task,
                    descriptors=toolset.descriptors(),
                    scratchpad=self.scratchpad.recent(
                        self.config.prompt_context_entries
                    ),
                    iteration=iteration,
                    max_iterations=self.config.max_iterations,
                ),
                ReActDecision,
                agent_name=self.name,
            )

        react = await run_react_loop(
            agent_name=self.name,
            tracker=self.tracker,
            tools=toolset,
            decide=decide,
            max_iterations=self.config.max_iterations,
            tool_budget=self.config.tool_budget,
            on_step=self._record_step,
            is_sufficient=self.is_sufficient,
            summary_limit=self.config.observation_summary_chars,
        )
        return react.model_copy(
            update={"errors": [*react.errors, *self.scratchpad.drain_errors()]}
        )

    async def run(self, state: ResearchState) -> AgentRun[VerifiedClaims]:
        """Extract claims, then verify each in its own bounded loop."""
        base_task = self.build_task(state)
        events: list[ResearchEvent] = []
        errors: list[ResearchError] = []
        claims: list[Claim] = []
        runs: list[ReActRun] = []

        async with self.tracker.agent_span(self.name) as span:
            drafts, extraction_errors, extraction_failed = (
                await self.extract_claims(state)
            )
            errors.extend(extraction_errors)
            span.set_outputs(
                {
                    "agent_name": self.name,
                    "phase": "extraction",
                    "claim_count": len(drafts),
                    "provider_failed": extraction_failed,
                }
            )
        events.append(
            claims_extracted_event(
                claim_count=len(drafts),
                findings_considered=len(state.raw_findings),
                sources_considered=len(state.evaluated_sources),
            )
        )

        for index, draft in enumerate(drafts, start=1):
            task = self.claim_task(base_task, draft)
            async with self.tracker.agent_span(self.name) as span:
                react = await self._check_claim(task)
                claim, reason, verify_errors, verify_failed = (
                    await self.verify_claim(task, react)
                )
                if verify_failed:
                    # Mirror the loop-level provider_error path so the
                    # merged run never claims "finished" over an abort that
                    # actually happened during verification.
                    react = react.model_copy(
                        update={"stop_reason": "provider_error"}
                    )
                independent = len(
                    independent_domains(
                        retrieved_source_urls(react),
                        claimed_domains=task.claimed_domains,
                    )
                )
                span.set_outputs(
                    {
                        "agent_name": self.name,
                        "claim_index": index,
                        "verdict": claim.verdict,
                        "independent_sources": independent,
                        "tool_calls": react.tool_calls,
                        "stop_reason": react.stop_reason,
                    }
                )

            runs.append(react)
            claims.append(claim)
            errors.extend(react.errors)
            errors.extend(verify_errors)
            events.append(
                claim_checked_event(
                    claim,
                    react,
                    index=index,
                    independent_sources=independent,
                    reason=reason,
                )
            )
            if not react.succeeded:
                # A provider failure — from the loop or from verification —
                # is non-recoverable; the next claim would almost certainly
                # repeat it at cost. Claims already judged are kept.
                break

        merged = merge_react_runs(self.name, runs).model_copy(
            update={"errors": errors}
        )
        events.append(
            fact_check_completed_event(claims, tool_calls=merged.tool_calls)
        )
        result = VerifiedClaims(claims=claims)
        return AgentRun(
            agent_name=self.name,
            result=result,
            react=merged,
            errors=errors,
            state_update={
                **self.state_update(result, merged),
                "events": events,
            },
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/ -v && ruff check src/deep_research/agents/ tests/`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/fact_checker.py \
  tests/test_agents/test_fact_checker.py
git commit -m "feat: emit fact-check verdict counts and run the pass"
```

---

### Task 11: Public Surface, End-To-End Seam Test, And Documentation

**Files:**
- Modify: `src/deep_research/agents/__init__.py`
- Modify: `tests/test_imports.py`
- Create: `tests/test_agents/test_evidence_quality_seam.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything Tasks 1-10 produced.
- Produces: the three new modules' public names on `deep_research.agents`, and a seam test proving `ResearcherAgent` output flows through `SourceEvaluatorAgent` into `FactCheckerAgent` via `merge_research_state`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_imports.py`, add to `test_agent_runtime_contracts_import_from_package`'s import list:

```python
        FACT_CHECKER_NAME,
        LOW_CONFIDENCE_THRESHOLD,
        SOURCE_EVALUATOR_NAME,
        VERDICT_VALUES,
        ClaimDraft,
        ClaimsDraft,
        ClaimTask,
        ClaimVerdictDraft,
        EvaluatedSources,
        FactCheckerAgent,
        ReputationSource,
        SourceEvaluationTask,
        SourceEvaluatorAgent,
        SourceGroup,
        SourceScoreDraft,
        SourceScoresDraft,
        VerifiedClaims,
        build_claim,
        build_scored_source,
        corroboration_score,
        group_findings_by_url,
        normalize_source_url,
        normalize_verdict,
        resolve_verdict,
        retrieved_source_urls,
        source_domain,
        verdict_counts,
```

In `test_agent_submodule_public_names_all_reach_all`, extend the `submodules` list to:

```python
    submodules = [
        "base",
        "errors",
        "events",
        "fact_checker",
        "planner",
        "prompts",
        "react",
        "researcher",
        "source_evaluator",
        "sources",
        "steps",
        "toolset",
        "validation",
    ]
```

Append to `test_concrete_agents_expose_their_identity_and_tools`:

```python
    from deep_research.agents import FactCheckerAgent, SourceEvaluatorAgent

    assert SourceEvaluatorAgent.name == "source_evaluator"
    assert SourceEvaluatorAgent.allowed_tools == ()
    assert FactCheckerAgent.name == "fact_checker"
    assert set(FactCheckerAgent.allowed_tools) == {
        "web_search",
        "web_scraper",
        "document_reader",
        "query_memory",
    }
```

Create `tests/test_agents/test_evidence_quality_seam.py`:

```python
"""End-to-end seam: Researcher output feeds the evidence-quality agents.

Every other Source Evaluator and Fact Checker test builds
``ResearchState`` by hand, so nothing exercises the real seam: that
``ResearcherAgent`` writes ``raw_findings`` whose URLs the Source
Evaluator can group, and that the Fact Checker only ever cites URLs that
actually reached state. This test runs all three agents in sequence,
merging state the way the orchestrator will.
"""

from __future__ import annotations

import pytest

from deep_research.agents.fact_checker import (
    ClaimDraft,
    ClaimsDraft,
    ClaimVerdictDraft,
    FactCheckerAgent,
)
from deep_research.agents.researcher import (
    FindingDraft,
    ResearcherAgent,
    SubTopicFindingsDraft,
)
from deep_research.agents.source_evaluator import (
    SourceEvaluatorAgent,
    SourceScoreDraft,
    SourceScoresDraft,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    MemorySnapshot,
    ResearchState,
    SubTopic,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import (
    FakeReputationSource,
    FakeSearchClient,
    fact_checker_tools,
    research_tools,
    search_response,
)

SOURCE_URL = "https://example.test/qec"
INDEPENDENT_URL = "https://third.test/review"


def _state() -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        sub_topics=[
            SubTopic(
                title="Alpha",
                rationale="Alpha is load-bearing.",
                search_queries=["alpha 2025"],
                success_criteria=["A named source about Alpha."],
                priority=1,
            )
        ],
        memory_context=MemorySnapshot(),
    )


def _pad(agent_name: str) -> ScratchpadMemory:
    return ScratchpadMemory(
        session_id="session-1", agent_name=agent_name, max_entries=20
    )


@pytest.mark.asyncio
async def test_findings_flow_through_scoring_into_verified_claims(
    tracker: Tracker,
) -> None:
    state = _state()

    researcher = ResearcherAgent(
        provider=ScriptedCompleter(
            decisions=[
                use_tool("Find sources.", "web_search", '{"query": "alpha"}'),
                use_tool(
                    "Read the source.",
                    "web_scraper",
                    f'{{"url": "{SOURCE_URL}"}}',
                ),
                finish("I have a source-backed answer.", "Evidence found."),
            ],
            outputs=[
                SubTopicFindingsDraft(
                    findings=[
                        FindingDraft(
                            content="Break-even was crossed in 2025.",
                            source_url=SOURCE_URL,
                            source_title="Quantum error correction in 2025",
                            confidence=0.8,
                        )
                    ]
                )
            ],
        ),
        tracker=tracker,
        scratchpad=_pad("researcher"),
        tools=research_tools(
            tracker, search=FakeSearchClient([search_response()])
        ),
        config=AgentRuntimeConfig(max_iterations=4, tool_budget=4),
    )
    async with tracker.session_span("session-1", state.original_question):
        outcome = await researcher.run(state)
    state = merge_research_state(state, outcome.state_update)

    assert [finding.source_url for finding in state.raw_findings] == [SOURCE_URL]

    evaluator = SourceEvaluatorAgent(
        provider=ScriptedCompleter(
            outputs=[
                SourceScoresDraft(
                    sources=[
                        SourceScoreDraft(
                            url=SOURCE_URL,
                            authority_score=0.9,
                            recency_score=0.8,
                            relevance_score=0.9,
                            rationale="Peer-reviewed and dated.",
                        )
                    ]
                )
            ]
        ),
        tracker=tracker,
        scratchpad=_pad("source_evaluator"),
        reputation=FakeReputationSource(reputations={SOURCE_URL: 0.8}),
    )
    async with tracker.session_span("session-1", state.original_question):
        outcome = await evaluator.run(state)
    state = merge_research_state(state, outcome.state_update)

    # Every source behind a finding is scored, keyed by its canonical URL.
    assert [source.url for source in state.evaluated_sources] == [SOURCE_URL]
    assert state.evaluated_sources[0].low_confidence is False
    assert 0.0 <= state.evaluated_sources[0].overall_score <= 1.0

    checker = FactCheckerAgent(
        provider=ScriptedCompleter(
            decisions=[
                use_tool(
                    "Find an independent source.",
                    "web_search",
                    '{"query": "break-even 2025"}',
                ),
                finish("I have independent material.", "Checked."),
            ],
            outputs=[
                ClaimsDraft(
                    claims=[
                        ClaimDraft(
                            text="Break-even was crossed in 2025.",
                            source_urls=[SOURCE_URL],
                        )
                    ]
                ),
                ClaimVerdictDraft(
                    verdict="verified",
                    confidence=0.85,
                    evidence=["An unrelated review reports the same result."],
                    contradictions=[],
                ),
            ],
        ),
        tracker=tracker,
        scratchpad=_pad("fact_checker"),
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient([search_response(url=INDEPENDENT_URL)]),
        ),
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=3),
    )
    async with tracker.session_span("session-1", state.original_question):
        outcome = await checker.run(state)
    state = merge_research_state(state, outcome.state_update)

    # The claim cites only a URL that actually reached state, and its
    # verdict came from an independent domain.
    assert len(state.verified_claims) == 1
    claim = state.verified_claims[0]
    assert claim.source_urls == [SOURCE_URL]
    assert claim.verdict == "verified"

    completed = next(
        event
        for event in outcome.state_update["events"]
        if event.event_type == "fact_checker.fact_check.completed"
    )
    assert completed.metadata["claim_count"] == 1
    assert completed.metadata["verified"] == 1
    assert completed.metadata["contradiction_count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_imports.py tests/test_agents/test_evidence_quality_seam.py -v`
Expected: FAIL — `ImportError: cannot import name 'SourceEvaluatorAgent' from 'deep_research.agents'`, plus `test_agent_submodule_public_names_all_reach_all` listing every unexported public name in the three new modules.

- [ ] **Step 3: Write minimal implementation**

In `src/deep_research/agents/__init__.py`, add three import blocks (keep them isort-ordered: `fact_checker` after `events`, `source_evaluator` and `sources` after `researcher`) and extend `__all__`. The names to import and export, by module:

From `deep_research.agents.fact_checker`:
`DEFAULT_FINDING_DIGEST`, `DEFAULT_MAX_CLAIMS`, `FACT_CHECKER_NAME`, `FACT_CHECK_EVIDENCE_CHARS`, `INSUFFICIENT_REASONS`, `VERDICT_VALUES`, `ClaimDraft`, `ClaimTask`, `ClaimVerdictDraft`, `ClaimsDraft`, `FactCheckerAgent`, `VerifiedClaims`, `build_claim`, `build_claim_drafts`, `claim_checked_event`, `claim_extraction_messages`, `claim_extraction_provider_error`, `claim_verification_messages`, `claim_verification_provider_error`, `claimed_domains_for`, `claims_extracted_event`, `fact_check_completed_event`, `independent_domains`, `insufficient_claim`, `invalid_claim_error`, `known_source_urls`, `no_findings_to_check_error`, `normalize_verdict`, `resolve_verdict`, `retrieved_source_urls`, `verdict_counts`

From `deep_research.agents.source_evaluator`:
`AUTHORITY_WEIGHT`, `CORROBORATION_WEIGHT`, `DEFAULT_EXCERPT_CHARS`, `DEFAULT_MAX_SOURCES`, `FALLBACK_REASONS`, `LOW_CONFIDENCE_THRESHOLD`, `RECENCY_WEIGHT`, `RELEVANCE_WEIGHT`, `REPUTATION_BLEND`, `SOURCE_EVALUATOR_NAME`, `EvaluatedSources`, `ReputationSource`, `SourceEvaluationTask`, `SourceEvaluatorAgent`, `SourceScoreDraft`, `SourceScoresDraft`, `average_score`, `blend_authority`, `build_rationale`, `build_scored_source`, `clamp_unit`, `evaluation_completed_event`, `evaluation_started_event`, `fallback_scored_source`, `low_confidence_count`, `no_sources_error`, `overall_score`, `reputation_lookup_error`, `scoring_messages`, `scoring_provider_error`

From `deep_research.agents.sources`:
`SourceGroup`, `corroboration_score`, `group_findings_by_url`, `normalize_source_url`, `source_domain`

From `deep_research.agents.prompts` (new names to add to the existing block):
`CLAIM_EXTRACTION_INSTRUCTION`, `CLAIM_EXTRACTION_SYSTEM_PROMPT`, `CLAIM_VERIFICATION_INSTRUCTION`, `CLAIM_VERIFICATION_SYSTEM_PROMPT`, `FACT_CHECKER_SYSTEM_PROMPT`, `SOURCE_EVALUATOR_SYSTEM_PROMPT`, `SOURCE_SCORING_INSTRUCTION`, `render_finding_digest`, `render_source_dossier`, `render_source_quality`

`__all__` is sorted with `SCREAMING_CASE` constants first, then `CamelCase`, then `snake_case` — follow the file's existing ordering. Run `test_agent_submodule_public_names_all_reach_all` to catch anything missed rather than checking by eye.

- [ ] **Step 4: Run the whole suite**

Run: `pytest -v && ruff check src/ tests/`
Expected: PASS, no lint findings

- [ ] **Step 5: Update the README**

In `README.md`, add a section after "Planner And Researcher":

````markdown
## Source Evaluator And Fact Checker

`SourceEvaluatorAgent` groups `state.raw_findings` by canonical source URL,
computes a corroboration score locally (the fraction of a source's
sub-topics that a *different* domain also covered), looks up any
reputation previous sessions recorded for each URL, and asks the model for
authority, recency, and relevance. `overall_score` is computed here, not by
the model: `0.35*authority + 0.15*recency + 0.30*relevance +
0.20*corroboration`, with a remembered reputation blended into authority at
weight 0.4. Every score is a `UnitScore` in `[0.0, 1.0]`. A source scoring
under `LOW_CONFIDENCE_THRESHOLD` (0.4) is flagged `low_confidence=True`, and
so is any source the model did not score, any source past `max_sources`, and
every source in a run where the scoring call failed — the guarantee is that
*every* source behind a finding gets a record. A failing reputation backend
records one recoverable `source_evaluator_reputation_unavailable` error and
scoring continues directly. This agent runs no ReAct loop and declares no
tools; reputation reaches it through an injected `ReputationSource`, which
`LongTermMemory` satisfies.

`FactCheckerAgent` extracts the major factual claims from the findings in
one structured call (dropping any claim whose source URL never appeared in
`raw_findings`), then runs one bounded ReAct loop **per claim** using
`web_search`, `web_scraper`, `document_reader`, and `query_memory`. A
verdict is only requested from the model once the loop has actually
retrieved content from a domain other than the claim's own publisher.
Verdicts are normalized locally: no independent domain, a loop that died to
a provider failure, an unrecognized verdict string, or a failed verdict call
all become `insufficient_evidence` with confidence 0.0, and any verdict
arriving alongside reported contradictions becomes `contradicted`. There is
no path that invents confidence.

```python
from deep_research.agents import FactCheckerAgent, SourceEvaluatorAgent
from deep_research.utils.types import merge_research_state

async with tracker.session_span(session_id, state.original_question):
    evaluation = await evaluator.run(state)
    state = merge_research_state(state, evaluation.state_update)

    fact_check = await checker.run(state)
    state = merge_research_state(state, fact_check.state_update)
```

| Event type | Emitted by | Key metadata |
| --- | --- | --- |
| `source_evaluator.evaluation.started` | Source Evaluator | `finding_count`, `source_count` |
| `source_evaluator.evaluation.completed` | Source Evaluator | `source_count`, `average_score`, `low_confidence_count`, `reputation_hits`, `reputation_failures` |
| `fact_checker.claims.extracted` | Fact Checker | `claim_count`, `findings_considered`, `sources_considered` |
| `fact_checker.claim.checked` | Fact Checker | `claim`, `verdict`, `confidence`, `contradictions`, `independent_sources`, `tool_calls`, `reason` |
| `fact_checker.fact_check.completed` | Fact Checker | `claim_count`, `verified`, `unverified`, `contradicted`, `insufficient_evidence`, `contradiction_count`, `tool_calls` |
````

Update the phase line at the bottom of `README.md` to:

```markdown
- Phase 3: Agents and LangGraph orchestration ← current (runtime, Planner, Researcher, Source Evaluator, and Fact Checker complete; Synthesizer, Critic, and the graph pending)
```

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/agents/__init__.py tests/test_imports.py \
  tests/test_agents/test_evidence_quality_seam.py README.md
git commit -m "docs: publish the evidence-quality agent surface"
```

---

## Self-Review

Run after the plan is written, before execution starts. Recorded here so a
reviewer can check the same things.

**1. Spec coverage.** Every spec bullet maps to at least one task:

| Spec requirement | Task |
| --- | --- |
| Scope: `SourceEvaluatorAgent` | 5, 6 |
| Scope: `FactCheckerAgent` | 9, 10 |
| Scope: source scoring prompts and schemas | 3 (prompts), 4 (`SourceScoreDraft`/`SourceScoresDraft`), 1 (`ScoredSource.low_confidence`) |
| Scope: claim extraction/verification prompts and schemas | 3, 7 (`ClaimDraft`/`ClaimsDraft`), 8 (`ClaimVerdictDraft`) |
| Scope: tests with mocked tools and provider responses | every task; fakes extended in 5 and 9 |
| Design: reads `raw_findings` | 5 (`build_task`), 7 (`claim_extraction_messages`) |
| Design: groups findings by source URL | 2 |
| Design: queries known source reputation from memory | 5 (`lookup_reputations`) |
| Design: scores authority, recency, relevance, corroboration | 2 (corroboration), 4 (the other three plus `overall_score`) |
| Design: writes `ScoredSource` records | 4 (`build_scored_source`), 6 (`state_update` via `run`) |
| Design: flags weak or low-confidence sources | 1, 4 |
| Design: reads `raw_findings` and `evaluated_sources` | 7 (`claim_extraction_messages` renders both) |
| Design: extracts major factual claims | 7, 9 (`extract_claims`) |
| Design: cross-references using search, scrape, document read, memory query | 9 (`allowed_tools`, `_check_claim` in 10) |
| Design: writes `Claim` records with the four verdicts | 8 (`build_claim`, `VERDICT_VALUES`), 10 (`run`) |
| Observability: source count, average score, low-confidence count | 6 (`evaluation_completed_event`, span outputs) |
| Observability: claim count, verdict counts, contradiction count | 10 (`fact_check_completed_event`) |
| Observability: tool calls per claim | 10 (`claim_checked_event.metadata["tool_calls"]`) |
| Error handling: reputation lookup failure → direct scoring | 5 (`lookup_reputations`, `reputation_lookup_error`) |
| Error handling: unverifiable claim → insufficient evidence | 8 (`resolve_verdict`, `insufficient_claim`), 9 (`verify_claim`) |
| Testing: source grouping by URL | 2 |
| Testing: score bounds and rationale | 4 |
| Testing: reputation cache usage | 5 |
| Testing: claim extraction | 7, 9 |
| Testing: claim verification outcomes | 9 |
| Testing: contradiction handling | 8, 9 |
| Testing: insufficient evidence handling | 8, 9 |
| Acceptance: every source gets a score or low-confidence flag | 5 (`score_sources` fallback loop), 6, 11 (seam) |
| Acceptance: major claims receive verdicts | 9, 10, 11 |
| Acceptance: weak sources and unsupported claims visible downstream | 1 (`low_confidence` on the record), 6 and 10 (`state_update`), 11 (seam asserts both reach state) |
| Acceptance: tests run without live provider or network calls | Global Constraints; every task uses `ScriptedCompleter` and the offline tool fakes |

Non-goals held: no synthesis, no Critic routing, no external fact database.

**2. Placeholder scan.** No "TBD", "implement later", "add error handling",
or "similar to Task N" appears. Every code step carries the actual code.
Two forward references are called out inline where they occur (`summarize_text`
in Task 5 and `source_domain` in Task 7 are imported one task before first
use, with instructions for what to do if Ruff flags them).

**3. Type consistency.** Checked across tasks:

- `ScoredSource` field names — `url`, `title`, `authority_score`,
  `recency_score`, `relevance_score`, `corroboration_score`,
  `overall_score`, `rationale`, `low_confidence` — used identically in
  Tasks 1, 3, 4, 5, 6, 7, 11.
- `Claim` field names — `text`, `source_urls`, `verdict`, `confidence`,
  `evidence`, `contradictions` — used identically in Tasks 7, 8, 9, 10, 11.
- The four verdict strings are spelled `verified`, `unverified`,
  `contradicted`, `insufficient_evidence` everywhere: `VERDICT_VALUES`
  (Task 8), `normalize_verdict`, `resolve_verdict`, `insufficient_claim`,
  `verdict_counts`, `fact_check_completed_event` (Task 10), the prompt text
  (Task 3), and the tests. `VERDICT_VALUES` is pinned against
  `get_args(ClaimVerdict)` by a test so it can never drift.
- `SourceGroup` fields `url`/`domain`/`title`/`sub_topics`/`findings` are
  identical in Tasks 2, 3, 4, 5.
- `SourceEvaluationTask.groups`/`corroborations`/`reputations` are the same
  in Tasks 5 and 6; `ClaimTask.claim`/`claimed_domains` the same in 7, 8, 9,
  10.
- `score_sources` and `verify_claim` return arities are fixed at Task 5/9
  and consumed unchanged in Tasks 6/10 (`(sources, errors, provider_failed)`
  and `(claim, reason, errors, provider_failed)`).
- `FACT_CHECK_EVIDENCE_CHARS` is deliberately *not* named
  `DEFAULT_EVIDENCE_CHARS`: `researcher.py` already exports that name and
  both are re-exported from `deep_research.agents`.
- Agent identity constants (`SOURCE_EVALUATOR_NAME = "source_evaluator"`,
  `FACT_CHECKER_NAME = "fact_checker"`) match the `ScratchpadMemory`
  `agent_name` used in every test helper — `BaseAgent.__init__` raises
  `AgentConfigurationError` if they diverge.
