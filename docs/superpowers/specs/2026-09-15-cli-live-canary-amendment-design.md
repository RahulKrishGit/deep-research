# CLI Live-Canary Amendment Design

**Status:** Approved by the user on 2026-09-15 after review of the failed Q1
canary. The Tavily credential exposed by the controller has been rotated and
updated by the user. This document records the design that governs the next
implementation wave; it does not authorize another live call.

## Objective

Turn the failed Q1 canary into durable evidence, make the next canary's request
limits observable and enforceable at the real network-attempt boundaries, and
collect enough safe scraper diagnostics to decide whether any agent tool budget
should change. The next paid run remains blocked until these changes pass the
complete offline gate, task review, a new predeclaration, and explicit user
authorization.

## Current evidence and ruling

The completed Q1 run at `7ef89ef057810464857413ea9078ff06b73814d9`
finished with terminal quality `partial`, critic score 4/10, and 4/6 topic
coverage. It published a 10,724-byte reader report and a 24,119-byte evidence
ledger, then correctly exited 4 under `--require-quality`. Integrity counters
were clean, but the run had 365 `web_search` tool invocations, 24
`web_scraper` invocations with 14 failures, and repeated tool-budget exhaustion.

That result is a failed live-release gate. Q2 and Q3 must not run, and Q1 must
not be repeated, until a new amendment and predeclaration supersede the spent
canary. Tool-budget exhaustion is a likely contributor, not a proven root
cause. The persisted evidence-ledger rows count 46 researcher, 18 fact-checker,
and 6 critic `agent_tool_budget_exhausted` events; any different count must name
its source and counting unit.

## Durable failed-validation record

Create a tracked record under `docs/superpowers/validation/`. It must include:

- candidate SHA and exact command;
- both live attempts and why the first failed;
- terminal quality, coverage, critic score, integrity and evidence counts;
- tool-invocation totals, explicitly labelled as tool invocations rather than
  provider HTTP requests;
- token totals, explicitly labelled as usage rather than dollar cost;
- reader, evidence-ledger, and log paths, byte sizes, and SHA-256 hashes;
- the missing DeepSeek/Tavily transport-request counts;
- the failed release criteria, skipped Q2/Q3, and unrun whole-report judge;
- controller errors and the credential-rotation disposition, without including
  a secret, raw prompt, raw provider output, or trace payload.

The record documents a failed validation; it is not withheld merely because
the gate failed. Ignored `output/` and `.superpowers/` files remain local audit
evidence and are not force-added.

## Runtime request-budget boundary

Add a small session-owned request-budget component and inject the same instance
through production runtime assembly into the chat provider and Tavily search
tool. Configuration supplies optional positive ceilings and a stop fraction;
absent ceilings preserve current behavior.

The DeepSeek/OpenAI counter increments immediately before every SDK transport
attempt, including repo-owned retry attempts and structured-output repair
requests. The Tavily counter increments immediately before every
`SearchClient.search` attempt, including retries. Agent tool invocations remain
a separate metric and must never be presented as transport requests.

For a ceiling `C` and stop fraction `F`, the effective attempt limit is
`floor(C * F)`. Attempts through that limit are permitted; the next attempt is
rejected before network I/O. For the former Q1 declaration this would allow at
most 216 DeepSeek attempts and 297 Tavily attempts. A typed, locally generated
limit error must halt the graph and produce a non-zero CLI result. It must not
be converted into a recoverable agent-tool failure.

The component exposes immutable snapshots containing only provider category,
attempt count, configured ceiling, effective limit, and cumulative reported
input/output tokens. Tokens update after a successful response because usage is
not knowable before the provider replies. Token reporting is observability, not
a pre-request dollar-cost guarantee.

In verbose mode the CLI emits bounded progress when an attempt is reserved,
when reported token totals change, and when a limit blocks an attempt. No
prompt, tool argument, URL, provider response, credential, or environment value
may appear in these messages. The terminal summary uses the same snapshot as
the enforcement path.

## Scraper failure diagnostics

Do not change scraper retry policy or timeout based on the failed report. The
LLM timeout is a different setting and is not evidence of scraper timeouts.

Preserve safe failure metadata already available in `ToolResult.error` when an
agent creates `agent_tool_failed`: tool error type, retry-attempt count, and
HTTP status code or content type when present. Render those categorical fields
in verbose CLI warnings. Do not render exception strings, page content, query
text, complete URLs, prompts, or provider responses. Add offline tests covering
timeout, retry-exhaustion, HTTP status, robots denial, and unsupported content
type so the next canary can classify failures without a trace inspection.

## Planning diagnostics

When a `PlanningError` halts the graph, copy its locally generated `problems`
tuple into the graph error details. Preserve the existing enumerated public
message and exception type. Tests must prove provider text and exception
strings are still excluded.

## Tool-budget policy

Do not raise the global tool budget in this amendment's first implementation
wave. Request-attempt enforcement and scraper classification must land first.
After their controlled offline fixtures reveal the failure mechanism, a later
configuration-only amendment may add per-agent or per-phase budgets. Any such
change requires evidence, its own review, and a new canary declaration.

Keep production reasoning effort at `high` for all six agents during this
amendment so the next experiment does not vary reasoning and budgets together.

## Verification and release sequence

Every production change follows test-first RED/GREEN evidence. Each task runs
focused and neighbouring offline tests, Ruff on touched Python paths,
`git diff --check`, and a clean-status check before commit. The complete offline
suite, compilation check, controlled whole-report campaign, and exact-head
review run before a new predeclaration.

After each task is pushed, it receives the required scoped Sol/High review.
Critical and Important findings enter the bounded Luna/max fix loop. A new Q1
canary is considered only after the amendment is review-clean. Q2 and Q3 remain
blocked until a new Q1 reaches `accepted`, coverage at least 0.80 with every
topic accounted for, all integrity/provenance gates, and whole-report judge at
least 0.80.

