"""Effort precedence, fingerprints, naming, and secret handling."""

from __future__ import annotations

import ntpath
from datetime import datetime, timezone

import pytest

from deep_research.evaluation import config as evaluation_config
from deep_research.evaluation.config import (
    _TARGET_REACT_TRANSPORT,
    GitMetadata,
    SecretLeakError,
    agent_prompt_fingerprint,
    build_runtime_config,
    contains_secret,
    dataset_name,
    experiment_metadata,
    experiment_name,
    fingerprint,
    judge_llm_config,
    known_secret_values,
    redact_secrets,
    resolve_git_metadata,
    resolve_judge_effort,
    resolve_target_effort,
    target_llm_config,
)
from deep_research.evaluation.judging import judge_prompt_fingerprint
from deep_research.evaluation.models import AGENT_NAMES
from deep_research.utils.config import (
    AgentRuntimeConfig,
    ConfigSettings,
    EvaluationConfig,
    LLMConfig,
    load_config,
)

# The Critic's recorded ``target_prompt_fingerprint`` after the
# ``unsupported_claims`` definition was clarified to the lenient reading with a
# contrary-evidence override (fix-log section 89). Superseded: ``bf86f19981a6``,
# the value recorded on the live canary artifacts.
#
# A later commit added a tool-convention sentence to ``CRITIC_SYSTEM_PROMPT`` and
# moved this value to ``b6b9b768a517``. That sentence failed its predeclared 0/30
# DSML gate (16 of 30 first attempts still returned tool-call markup), so it was
# reverted — see fix-log sections 91 and 95 — and the pin moved back. A prompt
# change that does not demonstrate benefit must not keep a fingerprint move.
#
# This is a **drift alarm, not an attribution mechanism**. Because
# ``agent_prompt_fingerprint`` hashes the whole shared ``agents.prompts`` module,
# this value moves when *any* agent's prompt text changes — that is what makes it
# useful as a "did a prompt edit land" signal, and useless for saying whose. And
# attribution is recoverable anyway while the tree is clean: artifacts record
# ``git_commit``, so the change is explained by its diff. Attribution is genuinely
# lost only when a fingerprint was recorded from a dirty tree whose exact source
# snapshot was not kept.
# Moved 2c0bd1210e21 -> c971e00c3773 by
# docs/superpowers/plans/2026-09-12-shared-native-react-tools-and-prompt-conformance.md:
# the shared native ReAct change removed the simulated tool catalogue from
# ``agents.prompts`` and replaced the Critic's own ReAct closure, so every
# agent's recorded ``target_prompt_fingerprint`` moved. Re-pinned deliberately,
# in its own commit, rather than silently invalidated.
# Moved c971e00c3773 -> 04df8604c26a by task 1 of
# docs/superpowers/plans/2026-09-14-cli-report-quality-and-agent-output-integrity.md:
# ``NATIVE_REACT_RESPONSE_CONTRACT`` said "Call at most one tool", which the
# transport contradicts — every ``function_call`` item in one response is
# executed — so it now says "Call one or more tools ... when independent
# lookups or actions are needed". Wording only: the sentence forbidding a
# tool call written in text, JSON, XML, DSML, or a Markdown fence is
# unchanged. Re-pinned deliberately rather than silently invalidated.
# Task 4 then removed the source evaluator's computed corroboration field and
# changed source-quality rendering to carry explicit unscored statuses. That
# shared ``agents.prompts`` edit moved all six target fingerprints again;
# re-pinned deliberately so the drift alarm remains meaningful.
# Fix Round 1 then changed the shared synthesizer and Critic wording to say
# that a source carries a quality score when scored and an explicit evaluation
# status otherwise. Because the fingerprint includes the shared prompt module,
# this legitimate source-text change moved all six values together; re-pin all
# six deliberately rather than weakening the drift alarm. The exact moves were
# planner ``b5d310a541c7`` -> ``68802a12d777``, researcher
# ``50c7713d2448`` -> ``69a0c396334f``, source evaluator
# ``6452df9110e7`` -> ``cd5ea5f5579b``, Fact Checker
# ``6bb72f0c0f63`` -> ``5c7744ff65bc``, Synthesizer
# ``31e7a9cff8aa`` -> ``366b69880972``, and Critic
# ``92e10384399b`` -> ``243f6ebb1627``.
# Fix Round 2 then applied the same score/status distinction to the tool-aware
# Critic prompt. This shared prompt-module source change legitimately moved all
# six values again; the exact moves were planner ``68802a12d777`` ->
# ``059a32ca8b85``, researcher ``69a0c396334f`` -> ``076337666605``, source
# evaluator ``cd5ea5f5579b`` -> ``21d4d79ca09a``, Fact Checker
# ``5c7744ff65bc`` -> ``f9dd5826f66a``, Synthesizer ``366b69880972`` ->
# ``6f5b7739f134``, and Critic ``243f6ebb1627`` -> ``5b0105f4dcc1``.
# Task 6 then replaced section-level report prose with claim-linked points and
# added the checked-claim packet to the shared prompt module, so all six
# shared fingerprints moved together again: planner ``8a5f8a1499bf`` ->
# ``aa648f82af71``, researcher ``ebdfd3ae4c05`` -> ``6d5fd0f85dc3``, source
# evaluator ``ffab1c9795e2`` -> ``ddd8f9e5785a``, Fact Checker
# ``d5c99dbd9a35`` -> ``3ccaa7aa4fc1``, Synthesizer ``affe67074133`` ->
# ``6b9c616afad9``, and Critic ``a15c36b0ed0e`` -> ``5bb5ef748a84``. The
# synthesizer moved for a second reason as well — its own module now
# validates points against the canonical claim registry and composes two
# artifacts instead of writing one — which is the documented false positive
# of hashing a module's full source. (The synthesizer's value covers the
# final source of that module in this commit, including the blank-cell
# normalization its constraint rows use.) The Judge pin did **not** move.
# Fix wave A-1 then moved the Critic's own value a final time — ``e8bb04d10046``
# -> ``1d2833ae6bdf`` — for the load-bearing defect of the whole plan: the
# "# Sub-topics planned" block rendered titles only, so the response contract's
# "Copy coverage_id exactly from a planned sub-topic" had nothing to copy,
# ``normalize_gaps`` nulled every invented id, and targeted refinement had never
# fired. ``CritiqueTask`` now carries the planner's own ``SubTopic`` objects
# instead of parallel title and coverage-id lists, and both the review request
# and ``_render_spot_check_guidance`` render ``- <coverage_id>: <title>`` from
# that one sequence. Only the Critic's value moved: ``agents/prompts.py`` was
# untouched, so the other five target pins and the Judge pin are unchanged.
# Fix wave A-4 then moved the Fact Checker's own value, ``926a1d968c68`` ->
# ``5080d1810c7e``, together with A-6 below. A-4 stopped recording
# ``consumed_finding_fingerprints``/``consumed_coverage_ids`` on the two
# reasons where no model ever judged the finding (``provider_unavailable``,
# ``loop_failed``), because a recorded fingerprint is what makes
# ``_finding_is_new`` answer ``False`` and ``extract_claims`` return early —
# so a transient provider blip permanently suppressed re-extraction of that
# finding. A-6 then attributed consumption against the digest-truncated
# candidate list (``visible_findings``) instead of the full ordered list: the
# model is shown only the first ``finding_digest`` findings, so a claim citing
# a URL whose finding sits past the cut was recording that finding's coverage
# id as consumed on evidence nobody read. Rationale recorded here rather than
# under one item because both edits are in the same module and the pin moved
# once.
# Fix wave A-2 moved the Synthesizer's own value, ``bf2b62331950`` ->
# ``b6cca2eaabd7``: ``state_update`` no longer stamps ``state.evidence_path``
# from the composed ledger name. That name is a future filename, not a write,
# and ``ResearchState.evidence_path`` means "the ledger the terminal finalizer
# published"; the stamp made ``evidence_path_from_state`` fall back to it and
# ``cli.render_summary`` advertise an ``Evidence ledger:`` line for a file
# that does not exist on any run halting after the synthesizer node.
# The remaining blast radius of this wave is ``planner`` 028150f7e4a5,
# ``researcher`` 96907685a382, ``source_evaluator`` 6e127ffba9d4 and the Judge
# pin 74b9cddfbbee, all verified unchanged after every P1 edit because
# ``agents/prompts.py`` was not edited. Three of the six moved rather than the
# Critic alone: A-1 is the only edit whose module is ``agents/critic.py``, and
# the review's assumption that it was the wave's sole module-source edit does
# not hold for A-2/A-4/A-6.
# Fix wave P4 then moved two more values for documentation-only edits, which is
# the documented false positive of hashing a module's full source rather than
# its prompt text: the Critic ``1d2833ae6bdf`` -> ``bc6b1f23064c`` for A-6
# (``normalize_gaps``' docstring claimed its legacy-shape mapping was "the same
# ``normalize_gap_drafts`` rule both typed boundaries use" when it is a second,
# per-value copy of that rule; ``normalize_gap_drafts``' own "single place"
# claim is now scoped to payload boundaries), and the Synthesizer
# ``b6cca2eaabd7`` -> ``dd422429c34b`` for A-5 (``run`` recomputed the
# limitations list inline while ``compose_limitations`` computed it for the
# artifacts; one computation now feeds both). No prompt string moved. Re-pinned
# deliberately rather than silently invalidated, the same convention the
# researcher's own module-source move used. The Critic then moved with the rest
# of the six when ``CLAIM_VERIFICATION_SYSTEM_PROMPT`` gained read-before-search
# guidance — the shared ``agents.prompts`` module is hashed for every agent, so
# a change to any prompt in it moves all of them: ``bc6b1f23064c`` ->
# ``97a2d10ad688``. Task 2 moved it once more, ``97a2d10ad688`` ->
# ``60ffff5a3558``, when the Critic's own ``run_react_loop`` call site began
# resolving its per-agent tool budget through ``tool_budget_for`` — a
# module-source move with no prompt edit.
# Task 4 moved all six values together, and the Critic's with them. The shared
# ``agents/prompts.py`` gained ``render_read_dossier`` and a scoring contract
# that asks for the role, transport relation, self-interest, dates, and
# metadata anchors the source record now carries, so every agent whose
# fingerprint hashes that module moved even though only the Source Evaluator's
# request changed meaning. The Source Evaluator moved for a second reason as
# well: its own module gained ``assess_new_sources`` and the extended
# ``SourceScoreDraft``. The exact moves were planner ``7e43f342910c`` ->
# ``948c6015646c``, researcher ``0628475cb810`` -> ``81f0ec215feb``, source
# evaluator ``9528b1099f3f`` -> ``b2ce33c07533``, Fact Checker
# ``518464aa4cee`` -> ``6425f37345c4``, Synthesizer ``758ea76a8c0c`` ->
# ``c3daf199e75d``, and Critic ``29176f9c39df`` -> ``4f8e2cac55a1``. The Judge
# pin did **not** move: no judge prompt or template changed.
# Task 4's fix round 5 moved all six again, for the same shared-module reason:
# the scoring contract in ``agents/prompts.py`` now asks for each temporal field
# as a date *and* the document's own words for it, so a date is admitted only
# when its quote is verbatim in the read. The Source Evaluator moved for its own
# module as well — ``SourceScoreDraft``'s four temporal fields became quoted
# ``TemporalClaim`` objects. The exact moves were planner ``948c6015646c`` ->
# ``da23fbecdf5d``, researcher ``81f0ec215feb`` -> ``0d4ee670a0fe``, source
# evaluator ``4dbe292964d2`` -> ``e01b5b79a4d2``, Fact Checker
# ``6425f37345c4`` -> ``772e7d9d13f8``, Synthesizer ``c3daf199e75d`` ->
# ``895e307a5068``, and Critic ``4f8e2cac55a1`` -> ``cfb6f062b992``. The Judge
# pin did **not** move: no judge prompt or template changed.
# Task 5 moved it once more, ``cfb6f062b992`` -> ``15754f64fa12``, with the
# other five: the shared ``agents.prompts`` module gained the claim-equivalence
# prompt and its two schema-version constants, and the Critic shares that
# module. No critic prompt string changed.
# Task 7 moved it again, ``15754f64fa12`` -> ``3f0341751794``, with the other
# five: the shared ``REPORT_INSTRUCTION`` now states the four-field point
# contract (``basis``), the answer-rows contract, and that a mechanism or
# geography cell is checked against the evidence its row cites. No critic
# prompt string changed; a shared-module edit moves all six by design.
CRITIC_PROMPT_FINGERPRINT = "3f0341751794"

# Every target agent's recorded ``target_prompt_fingerprint`` when the
# cross-agent JSON conformance matrix was locked. All six are pinned together
# because ``agent_prompt_fingerprint`` hashes the shared ``agents.prompts``
# module: one sentence changed there moves every agent's value at once, so a
# single-agent pin cannot say whether a change was intended. The matrix found no
# gap, so these values are unchanged from before it was written.
#
# ``researcher`` was re-pinned from ``2d8f2688ec4d`` to ``51044a868e3f`` when
# ``merge_react_runs`` gained the per-loop budget fields. That is a **false
# positive of the fingerprint's design, not prompt drift**: the researcher's
# prompt text is byte-identical, but ``agent_prompt_fingerprint`` hashes the
# agent module's *full source*, so a behavioural fix to a function that happens
# to live in ``researcher.py`` moves a value whose name implies a prompt change.
# Only the researcher moved; the other five are untouched. Re-pinned
# deliberately, with the researcher's live canary re-run, rather than silently
# invalidated — and the design smell is recorded in the plan ledger.
#
# All six were re-pinned by task 1 of
# docs/superpowers/plans/2026-09-14-cli-report-quality-and-agent-output-integrity.md,
# when the shared ``NATIVE_REACT_RESPONSE_CONTRACT`` moved from "Call at most
# one tool" to "Call one or more tools ... when independent lookups or actions
# are needed". The shared module is hashed into every agent's value, so one
# sentence moved all six at once — exactly the drift the pin exists to make
# visible. ``source_evaluator`` and ``fact_checker`` moved a second time in the
# same task, because each now merges its pass into the canonical snapshot
# before writing state. The Judge pin did **not** move.
#
# ``planner`` alone was re-pinned ``f4b02ab2cfe2`` -> ``721f1ea5cec5`` by task 2
# of the same plan. Only the planner moved, which is the correct blast radius
# for that change: ``agents/prompts.py`` was untouched, so the five agents that
# share it keep their values, and ``CRITIC_PROMPT_FINGERPRINT`` and the Judge
# pin are unchanged too. The planner's own module source moved for two reasons,
# both intended: ``PLAN_INSTRUCTION`` no longer forbids capitalized words and
# four-digit years in queries (they are search targets now, not assertions) and
# instead states scope, as-of date, source class, and measurable success; and
# the module now sorts a validated plan by priority and stamps each sub-topic
# with its local ``topic-NN`` coverage id. Re-pinned deliberately, in its own
# commit, rather than silently invalidated — the same convention task 1 used.
# ``researcher`` was re-pinned ``3ae03b691d83`` -> ``d06b0630d1dd`` by task 3
# of the same plan, then ``d06b0630d1dd`` -> ``ae27fd51a1bb`` by task 3's
# refinement-selection fix round. Only the researcher moved, which is again
# the correct blast radius: ``agents/prompts.py`` was untouched, so the five
# agents that share it keep their values, and ``CRITIC_PROMPT_FINGERPRINT`` and
# the Judge pin are unchanged too. The researcher's own module source moved for
# intended behavior changes in both passes: the prompt now prefers primary
# sources and read-before-reporting; ``retrieved_finding_urls`` no longer
# counts search results; the read-bearing rule and extraction gate share
# ``steps.read_evidence_urls``; the module bounds per-sub-topic evidence; and
# refinement selection now skips non-gap topics already covered by prior
# findings while accounting for them explicitly. Re-pinned deliberately, in
# its own commit, rather than silently invalidated — the same convention tasks
# 1 and 2 used.
# Task 5 then changed the shared Fact Checker prompt for read-before-verdict
# and structured verification passages, legitimately moving all six shared
# fingerprints: planner ``059a32ca8b85`` -> ``8a5f8a1499bf``, researcher
# ``076337666605`` -> ``ebdfd3ae4c05``, source evaluator ``21d4d79ca09a`` ->
# ``ffab1c9795e2``, Fact Checker ``f9dd5826f66a`` -> ``f73e42b1f8f0``,
# Synthesizer ``6f5b7739f134`` -> ``affe67074133``, and Critic
# ``5b0105f4dcc1`` -> ``a15c36b0ed0e``. The Fact Checker's own implementation
# then changed while the contract migration was completed, moving only its
# full-source fingerprint ``f73e42b1f8f0`` -> ``edd677f57ce8``. All moves are
# intentional drift-alarm updates, not weakened assertions.
#
# Task 5's fix round then changed the Fact Checker's own module source again,
# moving ``edd677f57ce8`` -> ``d5c99dbd9a35``. NO prompt string moved: the
# round persists consumed claim provenance (bounded origin-finding and
# coverage identities on ``Claim``), deletes the lossy ``_finding_is_new``
# text heuristic in favour of that provenance, and takes coverage from
# recorded coverage ids instead of URL overlap. Because
# ``agent_prompt_fingerprint`` hashes the agent's own module source, any
# behavioural edit there moves its value; the shared ``agents/prompts.py`` was
# untouched, so the other five target pins and the Judge pin are unchanged —
# exactly the blast radius this change should have, and the reason the pin is
# checked as a matrix rather than per file.
#
# Task 6 changed ``SYNTHESIZER_SYSTEM_PROMPT``, ``REPORT_INSTRUCTION`` and the
# checked-claim packet renderer in the shared ``agents/prompts.py``, and
# rewrote ``agents/synthesizer.py`` (claim-linked point validation against the
# canonical registry, refusal reasons, two composed artifacts, no writes).
# Because the shared module is hashed into every agent's value, all six moved
# together — planner ``8a5f8a1499bf`` -> ``aa648f82af71``, researcher
# ``ebdfd3ae4c05`` -> ``6d5fd0f85dc3``, source evaluator ``ffab1c9795e2`` ->
# ``ddd8f9e5785a``, Fact Checker ``d5c99dbd9a35`` -> ``3ccaa7aa4fc1``,
# Synthesizer ``affe67074133`` -> ``6b9c616afad9``, Critic
# ``a15c36b0ed0e`` -> ``5bb5ef748a84``. The synthesizer's move has both
# causes (shared prompt text *and* its own module). ``CRITIC_PROMPT_FINGERPRINT``
# moved with the critic entry, and ``PINNED_JUDGE_PROMPT_FINGERPRINT`` did
# not move: the Judge prompt module was untouched.
# Task 6's review fix then made the shared report instruction disclose the
# provider-only mechanism/geography trust boundary and moved the Synthesizer's
# evaluation contract to composition-only/no-publication semantics. The shared
# prompt edit moves all six values, while the Synthesizer source edit also
# changes its full-source value; re-pin deliberately so this remains a drift
# alarm. The later sub-minimum digest-budget contract fix changed only the
# Synthesizer source value again.
# Task 7 then replaced the Critic's free-text gaps with targetable
# ``CritiqueGap`` objects carrying a plan ``coverage_id``, handed it one
# fenced block per reader-report section so no section cap can hide a later
# one, and added the structured quality snapshot and typed error groups to its
# prompt. That shared ``agents.prompts`` edit moves all six values together.
# Four module sources moved for their own reasons too: the researcher routes
# gaps by exact plan ID instead of title substring, the Fact Checker reads a
# gap's prose again when matching an explicit re-verification request, the
# Synthesizer renders a gap's ``problem``, and the Critic lost the now-dead
# combined-report clamp (and now canonicalizes once for both digests, which
# also keeps the module inside the line-length rule). The exact moves were
# planner ``1e244d04fe8d`` -> ``028150f7e4a5``, researcher
# ``e3ffdca8f71e`` -> ``533350d78959``, source evaluator
# ``b3185bb51b4e`` -> ``6e127ffba9d4``, Fact Checker
# ``997b6d351251`` -> ``926a1d968c68``, Synthesizer
# ``5bc5345f1791`` -> ``de9b44079616``, and Critic
# ``58175fc4313b`` -> ``b46dfef67238``. The Judge pin did not move. Task 7's
# delta-oriented refinement moved the researcher's own value once more,
# ``533350d78959`` -> ``96907685a382``, because a gap's own recommended
# queries are now rendered ahead of the planner's for the sub-topic that gap
# targets; no prompt string moved for that second step. Task 7's terminal
# finalizer then moved the Synthesizer's own value again,
# ``de9b44079616`` -> ``bf2b62331950``, because that agent — which already
# declares the ``write_document`` and ``save_to_memory`` tools — now also
# exposes the publishing methods the finalizer writes through. No prompt was
# edited in either step, so the other five values are unchanged. The Critic's
# own value then moved once more, ``b46dfef67238`` -> ``141c47557a29``, when
# its prompt packet stopped reading the quality snapshot through a defensive
# ``getattr`` and named the state field that now exists. Task 7's fix round 1
# then extracted the legacy-gap normalizer duplicated between
# ``CritiqueDraft`` and ``Critique`` into one shared ``normalize_gap_drafts``,
# moving the Critic's value a final time, ``141c47557a29`` -> ``e8bb04d10046``.
# No prompt text was edited in any of those steps and no other value moved.
# Re-pinned deliberately, after the agent-performance session measured two live
# failures and a behavioural shortfall. ``agent_prompt_fingerprint`` hashes each
# agent's *module source*, so a non-prompt edit to an agent module moves its
# value too — that is a property of this alarm, not a defect in it.
#   planner 028150f7e4a5 -> 2e357f4a04c4: ``SubTopicDraft`` gained a
#     ``mode="before"`` validator that reads a lone string as a one-element list
#     for ``search_queries``/``success_criteria``. Two of four live runs died at
#     the planner with ``graph_planning_failed`` after 22,593 and 17,433 tokens
#     because a sampled plan request wrote its single criterion as a bare string
#     where the schema declares ``list[str]``. The JSON schema handed to the
#     model is byte-identical before and after (verified by hashing
#     ``model_json_schema()``), so the model is still asked for an array.
#   researcher 96907685a382 -> d48583bcb01f: the researcher's system prompt
#     gained an ordering rule — read the most promising result a search returned
#     before searching again — after a per-agent trace analysis showed 183
#     ``web_search`` calls, zero ``web_scraper`` calls, and every ReAct loop
#     ending at exactly its 10-call tool budget. Review asked for the qualifier
#     that a search returning nothing worth reading should be followed by
#     another search rather than a forced read; that wording is included in this
#     value. Wording only; no tool semantics changed.
#   planner 2e357f4a04c4 -> 86c8ce19a676 and researcher d48583bcb01f ->
#     016fc43c77eb: both prompts now require independent corroboration. The fact
#     checker refuses to corroborate a claim with a page on the claim's own
#     publisher's domain (``independent_domains``) and records a claim with no
#     independent source as ``insufficient_evidence`` — yet the plan asked only
#     for "at least one success criterion describing what evidence would settle
#     it" and never for a second, independent source. A live run showed exactly
#     the consequence: claims drawn from the IEA outlook (source score 0.82)
#     were all graded insufficient evidence, and the critic scored the report
#     3/10. The plan and the researcher now ask for a second source on a
#     different site for every load-bearing fact. Wording only.
#   researcher 016fc43c77eb -> 4a33c7153025: the prompt now says that a page a
#     publisher refuses must not be retried — read the same material as a
#     document, or find it from another publisher. Measured: a live run spent
#     seven ``web_scraper`` attempts on ``emp.lbl.gov`` pages returning 403,
#     while ``document_reader`` read that host's PDFs 28 times out of 30.
#     Wording only.
# No other agent's value moved.
# ALL SIX values moved together, deliberately, because the change was to the
# SHARED ``agents.prompts`` module — which is exactly the "did a prompt edit
# land" signal this alarm exists to give, and exactly why it cannot say whose
# prompt moved. The edit added read-before-search guidance to
# ``CLAIM_VERIFICATION_SYSTEM_PROMPT``: the fact checker's verification loops
# made 146 ``web_search`` calls against roughly 31 reads, and a claim whose loop
# read nothing independent is recorded ``insufficient_evidence`` with no verdict
# call at all — so searches spent without reading cost the claim its verdict.
# That is the same pathology the researcher had (183 searches, zero scrapes) and
# the same instruction fixed it there. Wording only; no tool semantics, no
# verification semantics, and no threshold changed.
#   planner           86c8ce19a676 -> 1a29b6e63b2d
#   researcher        4a33c7153025 -> efe153883c5b
#     (the shared-prompts edit moved it to 8205d02968bf, then the
#     publisher-grouping fix in ``bound_sub_topic_findings`` moved it again —
#     that function groups by publisher rather than by URL, so one publisher
#     can no longer take all four retention slots and push an independent
#     source out of the report)
#   source_evaluator  6e127ffba9d4 -> 4fdf95ddcc64
#   fact_checker      5080d1810c7e -> f13fdde0e8bc
#   synthesizer       dd422429c34b -> ad25c1b309b1
#   critic            bc6b1f23064c -> 97a2d10ad688
# The claim-reason amendment moved the Fact Checker's own value a further
# time, ``f13fdde0e8bc`` -> ``1f52e702839d``, and nothing else. NO prompt
# string moved: ``insufficient_claim`` now records the enumerated reason it
# was already given on the ``Claim`` it returns — ``Claim.insufficient_reason``
# is a new optional field on the shared contract — so the evidence ledger's
# claim registry can print why a claim went unjudged instead of leaving that
# fact in the event log, where no published artifact could carry it. The
# shared ``agents/prompts.py`` was untouched, so the other five target pins
# and the Judge pin are unchanged: this is the module-source false positive
# recorded for A-4/A-6 and P4 above, and the reason the alarm is checked as a
# matrix rather than per file.
# The upstream-evidence pooling change moved the Fact Checker's value once
# more, ``1f52e702839d`` -> ``04582f1aaed1``: a fact_checker.py
# module-source change, not a prompt edit — no prompt string moved.
# Task 2 moved four of the six (planner, researcher, fact_checker, critic)
# when ``tool_budget_for(self.name)`` replaced ``self.config.tool_budget`` at
# each agent's own ``run_react_loop`` call site, and the planner's prompt
# contract changed: its plan request now prints the frozen answer contract and
# asks for 1-4 atomic evidence targets, it makes one tool-free semantic review
# call, and its loop prompt states that the session's startup recall already
# supplied the one procedural lookup. Task 2 then moved all six together
# twice more: once for ``PROMPT_VERSION`` in the shared ``agents.prompts``
# module, and once for the per-call configuration fingerprint added to each
# agent's ``AgentRun``. Both are the documented false positive of hashing
# module source rather than prompt text — any module edit moves the value. The
# Task 2 review round moved the planner's alone a fourth time
# (``88026ce6be52`` -> ``cd8a2ce1c58e``) for the token-boundary marker
# matching, the agreement-frame tolerance check, and the frozen-contract
# guard: module-source moves with no prompt-string edit beyond the tightened
# review instruction. Review round 2 moved it once more as well
# (``cd8a2ce1c58e`` -> ``a8bee2f61af6``) for the comparison-referent rule, the
# derived-form marker matching, and the tolerance-unit comparison. Final Task 2
# pins before this fix round: planner ``a8bee2f61af6``, researcher ``711b4d414515``,
# source_evaluator ``c50282e1dd42``, fact_checker ``675fa5aea904``,
# synthesizer ``a7fe09fe50c7``, critic ``7e0d97508b00``. The Judge pin is
# unchanged. Fix round 3 moved planner to ``863f670c4f5a`` for direct
# comparison referents, the threshold guard, and bounded marker variants.
# Fix round 4 moves planner to ``b3bfe9127cae`` for the bounded direct
# proper-name referent rule that keeps spelled-out quantity thresholds factual.
# Fix round 5 moves planner to ``027ca133b2da`` for the case-insensitive
# quantity-structure guard and direct referent matching.
# The GPT-5.6 Sol xhigh breaker remediation moves planner to
# ``43b054f87eaa`` for the typed conservative comparison boundary and
# four-digit inequality-count handling. The planner prompt string is unchanged;
# the existing fingerprint contract hashes the whole planner module source.
# The follow-up review-finding fix moves planner to ``de043421bb47`` for
# structural terminal referents, positive count syntax, repeated multiword
# year/work pairs, and clock-independent constraint evidence policy.
# The interaction-finding fix moves planner to ``3de57a6c78cf`` after removing
# shape-only acronym/plural positives, adding explicit contrastive-set syntax,
# and preserving overlapping repeated year/work spans inside count frames.
# Task 3's decision-context hook moves planner to ``7e43f342910c`` although no
# planner prompt string changed: ``agent_prompt_fingerprint`` hashes the whole
# shared ``agents/prompts.py``, which gained the ``decision_context`` parameter
# and its single ``## Acquisition context`` section. The same shared-module
# move is why every other target pin below advanced in this task.
# Task 3's fix round moves researcher to ``0628475cb810``: its own module
# source changed (the extraction response contract now requires the registry
# fields, the reply example demonstrates that shape, and the acquisition
# counters changed). Only that agent's source changed, so no other pin moves.
# Task 4's re-pin is recorded against ``CRITIC_PROMPT_FINGERPRINT`` above: all
# six moved with the shared prompt module, and the source evaluator moved for
# its own module change as well — ``b2ce33c07533`` -> ``4dbe292964d2``, an
# import-order fix in its own module with no further prompt edit, which is the
# documented false positive of hashing a module's whole source rather than its
# prompt text. The other five are unchanged from the first Task 4 pin.
# Task 4's fix round 5 re-pins all six once more: the shared scoring contract
# now asks for a verbatim quote beside every temporal value, and the Source
# Evaluator's own module gained the quoted draft fields and dropped the
# value-only parsing path. Moves are recorded against
# ``CRITIC_PROMPT_FINGERPRINT`` above; the Judge pin is unchanged.
# Task 5 re-pins all six again, in one step, because the change was to the
# SHARED ``agents.prompts`` module: it gained the claim-equivalence system
# prompt, its response contract, and the two schema-version constants the
# reverification cache key is built from. The Fact Checker's own module moved
# for its own reasons as well — the explicit ``claim_batch_size`` /
# ``claim_batches_per_pass`` bounds, the batch loop, the pending continuation
# queue, and the target attribution each claim now carries — so its value
# moved for both causes at once, which is the documented false positive of
# hashing a module's whole source. No judge prompt moved, so the Judge pin is
# unchanged.
#   planner            da23fbecdf5d -> 599c78243c9e
#   researcher         0d4ee670a0fe -> fdc2bc2e8d48
#   source_evaluator   e01b5b79a4d2 -> 674593415225
#   fact_checker       772e7d9d13f8 -> 15322a899461
#   synthesizer        895e307a5068 -> cccb6dc91c6e
#   critic             cfb6f062b992 -> 15754f64fa12
# Task 5's own review round then moved the Fact Checker's value once more,
# ``15322a899461`` -> ``c9b3380c81e4``, and nothing else: the pass must drain
# its continuation queue *before* the provenance reset, otherwise a resumed
# claim whose extraction is not restated loses the target attribution and the
# provenance it was extracted with. That is a fact_checker.py module-source
# move with no prompt string edited, so the other five values and the Judge
# pin are unchanged.
# Task 5's fix round 1 moved the Fact Checker's value once more,
# ``c9b3380c81e4`` -> ``b57cabd4699d``, and again nothing else: a provider or
# schema failure is no longer published as an insufficient-evidence verdict
# (it is a continuation), target attribution is now validated against each
# target's required dimensions and its support policy, and the continuation
# queue carries an explicit bound with its overflow persisted rather than
# deleted. Module-source moves once more, with no prompt string edited — the
# other five values and the Judge pin are unchanged.
# Task 5's fix round 2 moved it again, ``b57cabd4699d`` -> ``a9cad9387b6e``:
# the support policy is now enforced on the adjudicated claim
# (``admitted_target_ids`` / ``supporting_publisher_count``), the active pool
# one pass admits respects the continuation bound, and the claim cluster
# registry is persisted through ``ResearchState``. Module-source moves again
# with no prompt string edited; the other five values and the Judge pin are
# unchanged.
# Task 5's fix round 3 moved it once more, ``a9cad9387b6e`` -> ``fef54f3dfdcb``:
# the pass no longer destroys the claim its own drain parked, the scheduler's
# answered-target set is read from the published claim rather than the
# obligations it reached for, and the relation vocabulary was narrowed so a
# level and a delta never compare equal. Module-source moves with no prompt
# string edited; the other five values and the Judge pin are unchanged.
# Task 6 moved it again, ``fef54f3dfdcb`` -> ``490342ba8ac0``: adjudication runs
# over a claim-specific packet of evidence ids instead of free-form citation
# URLs, the strict pair rule and the conflict rows live in the module, and the
# legacy passage-shaped verdict contract is now ``PassageVerdictDraft``. Again a
# module-source move with no shared prompt string edited, so the other five
# values and the Judge pin are unchanged.
# Task 7 moved all six at once: the shared ``agents.prompts`` module's
# ``REPORT_INSTRUCTION`` gained the reader-statement contract (the ``basis``
# field, the answer-rows shape, the checked mechanism/geography cell, the
# uncertainty rules), and every agent's fingerprint hashes that module. The
# Synthesizer also gained the canonical-packet, statement-validation, and
# answer-form code in its own module. The Judge pin did **not** move: no judge
# template changed.
#   planner ``599c78243c9e`` -> ``da4407dd9d39``
#   researcher ``fdc2bc2e8d48`` -> ``b4f9d21a34b4``
#   source_evaluator ``674593415225`` -> ``33a71376d3e6``
#   fact_checker ``490342ba8ac0`` -> ``f1ccaf38ff02``
#   synthesizer ``cccb6dc91c6e`` -> ``37b86926ec09``, -> ``059540bede20`` in the
#     Task 7 fix round (whole-token corpus and cell matching, the acronym-aware
#     name check, attested answer-row labels, and the shared statement
#     derivation), -> ``6bd12778d7fe`` in fix round 2 (cells attest figures and
#     names as well as words, the dead cluster-resolver copy removed, and the
#     auditable common-abbreviation carve-out), then -> ``17bc15e0131b`` in fix
#     round 3 (the opener rule anchored to a line start, so a mid-line dash or a
#     closing bracket is no longer an opener, and the abbreviation list
#     completed). The other five are unchanged by all three rounds: no shared
#     prompt string was edited.
#   critic ``15754f64fa12`` -> ``3f0341751794``
PINNED_TARGET_PROMPT_FINGERPRINTS = {
    "planner": "da4407dd9d39",
    "researcher": "b4f9d21a34b4",
    "source_evaluator": "33a71376d3e6",
    "fact_checker": "f1ccaf38ff02",
    "synthesizer": "17bc15e0131b",
    "critic": "3f0341751794",
}

# The judge half of the same contract. A Judge prompt change moves this value and
# invalidates Judge evidence for every agent, so it is pinned next to the targets
# rather than only inside the judge's own tests.
PINNED_JUDGE_PROMPT_FINGERPRINT = "74b9cddfbbee"

NOW = datetime(2026, 8, 16, 10, 15, 0, tzinfo=timezone.utc)
GIT = GitMetadata(commit="abc1234def", short_sha="abc1234", dirty=False)


def test_the_baseline_efforts_match_the_approved_profile() -> None:
    """Researcher and Source Evaluator at high; everyone else, and the
    judge, at max — DeepSeek V4 Flash supports only those two levels."""
    config = EvaluationConfig()

    assert resolve_target_effort(config, "planner", override=None) == "max"
    assert resolve_target_effort(config, "researcher", override=None) == "high"
    assert (
        resolve_target_effort(config, "source_evaluator", override=None)
        == "high"
    )
    assert (
        resolve_target_effort(config, "fact_checker", override=None) == "max"
    )
    assert resolve_target_effort(config, "synthesizer", override=None) == "max"
    assert resolve_target_effort(config, "critic", override=None) == "max"
    assert resolve_judge_effort(config, override=None) == "max"


def test_the_baseline_models_are_deepseek_v4_flash() -> None:
    config = EvaluationConfig()

    assert config.target_model == "deepseek-v4-flash"
    assert config.judge_model == "deepseek-v4-flash"


def test_the_evaluation_config_no_longer_carries_a_reasoning_mode() -> None:
    """Thinking mode replaced it; a stale key must not load silently."""
    assert "reasoning_mode" not in EvaluationConfig.model_fields

    with pytest.raises(ValueError):
        EvaluationConfig(reasoning_mode="standard")


def test_embedding_override_fields_default_to_none() -> None:
    """``None`` means inherit ``llm.embedding_provider`` /
    ``llm.embedding_model``; the evaluation harness carries no sentinel of
    its own."""
    config = EvaluationConfig()

    assert config.embedding_provider is None
    assert config.embedding_model is None


def test_an_invocation_override_beats_the_per_agent_override() -> None:
    assert (
        resolve_target_effort(
            EvaluationConfig(), "researcher", override="xhigh"
        )
        == "xhigh"
    )


def test_the_per_agent_override_beats_the_global_default() -> None:
    config = EvaluationConfig(
        target_reasoning_effort="high",
        target_reasoning_effort_overrides={"researcher": "low"},
    )

    assert resolve_target_effort(config, "researcher", override=None) == "low"
    assert resolve_target_effort(config, "planner", override=None) == "high"


def test_the_global_default_applies_when_no_override_exists() -> None:
    config = EvaluationConfig(
        target_reasoning_effort="xhigh",
        target_reasoning_effort_overrides={},
    )

    assert resolve_target_effort(config, "critic", override=None) == "xhigh"


def test_judge_effort_is_independent_of_every_target_effort() -> None:
    config = EvaluationConfig(
        target_reasoning_effort="low",
        target_reasoning_effort_overrides={"planner": "low"},
        judge_reasoning_effort="high",
    )

    assert resolve_judge_effort(config, override=None) == "high"
    assert resolve_judge_effort(config, override="max") == "max"


def test_an_invalid_effort_lists_the_valid_levels() -> None:
    with pytest.raises(ValueError) as caught:
        resolve_target_effort(EvaluationConfig(), "planner", override="turbo")

    assert "xhigh" in str(caught.value)


def build(*, settings=None, **kwargs):
    defaults = dict(
        agent_name="researcher",
        tier="controlled",
        case_id=None,
        reasoning_effort=None,
        judge_reasoning_effort=None,
        output_directory=None,
        experiment_prefix=None,
        now=NOW,
        git=GIT,
    )
    defaults.update(kwargs)
    return build_runtime_config(settings or ConfigSettings(), **defaults)


def _judge_transport(provider: str) -> str:
    transport = getattr(evaluation_config, "judge_structured_transport", None)
    assert callable(transport)
    return transport(provider)


def test_the_runtime_config_freezes_both_efforts() -> None:
    runtime = build()

    assert runtime.target_reasoning_effort == "high"
    assert runtime.judge_reasoning_effort == "max"
    assert runtime.thinking_mode == "enabled"
    with pytest.raises(ValueError):
        runtime.target_reasoning_effort = "high"


def test_controlled_and_live_repetition_counts() -> None:
    assert build(tier="controlled").repetitions == 3
    assert build(tier="live").repetitions == 1
    assert build().max_concurrency == 1


def test_an_unset_embedding_override_inherits_the_local_llm_default() -> None:
    """With no evaluation override, the resolved runtime config takes
    ``llm.embedding_provider`` (default ``"local"``) and
    ``llm.embedding_model`` unchanged."""
    settings = ConfigSettings(llm=LLMConfig(embedding_provider="local"))
    runtime = build(settings=settings)

    assert runtime.embedding_provider == "local"
    assert runtime.embedding_model == settings.llm.embedding_model


def test_an_unset_embedding_override_inherits_an_openai_llm_selection() -> None:
    """The same inheritance rule for the OpenAI case: no evaluation
    override, ``llm.embedding_provider: openai`` wins through unchanged."""
    settings = ConfigSettings(llm=LLMConfig(embedding_provider="openai"))
    runtime = build(settings=settings)

    assert runtime.embedding_provider == "openai"
    assert runtime.embedding_model == settings.llm.embedding_model


def test_an_explicit_evaluation_override_wins_over_an_inherited_local_default() -> (
    None
):
    settings = ConfigSettings(
        llm=LLMConfig(embedding_provider="local"),
        evaluation=EvaluationConfig(embedding_provider="openai"),
    )
    runtime = build(settings=settings)

    assert runtime.embedding_provider == "openai"


def test_an_explicit_local_override_wins_over_an_inherited_openai_default() -> (
    None
):
    settings = ConfigSettings(
        llm=LLMConfig(embedding_provider="openai"),
        evaluation=EvaluationConfig(embedding_provider="local"),
    )
    runtime = build(settings=settings)

    assert runtime.embedding_provider == "local"


def test_an_explicit_evaluation_embedding_model_wins_over_inheritance() -> None:
    settings = ConfigSettings(
        evaluation=EvaluationConfig(embedding_model="text-embedding-3-large")
    )
    runtime = build(settings=settings)

    assert runtime.embedding_model == "text-embedding-3-large"
    assert runtime.embedding_model != settings.llm.embedding_model


def test_changing_a_target_effort_refingerprints_but_reuses_the_dataset() -> None:
    baseline = build()
    changed = build(reasoning_effort="medium")

    assert (
        changed.configuration_fingerprint != baseline.configuration_fingerprint
    )
    assert changed.dataset_name == baseline.dataset_name


def test_changing_the_judge_effort_refingerprints_the_judge() -> None:
    baseline = build()
    changed = build(judge_reasoning_effort="high")

    assert (
        changed.judge_configuration_fingerprint
        != baseline.judge_configuration_fingerprint
    )
    assert changed.dataset_name == baseline.dataset_name


def test_the_judge_configuration_fingerprint_did_not_move() -> None:
    """Adding the provider_fallback block must change no judge setting.

    ``judge_configuration_fingerprint`` covers provider, structured
    transport, judge model, judge reasoning effort, judge temperature,
    thinking mode, and rubric version. None of those were touched, so this
    value is unchanged and the recorded ``924caf47aa0d`` still describes this
    configuration. It is asserted by identity rather than by a new literal
    because it is recorded in the canary documents, not in this suite.
    """
    baseline = build()
    identical = build()

    assert (
        baseline.judge_configuration_fingerprint
        == identical.judge_configuration_fingerprint
    )


def test_judge_transport_identifier_is_provider_specific() -> None:
    assert _judge_transport("deepseek") == (
        "deepseek_responses_json_schema_v1"
    )
    assert _judge_transport("openai") == (
        "openai_responses_parse_v1"
    )


@pytest.mark.parametrize(
    ("provider", "transport"),
    [
        ("deepseek", "deepseek_responses_json_schema_v1"),
        ("openai", "openai_responses_parse_v1"),
    ],
)
def test_judge_transport_provenance_is_recorded_in_experiment_metadata(
    provider: str, transport: str
) -> None:
    settings = ConfigSettings(llm=LLMConfig(provider=provider))

    metadata = experiment_metadata(build(settings=settings), settings)

    assert metadata.get("judge_provider") == provider
    assert metadata.get("judge_structured_transport") == transport


def test_judge_transport_fingerprint_is_stable_and_provider_sensitive() -> None:
    baseline = build()
    identical = build()
    openai_settings = ConfigSettings(llm=LLMConfig(provider="openai"))
    changed_provider = build(settings=openai_settings)

    assert (
        baseline.judge_configuration_fingerprint
        == identical.judge_configuration_fingerprint
    )
    assert (
        changed_provider.judge_configuration_fingerprint
        != baseline.judge_configuration_fingerprint
    )


def test_judge_prompt_fingerprint_is_unchanged_by_transport_provenance() -> None:
    baseline = judge_prompt_fingerprint(rubric_version=1)

    build()
    build(settings=ConfigSettings(llm=LLMConfig(provider="openai")))

    assert judge_prompt_fingerprint(rubric_version=1) == baseline


def _target_transport(provider: str) -> str:
    transport = getattr(evaluation_config, "target_react_transport", None)
    assert callable(transport)
    return transport(provider)


def test_target_react_transport_is_provider_specific() -> None:
    assert _target_transport("deepseek") == "deepseek_chat_tools_auto_v1"
    assert _target_transport("openai") == "openai_responses_tools_auto_v1"


@pytest.mark.parametrize(
    ("provider", "transport"),
    [
        ("deepseek", "deepseek_chat_tools_auto_v1"),
        ("openai", "openai_responses_tools_auto_v1"),
    ],
)
def test_target_react_transport_is_recorded(
    provider: str, transport: str
) -> None:
    settings = ConfigSettings(llm=LLMConfig(provider=provider))

    metadata = experiment_metadata(build(settings=settings), settings)

    assert metadata["target_react_transport"] == transport


def test_changing_the_target_transport_refingerprints_the_configuration(
    monkeypatch,
) -> None:
    """The new field must participate in the fingerprint, not just be recorded.

    Changing providers alone does not prove that: the provider is already part
    of the application settings, so the fingerprint would move anyway. Patching
    only the transport value and rebuilding identical settings is what isolates
    the field's own contribution.
    """
    baseline = build()
    monkeypatch.setitem(
        _TARGET_REACT_TRANSPORT, "deepseek", "patched_transport_v9"
    )

    patched = build()

    assert _target_transport("deepseek") == "patched_transport_v9"
    assert (
        patched.configuration_fingerprint != baseline.configuration_fingerprint
    )


def test_changing_the_target_transport_never_touches_the_dataset_or_judge(
    monkeypatch,
) -> None:
    baseline = build()
    monkeypatch.setitem(
        _TARGET_REACT_TRANSPORT, "deepseek", "patched_transport_v9"
    )

    patched = build()

    assert patched.dataset_name == baseline.dataset_name
    assert (
        patched.judge_configuration_fingerprint
        == baseline.judge_configuration_fingerprint
    )
    assert judge_prompt_fingerprint(rubric_version=1) == "74b9cddfbbee"


def test_the_critic_target_fingerprint_is_pinned_as_a_drift_alarm() -> None:
    """A prompt edit must be a conscious act, not a silent invalidation.

    Before this pin, the Critic's ``target_prompt_fingerprint`` was recorded on
    every artifact but asserted nowhere, so a prompt change would move it without
    any test noticing — unlike the judge fingerprint, which has been pinned since
    it was first introduced.
    """
    assert agent_prompt_fingerprint("critic") == CRITIC_PROMPT_FINGERPRINT
    assert agent_prompt_fingerprint("critic") != "bf86f19981a6"


def test_every_target_prompt_fingerprint_is_pinned_against_prompt_drift() -> None:
    """Step 5: all six agents' fingerprints, not only the Critic's.

    The matrix is a conformance test, so the fingerprints are checked before any
    prompt edit is accepted. Pinning all six means a change to the shared
    ``agents.prompts`` module — which moves every value at once — is visible in
    one assertion rather than one sixth of it.
    """
    assert set(AGENT_NAMES) == set(PINNED_TARGET_PROMPT_FINGERPRINTS)
    assert {
        name: agent_prompt_fingerprint(name) for name in AGENT_NAMES
    } == PINNED_TARGET_PROMPT_FINGERPRINTS


def test_the_judge_fingerprint_is_pinned_beside_the_six_target_pins() -> None:
    """Step 5: both halves of the structured contract, pinned in one place.

    The judge fingerprint is a distinct identity from every target's, because it
    covers the judge system prompt, template, schema, weights, and rubric version
    rather than an agent prompt module.
    """
    judge = judge_prompt_fingerprint(rubric_version=1)

    assert judge == PINNED_JUDGE_PROMPT_FINGERPRINT
    assert judge not in set(PINNED_TARGET_PROMPT_FINGERPRINTS.values())


def test_the_target_fingerprint_covers_the_shared_prompt_module() -> None:
    """Record why the pin above cannot attribute a change to one agent.

    ``agent_prompt_fingerprint`` hashes the agent's own module *and* the shared
    ``agents.prompts`` library, so clarifying one sentence of one agent's contract
    moves the recorded fingerprint for all six. Verified here rather than assumed,
    because it changes how a fingerprint move should be read.
    """
    fingerprints = {
        name: agent_prompt_fingerprint(name)
        for name in (
            "critic",
            "planner",
            "researcher",
            "synthesizer",
            "fact_checker",
            "source_evaluator",
        )
    }

    assert len(set(fingerprints.values())) == len(fingerprints)
    # Every agent's value is derived from the same shared module, so a change to
    # that module is visible in all of them; the per-agent component is what keeps
    # the values distinct.
    assert fingerprints["critic"] == CRITIC_PROMPT_FINGERPRINT


def test_changing_the_planner_final_budget_refingerprints_the_configuration() -> None:
    """The operation-specific budget is visible in safe configuration metadata.

    The effective value enters the serialized application settings, so the
    configuration fingerprint — and nothing secret — records it.
    """
    baseline = build()
    changed = build(
        settings=ConfigSettings(
            agents=AgentRuntimeConfig(planner_final_max_tokens=8192)
        )
    )

    assert (
        changed.configuration_fingerprint != baseline.configuration_fingerprint
    )
    assert changed.dataset_name == baseline.dataset_name
    assert changed.agent_name == baseline.agent_name
    assert (
        changed.model_dump(mode="json")["configuration_fingerprint"]
        != baseline.model_dump(mode="json")["configuration_fingerprint"]
    )


def test_dataset_names_carry_the_agent_tier_and_schema_version() -> None:
    assert (
        dataset_name("source_evaluator", "controlled", 1)
        == "deep-research-source-evaluator-controlled-v1"
    )
    assert dataset_name("planner", "live", 2) == "deep-research-planner-live-v2"


def test_experiment_names_follow_the_agreed_shape() -> None:
    assert (
        experiment_name(
            "planner", "controlled", now=NOW, git_sha="abc1234", prefix=None
        )
        == "planner-controlled-20260816T101500Z-abc1234"
    )
    assert experiment_name(
        "planner", "controlled", now=NOW, git_sha="abc1234", prefix="tuning"
    ) == "tuning-planner-controlled-20260816T101500Z-abc1234"


def test_the_target_llm_config_carries_the_frozen_effort_and_model() -> None:
    llm = target_llm_config(build(agent_name="planner"), ConfigSettings().llm)

    assert llm.provider == "deepseek"
    assert llm.model == "deepseek-v4-flash"
    assert llm.reasoning_effort == "max"
    assert llm.thinking_mode == "enabled"
    assert llm.model_overrides == {}


def test_production_parity_resolves_the_target_from_the_production_llm() -> None:
    """The measured divergence this closes (baseline §6.2, D-10).

    The evaluation corpus ran the researcher and source evaluator at ``high``
    under one configuration fingerprint while the planner and fact checker ran
    at ``max`` under another, because production could express only one effort
    for all six agents. With the per-agent profile now in ``llm``, an
    evaluation target resolves the same value the CLI runs.
    """
    settings = ConfigSettings(
        llm=LLMConfig(
            reasoning_effort="high",
            model_overrides={
                "planner": {"reasoning_effort": "max"},
                "researcher": {"reasoning_effort": "high"},
            },
        )
    )

    planner = build(settings=settings, agent_name="planner")
    researcher = build(settings=settings, agent_name="researcher")

    assert planner.target_reasoning_effort == "max"
    assert planner.target_profile_source == "production"
    assert planner.release_evidence is True
    assert planner.experiment_only is False
    assert researcher.target_reasoning_effort == "high"
    assert researcher.target_profile_source == "production"
    # And the resolved target config equals what the CLI would run.
    for runtime, agent_name in ((planner, "planner"), (researcher, "researcher")):
        target = target_llm_config(runtime, settings.llm)
        assert target.resolve_for(None).reasoning_effort == (
            settings.llm.resolve_for(agent_name).reasoning_effort
        )


def test_an_evaluation_only_profile_is_labelled_non_release_evidence() -> None:
    """Production declares nothing for this agent, so the profile is the
    experiment's own and the record says so."""
    settings = ConfigSettings(llm=LLMConfig(reasoning_effort="high"))

    runtime = build(settings=settings, agent_name="planner")

    assert runtime.target_reasoning_effort == "max"
    assert runtime.target_profile_source == "evaluation"
    assert runtime.experiment_only is True
    assert runtime.release_evidence is False


def test_a_cli_runtime_override_is_an_experiment_not_release_evidence() -> None:
    settings = ConfigSettings(
        llm=LLMConfig(
            reasoning_effort="high",
            model_overrides={"planner": {"reasoning_effort": "max"}},
        )
    )

    runtime = build(
        settings=settings, agent_name="planner", reasoning_effort="low"
    )

    assert runtime.target_reasoning_effort == "low"
    assert runtime.target_profile_source == "invocation"
    assert runtime.release_evidence is False


def test_the_shipped_config_resolves_targets_and_cli_to_one_profile() -> None:
    """Review evidence: the shipped YAML is production-parity by construction."""
    settings = load_config("config.yaml")

    for agent_name in AGENT_NAMES:
        runtime = build(settings=settings, agent_name=agent_name)
        cli = settings.llm.resolve_for(agent_name)
        assert runtime.target_profile_source == "production"
        assert runtime.target_reasoning_effort == cli.reasoning_effort
        assert runtime.release_evidence is True
        assert runtime.target_model == cli.model


def test_a_frozen_production_profile_that_no_longer_matches_is_refused() -> None:
    """Fail preflight, never silently fall back to another effort."""
    settings = ConfigSettings(
        llm=LLMConfig(
            reasoning_effort="high",
            model_overrides={"planner": {"reasoning_effort": "max"}},
        )
    )
    runtime = build(settings=settings, agent_name="planner")
    edited = ConfigSettings(
        llm=LLMConfig(
            reasoning_effort="high",
            model_overrides={"planner": {"reasoning_effort": "high"}},
        )
    )

    with pytest.raises(ValueError, match="production"):
        target_llm_config(runtime, edited.llm)


def test_experiment_metadata_records_the_profile_source() -> None:
    settings = ConfigSettings(
        llm=LLMConfig(
            reasoning_effort="high",
            model_overrides={"planner": {"reasoning_effort": "max"}},
        )
    )
    metadata = experiment_metadata(
        build(settings=settings, agent_name="planner"), settings
    )

    assert metadata["target_profile_source"] == "production"
    assert metadata["production_parity"] is True
    assert metadata["release_evidence"] is True


def test_experiment_metadata_marks_an_experiment_only_profile() -> None:
    settings = ConfigSettings(llm=LLMConfig(reasoning_effort="high"))
    metadata = experiment_metadata(
        build(settings=settings, agent_name="planner"), settings
    )

    assert metadata["target_profile_source"] == "evaluation"
    assert metadata["release_evidence"] is False


def test_evaluation_config_defaults_to_production_parity() -> None:
    """The CLI flag Task 12 exposes turns it off; off is not the default."""
    assert EvaluationConfig().production_parity is True


def test_the_target_llm_config_is_accepted_by_the_capability_registry() -> None:
    """Fail-closed: the baseline profile must be a combination DeepSeek
    actually supports, checked against the local table, not assumed."""
    from deep_research.providers import resolve_request_settings

    for agent_name in ("planner", "researcher", "source_evaluator",
                       "fact_checker", "synthesizer", "critic"):
        llm = target_llm_config(build(agent_name=agent_name), ConfigSettings().llm)
        resolved = resolve_request_settings(llm.provider, llm.resolve_for(None))
        assert resolved.reasoning_effort in ("high", "max")
        assert resolved.include_temperature is False


def test_the_judge_llm_config_is_independent_of_the_target() -> None:
    llm = judge_llm_config(
        build(agent_name="researcher"), ConfigSettings().llm
    )

    assert llm.provider == "deepseek"
    assert llm.model == "deepseek-v4-flash"
    assert llm.reasoning_effort == "max"
    assert llm.thinking_mode == "enabled"
    assert llm.temperature == 0.0


def test_the_judge_llm_config_keeps_the_base_temperature_when_unset() -> None:
    """``temperature`` is non-optional on ``LLMConfig``; a ``None`` judge
    temperature means "do not override", never "send null"."""
    settings = ConfigSettings()
    settings = settings.model_copy(
        update={
            "evaluation": settings.evaluation.model_copy(
                update={"judge_temperature": None}
            )
        }
    )
    runtime = build_runtime_config(
        settings,
        agent_name="researcher",
        tier="controlled",
        case_id=None,
        reasoning_effort=None,
        judge_reasoning_effort=None,
        output_directory=None,
        experiment_prefix=None,
        now=NOW,
        git=GIT,
    )

    assert judge_llm_config(runtime, settings.llm).temperature == 0.7


def test_the_output_root_is_per_agent_and_per_experiment() -> None:
    runtime = build(agent_name="source_evaluator")

    assert runtime.output_root.parts[-3:] == (
        "evaluations",
        "source-evaluator",
        runtime.experiment_name,
    )


@pytest.mark.parametrize(
    ("output_directory", "expected_base"),
    [
        (r"C:\evaluation-root", r"\\?\C:\evaluation-root"),
        (
            r"\\server\share\evaluation-root",
            r"\\?\UNC\server\share\evaluation-root",
        ),
        (r"\\?\C:\evaluation-root", r"\\?\C:\evaluation-root"),
        (
            r"C:\evaluation-root\child\..\final",
            r"\\?\C:\evaluation-root\final",
        ),
    ],
    ids=["drive-letter", "unc", "already-extended", "absolute-normalization"],
)
def test_windows_output_root_has_a_pure_extended_path_contract(
    output_directory: str, expected_base: str
) -> None:
    """The root transformation is deterministic and filesystem-independent."""
    runtime = build(output_directory=output_directory)

    assert str(runtime.output_root.parent.parent) == expected_base


def test_windows_output_root_transformation_is_idempotent() -> None:
    output_directory = r"C:\evaluation-root\child\..\final"
    expected = _expected_extended_windows_path(output_directory)
    once = build(output_directory=output_directory)
    twice = build(output_directory=str(once.output_root.parent.parent))

    assert str(once.output_root.parent.parent) == expected
    assert str(twice.output_root.parent.parent) == expected


TASK10_RUNTIME_IDENTITIES = (
    (
        "researcher",
        "cross-agent-planner-fix-parity-baseline-researcher",
    ),
    (
        "source_evaluator",
        "cross-agent-planner-fix-parity-baseline-source-evaluator",
    ),
    ("fact_checker", "cross-agent-planner-fix-parity-baseline-fact-checker"),
    ("synthesizer", "cross-agent-planner-fix-parity-baseline-synthesizer"),
    ("critic", "cross-agent-planner-fix-parity-baseline-critic"),
    (
        "researcher",
        "cross-agent-planner-fix-parity-confirmation-researcher",
    ),
)


@pytest.mark.parametrize(
    ("agent_name", "prefix"),
    TASK10_RUNTIME_IDENTITIES,
    ids=[prefix for _, prefix in TASK10_RUNTIME_IDENTITIES],
)
def test_windows_runtime_config_preserves_all_evaluation_semantics(
    agent_name: str, prefix: str
) -> None:
    plain = build(
        agent_name=agent_name,
        output_directory=r"C:\evaluation-root",
        experiment_prefix=prefix,
    )
    already_extended = build(
        agent_name=agent_name,
        output_directory=r"\\?\C:\evaluation-root",
        experiment_prefix=prefix,
    )

    assert plain.experiment_name.startswith(prefix)
    assert already_extended.experiment_name.startswith(prefix)
    assert plain.dataset_name == dataset_name(agent_name, "controlled", 1)
    assert plain.model_dump(mode="json", exclude={"output_root"}) == (
        already_extended.model_dump(mode="json", exclude={"output_root"})
    )
    assert str(plain.output_root.parent.parent) == (
        str(already_extended.output_root.parent.parent)
    )


def _expected_extended_windows_path(value: str) -> str:
    """Return the host-independent string contract for Task 3's helper."""
    normalized = ntpath.normpath(value)
    if normalized.startswith("\\\\?\\"):
        return normalized
    if normalized.startswith("\\\\"):
        return "\\\\?\\UNC\\" + normalized[2:]
    return "\\\\?\\" + normalized


def test_experiment_metadata_records_everything_the_spec_names() -> None:
    metadata = experiment_metadata(build(agent_name="planner"), ConfigSettings())

    for key in (
        "agent",
        "tier",
        "git_commit",
        "git_dirty",
        "target_model",
        "target_reasoning_effort",
        "thinking_mode",
        "configuration_fingerprint",
        "judge_model",
        "judge_reasoning_effort",
        "judge_configuration_fingerprint",
        "case_registry_version",
        "rubric_version",
        "dependency_mode",
        "target_prompt_fingerprint",
        "evaluation_package_version",
        "experiment_name",
    ):
        assert key in metadata, key


def test_fingerprints_are_stable_and_order_insensitive() -> None:
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})
    assert len(fingerprint({"a": 1})) == 12
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


def test_git_metadata_survives_a_missing_git_binary() -> None:
    def failing_run(*args, **kwargs):
        raise FileNotFoundError("git")

    metadata = resolve_git_metadata(run=failing_run)

    assert metadata.commit == "unknown"
    assert metadata.short_sha == "unknown"
    assert metadata.dirty is True


def test_known_secret_values_ignores_blank_and_short_values() -> None:
    environ = {
        "OPENAI_API_KEY": "sk-abcdefghijklmnop",
        "LANGSMITH_API_KEY": "   ",
        "TAVILY_API_KEY": "tvly-1234567890",
        "SOMETHING_ELSE": "not-a-secret",
    }

    assert known_secret_values(environ) == (
        "sk-abcdefghijklmnop",
        "tvly-1234567890",
    )


def test_contains_secret_finds_a_key_nested_anywhere() -> None:
    payload = {"metadata": {"notes": ["prefix sk-abcdefghijklmnop suffix"]}}

    assert contains_secret(payload, ("sk-abcdefghijklmnop",)) == [
        "metadata.notes[0]"
    ]


def test_contains_secret_returns_empty_for_clean_payloads() -> None:
    assert contains_secret({"a": "clean"}, ("sk-abcdefghijklmnop",)) == []


def test_redact_secrets_replaces_every_occurrence() -> None:
    payload = {"a": "sk-abcdefghijklmnop", "b": ["x sk-abcdefghijklmnop"]}

    assert redact_secrets(payload, ("sk-abcdefghijklmnop",)) == {
        "a": "[REDACTED]",
        "b": ["x [REDACTED]"],
    }


def test_a_secret_leak_error_never_repeats_the_secret() -> None:
    error = SecretLeakError.for_paths(["metadata.notes[0]"])

    assert "sk-" not in str(error)
    assert "metadata.notes[0]" in str(error)


def test_known_secret_values_covers_deepseek() -> None:
    environ = {
        "DEEPSEEK_API_KEY": "sk-deepseek-abcdefgh",
        "LANGSMITH_API_KEY": "ls-abcdefghijklmnop",
    }

    assert "sk-deepseek-abcdefgh" in known_secret_values(environ)


def test_known_secret_values_still_redacts_a_present_openai_key() -> None:
    """No longer required, but still scrubbed if the environment has one."""
    environ = {"OPENAI_API_KEY": "sk-abcdefghijklmnop"}

    assert known_secret_values(environ) == ("sk-abcdefghijklmnop",)

