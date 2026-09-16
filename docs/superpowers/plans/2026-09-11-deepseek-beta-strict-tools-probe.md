# DeepSeek `/beta` Strict Tools Probe — Pre-Registered Plan

**Goal:** Determine whether DeepSeek's `/beta` strict tool path permits **forced** tool calls while `thinking` is enabled, which the standard endpoint rejects with HTTP `400`.

**Why this is first.** The forced-call hypothesis failed on the standard endpoint: `function` and `required` both returned `400` whenever thinking was enabled (fix-log section 78). `/beta` with `strict` tools is the one remaining mechanism that could deliver both thinking and API-enforced JSON. If it fails, the fallback is prompt-level JSON conformance, which needs no API capability at all.

**Scope:** 3 authorized requests. DeepSeek only. No LangSmith, no search, no application tools, no evaluation harness, no repetition.

**References:** fix-log section 78 (the blocked capability gate), `docs/superpowers/plans/2026-09-11-tool-call-structured-transport.md`.

## What is uncertain, and why the probe is cheap

Third-party sources agree that DeepSeek exposes a strict tool-schema path — notably the langchain PR "feat(deepseek): support `strict` beta structured output" (#32727) — but they disagree on the mechanism: some describe a `strict` key inside the function definition, others a `/beta` base URL. **This plan does not guess.** The probe tests the mechanism and reports what the endpoint accepts.

## The three checks

All three send one short user message and one `ProbeAnswer` tool whose `parameters` derive from a Pydantic model with `additionalProperties: false`.

| # | Endpoint | Thinking | `tool_choice` | Tool `strict` | Purpose |
| --- | --- | --- | --- | --- | --- |
| 1 | `https://api.deepseek.com` | enabled | function-specific | absent | **Baseline.** Expected `400`. Confirms the probe reproduces the known failure rather than testing a different request shape. |
| 2 | `https://api.deepseek.com/beta` | enabled | function-specific | `true` | **The candidate.** This is the whole question. |
| 3 | `https://api.deepseek.com/beta` | enabled | function-specific | absent | **Isolation.** Separates "beta base URL" from "strict flag". If 3 fails and 2 passes, `strict` is the active ingredient; if both pass, the base URL is. |

A fourth variant (thinking disabled) is deliberately **not** included: a pass with thinking off proves nothing about production, which runs with thinking `enabled`. The standard-endpoint disabled case is already recorded in section 78.

Recorded per check, with no provider body retained: HTTP status, error class, `finish_reason`, tool-call count, whether exactly one call named `ProbeAnswer` came back, and whether its arguments passed `ProbeAnswer.model_validate_json`.

## Decision rule

| Observation | Decision |
| --- | --- |
| Check 2 succeeds: HTTP 200, `finish_reason=tool_calls`, exactly one `ProbeAnswer` call, arguments locally valid | **`BETA_STRICT_WORKS`.** Optionally note whether check 3 also passed, to name the active ingredient. Then write a fresh plan to adopt the strict tool transport for target structured output, with its own capability canary. |
| Check 2 returns `400`, or a non-tool finish, or zero/multiple/unnamed calls, or invalid arguments | **`BETA_STRICT_UNAVAILABLE`.** Abandon the enforced-JSON family entirely and proceed to prompt-level JSON conformance, which requires no provider capability. |
| Any check returns `401/403`, timeout, connection failure, or `429/5xx` | **`INCONCLUSIVE`.** Preserve everything, report the transport class, and obtain fresh authorization for a retry. Do not classify as unsupported. |
| Check 1 does **not** reproduce the expected `400` | **Stop and investigate.** It means the probe is not sending the request shape that the gate tested, so checks 2 and 3 would not be comparable. |

## Constraints

- No production file changes. The probe lives under `output/transport-probes/<uuid>/` and is preserved, untracked, for reproducibility.
- No change to budgets, retry policy, thinking mode, effort, thresholds, frozen cases, rubrics, or weights.
- No raw response body, prompt text, secret, or reasoning content recorded anywhere.
- Authorization is for **3 requests** on this candidate and none beyond it.

## Next step after this probe

Exactly one of two paths, chosen by the rule above and recorded in the fix log:

1. **`BETA_STRICT_WORKS`** → a new plan adopting the strict tool transport for the six target agents, reusing the reviewed plan's Task 2–4 structure (which remains valid as *design* even though it is dormant against the standard endpoint), plus its own capability canary.
2. **`BETA_STRICT_UNAVAILABLE`** → a prompt-level JSON conformance plan for the Critic review call: name JSON explicitly, supply the output skeleton, and supply one filled example; then a measured comparison of failure rate before and after, with enough repetitions to separate a real improvement from luck.
