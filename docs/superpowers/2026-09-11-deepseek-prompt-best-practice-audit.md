# DeepSeek Prompting Best-Practice Audit — Critic Review Call

Date: 2026-09-11. Candidate: `2eb63a2` on `codex/cross-agent-planner-fix-parity`.
Measured against the live-tested prompt and the effective request settings.

## Sources

DeepSeek publishes **no dedicated "prompting guide"**. Prompting guidance is distributed across three feature guides, all retrieved 2026-09-11:

- [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) — the only page with explicit prompt-content requirements.
- [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/) — parameter interactions and tool-call replay rules.
- [Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/) — tool definition shape and the `strict` beta path.

Anything below that is not traceable to one of those three is labelled **inference**, not vendor guidance.

## Part 1 — Documented practices, and our compliance

### JSON Output guide

| # | Vendor requirement | Status |
| --- | --- | --- |
| J1 | `response_format` set to `{"type": "json_object"}` | **N/A.** We call the Responses endpoint with `text.format=json_schema`, a stricter mechanism than `json_object`. Recorded as deliberately different, not as non-compliant. |
| J2 | Include the word "json" in the system or user prompt | ✅ Present in both the agent system prompt's response contract and the `## Reply format` section. |
| J3 | **Provide an example of the desired JSON format** | ⚠️ **Partial.** A keyless *skeleton* is present. There is no filled example. See Part 3. |
| J4 | Set `max_tokens` reasonably to prevent mid-JSON truncation | ✅ `32768`, generous, and truncation is separately excluded by the fact that a `length` finish reason raises `ProviderOutputLimitError` before validation. |
| J5 | The API may occasionally return empty content; modifying the prompt can mitigate | ⚠️ **Violation, measured.** Section 80 measured zero empty bodies in 20 guarded requests, so this mode is not what we are hitting. The earlier absence of a non-empty guard on the Responses path was a real local defect and is now fixed. |

### Thinking Mode guide

| # | Vendor statement | Status |
| --- | --- | --- |
| T1 | Thinking mode is **enabled by default**; default effort is `high` | ✅ Effective settings are `thinking_mode: enabled`, effort `high`. |
| T2 | Set the toggle via `extra_body={"thinking": {...}}` on the OpenAI SDK | ✅ Exactly what the provider sends. |
| T3 | Effort control via `reasoning_effort` | ✅ Sent as `high`. |
| T4 | Thinking mode **does not support `temperature`**; setting it is silently ignored | ✅ **Already correct** — verified that `temperature` is *not* present in the outgoing request at all. The `0.7` in `config.yaml` is inert, not harmful. |
| T5 | `top_p` has a lower bound of `0.95` in thinking mode; `1.0` and ignored in non-thinking mode | ✅ Not set by us; no conflict. |
| T6 | `reasoning_content` is returned alongside `content` | ⚠️ **Not observable.** Our provider reads only `content`; it never captures `reasoning_content`, so we cannot audit the reasoning channel or measure its token share. |
| T7 | If the request carries `tools`, `reasoning_content` from **all** prior turns must be passed back or the API returns `400` | ✅ **N/A and safe by construction.** The review call sends no `tools`. The ReAct path sends tools but does not enable thinking. The two are never combined, so the replay obligation never arises. |

### Tool Calls guide

| # | Vendor statement | Status |
| --- | --- | --- |
| C1 | Tool definitions carry `name`, `description`, and a `parameters` JSON schema; describe each parameter | ⚠️ **Only in the ReAct path.** Our tool descriptors do carry description and schema. The review call offers no tools at all. |
| C2 | `strict` mode requires `base_url="https://api.deepseek.com/beta"`, `strict: true` on **every** function, and a server-validated schema subset | ❌ **Unavailable in practice.** Probed and recorded in section 79: with `thinking` enabled, both `/beta` with `strict: true` and `/beta` without it returned `400` naming `tool_choice`. |

### Model naming (found incidentally)

The first-call guide states the current model name is **`deepseek-flash`**, and that the legacy aliases **`deepseek-v4-flash`** and `deepseek-v4-flash-vision-exp` are "still accepted, but the corresponding models have been **retired**; their requests are served by the **DeepSeek-V4.1-Flash** model and billed at the Flash price."

We request `deepseek-v4-flash` everywhere. Requests still succeed, so this is not breaking, but our recorded model identity is a retired alias and the served model is V4.1-Flash. This is a provenance issue for every fingerprint and comparison in this campaign.

## Part 2 — What is present and correct

- A system prompt with a clear role, written as a `developer` message, which the OpenAI-format translation maps to `system`. Matches the documented multi-message shape.
- Explicit, enumerated output fields with per-field instructions, including length guidance for the rationale and a rule for when to return empty lists.
- Explicit anti-fabrication instructions ("Do not invent a gap to look thorough").
- JSON named in words, plus a field skeleton. Added in `9ea5a88`.
- A generous output budget and no unsupported parameters sent. Thinking mode parameters are exactly as documented.
- Deterministic local validation via Pydantic, with a bounded typed diagnostic and a single repair — the guide's requirement that schema correctness be enforced locally is met by construction.

## Part 3 — What is missing or questionable

### Structural — these plausibly affect JSON conformance

1. **The `## Reply format` example is a skeleton, not an example.** The vendor wording is "provide an example of the desired JSON format", and its own sample shows a fully filled object with real-looking values. Ours is `{"score": <integer 1-10>, ...}`. Angle-bracket placeholders are not valid JSON, so the model is shown something that is neither a schema nor an instance.
2. **Three overlapping format instructions compete.** The request carries (a) `## Response contract` prose in the user message, (b) the `## Reply format` skeleton, and (c) a provider-appended system message restating the full JSON Schema. All three say "JSON" in different ways. Redundancy is not automatically harmful, but three competing renditions of the same contract is a plausible source of drift.
3. **The JSON demand sits in the middle of a prose document.** The user message is ten `##` sections of narrative, and the JSON request is the last two — the format instruction is surrounded by Markdown prose, and the report itself is embedded under a `##` heading, which collides with the prompt's own section grammar.
4. **JSON-mode grammar is already load-bearing.** `render_evidence` is JSON-shaped, and the `## Claim verdicts` and `## Source quality` sections are terse data lines. Whether this helps or dilutes is unmeasured.

### Context-shape

5. **The report is inlined verbatim, complete with its own headings**, inside `## Report under review`. A 6,000-character Markdown document nested inside a Markdown-structured prompt is an unusual shape for a JSON-output request.
6. **Several sections carry little decision value.** `## Recorded problems` is nearly always "0 error(s)". `## Spot checks` was `(no evidence retrieved)` in the audited render. Both add tokens and prose without informing the score.
7. **The JSON Schema's own `description` fields become prompt text.** `_json_instruction` serializes `model_json_schema()` verbatim, so the developer-facing Python docstring — including reStructuredText backticks like ``` ``score`` ``` — is sent to the model inside the trailing system message.

### Unmeasured, and therefore unknown

8. **Whether reasoning capacity is the real constraint.** Reasoning-mode guidance is minimal, and we do not capture `reasoning_content`, so we cannot see how much of the effort budget goes to reasoning versus the final object. This is the leading remaining hypothesis for the measured 22–27% failure, and this audit cannot confirm it.
9. **Effort `high` versus `max`.** The Critic review runs at the configured `high`. The claim in the fix log that the live Critic evaluation uses `max` applies to the *evaluation* effort resolution, not to this prompt path. The two have not been reconciled, so any future reasoning-effort experiment must state which it is changing.

## Part 4 — Verdict

The prompt is **compliant with every explicit prompt-content requirement DeepSeek publishes**, with one unambiguous gap: the "example of the desired JSON format" is a skeleton rather than an example. That gap is worth closing, but it is unlikely to be the whole story, because a 30-request A/B of the JSON-naming and skeleton change showed **no improvement** (`9ea5a88`, section 81).

Everything else the audit found is either already correct (thinking parameters, temperature, budget), unavailable (strict mode under thinking), safe by construction (tool-call replay), or a provenance issue (retired model alias).

**The strongest conclusion is a negative one:** the prompt now says everything the vendor asks it to say, and the defect persists. Combined with the earlier exclusions — not truncation, not empty content, not transport, not forced-tool availability — the evidence continues to point away from *what the prompt says* and toward *what the model does with its output budget*. The two concrete next steps that follow are:

1. Classify the shape of the failing responses (fenced, truncated, prose-prefixed, invalid-escape) without storing content, to identify the mode instead of guessing.
2. Test reasoning effort on the review call, once it is established which value is actually in force.

No provider call is authorized by this document.
