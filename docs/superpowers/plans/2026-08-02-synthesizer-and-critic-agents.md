# Synthesizer And Critic Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the two agents that close the research pass — `SynthesizerAgent`, which turns `verified_claims`, `evaluated_sources`, and `raw_findings` into a cited Markdown report saved through `write_document`, and `CriticAgent`, which scores that report and emits a `Critique` structured enough for LangGraph to route on.

**Architecture:** Both agents follow the `source_evaluator.py` / `fact_checker.py` template: a module of pure helpers and prompt-facing draft models, then a `BaseAgent` subclass that overrides `run` to add progress events. The division of labour is the project's existing one — the model writes prose and judges quality, local code computes everything countable. `report.py` (new) owns the whole Markdown skeleton: citation numbering, the verified/uncertain claim split, the limitations block, and the source appendix, all rendered from recorded evidence, so **the required sections exist even when the provider call fails**. `synthesizer.py` runs **no ReAct loop** — report generation is one structured call, and `write_document` / `save_to_memory` are deterministic writes made afterwards, never decisions handed to a model. `critic.py` runs **one bounded ReAct loop** for optional spot-checking (mirroring `ResearcherAgent`'s loop-then-extract shape), then one structured critique call; the routing decision itself is computed locally in `route_decision`, which checks the iteration bound *first* so no model judgement can make the loop run forever.

**Tech Stack:** Python 3.11+, Pydantic 2, OpenAI Python SDK (through the existing `OpenAIChatProvider`), LangSmith SDK 0.10+ (through the existing `Tracker`), pytest, pytest-asyncio (strict mode — every async test needs `@pytest.mark.asyncio`), Ruff

## Global Constraints

- Preserve `requires-python = ">=3.11"`. No new runtime dependencies: this plan adds zero entries to `pyproject.toml`.
- No LangGraph, FastAPI, or Streamlit import anywhere in `src/deep_research/agents/`. Both agents are plain async library code.
- **No graph routing, no API, no UI, no PDF export.** Spec 11 owns routing. This plan writes `report` and `critique` into `ResearchStateUpdate` and stops there. `Critique.should_continue` is a *recommendation record*; nothing in this plan acts on it.
- Never send a domain type to the provider. `Critique` declares `CriticScore` (`ge=1`, `le=10`) and `rationale: str = Field(min_length=1)`, which render as `minimum` / `maximum` / `minLength` and are rejected by strict structured outputs. Ask for the constraint-free `ReportDraft` / `CritiqueDraft` mirrors and validate locally, exactly as `ResearchPlanDraft`, `SubTopicFindingsDraft`, `SourceScoresDraft`, and `ClaimVerdictDraft` already do.
- Recorded `ResearchError.details` and `ResearchEvent.metadata` carry counts, identifiers, and enumerated reasons only — never `str(exception)` and never raw provider text.
- Tool failures are observations; they never stop a loop. Only provider failures are non-recoverable.
- Every model is a `ContractModel` subclass (`extra="forbid"`, `str_strip_whitespace=True`, `validate_default=True`).
- Scores keep their existing scales: `UnitScore` (`[0.0, 1.0]`) for claim and source scores, `CriticScore` (integer `1..10`) for the critique. Do not introduce a third scale, and do not express the critic score as a `UnitScore`.
- Every citation marker rendered into a report must resolve to a numbered line in the report's own Citations section. A URL that never reached `evaluated_sources` or a `Claim.source_urls` entry is never cited.
- Ruff `select = ["E", "F", "I"]`, line length 88. Imports must be isort-ordered.
- No test may make a real OpenAI, Tavily, LangSmith, ChromaDB, or HTTP network call. Every test constructs `Tracker(LangSmithRuntimeConfig(tracing_enabled=False, ...))` via the existing `tracker` fixture in `tests/test_agents/conftest.py`, and uses `tests/agent_fakes.py` / `tests/research_fakes.py` doubles. `write_document` writes go to pytest's `tmp_path`.
- `tests/test_imports.py::test_agent_submodule_public_names_all_reach_all` walks a hard-coded submodule list and asserts every public module-level name is in `deep_research.agents.__all__`. Every new module (`report`, `synthesizer`, `critic`) and every new public constant, function, and class in this plan must be added to both.

## Decisions And Assumptions

Recorded here because the spec does not settle them.

1. **The report skeleton is rendered locally, not written by the model.** The spec's acceptance criteria are "report includes required sections", "verified claims are cited", and "limitations are explicit when evidence is weak". None of those can be *guaranteed* by prompt text. `report.py` emits all seven `REPORT_SECTIONS` headings unconditionally, in order, from recorded evidence. The model supplies only the executive summary, the narrative section bodies, and free-text uncertainty notes. Task 1.
2. **All seven sections are emitted even when empty**, with an explicit placeholder body (`(no findings were reported)`, `No limitations were recorded for this pass.`). A reader — and the test suite — must never have to distinguish "empty" from "dropped".
3. **Citations are numbered locally by `build_citation_index`**: evaluated sources in order first, then any `Claim.source_urls` entry not already numbered. A model-supplied section URL that is not in that index is dropped and recorded as a rejection, the same discipline `fact_checker.build_claim_drafts` already applies to claim sources.
4. **The Synthesizer runs no ReAct loop.** Its two tool calls (`write_document`, `save_to_memory`) are unconditional consequences of having produced a report, not choices. Handing them to a model would make "the report was saved" non-deterministic. It still subclasses `BaseAgent`, implements all four hooks, and overrides `run` the way `SourceEvaluatorAgent` already does; the returned `ReActRun` is synthetic with zero iterations and zero tool calls.
5. **A failed `write_document` does not lose the report.** `state.report` is set from the composed Markdown regardless; the path is `None` and one recoverable `synthesizer_report_not_written` error is recorded. The report lives in state either way.
6. **"High-confidence final findings" saved to memory are `verified` claims with `confidence >= DEFAULT_MEMORY_CONFIDENCE` (0.7), capped at `DEFAULT_MAX_MEMORY_FINDINGS` (10).** The spec says "high-confidence final findings" without defining either bound; a verified verdict plus a confidence floor is the only definition this codebase can compute. Memory-write failures are recoverable and never block the report.
7. **The Critic *does* run a bounded ReAct loop.** The parent spec (2026-07-25-agentic-deep-research-design.md, "Critic") lists Web Search and Query Memory as its tools for spot-checking suspected gaps, so `allowed_tools = ("web_search", "query_memory")` — the same pair `PlannerAgent` declares. The loop is skipped entirely when there is no report to review.
8. **Routing is computed locally in `route_decision`, in a fixed precedence.** Iteration bound first (`iteration >= max_iterations` → stop, always), then missing report, then score, then gaps, then unsupported claims. The spec's "critic must not continue forever" is the one rule no model judgement may override, so it is checked before anything the model said.
9. **Every gap the model lists is treated as critical.** The spec's threshold is "score at least 7, no critical gaps, and no major unsupported claims", but `Critique` has one `gaps` field and no criticality flag, and widening a shared contract for one agent's bookkeeping is not this project's habit. `CRITIQUE_INSTRUCTION` therefore tells the model to list a gap *only* when it materially changes the answer. Documented, and pinned by a prompt test.
10. **A provider failure in the Critic ends the run** (`should_continue=False`, score `MIN_CRITIC_SCORE`). An outage is not evidence about the report, and a retry would almost certainly repeat it at cost. A *missing report*, by contrast, does buy another cycle — there is something concrete to fix — unless the iteration bound already forbids it.
11. **The Synthesizer skips the provider call entirely when there is no evidence at all** (no claims, no sources, no findings). Asking a model to write a report over nothing is the exact hallucination-pressure case the Researcher and Fact Checker already guard against. One recoverable `synthesizer_no_evidence` error is recorded and the skeleton report is still produced.
12. **`state.iteration` and `state.max_iterations` are read straight off `ResearchState`.** Nothing in this plan advances the iteration — that is `advance_research_iteration`'s job, called by the orchestrator in spec 11.

## Design Trade-Offs

- **`report.py` is a separate module from `synthesizer.py`.** Report rendering is pure, has no provider and no drafts, and is the thing most worth testing in isolation; the agent module is where I/O and error handling live. Splitting them keeps each file small enough to hold in context, matching the `sources.py` / `source_evaluator.py` split this codebase already made for the same reason.
- **New prompt text lives in `prompts.py`; message assembly stays in the agent module.** `prompts.py` is the project's pure-rendering boundary. `render_claim_digest` goes there because both agents' prompts use it; `report_messages` and `critique_messages` stay beside the agents that own them, mirroring `researcher.extraction_messages`, `source_evaluator.scoring_messages`, and `fact_checker.claim_verification_messages`.
- **`critic.py` imports `render_evidence` from `researcher.py`**, exactly as `fact_checker.py` already does. It is public, exported, and generic despite its home module; re-implementing it would be a DRY violation and relocating it would churn two passing test modules for no behavioural gain.
- **`SynthesisTask` and `CritiqueTask` extend `AgentTask`.** Carrying the claims, sources, and iteration bounds on the task is what lets `finalize(task, run)` know what it is finalizing without the agent holding mutable state across await points — the same reason `SubTopicTask`, `SourceEvaluationTask`, and `ClaimTask` exist.
- **`draft_report`, `write_report`, `save_findings`, and `review` return errors alongside their results**, and `finalize` is a thin adapter over them. The `finalize` hook signature has nowhere to return recoverable errors; `ResearcherAgent.extract_findings` already solved this the same way.
- **`CriticAgent`'s `ResultT` is `Critique` itself, not a wrapper model.** `EvaluatedSources` and `VerifiedClaims` exist only because their agents produce *lists*; a critique is a single record and `state.critique` is a single field, so a wrapper would be ceremony. `output_schema` returns `Critique` with a docstring saying it is never sent to the provider.

## File Structure

- Create `src/deep_research/agents/report.py` — `REPORT_SECTIONS`, `LIMITATION_REASONS`, `Citation`, `ReportSection`, citation numbering, section renderers, `assemble_report`. Pure, no I/O, no provider.
- Create `tests/test_agents/test_report.py`.
- Modify `src/deep_research/agents/prompts.py` — four new prompt constants and one new pure renderer.
- Modify `tests/test_agents/test_prompts.py`.
- Create `src/deep_research/agents/synthesizer.py` — draft models, limitation detection, filename slugging, section validation, memory selection, events, errors, `SynthesizerAgent`.
- Create `tests/test_agents/test_synthesizer.py`.
- Create `src/deep_research/agents/critic.py` — critique drafts, score clamping, routing maths, fallbacks, events, errors, `CriticAgent`.
- Create `tests/test_agents/test_critic.py`.
- Modify `tests/research_fakes.py` — `synthesizer_tools`, `critic_tools`.
- Create `tests/test_agents/test_synthesis_seam.py` — Fact Checker output feeds the Synthesizer feeds the Critic.
- Modify `src/deep_research/agents/__init__.py` — public exports for all three new modules.
- Modify `tests/test_imports.py` — extend the import list, the submodule list, and the identity test.
- Modify `README.md` — document both agents, their events, and the phase line.

---

### Task 1: The Report Skeleton — Citations, Sections, Limitations

**Files:**
- Create: `src/deep_research/agents/report.py`
- Create: `tests/test_agents/test_report.py`

**Interfaces:**
- Consumes: `normalize_source_url` from `deep_research.agents.sources`; `summarize_text` from `deep_research.agents.steps`; `Claim`, `ContractModel`, `ScoredSource` from `deep_research.utils.types`.
- Produces, all importable from `deep_research.agents.report`:
  - `REPORT_TITLE_PREFIX: str = "# Research report: "`
  - `REPORT_SECTIONS: tuple[str, ...]` — the seven H2 headings, in order
  - `LIMITATION_REASONS: dict[str, str]`
  - `Citation(ContractModel)` — `number: int`, `url: str`, `title: str`
  - `ReportSection(ContractModel)` — `title: str`, `body: str`, `source_urls: list[str]`
  - `build_citation_index(sources: Sequence[ScoredSource], claims: Sequence[Claim]) -> list[Citation]`
  - `citation_markers(urls: Sequence[str], index: Sequence[Citation]) -> str`
  - `render_citations(index: Sequence[Citation]) -> str`
  - `render_source_appendix(sources: Sequence[ScoredSource], index: Sequence[Citation]) -> str`
  - `render_findings(sections: Sequence[ReportSection], index: Sequence[Citation]) -> str`
  - `render_verified_claims(claims: Sequence[Claim], index: Sequence[Citation]) -> str`
  - `render_uncertain_claims(claims: Sequence[Claim], index: Sequence[Citation]) -> str`
  - `render_limitations(reasons: Sequence[str]) -> str`
  - `assemble_report(*, question, summary, sections, claims, sources, index, limitations, uncertainty_notes="") -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agents/test_report.py`:

```python
"""Tests for the pure Markdown report skeleton."""

from __future__ import annotations

import pytest

from deep_research.agents.report import (
    LIMITATION_REASONS,
    REPORT_SECTIONS,
    REPORT_TITLE_PREFIX,
    Citation,
    ReportSection,
    assemble_report,
    build_citation_index,
    citation_markers,
    render_citations,
    render_findings,
    render_limitations,
    render_source_appendix,
    render_uncertain_claims,
    render_verified_claims,
)
from deep_research.utils.types import Claim, ScoredSource


def _source(
    *,
    url: str = "https://example.org/a",
    title: str = "QEC 2025",
    overall: float = 0.76,
    low_confidence: bool = False,
    rationale: str = "Peer-reviewed and corroborated.",
) -> ScoredSource:
    return ScoredSource(
        url=url,
        title=title,
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        corroboration_score=0.5,
        overall_score=overall,
        rationale=rationale,
        low_confidence=low_confidence,
    )


def _claim(
    *,
    text: str = "Logical error rates fell below break-even in 2025.",
    urls: list[str] | None = None,
    verdict: str = "verified",
    confidence: float = 0.8,
    contradictions: list[str] | None = None,
) -> Claim:
    return Claim(
        text=text,
        source_urls=urls or ["https://example.org/a"],
        verdict=verdict,
        confidence=confidence,
        evidence=["An independent review states the same figure."],
        contradictions=contradictions or [],
    )


def test_citation_numbers_run_sources_first_then_claim_sources() -> None:
    index = build_citation_index(
        [_source(url="https://example.org/a"), _source(url="https://other.test/b")],
        [_claim(urls=["https://third.test/c", "https://example.org/a"])],
    )

    assert [(citation.number, citation.url) for citation in index] == [
        (1, "https://example.org/a"),
        (2, "https://other.test/b"),
        (3, "https://third.test/c"),
    ]


def test_citation_numbers_are_assigned_to_canonical_urls() -> None:
    index = build_citation_index(
        [_source(url="https://WWW.Example.ORG/a/")],
        [_claim(urls=["https://example.org/a"])],
    )

    assert len(index) == 1
    assert index[0].url == "https://example.org/a"


def test_markers_render_sorted_and_deduplicated() -> None:
    index = build_citation_index(
        [_source(url="https://example.org/a"), _source(url="https://other.test/b")],
        [],
    )

    assert (
        citation_markers(
            ["https://other.test/b", "https://example.org/a", "https://other.test/b"],
            index,
        )
        == "[1][2]"
    )


def test_an_uncited_url_is_never_marked() -> None:
    index = build_citation_index([_source()], [])

    assert citation_markers(["https://invented.test/x"], index) == ""


def test_citations_render_one_numbered_line_each() -> None:
    index = build_citation_index([_source(title="QEC 2025")], [])

    assert "1. QEC 2025 — https://example.org/a" in render_citations(index)
    assert render_citations([]) == "(no sources were cited)"


def test_the_appendix_marks_low_confidence_sources_and_escapes_pipes() -> None:
    index = build_citation_index(
        [_source(), _source(url="https://weak.test/b", title="A | B")], []
    )
    rendered = render_source_appendix(
        [
            _source(),
            _source(
                url="https://weak.test/b",
                title="A | B",
                overall=0.08,
                low_confidence=True,
                rationale="Anonymous blog.",
            ),
        ],
        index,
    )

    assert "| # | Source | Score | Confidence | Assessment |" in rendered
    assert "| 1 | QEC 2025 (https://example.org/a) | 0.76 | normal |" in rendered
    assert "| 2 | A \\| B (https://weak.test/b) | 0.08 | low |" in rendered
    assert render_source_appendix([], index) == "(no sources were evaluated)"


def test_findings_render_their_citation_line() -> None:
    index = build_citation_index([_source()], [])
    rendered = render_findings(
        [
            ReportSection(
                title="Error correction",
                body="Break-even was reached.",
                source_urls=["https://example.org/a"],
            ),
            ReportSection(title="Outlook", body="Scaling remains open."),
        ],
        index,
    )

    assert "### Error correction" in rendered
    assert "Sources: [1]" in rendered
    assert "Sources: none cited" in rendered
    assert render_findings([], index) == "(no findings were reported)"


def test_verified_claims_are_always_cited() -> None:
    index = build_citation_index([_source()], [])
    rendered = render_verified_claims([_claim(), _claim(verdict="unverified")], index)

    assert rendered.count("- ") == 1
    assert "[1] (confidence 0.80)" in rendered
    assert render_verified_claims([], index) == "(no claim reached a verified verdict)"


def test_unverified_claims_are_grouped_away_from_strong_findings() -> None:
    index = build_citation_index([_source()], [])
    rendered = render_uncertain_claims(
        [
            _claim(),
            _claim(text="Cost fell tenfold.", verdict="contradicted",
                   contradictions=["A vendor report disagrees."]),
            _claim(text="Adoption is broad.", verdict="unverified"),
            _claim(text="Latency improved.", verdict="insufficient_evidence"),
        ],
        index,
    )

    assert "### Contradicted by independent sources" in rendered
    assert "1 contradicting passage(s)" in rendered
    assert "### Not addressed by independent sources" in rendered
    assert "### Insufficient independent evidence" in rendered
    assert "Logical error rates" not in rendered
    assert render_uncertain_claims([], index) == (
        "(no unresolved claims were recorded)"
    )


def test_limitations_render_enumerated_reasons_only() -> None:
    rendered = render_limitations(["errors_recorded", "no_verified_claims"])

    assert rendered.count("- ") == 2
    assert LIMITATION_REASONS["errors_recorded"] in rendered
    assert render_limitations([]) == "No limitations were recorded for this pass."
    with pytest.raises(ValueError, match="limitation reason"):
        render_limitations(["because"])


def test_a_report_always_carries_every_required_section_in_order() -> None:
    index = build_citation_index([_source()], [_claim()])
    markdown = assemble_report(
        question="  How mature is quantum error correction?  ",
        summary="Break-even was reached in 2025.",
        sections=[
            ReportSection(
                title="Error correction",
                body="Break-even was reached.",
                source_urls=["https://example.org/a"],
            )
        ],
        claims=[_claim()],
        sources=[_source()],
        index=index,
        limitations=["errors_recorded"],
        uncertainty_notes="Vendor numbers remain unaudited.",
    )

    positions = [markdown.index(heading) for heading in REPORT_SECTIONS]
    assert positions == sorted(positions)
    assert markdown.startswith(
        f"{REPORT_TITLE_PREFIX}How mature is quantum error correction?"
    )
    assert "Vendor numbers remain unaudited." in markdown
    assert "1. QEC 2025 — https://example.org/a" in markdown
    assert markdown.endswith("\n")


def test_an_evidence_free_report_still_carries_every_section() -> None:
    markdown = assemble_report(
        question="What is known?",
        summary="   ",
        sections=[],
        claims=[],
        sources=[],
        index=[],
        limitations=[],
    )

    for heading in REPORT_SECTIONS:
        assert heading in markdown
    assert "(no executive summary was produced)" in markdown
    assert "No limitations were recorded for this pass." in markdown


def test_a_citation_object_rejects_a_zero_number() -> None:
    with pytest.raises(ValueError):
        Citation(number=0, url="https://example.org/a", title="A")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.agents.report'`

- [ ] **Step 3: Write minimal implementation**

Create `src/deep_research/agents/report.py`:

```python
"""Markdown report assembly — pure, offline rendering.

Nothing here performs I/O, reads a clock, or calls a provider, so a report
is a deterministic function of the evidence and prose handed to it. That is
what makes "the report always carries its required sections" and "every
verified claim is cited" testable without a provider, and true even when
the provider call failed.

Citation convention: one number per canonical source URL, assigned by
``build_citation_index`` — evaluated sources in order first, then claim
sources not already numbered. Every marker rendered into the report
resolves to a line in its Citations section; a URL with no number is never
marked.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from deep_research.agents.sources import normalize_source_url
from deep_research.agents.steps import summarize_text
from deep_research.utils.types import Claim, ContractModel, ScoredSource

REPORT_TITLE_PREFIX = "# Research report: "

# The H2 headings every report carries, in order. Emitted unconditionally,
# with an explicit placeholder body when empty: a reader must never have to
# tell "nothing to report" apart from "this section was dropped".
REPORT_SECTIONS = (
    "## Executive summary",
    "## Findings",
    "## Verified claims",
    "## Uncertainty and conflicting evidence",
    "## Limitations",
    "## Citations",
    "## Source appendix",
)

# Enumerated, project-generated limitation reasons. Never provider text:
# these strings reach the report body and ResearchEvent.metadata.
LIMITATION_REASONS = {
    "errors_recorded": (
        "Some steps of this research pass failed; sections of this report "
        "may be incomplete."
    ),
    "max_iterations_reached": (
        "The refinement budget was exhausted before the critic accepted "
        "the report."
    ),
    "no_sources_evaluated": (
        "No source behind these findings was scored, so source quality is "
        "unknown."
    ),
    "low_confidence_sources": (
        "Some sources behind these findings were flagged low confidence."
    ),
    "no_verified_claims": (
        "No claim was verified against a source independent of the one "
        "that made it."
    ),
    "contradicted_claims": (
        "At least one claim was contradicted by an independent source."
    ),
    "report_generation_failed": (
        "The model provider failed while this report was written; only the "
        "recorded evidence is included."
    ),
}

_APPENDIX_RATIONALE_CHARS = 200

# Verdict groups for the uncertainty section, in the order a reader should
# meet them: the evidence that argues against the report comes first.
_UNCERTAIN_VERDICTS = (
    ("contradicted", "Contradicted by independent sources"),
    ("unverified", "Not addressed by independent sources"),
    ("insufficient_evidence", "Insufficient independent evidence"),
)


class Citation(ContractModel):
    """One numbered source reference."""

    number: int = Field(ge=1)
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)


class ReportSection(ContractModel):
    """One validated narrative section of the report body.

    ``source_urls`` has already been checked against the citation index by
    the time a section reaches here; rendering never validates.
    """

    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    source_urls: list[str] = Field(default_factory=list)


def build_citation_index(
    sources: Sequence[ScoredSource],
    claims: Sequence[Claim],
) -> list[Citation]:
    """Number every canonical URL this report may cite, sources first.

    A source's own title is preferred; a URL that only ever appeared on a
    claim is titled with the URL itself, because nothing scored it.
    """
    titles: dict[str, str] = {}
    for source in sources:
        url = normalize_source_url(source.url)
        if url:
            titles.setdefault(url, source.title)
    for claim in claims:
        for raw in claim.source_urls:
            url = normalize_source_url(raw)
            if url:
                titles.setdefault(url, url)
    return [
        Citation(number=number, url=url, title=title)
        for number, (url, title) in enumerate(titles.items(), start=1)
    ]


def _lookup(index: Sequence[Citation]) -> dict[str, int]:
    return {citation.url: citation.number for citation in index}


def citation_markers(
    urls: Sequence[str],
    index: Sequence[Citation],
) -> str:
    """Render ``[1][3]`` for the URLs that carry a citation number."""
    numbers = _lookup(index)
    found = {
        numbers[normalize_source_url(url)]
        for url in urls
        if normalize_source_url(url) in numbers
    }
    return "".join(f"[{number}]" for number in sorted(found))


def render_citations(index: Sequence[Citation]) -> str:
    """Render the numbered citation list the markers point at."""
    lines = [
        f"{citation.number}. {citation.title} — {citation.url}"
        for citation in index
    ]
    return "\n".join(lines) or "(no sources were cited)"


def _cell(text: str) -> str:
    """Collapse a value onto one Markdown table cell.

    Pipes are escaped rather than dropped: a title containing ``|`` would
    otherwise silently split the row into extra columns.
    """
    return " ".join(text.split()).replace("|", "\\|")


def render_source_appendix(
    sources: Sequence[ScoredSource],
    index: Sequence[Citation],
) -> str:
    """Render one appendix row per scored source, weak ones visible."""
    if not sources:
        return "(no sources were evaluated)"
    numbers = _lookup(index)
    lines = [
        "| # | Source | Score | Confidence | Assessment |",
        "| --- | --- | --- | --- | --- |",
    ]
    for source in sources:
        number = numbers.get(normalize_source_url(source.url))
        marker = str(number) if number is not None else "-"
        confidence = "low" if source.low_confidence else "normal"
        rationale = _cell(
            summarize_text(source.rationale, limit=_APPENDIX_RATIONALE_CHARS)
        )
        lines.append(
            f"| {marker} | {_cell(source.title)} ({source.url}) "
            f"| {source.overall_score:.2f} | {confidence} | {rationale} |"
        )
    return "\n".join(lines)


def render_findings(
    sections: Sequence[ReportSection],
    index: Sequence[Citation],
) -> str:
    """Render the narrative sections, each closing with its citations."""
    blocks: list[str] = []
    for section in sections:
        cited = citation_markers(section.source_urls, index) or "none cited"
        blocks.append(
            f"### {section.title}\n\n{section.body}\n\nSources: {cited}"
        )
    return "\n\n".join(blocks) or "(no findings were reported)"


def render_verified_claims(
    claims: Sequence[Claim],
    index: Sequence[Citation],
) -> str:
    """Render only the claims that reached ``verified``, each cited."""
    lines: list[str] = []
    for claim in claims:
        if claim.verdict != "verified":
            continue
        markers = citation_markers(claim.source_urls, index)
        suffix = f" {markers}" if markers else ""
        lines.append(
            f"- {claim.text}{suffix} (confidence {claim.confidence:.2f})"
        )
    return "\n".join(lines) or "(no claim reached a verified verdict)"


def render_uncertain_claims(
    claims: Sequence[Claim],
    index: Sequence[Citation],
) -> str:
    """Render every claim that did not reach ``verified``, by verdict.

    Kept structurally apart from ``render_verified_claims`` so a weak claim
    can never be read as a strong finding — the spec's "unverified claims
    are separated from strong findings" requirement.
    """
    blocks: list[str] = []
    for verdict, heading in _UNCERTAIN_VERDICTS:
        lines: list[str] = []
        for claim in claims:
            if claim.verdict != verdict:
                continue
            markers = citation_markers(claim.source_urls, index)
            suffix = f" {markers}" if markers else ""
            note = (
                f" — {len(claim.contradictions)} contradicting passage(s)"
                if claim.contradictions
                else ""
            )
            lines.append(f"- {claim.text}{suffix}{note}")
        if lines:
            blocks.append(f"### {heading}\n\n" + "\n".join(lines))
    return "\n\n".join(blocks) or "(no unresolved claims were recorded)"


def render_limitations(reasons: Sequence[str]) -> str:
    """Render enumerated limitation reasons as one sentence each."""
    lines: list[str] = []
    for reason in reasons:
        explanation = LIMITATION_REASONS.get(reason)
        if explanation is None:
            raise ValueError(f"unknown limitation reason: {reason}")
        lines.append(f"- {explanation}")
    return "\n".join(lines) or "No limitations were recorded for this pass."


def assemble_report(
    *,
    question: str,
    summary: str,
    sections: Sequence[ReportSection],
    claims: Sequence[Claim],
    sources: Sequence[ScoredSource],
    index: Sequence[Citation],
    limitations: Sequence[str],
    uncertainty_notes: str = "",
) -> str:
    """Assemble the whole Markdown report from prose and recorded evidence.

    ``zip(..., strict=True)`` pins the body list to ``REPORT_SECTIONS``: a
    heading added without a body (or the reverse) fails here rather than
    silently shortening every future report.
    """
    uncertain = render_uncertain_claims(claims, index)
    notes = " ".join(uncertainty_notes.split())
    bodies = (
        summary.strip() or "(no executive summary was produced)",
        render_findings(sections, index),
        render_verified_claims(claims, index),
        f"{notes}\n\n{uncertain}" if notes else uncertain,
        render_limitations(limitations),
        render_citations(index),
        render_source_appendix(sources, index),
    )
    blocks = [f"{REPORT_TITLE_PREFIX}{' '.join(question.split())}"]
    for heading, body in zip(REPORT_SECTIONS, bodies, strict=True):
        blocks.append(f"{heading}\n\n{body}")
    return "\n\n".join(blocks) + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/test_report.py -v && ruff check src/deep_research/agents/report.py tests/test_agents/test_report.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/report.py tests/test_agents/test_report.py
git commit -m "feat: render the markdown report skeleton from recorded evidence"
```

---

### Task 2: Synthesizer And Critic Prompt Templates

**Files:**
- Modify: `src/deep_research/agents/prompts.py` (constants after `CLAIM_VERIFICATION_INSTRUCTION`, renderer after `render_source_quality`)
- Modify: `tests/test_agents/test_prompts.py` (append)

**Interfaces:**
- Consumes: `Claim` from `deep_research.utils.types`; `summarize_text` from `deep_research.agents.steps` (already imported).
- Produces, all importable from `deep_research.agents.prompts`:
  - `SYNTHESIZER_SYSTEM_PROMPT: str`
  - `REPORT_INSTRUCTION: str`
  - `CRITIC_SYSTEM_PROMPT: str`
  - `CRITIQUE_INSTRUCTION: str`
  - `render_claim_digest(claims: Sequence[Claim], *, limit: int = 240) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents/test_prompts.py`. Add the five new names to the existing `from deep_research.agents.prompts import (...)` block, and add `Claim` to the `deep_research.utils.types` import.

```python
def _digest_claim(
    *,
    text: str = "Logical error rates fell below break-even.",
    verdict: str = "verified",
    confidence: float = 0.8,
    urls: list[str] | None = None,
) -> Claim:
    return Claim(
        text=text,
        source_urls=urls or ["https://example.org/a"],
        verdict=verdict,
        confidence=confidence,
        evidence=[],
        contradictions=[],
    )


def test_claim_digest_shows_verdict_confidence_and_sources() -> None:
    rendered = render_claim_digest(
        [
            _digest_claim(),
            _digest_claim(
                text="Adoption is broad.",
                verdict="insufficient_evidence",
                confidence=0.0,
                urls=["https://other.test/b", "https://third.test/c"],
            ),
        ]
    )

    assert "1. [verified 0.80] Logical error rates fell below break-even." in rendered
    assert "(https://example.org/a)" in rendered
    assert "2. [insufficient_evidence 0.00] Adoption is broad." in rendered
    assert "(https://other.test/b, https://third.test/c)" in rendered


def test_claim_digest_clamps_long_claims_and_handles_an_empty_list() -> None:
    rendered = render_claim_digest([_digest_claim(text="x" * 500)], limit=50)

    assert "x" * 500 not in rendered
    assert "..." in rendered
    assert render_claim_digest([]) == "(no claims were checked)"


def test_the_synthesizer_prompt_forbids_inventing_evidence() -> None:
    assert "verified" in SYNTHESIZER_SYSTEM_PROMPT
    assert "invent" in SYNTHESIZER_SYSTEM_PROMPT
    # The skeleton is rendered locally; asking the model for it would let
    # the two disagree.
    assert "citation" in REPORT_INSTRUCTION
    assert "exactly" in REPORT_INSTRUCTION
    assert "executive summary" in REPORT_INSTRUCTION
    assert "uncertainty" in REPORT_INSTRUCTION


def test_the_critic_prompt_states_the_gap_and_score_contracts() -> None:
    assert "spot-check" in CRITIC_SYSTEM_PROMPT
    assert "1 to 10" in CRITIQUE_INSTRUCTION
    # Decision 9: every listed gap is treated as critical, so the prompt
    # must say only material gaps belong in the list.
    assert "materially" in CRITIQUE_INSTRUCTION
    assert "routing" not in CRITIQUE_INSTRUCTION
    assert "recommended" in CRITIQUE_INSTRUCTION
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_prompts.py -v`
Expected: FAIL with `ImportError: cannot import name 'SYNTHESIZER_SYSTEM_PROMPT' from 'deep_research.agents.prompts'`

- [ ] **Step 3: Write minimal implementation**

In `src/deep_research/agents/prompts.py`, add `Claim` to the `deep_research.utils.types` import block:

```python
from deep_research.utils.types import (
    Claim,
    ContractModel,
    Finding,
    MemorySnapshot,
    ScoredSource,
)
```

Append these constants after `CLAIM_VERIFICATION_INSTRUCTION`:

```python
SYNTHESIZER_SYSTEM_PROMPT = (
    "You are the synthesizer of a multi-agent research system. You write "
    "the prose of the final report from evidence this system already "
    "collected and checked.\n"
    "You are shown the research question, every claim with its verification "
    "verdict, the retrieved findings, the quality score of every source, "
    "and the limitations this pass already knows about.\n"
    "Report only what that evidence states. Never invent a source, a "
    "number, or a claim that is not in front of you, and never present a "
    "claim that was not verified as though it were settled.\n"
    "The report's headings, citation numbering, claim lists, limitations, "
    "and source appendix are assembled by this system. Write the prose; do "
    "not write the skeleton."
)

REPORT_INSTRUCTION = (
    "Return an executive summary, a list of narrative sections, and "
    "uncertainty notes.\n"
    "executive_summary: three to six sentences answering the research "
    "question directly, naming what is settled and what is not. Do not open "
    "with a heading.\n"
    "sections: one per theme worth its own heading, ordered as a reader "
    "should meet them. Each carries a short title, a body of plain "
    "paragraphs, and the source urls that body rests on, copied exactly "
    "from the evidence above. Do not write Markdown headings, citation "
    "markers, or a source list inside a body — the citation line is added "
    "for you from the urls you attach.\n"
    "uncertainty_notes: what a reader should distrust and why — thin "
    "sourcing, conflicting evidence, questions the research did not reach. "
    "Return an empty string when there is nothing to add.\n"
    "A url you attach that is not in the evidence above is dropped, and the "
    "section loses that citation. Copy urls exactly."
)

CRITIC_SYSTEM_PROMPT = (
    "You are the critic of a multi-agent research system. You judge one "
    "finished report and say what another research pass would have to fix.\n"
    "Use web_search to spot-check a suspected gap or a figure that looks "
    "wrong, and query_memory to compare this report against what previous "
    "sessions established. Finish without calling a tool when the report "
    "and the evidence summary are enough to judge.\n"
    "Judge completeness against the research question, accuracy against the "
    "claim verdicts, source diversity and strength against the source "
    "scores, and whether uncertainty is disclosed rather than hidden.\n"
    "Report what the evidence in front of you supports. Do not invent a gap "
    "to look thorough, and do not excuse a thin report to look agreeable."
)

CRITIQUE_INSTRUCTION = (
    "Return a score, the gaps, the unsupported claims, the queries a "
    "further pass should run, and a rationale.\n"
    "score: an integer from 1 to 10. 1 is unusable; 10 answers the question "
    "completely from strong, diverse, well-cited sources.\n"
    "gaps: list a gap only when closing it would materially change the "
    "answer to the research question. A missing nicety is not a gap. Return "
    "an empty list when the report is materially complete.\n"
    "unsupported_claims: statements the report makes that no cited source "
    "or verified claim backs. Quote or closely paraphrase each one.\n"
    "recommended_queries: concrete search queries that would close the gaps "
    "you listed, in the order they should be run.\n"
    "rationale: two to four sentences naming the concrete signals behind "
    "the score. Never restate the score alone.\n"
    "Do not decide whether research continues — this system computes that "
    "from your score, your gaps, and the remaining budget."
)
```

Append this renderer after `render_source_quality`:

```python
def render_claim_digest(
    claims: Sequence[Claim],
    *,
    limit: int = 240,
) -> str:
    """Render checked claims as one verdict-tagged, cited line each."""
    lines: list[str] = []
    for position, claim in enumerate(claims, start=1):
        text = summarize_text(claim.text, limit=limit)
        urls = ", ".join(claim.source_urls)
        lines.append(
            f"{position}. [{claim.verdict} {claim.confidence:.2f}] {text} "
            f"({urls})"
        )
    return "\n".join(lines) or "(no claims were checked)"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/test_prompts.py -v && ruff check src/deep_research/agents/prompts.py tests/test_agents/test_prompts.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/prompts.py tests/test_agents/test_prompts.py
git commit -m "feat: add synthesizer and critic prompt templates"
```

---

### Task 3: Synthesis Contracts, Limitation Detection, And Section Validation

**Files:**
- Create: `src/deep_research/agents/synthesizer.py` (pure module contents only — the agent class arrives in Task 4)
- Create: `tests/test_agents/test_synthesizer.py`

**Interfaces:**
- Consumes: `ReportSection`, `assemble_report`, `build_citation_index`, `render_limitations` from `deep_research.agents.report` (Task 1); `REPORT_INSTRUCTION`, `SYNTHESIZER_SYSTEM_PROMPT`, `AgentTask`, `render_claim_digest`, `render_finding_digest`, `render_source_quality` from `deep_research.agents.prompts` (Task 2); `normalize_source_url` from `deep_research.agents.sources`; `summarize_text` from `deep_research.agents.steps`; `ChatMessage` from `deep_research.providers`; `Claim`, `ContractModel`, `Finding`, `ResearchState`, `ScoredSource` from `deep_research.utils.types`.
- Produces, all importable from `deep_research.agents.synthesizer`:
  - `SYNTHESIZER_NAME: str = "synthesizer"`
  - `DEFAULT_MAX_SECTIONS: int = 8`, `SYNTHESIS_FINDING_DIGEST: int = 40`, `SYNTHESIS_CLAIM_DIGEST: int = 40`
  - `DEFAULT_MEMORY_CONFIDENCE: float = 0.7`, `DEFAULT_MAX_MEMORY_FINDINGS: int = 10`
  - `REPORT_SUMMARY_FALLBACK: str`
  - `ReportSectionDraft(ContractModel)` — `title: str`, `body: str`, `source_urls: list[str]`
  - `ReportDraft(ContractModel)` — `executive_summary: str`, `sections: list[ReportSectionDraft]`, `uncertainty_notes: str`
  - `SynthesisTask(AgentTask)` — `session_id`, `iteration`, `claims`, `sources`, `findings`, `limitations`
  - `SynthesizedReport(ContractModel)` — `markdown`, `path`, `section_count`, `citation_count`, `source_count`, `saved_findings`
  - `limitation_reasons(state: ResearchState) -> list[str]`
  - `report_filename(*, session_id: str, iteration: int) -> str`
  - `build_report_sections(draft, *, known_urls, max_sections) -> tuple[list[ReportSection], list[str]]`
  - `high_confidence_claims(claims, *, threshold) -> list[Claim]`
  - `memory_payload(claim, *, session_id) -> tuple[str, dict[str, JsonValue]]`
  - `render_revision_guidance(state: ResearchState) -> str`
  - `compose_report(task, *, summary, sections, uncertainty_notes, limitations) -> SynthesizedReport`
  - `report_messages(task, *, finding_digest, claim_digest) -> list[ChatMessage]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agents/test_synthesizer.py`:

```python
"""Tests for the Synthesizer's contracts, limitations, and composition."""

from __future__ import annotations

import pytest

from deep_research.agents.report import REPORT_SECTIONS, ReportSection
from deep_research.agents.synthesizer import (
    DEFAULT_MEMORY_CONFIDENCE,
    REPORT_SUMMARY_FALLBACK,
    ReportDraft,
    ReportSectionDraft,
    SynthesisTask,
    build_report_sections,
    compose_report,
    high_confidence_claims,
    limitation_reasons,
    memory_payload,
    render_revision_guidance,
    report_filename,
    report_messages,
)
from deep_research.utils.types import (
    Claim,
    Critique,
    Finding,
    ResearchError,
    ResearchState,
    ScoredSource,
)

SYNTH_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"
SOURCE_URL = "https://example.org/a"


def _finding(url: str = SOURCE_URL, sub_topic: str = "Alpha") -> Finding:
    return Finding(
        content="Logical error rates fell below break-even.",
        source_url=url,
        source_title="QEC 2025",
        extracted_at=SYNTH_EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
    )


def _source(
    *,
    url: str = SOURCE_URL,
    overall: float = 0.76,
    low_confidence: bool = False,
) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="QEC 2025",
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        corroboration_score=0.5,
        overall_score=overall,
        rationale="Peer-reviewed and corroborated.",
        low_confidence=low_confidence,
    )


def _claim(
    *,
    text: str = "Logical error rates fell below break-even in 2025.",
    verdict: str = "verified",
    confidence: float = 0.8,
    urls: list[str] | None = None,
) -> Claim:
    return Claim(
        text=text,
        source_urls=urls or [SOURCE_URL],
        verdict=verdict,
        confidence=confidence,
        evidence=[],
        contradictions=[],
    )


def _state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "How mature is quantum error correction?",
        "raw_findings": [_finding()],
        "evaluated_sources": [_source()],
        "verified_claims": [_claim()],
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def _task(**overrides: object) -> SynthesisTask:
    payload: dict[str, object] = {
        "instruction": "How mature is quantum error correction?",
        "session_id": "session-1",
        "iteration": 0,
        "claims": [_claim()],
        "sources": [_source()],
        "findings": [_finding()],
        "limitations": [],
    }
    payload.update(overrides)
    return SynthesisTask.model_validate(payload)


def test_a_clean_pass_records_no_limitations() -> None:
    assert limitation_reasons(_state()) == []


def test_every_weak_signal_becomes_an_enumerated_limitation() -> None:
    state = _state(
        errors=[
            ResearchError(
                error_type="researcher_sub_topic_without_findings",
                source="agent.researcher",
                message="A high-priority sub-topic produced no findings.",
            )
        ],
        iteration=3,
        max_iterations=3,
        evaluated_sources=[_source(overall=0.1, low_confidence=True)],
        verified_claims=[_claim(verdict="contradicted", confidence=0.4)],
    )

    assert limitation_reasons(state) == [
        "errors_recorded",
        "max_iterations_reached",
        "low_confidence_sources",
        "no_verified_claims",
        "contradicted_claims",
    ]


def test_an_unscored_pass_reports_that_source_quality_is_unknown() -> None:
    reasons = limitation_reasons(_state(evaluated_sources=[]))

    assert "no_sources_evaluated" in reasons
    assert "low_confidence_sources" not in reasons


@pytest.mark.parametrize(
    ("session_id", "iteration", "expected"),
    [
        ("session-1", 0, "report-session-1-0.md"),
        ("Session_42", 2, "report-session-42-2.md"),
        ("../../etc/passwd", 1, "report-etc-passwd-1.md"),
        ("   ", 0, "report-session-0.md"),
    ],
)
def test_report_filenames_are_slugged_and_traversal_free(
    session_id: str, iteration: int, expected: str
) -> None:
    assert report_filename(session_id=session_id, iteration=iteration) == expected


def test_report_filename_rejects_a_negative_iteration() -> None:
    with pytest.raises(ValueError, match="iteration"):
        report_filename(session_id="session-1", iteration=-1)


def test_sections_keep_only_source_urls_that_reached_the_evidence() -> None:
    sections, rejected = build_report_sections(
        ReportDraft(
            executive_summary="Break-even was reached.",
            sections=[
                ReportSectionDraft(
                    title="  Error correction  ",
                    body="Break-even was reached.\n\nScaling is open.",
                    source_urls=[
                        "https://WWW.example.org/a/",
                        "https://invented.test/x",
                        SOURCE_URL,
                    ],
                )
            ],
            uncertainty_notes="",
        ),
        known_urls=[SOURCE_URL],
        max_sections=4,
    )

    assert [section.title for section in sections] == ["Error correction"]
    assert sections[0].source_urls == [SOURCE_URL]
    assert "\n\n" in sections[0].body
    assert rejected == ["section 1: 1 source url(s) not in evidence"]


def test_a_blank_section_is_dropped_and_named() -> None:
    sections, rejected = build_report_sections(
        ReportDraft(
            executive_summary="",
            sections=[ReportSectionDraft(title="  ", body="Text.", source_urls=[])],
            uncertainty_notes="",
        ),
        known_urls=[SOURCE_URL],
        max_sections=4,
    )

    assert sections == []
    assert rejected == ["section 1: blank title or body"]


def test_sections_past_the_cap_are_dropped_and_named() -> None:
    draft = ReportDraft(
        executive_summary="",
        sections=[
            ReportSectionDraft(title=f"S{index}", body="Text.", source_urls=[])
            for index in range(3)
        ],
        uncertainty_notes="",
    )

    sections, rejected = build_report_sections(
        draft, known_urls=[SOURCE_URL], max_sections=2
    )

    assert [section.title for section in sections] == ["S0", "S1"]
    assert rejected == ["section 3: past the section cap"]


def test_build_report_sections_rejects_a_zero_cap() -> None:
    with pytest.raises(ValueError, match="max_sections"):
        build_report_sections(
            ReportDraft(executive_summary="", sections=[], uncertainty_notes=""),
            known_urls=[],
            max_sections=0,
        )


def test_only_confident_verified_claims_are_kept_for_memory() -> None:
    claims = [
        _claim(confidence=0.9),
        _claim(text="Weakly verified.", confidence=0.5),
        _claim(text="Unverified.", verdict="unverified", confidence=0.9),
    ]

    kept = high_confidence_claims(claims, threshold=DEFAULT_MEMORY_CONFIDENCE)

    assert [claim.confidence for claim in kept] == [0.9]
    assert kept[0].verdict == "verified"


def test_a_memory_payload_carries_the_claim_and_its_attribution() -> None:
    content, metadata = memory_payload(_claim(), session_id="session-1")

    assert content == "Logical error rates fell below break-even in 2025."
    assert metadata["entry_type"] == "finding"
    assert metadata["session_id"] == "session-1"
    assert metadata["agent_id"] == "synthesizer"
    assert metadata["source_url"] == SOURCE_URL
    assert metadata["confidence"] == pytest.approx(0.8)


def test_revision_guidance_repeats_the_critic_feedback() -> None:
    state = _state(
        critique=Critique(
            score=4,
            gaps=["No cost data."],
            unsupported_claims=["Costs fell tenfold."],
            recommended_queries=["qec cost 2025"],
            should_continue=True,
            rationale="Thin sourcing.",
        )
    )

    guidance = render_revision_guidance(state)

    assert "No cost data." in guidance
    assert "Costs fell tenfold." in guidance
    # Recommended queries are the Researcher's business, not the writer's.
    assert "qec cost 2025" not in guidance
    assert render_revision_guidance(_state()) == ""


def test_a_composed_report_carries_its_counts_and_every_section() -> None:
    report = compose_report(
        _task(),
        summary="Break-even was reached.",
        sections=[
            ReportSection(
                title="Error correction",
                body="Break-even was reached.",
                source_urls=[SOURCE_URL],
            )
        ],
        uncertainty_notes="Vendor numbers remain unaudited.",
        limitations=["errors_recorded"],
    )

    for heading in REPORT_SECTIONS:
        assert heading in report.markdown
    assert report.section_count == 1
    assert report.citation_count == 1
    assert report.source_count == 1
    assert report.path is None
    assert report.saved_findings == 0


def test_a_report_composed_without_a_model_still_cites_its_claims() -> None:
    report = compose_report(
        _task(),
        summary=REPORT_SUMMARY_FALLBACK,
        sections=[],
        uncertainty_notes="",
        limitations=["report_generation_failed"],
    )

    assert REPORT_SUMMARY_FALLBACK in report.markdown
    assert "(no findings were reported)" in report.markdown
    assert "[1] (confidence 0.80)" in report.markdown
    assert "The model provider failed while this report was written" in (
        report.markdown
    )


def test_report_messages_carry_every_input_the_writer_needs() -> None:
    messages = report_messages(
        _task(guidance="Close the cost gap.", limitations=["errors_recorded"]),
        finding_digest=10,
        claim_digest=10,
    )

    assert [message.role for message in messages] == ["developer", "user"]
    body = messages[1].content
    assert "## Research question" in body
    assert "## Context" in body
    assert "Close the cost gap." in body
    assert "## Verified and checked claims" in body
    assert "[verified 0.80]" in body
    assert "## Retrieved findings" in body
    assert "## Source quality" in body
    assert "## Known limitations" in body
    assert "## Response contract" in body


def test_report_messages_drop_the_context_section_without_guidance() -> None:
    body = report_messages(_task(), finding_digest=10, claim_digest=10)[1].content

    assert "## Context" not in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_synthesizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.agents.synthesizer'`

- [ ] **Step 3: Write minimal implementation**

Create `src/deep_research/agents/synthesizer.py`:

```python
"""The Synthesizer: turn checked evidence into the final Markdown report.

Like every other agent here, the provider is asked for a constraint-free
draft (``ReportDraft``) and never for a domain type. The report's skeleton —
citations, the verified-claim list, the uncertainty grouping, the
limitations block, and the source appendix — is rendered locally by
``agents.report`` from recorded evidence, so the required sections exist and
every verified claim is cited even when the model call fails.

This module runs no ReAct loop. Report generation is one structured call,
and ``write_document`` / ``save_to_memory`` are deterministic writes made
afterwards, not decisions handed to a model.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field, JsonValue

from deep_research.agents.prompts import (
    REPORT_INSTRUCTION,
    SYNTHESIZER_SYSTEM_PROMPT,
    AgentTask,
    render_claim_digest,
    render_finding_digest,
    render_source_quality,
)
from deep_research.agents.report import (
    ReportSection,
    assemble_report,
    build_citation_index,
    render_limitations,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.agents.steps import summarize_text
from deep_research.providers import ChatMessage
from deep_research.utils.types import (
    Claim,
    ContractModel,
    Finding,
    ResearchState,
    ScoredSource,
)

SYNTHESIZER_NAME = "synthesizer"

DEFAULT_MAX_SECTIONS = 8
SYNTHESIS_FINDING_DIGEST = 40
SYNTHESIS_CLAIM_DIGEST = 40

# A claim must be verified *and* at least this confident before it is kept
# for future sessions. The spec says "high-confidence final findings"
# without defining either bound; this is the only definition this codebase
# can compute.
DEFAULT_MEMORY_CONFIDENCE = 0.7
DEFAULT_MAX_MEMORY_FINDINGS = 10

REPORT_SUMMARY_FALLBACK = (
    "No executive summary was written for this pass. The claims, sources, "
    "and limitations recorded below are the whole of what this research "
    "established."
)

_SECTION_BODY_CHARS = 4000
_GUIDANCE_CHARS = 200
# Characters kept verbatim in a report filename. Narrow on purpose:
# WriteDocumentTool rejects absolute paths and traversal segments, and a
# rejected write would lose the report.
_FILENAME_SAFE = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")


class ReportSectionDraft(ContractModel):
    """One model-written narrative section, before validation.

    Declares no ``Field`` constraints for the same reason as
    ``planner.SubTopicDraft``: it is converted to a strict OpenAI JSON
    schema.
    """

    title: str
    body: str
    source_urls: list[str]


class ReportDraft(ContractModel):
    """The provider-facing report schema for one synthesis pass."""

    executive_summary: str
    sections: list[ReportSectionDraft]
    uncertainty_notes: str


class SynthesisTask(AgentTask):
    """An ``AgentTask`` bound to the evidence its report is written from.

    Carrying the evidence on the task is what lets ``finalize(task, run)``
    compose a report without the agent holding mutable state across await
    points — the same reason ``SourceEvaluationTask`` exists.
    """

    session_id: str = Field(min_length=1)
    iteration: int = Field(default=0, ge=0)
    claims: list[Claim] = []
    sources: list[ScoredSource] = []
    findings: list[Finding] = []
    limitations: list[str] = []


class SynthesizedReport(ContractModel):
    """The report ``SynthesizerAgent`` produces, with its own counts.

    Never sent to the provider — ``ReportDraft`` is. Do not route this agent
    through ``complete_output``. ``path`` is ``None`` when the report was
    composed but could not be written to disk; ``markdown`` is authoritative
    either way.
    """

    markdown: str = ""
    path: str | None = None
    section_count: int = Field(default=0, ge=0)
    citation_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    saved_findings: int = Field(default=0, ge=0)


def limitation_reasons(state: ResearchState) -> list[str]:
    """Enumerate every limitation this pass must disclose, in report order.

    Purely a function of recorded state, so "the report is honest about weak
    evidence" is testable without a provider. Keys are
    ``report.LIMITATION_REASONS`` keys; ``render_limitations`` raises on
    anything else.
    """
    reasons: list[str] = []
    if state.errors:
        reasons.append("errors_recorded")
    if state.iteration >= state.max_iterations:
        reasons.append("max_iterations_reached")
    if not state.evaluated_sources:
        reasons.append("no_sources_evaluated")
    elif any(source.low_confidence for source in state.evaluated_sources):
        reasons.append("low_confidence_sources")
    if not any(claim.verdict == "verified" for claim in state.verified_claims):
        reasons.append("no_verified_claims")
    if any(claim.verdict == "contradicted" for claim in state.verified_claims):
        reasons.append("contradicted_claims")
    return reasons


def report_filename(*, session_id: str, iteration: int) -> str:
    """Return a traversal-free ``.md`` filename for one report.

    ``session_id`` reaches this from state and may hold anything, so it is
    slugged rather than trusted.
    """
    if iteration < 0:
        raise ValueError("iteration must not be negative")
    slug = "".join(
        character if character in _FILENAME_SAFE else "-"
        for character in session_id.strip().casefold()
    ).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"report-{slug or 'session'}-{iteration}.md"


def _clamp_body(text: str, *, limit: int) -> str:
    """Clamp a section body without collapsing its paragraph breaks.

    ``summarize_text`` is deliberately not used here: it joins on
    whitespace, which would turn a multi-paragraph section into one line.
    """
    body = text.strip()
    if len(body) <= limit:
        return body
    return body[: limit - 3].rstrip() + "..."


def build_report_sections(
    draft: ReportDraft,
    *,
    known_urls: Sequence[str],
    max_sections: int,
) -> tuple[list[ReportSection], list[str]]:
    """Keep the sections that carry prose, naming everything dropped.

    A URL the model attached that nobody retrieved is dropped rather than
    cited: an invented citation is the one failure this project refuses to
    put in a report. Rejection reasons are generated here and never copied
    from provider output, so they are safe for ``ResearchError.details``.
    """
    if max_sections < 1:
        raise ValueError("max_sections must be at least 1")
    allowed = {normalize_source_url(url) for url in known_urls}
    sections: list[ReportSection] = []
    rejected: list[str] = []
    for position, item in enumerate(draft.sections, start=1):
        title = " ".join(item.title.split())
        body = item.body.strip()
        if not title or not body:
            rejected.append(f"section {position}: blank title or body")
            continue
        urls: list[str] = []
        dropped = 0
        for raw in item.source_urls:
            url = normalize_source_url(raw)
            if url in allowed:
                if url not in urls:
                    urls.append(url)
            else:
                dropped += 1
        if dropped:
            rejected.append(
                f"section {position}: {dropped} source url(s) not in evidence"
            )
        if len(sections) >= max_sections:
            rejected.append(f"section {position}: past the section cap")
            continue
        sections.append(
            ReportSection(
                title=title,
                body=_clamp_body(body, limit=_SECTION_BODY_CHARS),
                source_urls=urls,
            )
        )
    return sections, rejected


def high_confidence_claims(
    claims: Sequence[Claim],
    *,
    threshold: float,
) -> list[Claim]:
    """Verified claims confident enough to keep for future sessions."""
    return [
        claim
        for claim in claims
        if claim.verdict == "verified" and claim.confidence >= threshold
    ]


def memory_payload(
    claim: Claim,
    *,
    session_id: str,
) -> tuple[str, dict[str, JsonValue]]:
    """Render one verified claim as a ``save_to_memory`` call's arguments.

    Metadata keys mirror ``memory.entries.MemoryEntry`` so a stored claim
    reads back the same way a stored finding does.
    """
    metadata: dict[str, JsonValue] = {
        "entry_type": "finding",
        "session_id": session_id,
        "agent_id": SYNTHESIZER_NAME,
        "confidence": round(claim.confidence, 4),
        "source_url": claim.source_urls[0],
        "verdict": claim.verdict,
    }
    return claim.text, metadata


def render_revision_guidance(state: ResearchState) -> str:
    """Render the critic's last feedback for a rewrite, or an empty string.

    Recommended queries are deliberately absent: they tell the *Researcher*
    what to retrieve next and would only invite this agent to write about
    evidence it does not have.
    """
    critique = state.critique
    if critique is None:
        return ""
    lines = ["A previous pass of this report was reviewed and sent back."]
    if critique.gaps:
        lines.append("Gaps the reviewer named:")
        lines.extend(
            f"- {summarize_text(gap, limit=_GUIDANCE_CHARS)}"
            for gap in critique.gaps
        )
    if critique.unsupported_claims:
        lines.append("Statements the reviewer found unsupported:")
        lines.extend(
            f"- {summarize_text(claim, limit=_GUIDANCE_CHARS)}"
            for claim in critique.unsupported_claims
        )
    return "\n".join(lines)


def compose_report(
    task: SynthesisTask,
    *,
    summary: str,
    sections: Sequence[ReportSection],
    uncertainty_notes: str,
    limitations: Sequence[str],
) -> SynthesizedReport:
    """Assemble the report and record the counts observability needs."""
    index = build_citation_index(task.sources, task.claims)
    markdown = assemble_report(
        question=task.instruction,
        summary=summary,
        sections=sections,
        claims=task.claims,
        sources=task.sources,
        index=index,
        limitations=limitations,
        uncertainty_notes=uncertainty_notes,
    )
    return SynthesizedReport(
        markdown=markdown,
        section_count=len(sections),
        citation_count=len(index),
        source_count=len(task.sources),
    )


def report_messages(
    task: SynthesisTask,
    *,
    finding_digest: int,
    claim_digest: int,
) -> list[ChatMessage]:
    """Build the messages that request one structured report draft."""
    sections = [f"## Research question\n{task.instruction}"]
    if task.guidance.strip():
        sections.append(f"## Context\n{task.guidance}")
    sections.extend(
        [
            (
                "## Verified and checked claims\n"
                f"{render_claim_digest(list(task.claims)[:claim_digest])}"
            ),
            (
                "## Retrieved findings\n"
                f"{render_finding_digest(list(task.findings)[:finding_digest])}"
            ),
            f"## Source quality\n{render_source_quality(task.sources)}",
            f"## Known limitations\n{render_limitations(task.limitations)}",
            f"## Response contract\n{REPORT_INSTRUCTION}",
        ]
    )
    return [
        ChatMessage(role="developer", content=SYNTHESIZER_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/test_synthesizer.py -v && ruff check src/deep_research/agents/synthesizer.py tests/test_agents/test_synthesizer.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/synthesizer.py \
  tests/test_agents/test_synthesizer.py
git commit -m "feat: add synthesis contracts, limitations, and section validation"
```

---

### Task 4: `SynthesizerAgent` — Drafting, Writing, Memory, And `run`

**Files:**
- Modify: `src/deep_research/agents/synthesizer.py` (append)
- Modify: `tests/research_fakes.py` (append `synthesizer_tools`)
- Modify: `tests/test_agents/test_synthesizer.py` (append)

**Interfaces:**
- Consumes: everything Task 3 produced; `AgentRun`, `BaseAgent`, `StructuredCompleter` from `deep_research.agents.base`; `AgentConfigurationError`, `agent_error` from `deep_research.agents.errors`; `agent_event` from `deep_research.agents.events`; `ReActRun` from `deep_research.agents.steps`; `OpenAIProviderError` from `deep_research.providers`; `BaseTool` from `deep_research.tools.base`; `AgentRuntimeConfig` from `deep_research.utils.config`.
- Produces:
  - `WRITE_FAILURE_REASONS: dict[str, str]`
  - `report_provider_error(error: Exception) -> ResearchError`
  - `invalid_section_error(rejected: Sequence[str]) -> ResearchError`
  - `report_not_written_error(*, reason: str) -> ResearchError`
  - `memory_save_error(*, failures: int, attempted: int) -> ResearchError`
  - `no_evidence_error() -> ResearchError`
  - `synthesis_started_event(*, claim_count, source_count, finding_count, limitation_count) -> ResearchEvent`
  - `synthesis_completed_event(report: SynthesizedReport, *, limitations, claim_count) -> ResearchEvent`
  - `SynthesizerAgent(BaseAgent[SynthesizedReport])` with `name = "synthesizer"`, `allowed_tools = ("write_document", "save_to_memory")`, constructor `(*, provider, tracker, scratchpad, tools=(), config=None, max_sections=DEFAULT_MAX_SECTIONS, finding_digest=SYNTHESIS_FINDING_DIGEST, claim_digest=SYNTHESIS_CLAIM_DIGEST, memory_confidence=DEFAULT_MEMORY_CONFIDENCE, max_memory_findings=DEFAULT_MAX_MEMORY_FINDINGS)`, and methods `build_task(state) -> SynthesisTask`, `async draft_report(task)`, `async write_report(task, markdown)`, `async save_findings(task)`, `async finalize(task, run)`, `state_update(result, run)`, `async run(state)`

- [ ] **Step 1: Write the failing tests**

Append `synthesizer_tools` to `tests/research_fakes.py` (and add `WriteDocumentTool` to its `deep_research.tools` imports plus `Path` — already imported):

```python
def synthesizer_tools(
    tracker: Tracker,
    *,
    output_root: Path,
    memory: FakeMemory | None = None,
) -> list[BaseTool]:
    """Build the two tools ``SynthesizerAgent`` declares, all offline.

    ``WriteDocumentTool`` is the real class writing under a pytest
    ``tmp_path``, so the agent is exercised against the same path
    validation production uses.
    """
    return [
        WriteDocumentTool(tracker, output_root),
        SaveToMemoryTool(tracker, memory or FakeMemory()),
    ]
```

Add the import at the top of `tests/research_fakes.py`:

```python
from deep_research.tools.write_document import WriteDocumentTool
```

Append to `tests/test_agents/test_synthesizer.py` (extend the existing import block with the new names, and add the imports the tests below need):

```python
def _synthesizer(
    tracker: Tracker,
    completer: ScriptedCompleter,
    tools: list[BaseTool],
    **overrides: object,
) -> SynthesizerAgent:
    return SynthesizerAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1",
            agent_name="synthesizer",
            max_entries=20,
        ),
        tools=tools,
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
        **overrides,
    )


def _draft(
    *,
    summary: str = "Break-even was reached in 2025.",
    urls: list[str] | None = None,
    notes: str = "Vendor numbers remain unaudited.",
) -> ReportDraft:
    return ReportDraft(
        executive_summary=summary,
        sections=[
            ReportSectionDraft(
                title="Error correction",
                body="Break-even was reached.",
                source_urls=urls if urls is not None else [SOURCE_URL],
            )
        ],
        uncertainty_notes=notes,
    )


def test_build_task_carries_the_evidence_limitations_and_revision_notes(
    tracker: Tracker, tmp_path: Path
) -> None:
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(),
        synthesizer_tools(tracker, output_root=tmp_path),
    )
    state = _state(
        evaluated_sources=[_source(overall=0.1, low_confidence=True)],
        critique=Critique(
            score=4,
            gaps=["No cost data."],
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=True,
            rationale="Thin sourcing.",
        ),
    )

    task = agent.build_task(state)

    assert task.instruction == state.original_question
    assert task.session_id == "session-1"
    assert task.iteration == 0
    assert [claim.text for claim in task.claims] == [
        "Logical error rates fell below break-even in 2025."
    ]
    assert task.limitations == ["low_confidence_sources"]
    assert "No cost data." in task.guidance


@pytest.mark.asyncio
async def test_a_run_writes_the_report_and_records_its_counts(
    tracker: Tracker, tmp_path: Path
) -> None:
    memory = FakeMemory()
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path, memory=memory),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert outcome.result.path == "report-session-1-0.md"
    assert (tmp_path / "report-session-1-0.md").read_text(encoding="utf-8") == (
        outcome.result.markdown
    )
    assert outcome.state_update["report"] == outcome.result.markdown
    assert "## Executive summary" in outcome.result.markdown
    assert "Break-even was reached in 2025." in outcome.result.markdown
    assert "Vendor numbers remain unaudited." in outcome.result.markdown
    assert outcome.react.stop_reason == "finished"
    assert outcome.errors == []
    # One high-confidence verified claim was kept for future sessions.
    assert [content for content, _ in memory.saved] == [
        "Logical error rates fell below break-even in 2025."
    ]
    assert outcome.result.saved_findings == 1


@pytest.mark.asyncio
async def test_a_run_emits_the_counts_the_spec_requires(
    tracker: Tracker, tmp_path: Path
) -> None:
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    events = outcome.state_update["events"]
    assert [event.event_type for event in events] == [
        "synthesizer.synthesis.started",
        "synthesizer.synthesis.completed",
    ]
    completed = events[-1].metadata
    assert completed["section_count"] == 1
    assert completed["citation_count"] == 1
    assert completed["source_appendix_count"] == 1
    assert completed["output_path"] == "report-session-1-0.md"
    assert completed["saved_findings"] == 1
    assert completed["limitations"] == []


@pytest.mark.asyncio
async def test_an_invented_section_url_is_dropped_and_recorded(
    tracker: Tracker, tmp_path: Path
) -> None:
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft(urls=["https://invented.test/x"])]),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert "https://invented.test/x" not in outcome.result.markdown
    assert "Sources: none cited" in outcome.result.markdown
    assert [error.error_type for error in outcome.errors] == [
        "synthesizer_invalid_section"
    ]
    assert outcome.errors[0].recoverable is True


@pytest.mark.asyncio
async def test_a_provider_failure_still_produces_a_cited_report(
    tracker: Tracker, tmp_path: Path
) -> None:
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[OpenAIProviderError("down")]),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert REPORT_SUMMARY_FALLBACK in outcome.result.markdown
    assert "[1] (confidence 0.80)" in outcome.result.markdown
    assert "The model provider failed while this report was written" in (
        outcome.result.markdown
    )
    assert outcome.react.stop_reason == "provider_error"
    errors = {error.error_type: error for error in outcome.errors}
    assert errors["synthesizer_report_provider_error"].recoverable is False
    assert errors["synthesizer_report_provider_error"].details == {
        "exception_type": "OpenAIProviderError"
    }
    assert (tmp_path / "report-session-1-0.md").is_file()


@pytest.mark.asyncio
async def test_no_evidence_skips_the_provider_and_says_so(
    tracker: Tracker, tmp_path: Path
) -> None:
    completer = ScriptedCompleter()
    agent = _synthesizer(
        tracker, completer, synthesizer_tools(tracker, output_root=tmp_path)
    )
    state = _state(raw_findings=[], evaluated_sources=[], verified_claims=[])

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert completer.calls == []
    assert outcome.result is not None
    assert "(no claim reached a verified verdict)" in outcome.result.markdown
    assert "no_evidence" in {
        error.error_type.removeprefix("synthesizer_") for error in outcome.errors
    }
    assert "No source behind these findings was scored" in outcome.result.markdown


@pytest.mark.asyncio
async def test_a_failed_write_keeps_the_report_in_state(
    tracker: Tracker, tmp_path: Path
) -> None:
    # A directory where the report file must go makes the real tool fail.
    (tmp_path / "report-session-1-0.md").mkdir()
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert outcome.result.path is None
    assert outcome.state_update["report"] == outcome.result.markdown
    assert [error.error_type for error in outcome.errors] == [
        "synthesizer_report_not_written"
    ]
    assert outcome.errors[0].details["reason"] == "tool_failed"


@pytest.mark.asyncio
async def test_a_failed_memory_write_never_blocks_the_report(
    tracker: Tracker, tmp_path: Path
) -> None:
    memory = FakeMemory(error=RuntimeError("memory down"))
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path, memory=memory),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert outcome.result.path == "report-session-1-0.md"
    assert outcome.result.saved_findings == 0
    error = next(
        error
        for error in outcome.errors
        if error.error_type == "synthesizer_memory_save_failed"
    )
    assert error.recoverable is True
    assert error.details == {"failures": 1, "attempted": 1}


@pytest.mark.asyncio
async def test_only_capped_high_confidence_claims_reach_memory(
    tracker: Tracker, tmp_path: Path
) -> None:
    memory = FakeMemory()
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path, memory=memory),
        max_memory_findings=1,
    )
    state = _state(
        verified_claims=[
            _claim(text="First.", confidence=0.9),
            _claim(text="Second.", confidence=0.9),
            _claim(text="Weak.", confidence=0.2),
        ]
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert [content for content, _ in memory.saved] == ["First."]
    assert outcome.result is not None
    assert outcome.result.saved_findings == 1


@pytest.mark.asyncio
async def test_finalize_requires_a_synthesis_task(
    tracker: Tracker, tmp_path: Path
) -> None:
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    with pytest.raises(AgentConfigurationError, match="SynthesisTask"):
        await agent.finalize(
            AgentTask(instruction="anything"),
            ReActRun(agent_name="synthesizer", stop_reason="finished"),
        )


def test_the_synthesizer_declares_its_two_writes(
    tracker: Tracker, tmp_path: Path
) -> None:
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    assert SynthesizerAgent.name == "synthesizer"
    assert SynthesizerAgent.allowed_tools == ("write_document", "save_to_memory")
    assert agent.output_schema is SynthesizedReport
```

The appended tests need these imports added to the module's import block:

```python
from pathlib import Path

from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.prompts import AgentTask
from deep_research.agents.steps import ReActRun
from deep_research.agents.synthesizer import SynthesizerAgent, SynthesizedReport
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import OpenAIProviderError
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from tests.agent_fakes import ScriptedCompleter
from tests.research_fakes import FakeMemory, synthesizer_tools
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_synthesizer.py -v`
Expected: FAIL with `ImportError: cannot import name 'SynthesizerAgent' from 'deep_research.agents.synthesizer'`

- [ ] **Step 3: Write minimal implementation**

Extend the imports at the top of `src/deep_research/agents/synthesizer.py`:

```python
from deep_research.agents.base import AgentRun, BaseAgent, StructuredCompleter
from deep_research.agents.errors import AgentConfigurationError, agent_error
from deep_research.agents.events import agent_event
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, OpenAIProviderError
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Claim,
    ContractModel,
    Finding,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
)
```

Append to `src/deep_research/agents/synthesizer.py`:

```python
# Enumerated, project-generated reasons a composed report never reached
# disk. Never provider or filesystem text: these reach
# ResearchError.details.
WRITE_FAILURE_REASONS = {
    "tool_failed": "The write_document tool reported a failure.",
    "malformed_result": "The write_document tool returned no usable path.",
}


def report_provider_error(error: Exception) -> ResearchError:
    """Record that the report call could not reach the provider.

    Non-recoverable, mirroring ``researcher.extraction_provider_error``: no
    prose exists for this pass. The report is still composed and still
    saved — the skeleton is rendered from recorded evidence — so this is a
    quality failure, not a lost report.
    """
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_report_provider_error",
        message=(
            "The model provider failed while the report was written; the "
            "report was assembled from recorded evidence alone."
        ),
        recoverable=False,
        details={"exception_type": type(error).__name__},
    )


def invalid_section_error(rejected: Sequence[str]) -> ResearchError:
    """Warn that some drafted sections or citations were dropped."""
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_invalid_section",
        message=(
            "Some drafted report sections were malformed or cited sources "
            "that were never retrieved."
        ),
        details={"rejected": list(rejected)},
    )


def report_not_written_error(*, reason: str) -> ResearchError:
    """Warn that the composed report could not be written to disk.

    Recoverable: ``state.report`` still carries the Markdown, so nothing is
    lost but the artifact.
    """
    explanation = WRITE_FAILURE_REASONS.get(reason)
    if explanation is None:
        raise ValueError(f"unknown write failure reason: {reason}")
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_report_not_written",
        message=(
            f"{explanation} The report is still available in research state."
        ),
        details={"reason": reason},
    )


def memory_save_error(*, failures: int, attempted: int) -> ResearchError:
    """Warn that some high-confidence claims were not kept for later."""
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_memory_save_failed",
        message=(
            "Some high-confidence claims could not be saved to long-term "
            "memory; this report is unaffected."
        ),
        details={"failures": failures, "attempted": attempted},
    )


def no_evidence_error() -> ResearchError:
    """Warn that the report was assembled over no evidence at all."""
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_no_evidence",
        message=(
            "No claim, source, or finding was available; the report states "
            "its own limitations and nothing else."
        ),
    )


def synthesis_started_event(
    *,
    claim_count: int,
    source_count: int,
    finding_count: int,
    limitation_count: int,
) -> ResearchEvent:
    """Announce that synthesis began, before any provider call."""
    return agent_event(
        agent_name=SYNTHESIZER_NAME,
        event_type="synthesizer.synthesis.started",
        message="Report synthesis started.",
        metadata={
            "claim_count": claim_count,
            "source_count": source_count,
            "finding_count": finding_count,
            "limitation_count": limitation_count,
        },
    )


def synthesis_completed_event(
    report: SynthesizedReport,
    *,
    limitations: Sequence[str],
    claim_count: int,
) -> ResearchEvent:
    """Report the counts the spec requires of this agent.

    ``limitations`` carries enumerated ``LIMITATION_REASONS`` keys, never
    prose, so a consumer can group on them.
    """
    return agent_event(
        agent_name=SYNTHESIZER_NAME,
        event_type="synthesizer.synthesis.completed",
        message="Report synthesis complete.",
        metadata={
            "section_count": report.section_count,
            "citation_count": report.citation_count,
            "source_appendix_count": report.source_count,
            "output_path": report.path,
            "saved_findings": report.saved_findings,
            "report_chars": len(report.markdown),
            "claim_count": claim_count,
            "limitations": list(limitations),
        },
    )


class SynthesizerAgent(BaseAgent[SynthesizedReport]):
    """Compose the final report, write it, and keep what is worth keeping.

    Runs no ReAct loop: the report is one structured call, and both tool
    calls are unconditional consequences of having produced it. ``run`` is
    overridden for the same reason ``SourceEvaluatorAgent`` overrides it —
    the shared single-loop ``BaseAgent.run`` cannot express this shape.
    """

    name = SYNTHESIZER_NAME
    description = "Write the final report from verified claims and sources."
    allowed_tools = ("write_document", "save_to_memory")

    def __init__(
        self,
        *,
        provider: StructuredCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        max_sections: int = DEFAULT_MAX_SECTIONS,
        finding_digest: int = SYNTHESIS_FINDING_DIGEST,
        claim_digest: int = SYNTHESIS_CLAIM_DIGEST,
        memory_confidence: float = DEFAULT_MEMORY_CONFIDENCE,
        max_memory_findings: int = DEFAULT_MAX_MEMORY_FINDINGS,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
        )
        if max_sections < 1:
            raise ValueError("max_sections must be at least 1")
        if finding_digest < 1:
            raise ValueError("finding_digest must be at least 1")
        if claim_digest < 1:
            raise ValueError("claim_digest must be at least 1")
        if not 0.0 <= memory_confidence <= 1.0:
            raise ValueError("memory_confidence must be in [0.0, 1.0]")
        if max_memory_findings < 0:
            raise ValueError("max_memory_findings must not be negative")
        self._max_sections = max_sections
        self._finding_digest = finding_digest
        self._claim_digest = claim_digest
        self._memory_confidence = memory_confidence
        self._max_memory_findings = max_memory_findings

    @property
    def output_schema(self) -> type[SynthesizedReport]:
        """The composed report. Never sent to the provider.

        ``draft_report`` asks for ``ReportDraft`` instead, because the
        report record carries constraints that do not survive strict JSON
        schema conversion. Do not route this agent through
        ``complete_output``.
        """
        return SynthesizedReport

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return SYNTHESIZER_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> SynthesisTask:
        """Bind this run to the evidence and limitations already recorded."""
        return SynthesisTask(
            instruction=state.original_question,
            guidance=render_revision_guidance(state),
            session_id=state.session_id,
            iteration=state.iteration,
            claims=list(state.verified_claims),
            sources=list(state.evaluated_sources),
            findings=list(state.raw_findings),
            limitations=limitation_reasons(state),
        )

    def _require_tool(self, name: str) -> BaseTool:
        tool = self.toolset.get(name)
        if tool is None:
            raise AgentConfigurationError(f"{name} was not injected")
        return tool

    async def draft_report(
        self,
        task: SynthesisTask,
    ) -> tuple[ReportDraft | None, list[ResearchError], bool]:
        """Ask the model for the report's prose.

        Makes no provider call when there is no evidence at all, so the
        writing step can never invent a report out of nothing. The third
        element is ``True`` only when the call itself failed.
        """
        if not task.claims and not task.sources and not task.findings:
            return None, [no_evidence_error()], False
        try:
            draft = await self.provider.complete_structured(
                report_messages(
                    task,
                    finding_digest=self._finding_digest,
                    claim_digest=self._claim_digest,
                ),
                ReportDraft,
                agent_name=self.name,
            )
        except OpenAIProviderError as error:
            return None, [report_provider_error(error)], True
        return draft, [], False

    async def write_report(
        self,
        task: SynthesisTask,
        markdown: str,
    ) -> tuple[str | None, list[ResearchError]]:
        """Write the composed report, tolerating a failing filesystem."""
        tool = self._require_tool("write_document")
        result = await tool.execute(
            filename=report_filename(
                session_id=task.session_id, iteration=task.iteration
            ),
            content=markdown,
        )
        if not result.success:
            return None, [report_not_written_error(reason="tool_failed")]
        data = result.data
        path = data.get("path") if isinstance(data, dict) else None
        if not isinstance(path, str) or not path:
            return None, [report_not_written_error(reason="malformed_result")]
        return path, []

    async def save_findings(
        self,
        task: SynthesisTask,
    ) -> tuple[int, list[ResearchError]]:
        """Keep the confident, verified claims for future sessions."""
        selected = high_confidence_claims(
            task.claims, threshold=self._memory_confidence
        )[: self._max_memory_findings]
        if not selected:
            return 0, []
        tool = self._require_tool("save_to_memory")
        saved = 0
        failures = 0
        for claim in selected:
            content, metadata = memory_payload(
                claim, session_id=task.session_id
            )
            result = await tool.execute(content=content, metadata=metadata)
            if result.success:
                saved += 1
            else:
                failures += 1
        errors = (
            [memory_save_error(failures=failures, attempted=len(selected))]
            if failures
            else []
        )
        return saved, errors

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> SynthesizedReport | None:
        """Adapt drafting and composition to the ``BaseAgent`` hook.

        ``run`` calls the pieces directly so it can keep the errors this
        hook signature has nowhere to return, and so the writes happen once.
        """
        del run
        if not isinstance(task, SynthesisTask):
            raise AgentConfigurationError(
                "SynthesizerAgent.finalize requires a SynthesisTask"
            )
        draft, _, provider_failed = await self.draft_report(task)
        summary, sections, notes, limitations, _ = self._compose_inputs(
            task, draft, provider_failed=provider_failed
        )
        return compose_report(
            task,
            summary=summary,
            sections=sections,
            uncertainty_notes=notes,
            limitations=limitations,
        )

    def _compose_inputs(
        self,
        task: SynthesisTask,
        draft: ReportDraft | None,
        *,
        provider_failed: bool,
    ) -> tuple[str, list[ReportSection], str, list[str], list[ResearchError]]:
        """Turn a draft (or its absence) into everything ``compose_report`` needs."""
        limitations = list(task.limitations)
        if provider_failed:
            limitations.append("report_generation_failed")
        if draft is None:
            return REPORT_SUMMARY_FALLBACK, [], "", limitations, []
        known = [
            citation.url
            for citation in build_citation_index(task.sources, task.claims)
        ]
        sections, rejected = build_report_sections(
            draft, known_urls=known, max_sections=self._max_sections
        )
        errors = [invalid_section_error(rejected)] if rejected else []
        summary = draft.executive_summary.strip() or REPORT_SUMMARY_FALLBACK
        return summary, sections, draft.uncertainty_notes, limitations, errors

    def state_update(
        self,
        result: SynthesizedReport | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """The report and errors only. ``run`` adds the progress events."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["report"] = result.markdown
        return update

    async def run(self, state: ResearchState) -> AgentRun[SynthesizedReport]:
        """Draft, compose, write, and keep — recording every count.

        No ReAct loop runs, so the returned ``ReActRun`` is synthetic with
        zero iterations and zero tool calls. ``stop_reason`` is
        ``"provider_error"`` only when the report call itself failed, so a
        caller reading ``react.succeeded`` learns the same thing it would
        from any other agent.
        """
        task = self.build_task(state)
        events: list[ResearchEvent] = [
            synthesis_started_event(
                claim_count=len(task.claims),
                source_count=len(task.sources),
                finding_count=len(task.findings),
                limitation_count=len(task.limitations),
            )
        ]
        errors: list[ResearchError] = []

        async with self.tracker.agent_span(self.name) as span:
            draft, draft_errors, provider_failed = await self.draft_report(task)
            errors.extend(draft_errors)
            (
                summary,
                sections,
                notes,
                limitations,
                section_errors,
            ) = self._compose_inputs(task, draft, provider_failed=provider_failed)
            errors.extend(section_errors)
            report = compose_report(
                task,
                summary=summary,
                sections=sections,
                uncertainty_notes=notes,
                limitations=limitations,
            )
            path, write_errors = await self.write_report(task, report.markdown)
            errors.extend(write_errors)
            saved, memory_errors = await self.save_findings(task)
            errors.extend(memory_errors)
            report = report.model_copy(
                update={"path": path, "saved_findings": saved}
            )
            events.append(
                synthesis_completed_event(
                    report,
                    limitations=limitations,
                    claim_count=len(task.claims),
                )
            )
            span.set_outputs(
                {
                    "agent_name": self.name,
                    "section_count": report.section_count,
                    "citation_count": report.citation_count,
                    "output_path": report.path,
                    "saved_findings": report.saved_findings,
                    "provider_failed": provider_failed,
                }
            )

        react = ReActRun(
            agent_name=self.name,
            stop_reason="provider_error" if provider_failed else "finished",
            errors=errors,
        )
        return AgentRun(
            agent_name=self.name,
            result=report,
            react=react,
            errors=errors,
            state_update={
                **self.state_update(report, react),
                "events": events,
            },
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/test_synthesizer.py -v && ruff check src/deep_research/agents/synthesizer.py tests/test_agents/test_synthesizer.py tests/research_fakes.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/synthesizer.py \
  tests/test_agents/test_synthesizer.py tests/research_fakes.py
git commit -m "feat: write, save, and report on the synthesized research report"
```

---

### Task 5: Critique Contracts, Score Clamping, And Routing Maths

**Files:**
- Create: `src/deep_research/agents/critic.py` (pure module contents only — the agent class arrives in Task 6)
- Create: `tests/test_agents/test_critic.py`

**Interfaces:**
- Consumes: `CRITIC_SYSTEM_PROMPT`, `CRITIQUE_INSTRUCTION`, `AgentTask`, `render_claim_digest`, `render_source_quality` from `deep_research.agents.prompts` (Task 2); `render_evidence` from `deep_research.agents.researcher`; `ReActRun`, `summarize_text` from `deep_research.agents.steps`; `ChatMessage` from `deep_research.providers`; `Claim`, `ContractModel`, `Critique`, `ScoredSource` from `deep_research.utils.types`.
- Produces, all importable from `deep_research.agents.critic`:
  - `CRITIC_NAME: str = "critic"`
  - `ACCEPTANCE_SCORE: int = 7`, `MIN_CRITIC_SCORE: int = 1`, `MAX_CRITIC_SCORE: int = 10`
  - `CRITIC_REPORT_CHARS: int = 6000`, `CRITIC_CLAIM_DIGEST: int = 40`, `CRITIC_EVIDENCE_CHARS: int = 2000`, `DEFAULT_MAX_NOTES: int = 10`
  - `ROUTING_REASONS: dict[str, str]`, `CRITIQUE_FALLBACK_REASONS: tuple[str, ...]`
  - `CritiqueDraft(ContractModel)` — `score: int`, `gaps`, `unsupported_claims`, `recommended_queries`, `rationale: str`
  - `CritiqueTask(AgentTask)` — `report`, `iteration`, `max_iterations`, `claims`, `sources`, `sub_topics`, `error_count`
  - `clamp_score(value: int) -> int`
  - `normalize_notes(values: Sequence[str], *, limit: int = DEFAULT_MAX_NOTES) -> list[str]`
  - `route_decision(*, score, gaps, unsupported_claims, iteration, max_iterations, has_report) -> tuple[bool, str]`
  - `build_critique(draft, *, iteration, max_iterations) -> tuple[Critique, str]`
  - `fallback_critique(*, reason, iteration, max_iterations) -> tuple[Critique, str]`
  - `critique_messages(task, run, *, report_chars, claim_digest) -> list[ChatMessage]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agents/test_critic.py`:

```python
"""Tests for the Critic's score clamping, routing maths, and prompts."""

from __future__ import annotations

import pytest

from deep_research.agents.critic import (
    ACCEPTANCE_SCORE,
    MAX_CRITIC_SCORE,
    MIN_CRITIC_SCORE,
    ROUTING_REASONS,
    CritiqueDraft,
    CritiqueTask,
    build_critique,
    clamp_score,
    critique_messages,
    fallback_critique,
    normalize_notes,
    route_decision,
)
from deep_research.agents.steps import ReActRun
from deep_research.utils.types import Claim, Critique, ScoredSource

CRITIC_SOURCE_URL = "https://example.org/a"


def _source(*, low_confidence: bool = False) -> ScoredSource:
    return ScoredSource(
        url=CRITIC_SOURCE_URL,
        title="QEC 2025",
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        corroboration_score=0.5,
        overall_score=0.76,
        rationale="Peer-reviewed and corroborated.",
        low_confidence=low_confidence,
    )


def _claim(*, verdict: str = "verified") -> Claim:
    return Claim(
        text="Logical error rates fell below break-even in 2025.",
        source_urls=[CRITIC_SOURCE_URL],
        verdict=verdict,
        confidence=0.8,
        evidence=[],
        contradictions=[],
    )


def _draft(
    *,
    score: int = 8,
    gaps: list[str] | None = None,
    unsupported: list[str] | None = None,
    queries: list[str] | None = None,
    rationale: str = "Well sourced and complete.",
) -> CritiqueDraft:
    return CritiqueDraft(
        score=score,
        gaps=gaps or [],
        unsupported_claims=unsupported or [],
        recommended_queries=queries or [],
        rationale=rationale,
    )


def _task(**overrides: object) -> CritiqueTask:
    payload: dict[str, object] = {
        "instruction": "How mature is quantum error correction?",
        "report": "# Research report: How mature is quantum error correction?",
        "iteration": 0,
        "max_iterations": 3,
        "claims": [_claim()],
        "sources": [_source()],
        "sub_topics": ["Alpha"],
        "error_count": 2,
    }
    payload.update(overrides)
    return CritiqueTask.model_validate(payload)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(-4, 1), (0, 1), (1, 1), (7, 7), (10, 10), (99, 10)],
)
def test_scores_are_pinned_into_the_critic_score_range(
    raw: int, expected: int
) -> None:
    assert clamp_score(raw) == expected
    assert MIN_CRITIC_SCORE <= clamp_score(raw) <= MAX_CRITIC_SCORE


def test_notes_are_collapsed_deduplicated_and_capped() -> None:
    notes = normalize_notes(
        ["  No  cost data. ", "No cost data.", "", "   ", "No vendor audit."],
        limit=5,
    )

    assert notes == ["No cost data.", "No vendor audit."]
    assert normalize_notes(["a", "b", "c"], limit=2) == ["a", "b"]
    with pytest.raises(ValueError, match="limit"):
        normalize_notes(["a"], limit=0)


def test_an_acceptable_report_ends_the_run() -> None:
    assert route_decision(
        score=ACCEPTANCE_SCORE,
        gaps=[],
        unsupported_claims=[],
        iteration=0,
        max_iterations=3,
        has_report=True,
    ) == (False, "accepted_quality")


@pytest.mark.parametrize(
    ("score", "gaps", "unsupported", "reason"),
    [
        (6, [], [], "low_score"),
        (9, ["No cost data."], [], "critical_gaps"),
        (9, [], ["Costs fell tenfold."], "unsupported_claims"),
    ],
)
def test_a_weak_report_continues_with_the_reason_that_applies(
    score: int, gaps: list[str], unsupported: list[str], reason: str
) -> None:
    assert route_decision(
        score=score,
        gaps=gaps,
        unsupported_claims=unsupported,
        iteration=0,
        max_iterations=3,
        has_report=True,
    ) == (True, reason)


def test_the_iteration_bound_beats_every_quality_signal() -> None:
    assert route_decision(
        score=1,
        gaps=["Everything is missing."],
        unsupported_claims=["All of it."],
        iteration=3,
        max_iterations=3,
        has_report=True,
    ) == (False, "max_iterations_reached")


def test_a_missing_report_continues_while_budget_remains() -> None:
    assert route_decision(
        score=10,
        gaps=[],
        unsupported_claims=[],
        iteration=1,
        max_iterations=3,
        has_report=False,
    ) == (True, "missing_report")


def test_a_critique_is_validated_clamped_and_routed() -> None:
    critique, reason = build_critique(
        _draft(
            score=99,
            gaps=["  No cost data. ", "No cost data."],
            queries=["qec cost 2025"],
        ),
        iteration=0,
        max_iterations=3,
    )

    assert isinstance(critique, Critique)
    assert critique.score == MAX_CRITIC_SCORE
    assert critique.gaps == ["No cost data."]
    assert critique.recommended_queries == ["qec cost 2025"]
    assert critique.should_continue is True
    assert reason == "critical_gaps"
    assert critique.rationale.startswith("Well sourced and complete.")
    assert ROUTING_REASONS["critical_gaps"] in critique.rationale


def test_a_blank_model_rationale_still_yields_a_usable_one() -> None:
    critique, reason = build_critique(
        _draft(rationale="   "), iteration=0, max_iterations=3
    )

    assert critique.rationale == ROUTING_REASONS["accepted_quality"]
    assert reason == "accepted_quality"
    assert critique.should_continue is False


def test_the_last_iteration_stops_even_on_a_scathing_critique() -> None:
    critique, reason = build_critique(
        _draft(score=2, gaps=["No cost data."]),
        iteration=3,
        max_iterations=3,
    )

    assert critique.should_continue is False
    assert reason == "max_iterations_reached"
    assert critique.gaps == ["No cost data."]
    assert ROUTING_REASONS["max_iterations_reached"] in critique.rationale


def test_a_provider_outage_never_buys_another_research_cycle() -> None:
    critique, reason = fallback_critique(
        reason="provider_unavailable", iteration=0, max_iterations=3
    )

    assert critique.score == MIN_CRITIC_SCORE
    assert critique.should_continue is False
    assert reason == "provider_unavailable"
    assert critique.rationale == ROUTING_REASONS["provider_unavailable"]


def test_a_missing_report_is_worth_one_more_cycle() -> None:
    critique, reason = fallback_critique(
        reason="missing_report", iteration=0, max_iterations=3
    )

    assert critique.should_continue is True
    assert reason == "missing_report"
    assert critique.gaps == ["No report was available to review."]

    exhausted, exhausted_reason = fallback_critique(
        reason="missing_report", iteration=3, max_iterations=3
    )
    assert exhausted.should_continue is False
    assert exhausted_reason == "max_iterations_reached"


def test_fallback_critique_rejects_an_unenumerated_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        fallback_critique(reason="because", iteration=0, max_iterations=3)


def test_critique_messages_carry_the_report_and_every_quality_signal() -> None:
    messages = critique_messages(
        _task(),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=6000,
        claim_digest=10,
    )

    assert [message.role for message in messages] == ["developer", "user"]
    body = messages[1].content
    assert "## Research question" in body
    assert "## Report under review" in body
    assert "# Research report:" in body
    assert "## Sub-topics planned" in body
    assert "- Alpha" in body
    assert "## Claim verdicts" in body
    assert "[verified 0.80]" in body
    assert "## Source quality" in body
    assert "## Recorded problems" in body
    assert "2 error(s)" in body
    assert "## Spot checks" in body
    assert "## Response contract" in body


def test_critique_messages_clamp_a_long_report_without_flattening_it() -> None:
    report = "# Title\n\n" + ("x" * 500)
    body = critique_messages(
        _task(report=report),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=80,
        claim_digest=10,
    )[1].content

    assert "# Title\n" in body
    assert "x" * 500 not in body
    assert "..." in body


def test_critique_messages_say_so_when_there_is_no_report() -> None:
    body = critique_messages(
        _task(report="   "),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=80,
        claim_digest=10,
    )[1].content

    assert "(no report)" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_critic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.agents.critic'`

- [ ] **Step 3: Write minimal implementation**

Create `src/deep_research/agents/critic.py`:

```python
"""The Critic: score the report and recommend whether research continues.

The provider is asked for ``CritiqueDraft`` and never for ``Critique``:
``Critique`` declares ``CriticScore`` (1..10) and a non-blank rationale,
which strict structured outputs reject. Local code stamps the parts the
model must not be trusted with — the clamped score, the de-duplicated
notes, and above all the routing decision.

Routing convention: ``route_decision`` checks the iteration bound *first*.
"The critic must not continue forever" is the one rule no model judgement
may override, so it is settled before anything the model said is read.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from deep_research.agents.prompts import (
    CRITIC_SYSTEM_PROMPT,
    CRITIQUE_INSTRUCTION,
    AgentTask,
    render_claim_digest,
    render_source_quality,
)
from deep_research.agents.researcher import render_evidence
from deep_research.agents.steps import ReActRun
from deep_research.providers import ChatMessage
from deep_research.utils.types import (
    Claim,
    ContractModel,
    Critique,
    ScoredSource,
)

CRITIC_NAME = "critic"

# The spec's acceptance threshold: a report scoring below this always buys
# another research pass while budget remains.
ACCEPTANCE_SCORE = 7
MIN_CRITIC_SCORE = 1
MAX_CRITIC_SCORE = 10

CRITIC_REPORT_CHARS = 6000
CRITIC_CLAIM_DIGEST = 40
CRITIC_EVIDENCE_CHARS = 2000
DEFAULT_MAX_NOTES = 10

_RATIONALE_CHARS = 600

# Enumerated, project-generated routing reasons. Never provider text: these
# reach ResearchEvent.metadata and the recorded rationale.
ROUTING_REASONS = {
    "accepted_quality": "The report met the acceptance threshold.",
    "max_iterations_reached": (
        "The refinement budget is exhausted; this is the final report."
    ),
    "low_score": "The report scored below the acceptance threshold.",
    "critical_gaps": "Material gaps remain in the research.",
    "unsupported_claims": (
        "The report leans on statements no source supports."
    ),
    "missing_report": "No report was available to review.",
    "provider_unavailable": (
        "The model provider failed while the report was reviewed."
    ),
}

# The two conditions under which no model review exists at all.
CRITIQUE_FALLBACK_REASONS = ("missing_report", "provider_unavailable")


class CritiqueDraft(ContractModel):
    """One model review, before domain validation.

    ``score`` is a plain ``int`` rather than ``CriticScore``: a model that
    answers 0 or 42 is making a formatting mistake, which ``clamp_score``
    fixes locally rather than discarding the whole review over.
    """

    score: int
    gaps: list[str]
    unsupported_claims: list[str]
    recommended_queries: list[str]
    rationale: str


class CritiqueTask(AgentTask):
    """An ``AgentTask`` bound to the report and budget it reviews.

    Carrying the report and the iteration bounds on the task is what lets
    ``finalize(task, run)`` route without the agent holding mutable state
    across await points — the same reason ``ClaimTask`` exists.
    """

    report: str = ""
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=1, ge=1)
    claims: list[Claim] = []
    sources: list[ScoredSource] = []
    sub_topics: list[str] = []
    error_count: int = Field(default=0, ge=0)


def clamp_score(value: int) -> int:
    """Pin a model score into the ``CriticScore`` range."""
    return min(MAX_CRITIC_SCORE, max(MIN_CRITIC_SCORE, int(value)))


def normalize_notes(
    values: Sequence[str],
    *,
    limit: int = DEFAULT_MAX_NOTES,
) -> list[str]:
    """Collapse, de-duplicate, and cap one model-supplied note list."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    notes: list[str] = []
    for value in values:
        note = " ".join(value.split())
        if note and note not in notes:
            notes.append(note)
    return notes[:limit]


def route_decision(
    *,
    score: int,
    gaps: Sequence[str],
    unsupported_claims: Sequence[str],
    iteration: int,
    max_iterations: int,
    has_report: bool,
) -> tuple[bool, str]:
    """Decide routing locally, in a fixed precedence.

    The iteration bound comes first and beats every quality signal. After
    that: a missing report is the most concrete thing to fix, then the
    score threshold, then gaps, then unsupported claims. Every gap the
    model listed counts as critical — ``CRITIQUE_INSTRUCTION`` tells it to
    list a gap only when closing it would materially change the answer.
    """
    if iteration >= max_iterations:
        return False, "max_iterations_reached"
    if not has_report:
        return True, "missing_report"
    if score < ACCEPTANCE_SCORE:
        return True, "low_score"
    if gaps:
        return True, "critical_gaps"
    if unsupported_claims:
        return True, "unsupported_claims"
    return False, "accepted_quality"


def _rationale(model_rationale: str, *, reason: str) -> str:
    """Extend the model's rationale with the routing reason this run used."""
    text = " ".join(model_rationale.split())
    explanation = ROUTING_REASONS[reason]
    body = f"{text} {explanation}" if text else explanation
    return body[:_RATIONALE_CHARS].rstrip()


def build_critique(
    draft: CritiqueDraft,
    *,
    iteration: int,
    max_iterations: int,
) -> tuple[Critique, str]:
    """Stamp one model review into a validated ``Critique`` and its route."""
    score = clamp_score(draft.score)
    gaps = normalize_notes(draft.gaps)
    unsupported = normalize_notes(draft.unsupported_claims)
    queries = normalize_notes(draft.recommended_queries)
    should_continue, reason = route_decision(
        score=score,
        gaps=gaps,
        unsupported_claims=unsupported,
        iteration=iteration,
        max_iterations=max_iterations,
        has_report=True,
    )
    return (
        Critique(
            score=score,
            gaps=gaps,
            unsupported_claims=unsupported,
            recommended_queries=queries,
            should_continue=should_continue,
            rationale=_rationale(draft.rationale, reason=reason),
        ),
        reason,
    )


def fallback_critique(
    *,
    reason: str,
    iteration: int,
    max_iterations: int,
) -> tuple[Critique, str]:
    """Record a review that could not be made, with no invented score.

    A provider outage never buys another research cycle: an outage says
    nothing about the report, and a retry would almost certainly repeat it
    at cost. A missing report does buy one — there is something concrete to
    fix — unless the iteration bound already forbids it.
    """
    if reason not in CRITIQUE_FALLBACK_REASONS:
        raise ValueError(f"unknown fallback reason: {reason}")
    if reason == "provider_unavailable":
        should_continue = False
        route = (
            "max_iterations_reached"
            if iteration >= max_iterations
            else reason
        )
        gaps: list[str] = []
    else:
        should_continue, route = route_decision(
            score=MIN_CRITIC_SCORE,
            gaps=[],
            unsupported_claims=[],
            iteration=iteration,
            max_iterations=max_iterations,
            has_report=False,
        )
        gaps = ["No report was available to review."]
    sentences = [ROUTING_REASONS[reason]]
    if route != reason:
        sentences.append(ROUTING_REASONS[route])
    return (
        Critique(
            score=MIN_CRITIC_SCORE,
            gaps=gaps,
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=should_continue,
            rationale=" ".join(sentences),
        ),
        route,
    )


def _clamp_report(text: str, *, limit: int) -> str:
    """Clamp the report for a prompt without flattening its headings.

    ``summarize_text`` is deliberately not used here: it joins on
    whitespace, which would run every Markdown heading into one line.
    """
    report = text.strip()
    if not report:
        return "(no report)"
    if len(report) <= limit:
        return report
    return report[: limit - 3].rstrip() + "..."


def critique_messages(
    task: CritiqueTask,
    run: ReActRun,
    *,
    report_chars: int,
    claim_digest: int,
) -> list[ChatMessage]:
    """Build the messages that request one structured review."""
    sub_topics = (
        "\n".join(f"- {title}" for title in task.sub_topics)
        or "(none planned)"
    )
    sections = [
        f"## Research question\n{task.instruction}",
        (
            "## Report under review\n"
            f"{_clamp_report(task.report, limit=report_chars)}"
        ),
        f"## Sub-topics planned\n{sub_topics}",
        (
            "## Claim verdicts\n"
            f"{render_claim_digest(list(task.claims)[:claim_digest])}"
        ),
        f"## Source quality\n{render_source_quality(task.sources)}",
        (
            "## Recorded problems\n"
            f"{task.error_count} error(s) were recorded during this pass."
        ),
        (
            "## Spot checks\n"
            f"{render_evidence(run, limit=CRITIC_EVIDENCE_CHARS)}"
        ),
        f"## Response contract\n{CRITIQUE_INSTRUCTION}",
    ]
    return [
        ChatMessage(role="developer", content=CRITIC_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/test_critic.py -v && ruff check src/deep_research/agents/critic.py tests/test_agents/test_critic.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/critic.py tests/test_agents/test_critic.py
git commit -m "feat: add critique contracts and local routing maths"
```

---

### Task 6: `CriticAgent` — Spot-Check Loop, Review, And `run`

**Files:**
- Modify: `src/deep_research/agents/critic.py` (append)
- Modify: `tests/research_fakes.py` (append `critic_tools`)
- Modify: `tests/test_agents/test_critic.py` (append)

**Interfaces:**
- Consumes: everything Task 5 produced; `AgentRun`, `BaseAgent`, `StructuredCompleter` from `deep_research.agents.base`; `AgentConfigurationError`, `agent_error` from `deep_research.agents.errors`; `agent_event` from `deep_research.agents.events`; `render_react_messages` from `deep_research.agents.prompts`; `run_react_loop` from `deep_research.agents.react`; `ReActDecision`, `ReActStep` from `deep_research.agents.steps`; `OpenAIProviderError` from `deep_research.providers`.
- Produces:
  - `critique_provider_error(error: Exception) -> ResearchError`
  - `missing_report_error() -> ResearchError`
  - `critique_started_event(*, iteration, max_iterations, claim_count, has_report) -> ResearchEvent`
  - `critique_completed_event(critique, run, *, reason, iteration, max_iterations) -> ResearchEvent`
  - `CriticAgent(BaseAgent[Critique])` with `name = "critic"`, `allowed_tools = ("web_search", "query_memory")`, constructor `(*, provider, tracker, scratchpad, tools=(), config=None, report_chars=CRITIC_REPORT_CHARS, claim_digest=CRITIC_CLAIM_DIGEST)`, and methods `build_task(state) -> CritiqueTask`, `async review(task, run)`, `async finalize(task, run)`, `state_update(result, run)`, `async run(state)`

- [ ] **Step 1: Write the failing tests**

Append `critic_tools` to `tests/research_fakes.py`:

```python
def critic_tools(
    tracker: Tracker,
    *,
    search: FakeSearchClient | None = None,
    memory: FakeMemory | None = None,
) -> list[BaseTool]:
    """Build the two tools ``CriticAgent`` declares, all offline.

    The same pair ``PlannerAgent`` declares — spot-checking a suspected gap
    is scoping work, not research — so this delegates rather than
    re-listing them.
    """
    return planner_tools(tracker, search=search, memory=memory)
```

Append to `tests/test_agents/test_critic.py` (extend the existing import block with the new names, and add the imports the tests below need):

```python
def _critic(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    tools: list[BaseTool] | None = None,
    tool_budget: int = 0,
) -> CriticAgent:
    return CriticAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="critic", max_entries=20
        ),
        tools=tools if tools is not None else [],
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=tool_budget),
    )


def _critic_state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "How mature is quantum error correction?",
        "sub_topics": [
            SubTopic(
                title="Alpha",
                rationale="Alpha is load-bearing.",
                search_queries=["alpha 2025"],
                success_criteria=["A named source about Alpha."],
                priority=1,
            )
        ],
        "evaluated_sources": [_source()],
        "verified_claims": [_claim()],
        "report": "# Research report: How mature is quantum error correction?",
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def test_build_task_carries_the_report_budget_and_quality_signals(
    tracker: Tracker,
) -> None:
    agent = _critic(tracker, ScriptedCompleter())
    state = _critic_state(iteration=2, max_iterations=3)

    task = agent.build_task(state)

    assert task.instruction == state.original_question
    assert task.report == state.report
    assert task.iteration == 2
    assert task.max_iterations == 3
    assert task.sub_topics == ["Alpha"]
    assert len(task.claims) == 1
    assert len(task.sources) == 1
    assert task.error_count == 0


@pytest.mark.asyncio
async def test_an_acceptable_report_ends_the_graph(tracker: Tracker) -> None:
    agent = _critic(
        tracker, ScriptedCompleter(decisions=[], outputs=[_draft(score=9)])
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is False
    assert critique.score == 9
    assert outcome.state_update["critique"] == critique
    assert outcome.errors == []


@pytest.mark.asyncio
async def test_a_low_quality_report_asks_for_another_pass(
    tracker: Tracker,
) -> None:
    agent = _critic(
        tracker,
        ScriptedCompleter(
            outputs=[
                _draft(
                    score=4,
                    gaps=["No cost data."],
                    unsupported=["Costs fell tenfold."],
                    queries=["qec cost 2025"],
                    rationale="One source carries the whole argument.",
                )
            ]
        ),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is True
    assert critique.gaps == ["No cost data."]
    assert critique.recommended_queries == ["qec cost 2025"]


@pytest.mark.asyncio
async def test_the_final_iteration_forces_a_stop(tracker: Tracker) -> None:
    agent = _critic(
        tracker,
        ScriptedCompleter(outputs=[_draft(score=2, gaps=["No cost data."])]),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state(iteration=3, max_iterations=3))

    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is False
    assert critique.gaps == ["No cost data."]
    event = outcome.state_update["events"][-1]
    assert event.metadata["reason"] == "max_iterations_reached"
    assert event.metadata["should_continue"] is False


@pytest.mark.asyncio
async def test_a_run_emits_the_routing_counts_the_spec_requires(
    tracker: Tracker,
) -> None:
    agent = _critic(
        tracker,
        ScriptedCompleter(
            outputs=[
                _draft(
                    score=5,
                    gaps=["No cost data.", "No vendor audit."],
                    unsupported=["Costs fell tenfold."],
                )
            ]
        ),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    events = outcome.state_update["events"]
    assert [event.event_type for event in events] == [
        "critic.critique.started",
        "critic.critique.completed",
    ]
    completed = events[-1].metadata
    assert completed["score"] == 5
    assert completed["gap_count"] == 2
    assert completed["unsupported_claim_count"] == 1
    assert completed["should_continue"] is True
    assert completed["reason"] == "low_score"
    assert completed["iteration"] == 0
    assert completed["max_iterations"] == 3


@pytest.mark.asyncio
async def test_a_missing_report_is_recorded_without_a_provider_call(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _critic(tracker, completer)

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state(report=None))

    assert completer.calls == []
    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is True
    assert critique.score == MIN_CRITIC_SCORE
    assert [error.error_type for error in outcome.errors] == [
        "critic_missing_report"
    ]
    assert outcome.errors[0].recoverable is True


@pytest.mark.asyncio
async def test_a_provider_failure_still_routes_and_stops(
    tracker: Tracker,
) -> None:
    agent = _critic(
        tracker, ScriptedCompleter(outputs=[OpenAIProviderError("down")])
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    critique = outcome.result
    assert critique is not None
    assert critique.should_continue is False
    assert critique.score == MIN_CRITIC_SCORE
    assert outcome.react.stop_reason == "provider_error"
    error = next(
        error
        for error in outcome.errors
        if error.error_type == "critic_review_provider_error"
    )
    assert error.recoverable is False
    assert error.details == {"exception_type": "OpenAIProviderError"}


@pytest.mark.asyncio
async def test_a_spot_check_reaches_the_review_prompt(tracker: Tracker) -> None:
    completer = ScriptedCompleter(
        decisions=[
            use_tool(
                "Check the cost figure.",
                "web_search",
                '{"query": "qec cost 2025"}',
            ),
            finish("Enough to judge.", "The cost figure checks out."),
        ],
        outputs=[_draft(score=9)],
    )
    agent = _critic(
        tracker,
        completer,
        tools=critic_tools(tracker),
        tool_budget=2,
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    assert outcome.react.tool_calls == 1
    review_call = next(
        call for call in completer.calls if call[0] == "CritiqueDraft"
    )
    assert "[web_search]" in review_call[2][1].content
    event = outcome.state_update["events"][-1]
    assert event.metadata["tool_calls"] == 1


@pytest.mark.asyncio
async def test_a_failing_spot_check_never_stops_the_review(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Search first.", "web_search", '{"query": "qec"}'),
            finish("Judge without it.", "The search failed."),
        ],
        outputs=[_draft(score=8)],
    )
    agent = _critic(
        tracker,
        completer,
        tools=critic_tools(
            tracker, search=FakeSearchClient([RuntimeError("tavily down")])
        ),
        tool_budget=2,
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_critic_state())

    critique = outcome.result
    assert critique is not None
    assert critique.score == 8
    assert outcome.react.stop_reason == "finished"


@pytest.mark.asyncio
async def test_finalize_requires_a_critique_task(tracker: Tracker) -> None:
    agent = _critic(tracker, ScriptedCompleter(outputs=[_draft()]))

    with pytest.raises(AgentConfigurationError, match="CritiqueTask"):
        await agent.finalize(
            AgentTask(instruction="anything"),
            ReActRun(agent_name="critic", stop_reason="finished"),
        )


def test_the_critic_declares_its_spot_check_tools(tracker: Tracker) -> None:
    agent = _critic(tracker, ScriptedCompleter(), tools=critic_tools(tracker))

    assert CriticAgent.name == "critic"
    assert CriticAgent.allowed_tools == ("web_search", "query_memory")
    assert agent.output_schema is Critique
```

The appended tests need these imports added to the module's import block:

```python
from deep_research.agents.critic import CriticAgent
from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.prompts import AgentTask
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import OpenAIProviderError
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import ResearchState, SubTopic
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import FakeSearchClient, critic_tools
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents/test_critic.py -v`
Expected: FAIL with `ImportError: cannot import name 'CriticAgent' from 'deep_research.agents.critic'`

- [ ] **Step 3: Write minimal implementation**

Extend the imports at the top of `src/deep_research/agents/critic.py`:

```python
from deep_research.agents.base import AgentRun, BaseAgent, StructuredCompleter
from deep_research.agents.errors import AgentConfigurationError, agent_error
from deep_research.agents.events import agent_event
from deep_research.agents.prompts import (
    CRITIC_SYSTEM_PROMPT,
    CRITIQUE_INSTRUCTION,
    AgentTask,
    render_claim_digest,
    render_react_messages,
    render_source_quality,
)
from deep_research.agents.react import run_react_loop
from deep_research.agents.researcher import render_evidence
from deep_research.agents.steps import ReActDecision, ReActRun, ReActStep
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, OpenAIProviderError
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Claim,
    ContractModel,
    Critique,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
)
```

Append to `src/deep_research/agents/critic.py`:

```python
def critique_provider_error(error: Exception) -> ResearchError:
    """Record that the review call could not reach the provider.

    Non-recoverable: no review of this report exists. The run still ends
    with a routing decision, because a graph with no critique cannot route
    at all.
    """
    return agent_error(
        agent_name=CRITIC_NAME,
        error_type="critic_review_provider_error",
        message=(
            "The model provider failed while the report was reviewed; the "
            "research pass was ended rather than repeated."
        ),
        recoverable=False,
        details={"exception_type": type(error).__name__},
    )


def missing_report_error() -> ResearchError:
    """Warn that there was no report to review."""
    return agent_error(
        agent_name=CRITIC_NAME,
        error_type="critic_missing_report",
        message="No report was available to review.",
    )


def critique_started_event(
    *,
    iteration: int,
    max_iterations: int,
    claim_count: int,
    has_report: bool,
) -> ResearchEvent:
    """Announce that the review began, before any provider call."""
    return agent_event(
        agent_name=CRITIC_NAME,
        event_type="critic.critique.started",
        message="Report review started.",
        metadata={
            "iteration": iteration,
            "max_iterations": max_iterations,
            "claim_count": claim_count,
            "has_report": has_report,
        },
    )


def critique_completed_event(
    critique: Critique,
    run: ReActRun,
    *,
    reason: str,
    iteration: int,
    max_iterations: int,
) -> ResearchEvent:
    """Report the score, the counts, and the routing recommendation.

    ``reason`` is a ``ROUTING_REASONS`` key, never provider text, so a
    consumer can group runs by *why* they continued or stopped rather than
    parsing a rationale.
    """
    return agent_event(
        agent_name=CRITIC_NAME,
        event_type="critic.critique.completed",
        message="Report review complete.",
        metadata={
            "score": critique.score,
            "gap_count": len(critique.gaps),
            "unsupported_claim_count": len(critique.unsupported_claims),
            "recommended_query_count": len(critique.recommended_queries),
            "should_continue": critique.should_continue,
            "reason": reason,
            "iteration": iteration,
            "max_iterations": max_iterations,
            "tool_calls": run.tool_calls,
            "stop_reason": run.stop_reason,
        },
    )


class CriticAgent(BaseAgent[Critique]):
    """Review the report, score it, and recommend a route.

    ``run`` is overridden to emit progress events and to skip the
    spot-check loop when there is no report; everything below it — bounds,
    tracing, tool execution, scratchpad writes — is still the shared
    runtime's.
    """

    name = CRITIC_NAME
    description = "Judge the report and recommend whether research continues."
    allowed_tools = ("web_search", "query_memory")

    def __init__(
        self,
        *,
        provider: StructuredCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        report_chars: int = CRITIC_REPORT_CHARS,
        claim_digest: int = CRITIC_CLAIM_DIGEST,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
        )
        if report_chars < 1:
            raise ValueError("report_chars must be at least 1")
        if claim_digest < 1:
            raise ValueError("claim_digest must be at least 1")
        self._report_chars = report_chars
        self._claim_digest = claim_digest

    @property
    def output_schema(self) -> type[Critique]:
        """The validated critique. Never sent to the provider.

        ``review`` asks for ``CritiqueDraft`` instead, because ``Critique``
        carries ``CriticScore`` bounds and a non-blank rationale that do not
        survive strict JSON schema conversion. Do not route this agent
        through ``complete_output``.
        """
        return Critique

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return CRITIC_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> CritiqueTask:
        """Bind this review to the report and the remaining budget."""
        return CritiqueTask(
            instruction=state.original_question,
            report=state.report or "",
            iteration=state.iteration,
            max_iterations=state.max_iterations,
            claims=list(state.verified_claims),
            sources=list(state.evaluated_sources),
            sub_topics=[sub_topic.title for sub_topic in state.sub_topics],
            error_count=len(state.errors),
        )

    async def review(
        self,
        task: CritiqueTask,
        run: ReActRun,
    ) -> tuple[Critique, str, list[ResearchError], bool]:
        """Judge one report from one finished spot-check loop.

        Returns ``(critique, reason, errors, provider_failed)``. No provider
        call is made when there is no report, so a score is never invented
        over an empty review.
        """
        if not task.report.strip():
            critique, reason = fallback_critique(
                reason="missing_report",
                iteration=task.iteration,
                max_iterations=task.max_iterations,
            )
            return critique, reason, [missing_report_error()], False

        try:
            draft = await self.provider.complete_structured(
                critique_messages(
                    task,
                    run,
                    report_chars=self._report_chars,
                    claim_digest=self._claim_digest,
                ),
                CritiqueDraft,
                agent_name=self.name,
            )
        except OpenAIProviderError as error:
            critique, reason = fallback_critique(
                reason="provider_unavailable",
                iteration=task.iteration,
                max_iterations=task.max_iterations,
            )
            return critique, reason, [critique_provider_error(error)], True

        critique, reason = build_critique(
            draft,
            iteration=task.iteration,
            max_iterations=task.max_iterations,
        )
        return critique, reason, [], False

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> Critique | None:
        """Adapt ``review`` to the ``BaseAgent`` hook.

        ``run`` calls ``review`` directly so it can keep the routing reason
        and the errors this hook signature has nowhere to return.
        """
        if not isinstance(task, CritiqueTask):
            raise AgentConfigurationError(
                "CriticAgent.finalize requires a CritiqueTask"
            )
        critique, _, _, _ = await self.review(task, run)
        return critique

    def state_update(
        self,
        result: Critique | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """The critique and errors only. ``run`` adds the progress events."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["critique"] = result
        return update

    async def _spot_check(self, task: CritiqueTask) -> ReActRun:
        """Run one bounded ReAct loop inside the caller's agent span.

        The scratchpad is cleared first: notes from a previous iteration's
        review are noise in this one's prompt.
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

    async def run(self, state: ResearchState) -> AgentRun[Critique]:
        """Spot-check what is worth checking, then score and route."""
        task = self.build_task(state)
        has_report = bool(task.report.strip())
        events: list[ResearchEvent] = [
            critique_started_event(
                iteration=task.iteration,
                max_iterations=task.max_iterations,
                claim_count=len(task.claims),
                has_report=has_report,
            )
        ]
        errors: list[ResearchError] = []

        async with self.tracker.agent_span(self.name) as span:
            if has_report:
                react = await self._spot_check(task)
            else:
                # Nothing to check against: spending tool budget here would
                # buy no information the review could use.
                react = ReActRun(agent_name=self.name, stop_reason="finished")
            errors.extend(react.errors)
            critique, reason, review_errors, provider_failed = await self.review(
                task, react
            )
            errors.extend(review_errors)
            if provider_failed:
                # Mirror the loop-level provider_error path so a caller
                # reading react.succeeded never sees "finished" over an
                # abort that happened during the review.
                react = react.model_copy(update={"stop_reason": "provider_error"})
            react = react.model_copy(update={"errors": errors})
            events.append(
                critique_completed_event(
                    critique,
                    react,
                    reason=reason,
                    iteration=task.iteration,
                    max_iterations=task.max_iterations,
                )
            )
            span.set_outputs(
                {
                    "agent_name": self.name,
                    "score": critique.score,
                    "gap_count": len(critique.gaps),
                    "should_continue": critique.should_continue,
                    "reason": reason,
                    "tool_calls": react.tool_calls,
                    "stop_reason": react.stop_reason,
                }
            )

        return AgentRun(
            agent_name=self.name,
            result=critique,
            react=react,
            errors=errors,
            state_update={
                **self.state_update(critique, react),
                "events": events,
            },
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents/test_critic.py -v && ruff check src/deep_research/agents/critic.py tests/test_agents/test_critic.py tests/research_fakes.py`
Expected: PASS, no lint findings

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/agents/critic.py tests/test_agents/test_critic.py \
  tests/research_fakes.py
git commit -m "feat: review the report and recommend a route"
```

---

### Task 7: Public Surface, The Synthesis Seam Test, And Documentation

**Files:**
- Modify: `src/deep_research/agents/__init__.py`
- Modify: `tests/test_imports.py`
- Create: `tests/test_agents/test_synthesis_seam.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: every public name Tasks 1–6 produced.
- Produces: `deep_research.agents` re-exports for `report`, `synthesizer`, and `critic`; the documented agent surface.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agents/test_synthesis_seam.py`:

```python
"""End-to-end seam: checked evidence becomes a report, then a critique.

Every other Synthesizer and Critic test builds state by hand, so nothing
exercises the real seam: that ``FactCheckerAgent`` writes claims whose URLs
the Synthesizer can cite, that the report it writes is what the Critic
reviews, and that the Critic's routing recommendation lands in the same
state a graph would read. This test runs all three agents in sequence,
merging state the way the orchestrator will.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deep_research.agents.critic import CriticAgent, CritiqueDraft
from deep_research.agents.fact_checker import (
    ClaimDraft,
    ClaimsDraft,
    ClaimVerdictDraft,
    FactCheckerAgent,
)
from deep_research.agents.report import REPORT_SECTIONS
from deep_research.agents.synthesizer import (
    ReportDraft,
    ReportSectionDraft,
    SynthesizerAgent,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Finding,
    ResearchState,
    ScoredSource,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import (
    FakeMemory,
    FakeSearchClient,
    critic_tools,
    fact_checker_tools,
    search_response,
    synthesizer_tools,
)

SEAM_SOURCE_URL = "https://example.test/qec"
SEAM_INDEPENDENT_URL = "https://third.test/review"
SEAM_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _pad(agent_name: str) -> ScratchpadMemory:
    return ScratchpadMemory(
        session_id="session-1", agent_name=agent_name, max_entries=20
    )


def _seam_state() -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        raw_findings=[
            Finding(
                content="Logical error rates fell below break-even in 2025.",
                source_url=SEAM_SOURCE_URL,
                source_title="QEC 2025",
                extracted_at=SEAM_EXTRACTED_AT,
                confidence=0.9,
                related_sub_topic="Alpha",
            )
        ],
        evaluated_sources=[
            ScoredSource(
                url=SEAM_SOURCE_URL,
                title="QEC 2025",
                authority_score=0.8,
                recency_score=0.7,
                relevance_score=0.9,
                corroboration_score=0.5,
                overall_score=0.76,
                rationale="Peer-reviewed and corroborated.",
            )
        ],
        max_iterations=3,
    )


@pytest.mark.asyncio
async def test_verified_claims_become_a_cited_report_the_critic_accepts(
    tracker: Tracker, tmp_path: Path
) -> None:
    state = _seam_state()
    memory = FakeMemory()

    checker = FactCheckerAgent(
        provider=ScriptedCompleter(
            decisions=[
                use_tool(
                    "Look for an independent review.",
                    "web_search",
                    '{"query": "qec break-even 2025"}',
                ),
                finish("Enough retrieved.", "An independent review agrees."),
            ],
            outputs=[
                ClaimsDraft(
                    claims=[
                        ClaimDraft(
                            text=(
                                "Logical error rates fell below break-even "
                                "in 2025."
                            ),
                            source_urls=[SEAM_SOURCE_URL],
                        )
                    ]
                ),
                ClaimVerdictDraft(
                    verdict="verified",
                    confidence=0.9,
                    evidence=["An independent review states the same figure."],
                    contradictions=[],
                ),
            ],
        ),
        tracker=tracker,
        scratchpad=_pad("fact_checker"),
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [search_response(url=SEAM_INDEPENDENT_URL)]
            ),
        ),
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=2),
    )
    synthesizer = SynthesizerAgent(
        provider=ScriptedCompleter(
            outputs=[
                ReportDraft(
                    executive_summary="Break-even was reached in 2025.",
                    sections=[
                        ReportSectionDraft(
                            title="Error correction",
                            body="Break-even was reached.",
                            source_urls=[SEAM_SOURCE_URL],
                        )
                    ],
                    uncertainty_notes="Vendor numbers remain unaudited.",
                )
            ]
        ),
        tracker=tracker,
        scratchpad=_pad("synthesizer"),
        tools=synthesizer_tools(tracker, output_root=tmp_path, memory=memory),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )
    critic = CriticAgent(
        provider=ScriptedCompleter(
            outputs=[
                CritiqueDraft(
                    score=8,
                    gaps=[],
                    unsupported_claims=[],
                    recommended_queries=[],
                    rationale="Well sourced for the question asked.",
                )
            ]
        ),
        tracker=tracker,
        scratchpad=_pad("critic"),
        tools=critic_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )

    async with tracker.session_span("session-1", state.original_question):
        state = merge_research_state(
            state, (await checker.run(state)).state_update
        )
        state = merge_research_state(
            state, (await synthesizer.run(state)).state_update
        )
        state = merge_research_state(
            state, (await critic.run(state)).state_update
        )

    assert state.report is not None
    for heading in REPORT_SECTIONS:
        assert heading in state.report
    # The claim the Fact Checker verified is cited against the source the
    # Researcher actually retrieved.
    assert "[1] (confidence 0.90)" in state.report
    assert f"1. QEC 2025 — {SEAM_SOURCE_URL}" in state.report
    assert (tmp_path / "report-session-1-0.md").is_file()
    assert [content for content, _ in memory.saved] == [
        "Logical error rates fell below break-even in 2025."
    ]

    assert state.critique is not None
    assert state.critique.should_continue is False
    assert state.critique.score == 8
    assert [event.event_type for event in state.events][-2:] == [
        "critic.critique.started",
        "critic.critique.completed",
    ]


@pytest.mark.asyncio
async def test_a_weak_pass_reports_its_limits_and_asks_for_another_cycle(
    tracker: Tracker, tmp_path: Path
) -> None:
    state = _seam_state().model_copy(
        update={"evaluated_sources": []}, deep=True
    )
    synthesizer = SynthesizerAgent(
        provider=ScriptedCompleter(
            outputs=[
                ReportDraft(
                    executive_summary="Little is settled.",
                    sections=[],
                    uncertainty_notes="",
                )
            ]
        ),
        tracker=tracker,
        scratchpad=_pad("synthesizer"),
        tools=synthesizer_tools(tracker, output_root=tmp_path),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )
    critic = CriticAgent(
        provider=ScriptedCompleter(
            outputs=[
                CritiqueDraft(
                    score=3,
                    gaps=["No source was scored."],
                    unsupported_claims=[],
                    recommended_queries=["qec break-even independent review"],
                    rationale="One unscored source carries everything.",
                )
            ]
        ),
        tracker=tracker,
        scratchpad=_pad("critic"),
        tools=critic_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )

    async with tracker.session_span("session-1", state.original_question):
        state = merge_research_state(
            state, (await synthesizer.run(state)).state_update
        )
        state = merge_research_state(
            state, (await critic.run(state)).state_update
        )

    assert state.report is not None
    assert "No source behind these findings was scored" in state.report
    assert "No claim was verified" in state.report
    assert state.critique is not None
    assert state.critique.should_continue is True
    assert state.critique.recommended_queries == [
        "qec break-even independent review"
    ]
```

Extend `tests/test_imports.py`:

- Add to the `from deep_research.agents import (...)` block in
  `test_agent_runtime_contracts_import_from_package`:

```python
        ACCEPTANCE_SCORE,
        CRITIC_NAME,
        REPORT_SECTIONS,
        SYNTHESIZER_NAME,
        Citation,
        CriticAgent,
        CritiqueDraft,
        CritiqueTask,
        ReportDraft,
        ReportSection,
        ReportSectionDraft,
        SynthesisTask,
        SynthesizedReport,
        SynthesizerAgent,
        assemble_report,
        build_citation_index,
        build_critique,
        build_report_sections,
        compose_report,
        limitation_reasons,
        render_claim_digest,
        report_filename,
        route_decision,
```

- Add `"critic"`, `"report"`, and `"synthesizer"` to the `submodules` list in
  `test_agent_submodule_public_names_all_reach_all` (keeping it alphabetical).
- Extend `test_concrete_agents_expose_their_identity_and_tools`:

```python
    assert SynthesizerAgent.name == "synthesizer"
    assert SynthesizerAgent.allowed_tools == ("write_document", "save_to_memory")
    assert CriticAgent.name == "critic"
    assert CriticAgent.allowed_tools == ("web_search", "query_memory")
```

with `CriticAgent` and `SynthesizerAgent` added to that test's import.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_imports.py tests/test_agents/test_synthesis_seam.py -v`
Expected: FAIL with `ImportError: cannot import name 'SynthesizerAgent' from 'deep_research.agents'` and, once that is fixed, `AssertionError: public submodule names missing from 'deep_research.agents.__all__'`

- [ ] **Step 3: Write minimal implementation**

In `src/deep_research/agents/__init__.py`, add three import blocks (isort order puts `critic` after `base`/`errors`/`events`, and `report` / `synthesizer` after `react` / `researcher`):

```python
from deep_research.agents.critic import (
    ACCEPTANCE_SCORE,
    CRITIC_CLAIM_DIGEST,
    CRITIC_EVIDENCE_CHARS,
    CRITIC_NAME,
    CRITIC_REPORT_CHARS,
    CRITIQUE_FALLBACK_REASONS,
    DEFAULT_MAX_NOTES,
    MAX_CRITIC_SCORE,
    MIN_CRITIC_SCORE,
    ROUTING_REASONS,
    CriticAgent,
    CritiqueDraft,
    CritiqueTask,
    build_critique,
    clamp_score,
    critique_completed_event,
    critique_messages,
    critique_provider_error,
    critique_started_event,
    fallback_critique,
    missing_report_error,
    normalize_notes,
    route_decision,
)
from deep_research.agents.report import (
    LIMITATION_REASONS,
    REPORT_SECTIONS,
    REPORT_TITLE_PREFIX,
    Citation,
    ReportSection,
    assemble_report,
    build_citation_index,
    citation_markers,
    render_citations,
    render_findings,
    render_limitations,
    render_source_appendix,
    render_uncertain_claims,
    render_verified_claims,
)
from deep_research.agents.synthesizer import (
    DEFAULT_MAX_MEMORY_FINDINGS,
    DEFAULT_MAX_SECTIONS,
    DEFAULT_MEMORY_CONFIDENCE,
    REPORT_SUMMARY_FALLBACK,
    SYNTHESIS_CLAIM_DIGEST,
    SYNTHESIS_FINDING_DIGEST,
    SYNTHESIZER_NAME,
    WRITE_FAILURE_REASONS,
    ReportDraft,
    ReportSectionDraft,
    SynthesisTask,
    SynthesizedReport,
    SynthesizerAgent,
    build_report_sections,
    compose_report,
    high_confidence_claims,
    invalid_section_error,
    limitation_reasons,
    memory_payload,
    memory_save_error,
    no_evidence_error,
    report_filename,
    report_messages,
    report_not_written_error,
    report_provider_error,
    render_revision_guidance,
    synthesis_completed_event,
    synthesis_started_event,
)
```

Add `render_claim_digest` to the existing `deep_research.agents.prompts` import block, alongside the four new prompt constants:

```python
from deep_research.agents.prompts import (
    CLAIM_EXTRACTION_INSTRUCTION,
    CLAIM_EXTRACTION_SYSTEM_PROMPT,
    CLAIM_VERIFICATION_INSTRUCTION,
    CLAIM_VERIFICATION_SYSTEM_PROMPT,
    CRITIC_SYSTEM_PROMPT,
    CRITIQUE_INSTRUCTION,
    FACT_CHECKER_SYSTEM_PROMPT,
    REACT_RESPONSE_CONTRACT,
    REPORT_INSTRUCTION,
    SOURCE_EVALUATOR_SYSTEM_PROMPT,
    SOURCE_SCORING_INSTRUCTION,
    SYNTHESIZER_SYSTEM_PROMPT,
    AgentTask,
    render_claim_digest,
    render_finding_digest,
    render_memory_guidance,
    render_react_messages,
    render_scratchpad,
    render_source_dossier,
    render_source_quality,
    render_tool_catalog,
)
```

Then add every one of those names to `__all__`, keeping its existing sort order
(SCREAMING_SNAKE constants first, then CapWords, then snake_case):

```python
    "ACCEPTANCE_SCORE",
    "CRITIC_CLAIM_DIGEST",
    "CRITIC_EVIDENCE_CHARS",
    "CRITIC_NAME",
    "CRITIC_REPORT_CHARS",
    "CRITIC_SYSTEM_PROMPT",
    "CRITIQUE_FALLBACK_REASONS",
    "CRITIQUE_INSTRUCTION",
    "DEFAULT_MAX_MEMORY_FINDINGS",
    "DEFAULT_MAX_NOTES",
    "DEFAULT_MAX_SECTIONS",
    "DEFAULT_MEMORY_CONFIDENCE",
    "LIMITATION_REASONS",
    "MAX_CRITIC_SCORE",
    "MIN_CRITIC_SCORE",
    "REPORT_INSTRUCTION",
    "REPORT_SECTIONS",
    "REPORT_SUMMARY_FALLBACK",
    "REPORT_TITLE_PREFIX",
    "ROUTING_REASONS",
    "SYNTHESIS_CLAIM_DIGEST",
    "SYNTHESIS_FINDING_DIGEST",
    "SYNTHESIZER_NAME",
    "SYNTHESIZER_SYSTEM_PROMPT",
    "WRITE_FAILURE_REASONS",
    "Citation",
    "CriticAgent",
    "CritiqueDraft",
    "CritiqueTask",
    "ReportDraft",
    "ReportSection",
    "ReportSectionDraft",
    "SynthesisTask",
    "SynthesizedReport",
    "SynthesizerAgent",
    "assemble_report",
    "build_citation_index",
    "build_critique",
    "build_report_sections",
    "citation_markers",
    "clamp_score",
    "compose_report",
    "critique_completed_event",
    "critique_messages",
    "critique_provider_error",
    "critique_started_event",
    "fallback_critique",
    "high_confidence_claims",
    "invalid_section_error",
    "limitation_reasons",
    "memory_payload",
    "memory_save_error",
    "missing_report_error",
    "no_evidence_error",
    "normalize_notes",
    "render_citations",
    "render_claim_digest",
    "render_findings",
    "render_limitations",
    "render_revision_guidance",
    "render_source_appendix",
    "render_uncertain_claims",
    "render_verified_claims",
    "report_filename",
    "report_messages",
    "report_not_written_error",
    "report_provider_error",
    "route_decision",
    "synthesis_completed_event",
    "synthesis_started_event",
```

- [ ] **Step 4: Run the whole suite**

Run: `pytest -q && ruff check src/ tests/`
Expected: PASS, no lint findings. If `test_agent_submodule_public_names_all_reach_all` still fails, it names the exact missing symbol — add it to both the import block and `__all__`.

- [ ] **Step 5: Document the two agents**

Append this section to `README.md`, immediately after the `## Source Evaluator And Fact Checker` section's event table:

```markdown
## Synthesizer And Critic

`SynthesizerAgent` turns `verified_claims`, `evaluated_sources`, and
`raw_findings` into the final Markdown report. The report's skeleton is
rendered locally by `agents.report` — all seven sections in
`REPORT_SECTIONS`, in order, whether or not they have content — so a report
always carries an executive summary, findings, verified claims, an
uncertainty section, limitations, numbered citations, and a source
appendix. Citations are numbered locally: evaluated sources first, then any
claim source not already numbered, and a URL the model attached that never
reached the evidence is dropped rather than cited. The model supplies only
prose. The composed Markdown is written through `write_document` and lands
in `state.report`; a failed write records a recoverable
`synthesizer_report_not_written` error and keeps the report in state
anyway. Verified claims at or above `DEFAULT_MEMORY_CONFIDENCE` (0.7) are
kept for future sessions through `save_to_memory`, capped at
`DEFAULT_MAX_MEMORY_FINDINGS` (10). This agent runs no ReAct loop: report
generation is one structured call, and both tool calls are deterministic
consequences of having produced a report.

Limitations are explicit and enumerated, never prose invented by a model:
recorded errors, an exhausted iteration budget, unscored or low-confidence
sources, no verified claim, a contradicted claim, and a failed report call
each add their own `LIMITATION_REASONS` line.

`CriticAgent` reviews that report. It may spot-check a suspected gap with
`web_search` or compare against prior sessions with `query_memory` in one
bounded ReAct loop, then asks for one structured review and computes the
routing decision itself. `route_decision` checks the iteration bound
first — `state.iteration >= state.max_iterations` always ends the run,
whatever the model said — then a missing report, then the score against
`ACCEPTANCE_SCORE` (7), then gaps, then unsupported claims. Every gap the
model lists counts as critical, because the prompt asks it to list a gap
only when closing it would materially change the answer. A provider failure
ends the run with the lowest score rather than buying another cycle; a
missing report buys one while budget remains. `Critique.should_continue` is
a recommendation record — nothing in the agent layer acts on it.

```python
from deep_research.agents import CriticAgent, SynthesizerAgent
from deep_research.utils.types import merge_research_state

async with tracker.session_span(session_id, state.original_question):
    synthesis = await synthesizer.run(state)
    state = merge_research_state(state, synthesis.state_update)

    review = await critic.run(state)
    state = merge_research_state(state, review.state_update)
```

| Event type | Emitted by | Key metadata |
| --- | --- | --- |
| `synthesizer.synthesis.started` | Synthesizer | `claim_count`, `source_count`, `finding_count`, `limitation_count` |
| `synthesizer.synthesis.completed` | Synthesizer | `section_count`, `citation_count`, `source_appendix_count`, `output_path`, `saved_findings`, `report_chars`, `limitations` |
| `critic.critique.started` | Critic | `iteration`, `max_iterations`, `claim_count`, `has_report` |
| `critic.critique.completed` | Critic | `score`, `gap_count`, `unsupported_claim_count`, `recommended_query_count`, `should_continue`, `reason`, `tool_calls` |
```

Update the phase line at the bottom of `README.md`:

```markdown
- Phase 3: Agents and LangGraph orchestration ← current (runtime and all six agents complete; the graph pending)
```

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/agents/__init__.py tests/test_imports.py \
  tests/test_agents/test_synthesis_seam.py README.md
git commit -m "docs: publish the synthesizer and critic agent surface"
```

---

## Self-Review

Run after the last task, against `docs/superpowers/specs/2026-07-25-10-synthesizer-and-critic-agents-design.md`:

**Spec coverage**

| Spec requirement | Where |
| --- | --- |
| `SynthesizerAgent` | Task 4 |
| `CriticAgent` | Task 6 |
| Markdown report schema and layout | Task 1 (`REPORT_SECTIONS`, `assemble_report`) |
| Critique schema and routing fields | Task 5 (`CritiqueDraft`, `route_decision`) |
| Tests with mocked provider and tools | Every task; `ScriptedCompleter` + offline tool doubles |
| Reads question, sub-topics, claims, sources, findings, errors | Tasks 4 and 6 `build_task` |
| Executive summary, sections, uncertainty, citations, appendix | Task 1 |
| Saves report through `write_document` | Task 4 `write_report` |
| Saves high-confidence findings to memory | Task 4 `save_findings` |
| Critique with score, gaps, unsupported claims, queries, routing, rationale | Task 5 `build_critique` |
| Acceptance threshold (score ≥ 7, no gaps, no unsupported claims) | Task 5 `route_decision` |
| Force stop at max iterations | Task 5 `route_decision`, first branch |
| Synthesizer observability (sections, citations, appendix count, path) | Task 4 `synthesis_completed_event` |
| Critic observability (score, gaps, unsupported, routing) | Task 6 `critique_completed_event` |
| Limitations on errors / weak evidence / max-iteration stop | Task 3 `limitation_reasons` |
| Report includes required sections | Task 1 + Task 7 seam test |
| Verified claims are cited | Task 1 `render_verified_claims` + seam test |
| Unverified claims separated from strong findings | Task 1 `render_uncertain_claims` |
| Critic identifies low-quality reports / ends acceptable ones / forces stop | Task 6 |
| Tests run without live provider calls | Global constraints |

**Non-goals held:** no graph routing (nothing consumes `should_continue`), no API or UI, no PDF export.

**Type consistency:** `ReportSection` is defined once in `report.py` and consumed by `synthesizer.py`; `ReportSectionDraft` is the provider-facing mirror and never reaches `assemble_report`. `SynthesizedReport.path` is the same string `write_document` returns. `Critique` is the shared type from `utils.types`, never redefined. Every limitation key used by `limitation_reasons` exists in `LIMITATION_REASONS`, and every routing key used by `build_critique` / `fallback_critique` exists in `ROUTING_REASONS`.

