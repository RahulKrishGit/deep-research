# Synthesizer And Critic Agents Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Produce the final report and decide whether the research needs another refinement cycle.

## Scope

This feature adds:

- `SynthesizerAgent`
- `CriticAgent`
- Markdown report schema and layout
- Critique schema and routing fields
- Tests with mocked provider and tools

## Non-Goals

- No graph routing implementation.
- No API or UI rendering.
- No PDF export.

## Design

Synthesizer:

- Reads original question, sub-topics, verified claims, evaluated sources, raw findings, and errors.
- Produces a Markdown report with executive summary, sectioned findings, uncertainty notes, citations, and source appendix.
- Saves the report through `write_document`.
- Saves high-confidence final findings to memory.

Critic:

- Reads the report, original question, sub-topics, evaluated sources, verified claims, and errors.
- Produces a `Critique` with score, gaps, unsupported claims, recommended queries, `should_continue`, and rationale.
- Applies the acceptance threshold: score at least 7, no critical gaps, and no major unsupported claims.
- Forces stop when max iterations have been reached.

## Observability

Synthesizer emits:

- Report sections generated.
- Citation count.
- Source appendix count.
- Output path.

Critic emits:

- Critic score.
- Gap count.
- Unsupported claim count.
- Continue/end routing recommendation.

## Error Handling

Synthesizer must include a limitations section when inputs contain errors, insufficient evidence, or max-iteration stop conditions.

Critic must not continue forever. It sets `should_continue=false` when max iterations are reached, even if gaps remain.

## Testing

Tests should cover:

- Report includes required sections.
- Verified claims are cited.
- Unverified claims are separated from strong findings.
- Critic identifies low-quality reports.
- Critic ends acceptable reports.
- Critic forces stop at max iterations.

## Acceptance Criteria

- A Markdown report can be generated from verified claims and evaluated sources.
- Critic output is structured enough for LangGraph routing.
- Limitations are explicit when evidence is weak.
- Tests run without live provider calls.
