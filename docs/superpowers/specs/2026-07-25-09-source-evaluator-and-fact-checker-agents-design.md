# Source Evaluator And Fact Checker Agents Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Add evidence quality controls before report synthesis by scoring sources and verifying claims.

## Scope

This feature adds:

- `SourceEvaluatorAgent`
- `FactCheckerAgent`
- Source scoring prompts and schemas
- Claim extraction and verification prompts and schemas
- Tests with mocked tools and provider responses

## Non-Goals

- No report synthesis.
- No final Critic routing.
- No external fact database integration.

## Design

Source Evaluator:

- Reads `raw_findings`.
- Groups findings by source URL.
- Queries known source reputation from memory.
- Scores authority, recency, relevance, and corroboration.
- Writes `ScoredSource` records.
- Flags weak or low-confidence sources.

Fact Checker:

- Reads `raw_findings` and `evaluated_sources`.
- Extracts major factual claims.
- Cross-references claims against independent sources using search, scrape, document read, and memory query.
- Writes `Claim` records with verdicts: verified, unverified, contradicted, or insufficient evidence.

## Observability

Source Evaluator emits:

- Source count.
- Average score.
- Low-confidence count.

Fact Checker emits:

- Claim count.
- Verdict counts.
- Contradiction count.
- Tool calls per claim.

## Error Handling

If source reputation lookup fails, Source Evaluator continues with direct scoring.

If a claim cannot be verified due to tool failures or lack of independent sources, Fact Checker marks it as insufficient evidence instead of inventing confidence.

## Testing

Tests should cover:

- Source grouping by URL.
- Score bounds and rationale.
- Reputation cache usage.
- Claim extraction.
- Claim verification outcomes.
- Contradiction handling.
- Insufficient evidence handling.

## Acceptance Criteria

- Every source used by findings gets a score or explicit low-confidence flag.
- Major claims receive verification verdicts.
- Weak sources and unsupported claims are visible to Synthesizer and Critic.
- Tests run without live provider or network calls.
