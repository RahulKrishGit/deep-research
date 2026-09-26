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
from deep_research.providers import validate_agent_model_configs
from deep_research.utils.config import (
    AgentRuntimeConfig,
    ConfigSettings,
    EvaluationConfig,
    LLMConfig,
    load_config,
)

# This is a **drift alarm, not an attribution mechanism**. Because
# ``agent_prompt_fingerprint`` hashes the whole shared ``agents.prompts`` module,
# these values move when *any* agent's prompt text changes — that is what makes
# them useful as a "did a prompt edit land" signal, and useless for saying
# whose. And attribution is recoverable anyway while the tree is clean:
# artifacts record ``git_commit``, so the change is explained by its diff.
# Attribution is genuinely lost only when a fingerprint was recorded from a
# dirty tree whose exact source snapshot was not kept.
#
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
# coverage identities on the claim record), deletes the lossy ``_finding_is_new``
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
# critique-gap objects carrying a plan ``coverage_id``, handed it one
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
# ``CritiqueDraft`` and the critique contract into one shared ``normalize_gap_drafts``,
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
# was already given on the claim record it returns — its ``insufficient_reason``
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
# Task 4's re-pin moved all six with the shared prompt module, and the source evaluator moved for
# its own module change as well — ``b2ce33c07533`` -> ``4dbe292964d2``, an
# import-order fix in its own module with no further prompt edit, which is the
# documented false positive of hashing a module's whole source rather than its
# prompt text. The other five are unchanged from the first Task 4 pin.
# Task 4's fix round 5 re-pins all six once more: the shared scoring contract
# now asks for a verbatim quote beside every temporal value, and the Source
# Evaluator's own module gained the quoted draft fields and dropped the
# value-only parsing path. The Judge pin is unchanged.
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
#   critic ``15754f64fa12`` -> ``3f0341751794`` -> ``98c2864fd56c`` ->
#     ``6da1052f44b6`` in fix round 3 (the score and gap bounds became part of
#     the reply contract, so an out-of-range or overflowing reply is repaired
#     instead of clamped or truncated), -> ``30496ba14ad6`` in fix round 4 (the
#     provider boundary was swept: strict and non-blank fields, no legacy string
#     gap on the live schema, a re-validated reply, whole evidence excerpts and
#     no unit ceiling, every claim and source rendered, conservative badges),
#     -> ``24f2a9be4630`` in the same round's cleanup (the dead bound constants
#     and the ignored ``claim_digest`` argument removed)
# Task 8 moved all six. The critic's own module changed (the tool path removed,
# the packet and its fingerprint added, the request sections rebuilt, and the
# repair path's failure handling split between schema and provider causes), and
# the shared ``agents.prompts`` module changed (the tool-free review prompt, the
# typed gap contract, the rewritten examples, and the repair instruction), which
# moves the other five even though their prompt text is byte-identical:
#   planner ``da4407dd9d39`` -> ``02d66a4a2d15``
#   researcher ``b4f9d21a34b4`` -> ``438bd632edce``
#   source_evaluator ``33a71376d3e6`` -> ``6c12c0fffc92``
#   fact_checker ``f1ccaf38ff02`` -> ``7695ca2d0524``
#   synthesizer ``17bc15e0131b`` -> ``0ec21503cc00``
#   critic ``3f0341751794`` -> ``98c2864fd56c``, -> ``6da1052f44b6`` in fix
#     round 3 (no prompt string was edited: the reply contract's bounds moved),
#     -> ``30496ba14ad6`` in fix round 4 (the same: the swept boundary is the
#     module that renders the request, not the shared prompt library), and
#     -> ``24f2a9be4630`` when that round's dead bound constants were removed
# The Judge pin did not move: no judge prompt or template changed.
# Task 9 re-pinned planner, researcher, and critic together, and left the other
# three untouched. None of the three moved for a prompt edit: the planner's
# ``state_update`` stopped re-emitting a topic list the session already has, the
# researcher's refinement eligibility moved from "a prior finding exists" to
# "the required target is still unanswered", and the critic's gap dedupe gained
# the repair action. Each is a behavioural fix inside a module the fingerprint
# hashes in full — the same false positive of its design already recorded for
# the researcher above, and the reason these pins are re-recorded deliberately
# rather than silently invalidated.
#
# Task 9 fix round 1 re-pinned planner and fact_checker; the other four did not
# move. Again no prompt text changed: the planner gained the branch that
# *extends* rather than re-plans when the graph routes an ``extend_plan`` job to
# it, and the fact checker gained the read of the refinement targets that makes
# an ``adjudicate``/``consolidate`` route land on a node which acts on it.
#
# Task 9 fix round 2 re-pinned the planner alone (`f2507b56d0c6`): the routed
# extension now records a failed extension instead of letting the PlanningError
# halt a run that already holds a publishable report. Same false positive, same
# reason — a behavioural fix inside a module the fingerprint hashes in full.
#
# Task 11 re-pinned the synthesizer alone (`0ec21503cc00` -> `2af90b7ac8a8`).
# As with Task 9's re-pins, no prompt instruction changed: the synthesizer's
# only edit in the round was adding the `quality_report_filename` helper, which
# names the third publication artifact (`-quality.json`) the way the evidence
# ledger's name is already derived from the reader report's. The shared
# `agents.prompts` library was not edited at all, and the other five agents'
# pins did not move (verified by recomputing all six). The move is a false
# positive of the documented design — the fingerprint hashes the whole module
# source, so a new pure name helper shifts it exactly as a prompt edit would.
#
# Task 12 re-pinned the researcher and the synthesizer together (`4f68ae8f190d`
# -> `613603dc5cbd`, `2af90b7ac8a8` -> `97cf77acbb15`). As with Tasks 9 and 11,
# no prompt instruction changed: the round corrected the orientation of
# the claim record's `evidence_selection`, which is keyed by the evidence id and valued with
# the stance, and one call site in each of those two modules read `.values()`
# where an evidence id is required, so the stance string was handed to every
# consumer expecting an id. The shared `agents.prompts` library was not edited,
# and the other four pins did not move (verified by recomputing all six).
#
# Task 6 repair re-pinned the fact_checker alone (`77ac66835773` ->
# `246b797b9534`). As with the re-pins above, no prompt instruction changed: the
# round wired `consolidate_claims` into `FactCheckerAgent.run`, which added one
# call site, one completion-event metadata field, one degraded-consolidation
# helper, and one update key to the module. The shared `agents.prompts` library
# was not edited, and the other five pins and the judge did not move (verified
# by recomputing all six and the judge).
#
# The support-policy repair re-pinned the fact_checker alone (`246b797b9534` ->
# `7012a186eb59`). No prompt instruction changed, and no prompt text moved: the
# claim admission gate now passes the claim's own ``evidence_status`` through
# ``admitted_target_ids`` into ``claim_meets_support_policy`` — the predicate
# lives in the since-deleted claim-cluster module, which this fingerprint does not hash —
# so that a faithful source-supported primary attribution can answer a
# ``primary_attribution``/``derivation`` target, and the call site says so.
# The shared `agents.prompts` library was not edited, and the other five pins
# and the judge did not move (verified by recomputing all six and the judge).
#
# The comparison answer form re-pinned the planner alone (`f2507b56d0c6` ->
# `4fab1aa863d8`). This is real prompt-text drift and is meant to be:
# ``_ANSWER_FORM_REQUIREMENTS["comparison"]`` is rendered into the obligation
# the researcher is handed, and it read "for every option compared" — a phrase
# sharing no word with ``utils.types._DIMENSION_SIGNALS``, so the obligation it
# stamped onto every target of a comparison question was one no recorded
# proposition could ever fill. It now reads "for every option in the
# comparison", whose noun is the token the ``("share", "proportion", "percent",
# "denominator", "comparison")`` group already keys on, so a statement resting
# on evidence that states its share basis credits the dimension. The obligation
# changed; what counts as discharging it did not. The other five pins and the
# judge did not move (verified by recomputing all six and the judge).
#
# The boundary-audit sequence repair re-pinned the fact_checker alone
# (`7012a186eb59` -> `c4d37173726c`). This is module-source drift, not prompt
# text: the pin hashes the agent's whole module, and ``_record_packet_audit``
# now mints its sequence from a counter that survives a pass boundary instead
# of from the length of a dict each pass clears. No instruction, packet
# rendering, or verdict rule changed — the audit id is bookkeeping a replay
# joins against, never a prompt input. The shared `agents.prompts` library was
# not edited, and the other five pins and the judge did not move (verified by
# recomputing all six and the judge).
#
# The claim-pool target link re-pinned the fact_checker again
# (`c4d37173726c` -> `70fa432dfc6d`). Again module-source drift rather than
# prompt text: ``claim_evidence_pool`` resolves a claim's obligation ids
# through the plan to the sub-topics they live in before matching them against
# a unit's target ids. No instruction, packet rendering, or verdict rule
# changed; what changed is which reads reach the packet the instruction is
# applied to. The shared `agents.prompts` library was not edited, and the
# other five pins and the judge did not move (verified by recomputing all six
# and the judge).
#
# The shared acquisition-audit sequence repair re-pinned the researcher alone
# (`613603dc5cbd` -> `25fba5d22654`). As with the boundary-audit sequence repair
# above, this is module-source drift and not prompt text: the researcher now
# holds one ``ManifestSequence`` counter for the whole run and hands it to every
# ``AcquisitionPolicy`` it spawns per sub-topic, so policies sharing one
# ``boundary_audits`` mapping claim distinct sequence numbers and no sub-topic
# overwrites another's admission or selection manifests. No instruction,
# packet rendering, or verdict rule changed — the audit id is bookkeeping a
# replay joins against, never a prompt input. The shared ``agents.prompts``
# library was not edited, and the other five pins and the judge did not move
# (verified by recomputing all six and the judge).
#
# Bug 3's instance-scoped audit sequence fix re-pinned the researcher and the
# fact_checker together (`25fba5d22654` -> `70d8d679ea89`, `70fa432dfc6d` ->
# `97de796ffb4a`). Module-source drift again, not prompt text: a second
# construction of either agent against a state that already carries
# ``boundary_audits`` — a checkpoint restore, in production, though currently
# unreachable there — now seeds its own audit-sequence counter from
# ``len(state.boundary_audits)`` at the start of ``run()``/a pass, instead of
# always starting at 0, so it cannot remint an id an earlier instance already
# claimed for the same job/agent/operation. No instruction, packet rendering,
# or verdict rule changed — the sequence is bookkeeping a replay joins
# against, never a prompt input. The shared ``agents.prompts`` library was not
# edited, and the other four pins and the judge did not move (verified by
# recomputing all six and the judge).
# The canonical source identity fix re-pinned all six together
# (`4fab1aa863d8`/`70d8d679ea89`/`6c12c0fffc92`/`97de796ffb4a`/
# `97cf77acbb15`/`9694e44926d3` -> `3172856c4bfc`/`d35dcbd0689a`/
# `8e5bd6b86a3a`/`40afc3090052`/`27b2c2acc01c`/`3d6a1db27572`). This is real
# prompt drift, and it is the intended kind: ``agents.prompts`` gained
# ``ADJUDICATION_DEPENDENCE_INSTRUCTION``, which the adjudication request now
# carries beside the response contract — the model is asked for each passage's
# ``dependence`` and, when its figure comes from another candidate's origin,
# for that origin's id — and the scoring instruction now asks for
# ``derived_from``, the works a document says its data come from. Both are new
# contract text the model has to read, so every agent that renders the shared
# library moves with it. ``CLAIM_VERIFICATION_INSTRUCTION`` itself is
# unchanged; the new section cites it rather than replacing it. The
# source_evaluator's value moved a second time within the same change
# (`8e5bd6b86a3a` -> `ae6aac567f2b`) for module-source drift alone: the private
# single-read identity helper it now shares with the batch resolver was renamed
# to ``_read_fitness_identity`` so no private name echoes the removed
# ``evidence.read_identity``. No instruction changed; the other five did not
# move. Two follow-up fixes moved it twice more for module-source drift alone:
# ``ae6aac567f2b`` for the mirror-origin rule (``source_origin_id`` gained
# ``COPIED_TRANSPORT_RELATIONS``), and ``e098e605abc4`` for the legacy-snapshot
# rule (``resolve_read_identities`` now takes ``ReadIdentityRequest``, so the
# single-read caller builds one). No prompt text changed either time, and the
# other five and the judge did not move (verified by recomputing all six).
# The Fact Checker verdict-correctness pass re-pinned the fact_checker alone
# (`40afc3090052` -> `2fb6e7611b15`, -> `efb73308d093`). Module-source drift, not prompt text: the
# adjudication request now renders the candidates a bounded budget can carry
# (with the rest recorded as explicit omissions), a refutation is material by
# scope rather than by ``complete_support``, a contradiction is read from the
# union of the model's selections and its rows, and a duplicated assessment row
# makes its id unusable. ``agents.prompts`` was not edited, and the other five
# and the judge did not move (verified by recomputing all six).
# The Fact Checker adjudication-contract pass re-pinned all six again
# (`3172856c4bfc`/`d35dcbd0689a`/`e098e605abc4`/`efb73308d093`/
# `27b2c2acc01c`/`3d6a1db27572` -> `7d0282b16bc5`/`7288d912bee3`/
# `ad9e2afac12c`/`87fc0bb24c42`, and again to `96e265a90f59` for the shared excerpt budget, and `0b24ed022ed8` for the review fixes, and `d14573d8be42` for the widened evidence_not_admitted wording, and `9c5cba6e9b39` for the unstated-scope reading (the packet's own record of what it could not carry, the least-creditable duplicate row, and unclear stances that block without asserting)/`3a96c3e6293d`/`2c80a78040b9`). Real prompt
# drift: ``agents.prompts`` gained ``ADJUDICATION_INSTRUCTION``, the packet
# path's own response contract — it asks for the record ``ClaimVerdictDraft``
# actually accepts (assessment rows, selections by id) and states what
# ``complete_support``, ``scope_compatible``, and a refuting ``stance`` mean.
# The legacy ``CLAIM_VERIFICATION_INSTRUCTION`` is unchanged and still used by
# the extracted-claim path, but the shared library moved, so every agent that
# renders it moves with it. The judge did not move.
# The report-gates pass moved the synthesizer alone, to ``26372cb8f056``, for
# the composition-build change that runs the word-limit fit where the
# composition is built instead of inside each renderer. No prompt text changed
# and ``agents.prompts`` was not edited: ``build_report_composition`` now
# returns the fitted composition with its fit reasons recorded on it, which is
# a module-source edit and therefore a fingerprint move. The other five and the
# judge were recomputed and did not move.
# The branch-review target-view pass moved the critic alone, ``2c80a78040b9``
# -> ``aedce1ccca9e`` (§2.1): ``CriticTarget.open`` stopped being a second
# definition of coverage — a boolean the packet derived by pooling
# ``answered_dimensions`` across every statement naming the target — and the
# packet now records ``answered`` straight from the target-answered predicate, the
# gate that decides coverage, with ``open`` as its negation. Again a
# module-source edit with no prompt instruction and no shared-prompts edit,
# which the other five pins prove: they hash that same module and none of them
# moved. ``test_the_critic_target_view_repin_is_module_source_drift_not_prompt_text``
# is the evidence for that claim rather than this comment asserting it.
# The merge-conditions pass moved two together, ``53c371093ae9`` ->
# ``00e2229ad4fa`` and ``26372cb8f056`` -> ``dadb72078555``, for two structural
# edits: the Fact Checker's ``state_update`` now publishes the cumulative
# source snapshot beside the reads it just made, so a document read during
# verification is saved with the assessment of it, and the Synthesizer stamps
# ``generated_on`` from the run clock it now takes as an injected dependency
# while ``as_of`` comes from the recorded evidence timestamps instead of every
# graph event. No prompt text changed and ``agents.prompts`` was not edited —
# the other four pins and the judge hash that same module and none of them
# moved, which
# ``test_the_merge_conditions_repin_is_module_source_drift_not_prompt_text``
# asserts rather than this comment.
# The read-identity pass moved the fact checker alone, ``00e2229ad4fa`` ->
# ``340b8267dbe9``: a body the run already holds is now admitted under the
# description it was recorded with, so a re-read of one page under another
# spelling of its URL (``www.`` is normalized away in the registry) cannot
# restate one read identity and have the node's whole state update refused.
# Module source only — ``acquisition.py`` holds the rule and is not one of the
# six pinned modules, and the shared ``agents.prompts`` library was not edited,
# which the other five pins and the judge prove.
# ``test_the_read_identity_repin_is_module_source_drift_not_prompt_text``
# asserts rather than this comment.
# The stale-anchor question-year pass moved the planner alone, ``d2d7dec17bcd``
# -> ``fc6518cbfe60``: ``stale_year_anchors`` gained the ``question_years``
# exemption, and ``target_problems`` now derives it once from the frozen
# question, so a year the question itself names is never reported as a stale
# currency anchor. Module source only — no prompt string was edited and the
# shared ``agents.prompts`` library was not touched, which the other five pins
# and the judge prove.
# The planning-gate policy pass moved the planner again, ``fc6518cbfe60`` ->
# ``44d9a4e8842d``: ``finalize`` now ends a planning pass only when neither the
# draft nor its one repair is structurally valid, records every defect that
# outlived its repair as ``planner_plan_defects_unresolved`` with the plan it
# came from, and falls back to the reviewed plan when the review's repair
# cannot be produced. ``_request_plan`` returns a ``_PlanAttempt`` carrying the
# two problem lists and the label, which is a module-source change like the
# one above — no prompt string moved, and the other five pins and the judge
# are unchanged.
# The review round on that pass moved the planner a third time,
# ``44d9a4e8842d`` -> ``cd1480236a3b``: ``target_problems`` was partitioned
# into structural and advisory problems (the target count and the question
# form decide whether a plan can be executed, the anchor and tolerance lints
# do not), the lint repair now degrades to a draft that is still researchable
# instead of ending the run, the review's findings are recorded against the
# plan that stands on both review-repair fallbacks, and the model-facing
# repair request is back to unlabelled problem lines. Module source only, as
# before. ``_plan_problems`` returns the same *lines* the unlabelled list
# always had, in the same report order, but the repair request is not
# byte-identical to the one the previous pin covered in one case: when one
# sub-topic carries an advisory problem and another a structural one, the
# attempt's ``problems`` renders them grouped structural-first, and the
# pre-partition list rendered them in source order. Same lines, different
# order, and only in that mixed case — a plan whose problems are all one kind
# renders exactly what ``f536a09`` rendered. The other five pins and the judge
# are unchanged.
# The scratch-docstring pass moved the planner a fourth time,
# ``cd1480236a3b`` -> ``062766ba4600``: ``_PlanAttempt``'s docstring said
# structural problems were ``validate_plan_draft``'s alone, and they have also
# been the two local ones that make a plan unexecutable — the 1-4 target count
# and the question form — since the partition above. Documentation only, so no
# behaviour moved: no prompt string was edited, and the other five pins and
# the judge are unchanged.
# The output-limit pass moved the critic alone, ``aedce1ccca9e`` ->
# ``99e1ca09d28e``: a review call truncated by its output limit is re-asked
# once at ``high`` under the same cap, and a second truncation leaves the run
# without a critique instead of ending it. Module source only — no prompt
# string was edited and the shared ``agents.prompts`` library was not touched,
# which the other five pins and the judge prove. The same pass then moved it a
# second time, ``99e1ca09d28e`` -> ``4d3c1233230a``, when the Critic's one
# repair got the same one-retry rule: the repair request is the longest review
# request the run makes, and a truncation there now degrades instead of ending
# the run. Module source only, again — no prompt string edited, the other five
# pins and the judge unchanged.
# The report-call pass moved two at once. The critic moved a third time,
# ``8207599d7f6c`` -> ``4d3c1233230a``, when the shared retry values
# (``OUTPUT_LIMIT_RETRY_EFFORT``, ``OUTPUT_LIMIT_RETRY_OUTCOMES``,
# ``OUTPUT_LIMIT_ATTEMPT_EFFORTS``) moved out of ``agents.critic`` into
# ``agents.base``, the module that owns the structured-call contract all three
# callers go through; the synthesizer moved ``a41d1f86be3b`` ->
# ``dadb72078555`` because its own report call — the largest output the run asks
# for, 27,301 of 32,768 tokens live — now gets that same one retry. Module
# source only for both, and no prompt string or shared prompt library was
# touched: the other four pins and the judge are unchanged.
# Report-quality integration changed the shared prompts and five agent modules:
# evidence visibility, answerable planning, research mining, and honest reports.
# All six source fingerprints moved; the Judge template did not.
# The live-cycle review fixes (session 08b9b469) moved four: planner
# 2b41c6c1061e -> e4f201e4549d (currency phrases are not rankings, and the
# per-issuer primary_attribution rule in the plan instruction), researcher
# f3b71c3a22ac -> 9a5725dfbd0c (the owed-figure re-extraction section),
# fact_checker 76bc5eb6a572 -> edba273d5f0f (scoped pool, capacity cap,
# relevance-first rendering, single-source loop stop), synthesizer
# 997e7581f1b3 -> 73c7f5edb7c8 (word-boundary display clamps). The source
# evaluator, the critic and the judge did not move. The integration review's
# fix loop then moved two more: fact_checker edba273d5f0f -> 78720bc778f2
# (a linked passage must state the claim's measurement, and a single-source
# skip keeps its one bounded retrieval) and synthesizer 73c7f5edb7c8 ->
# 49d2da844853 (Key facts rows derived from the answering statements).
# The re-review then moved the same two again: fact_checker 78720bc778f2 ->
# 4d5e0b507004 (a rival figure is admitted only when it measures the claim's
# own quantity, and the request budget rose to 8000 characters) and
# synthesizer 49d2da844853 -> 51ffa46f3cff (a year the finding does not
# claim is not its period; a reused statement is recorded once).
# The combined expert plan (scratch/COMBINED-PLAN.md) then moved all six:
# planner e4f201e4549d -> 76c38dbf1bc3 (named-issuer policy floor, explicit
# as-of reader), researcher 9a5725dfbd0c -> 5ea8099692f5 (relay attribution,
# scope and release date on findings), source_evaluator 7a1a4f49d6e7 ->
# fbb1774daf57 (written dates kept at their precision), fact_checker
# 4d5e0b507004 -> bee2d47bb751 (issuer badge from the evidenced publisher;
# a proven independence failure is the published reason before the badge's),
# synthesizer 1d89e3e964cf -> 5fd094e79b42 (claim-scoped figures, per-claim
# provenance), critic ca830cca71d6 -> 4a61be93dece (shared prompt module).
# The audit-2 live-run fixes then moved all six again: planner -> 60674f0feaa5
# (checkable dimensions, unrequested-edition repair), researcher ->
# 179a37b60a01 and source_evaluator -> 6f7b26cab9f8 (shared prompt/evidence
# modules: commercial first-party issuer), fact_checker -> 7fb671ea77db
# (output-limit retry, first-party provenance, retained finding figures),
# synthesizer -> c67fa82b43dd (name attestation, citation narrowing, bound
# claims rendered), critic -> 709e7d14adb6 (shared prompt module).
# The audit-3 fixes moved all six once more: planner -> 7334bfd710e7
# (unrequested-measure and paywall targets planned optional), researcher ->
# d81af60c3103 and source_evaluator -> 1a7f057fad85 (shared modules),
# fact_checker -> 96e323f7271a (every planned target a binding candidate),
# synthesizer -> aa747550f226 (one label space, narrowed citations, restated
# bound claims attached, rejected points published), critic ->
# 9f62f005745a (shared prompt module).
# Evidence Verifier plan, Tasks 1.3, 1.4 and 3.3: researcher and planner
# module edits; wording rules moved to agents/wording.py.
# Task 1.5 fix round 1: Task 1.3's own fix round then moved the researcher's
# value again, ``981e42c6588f`` -> ``d6200b7bd918``: a dropped figure is now
# recorded under its own error type instead of being folded into
# ``researcher_invalid_finding``. Module-source drift only — no prompt string
# was edited, and the other five pins are unchanged.
# Evidence Verifier plan, Task 2.1: attribution helpers moved to evidence.py.
# Task 2.1 fix round 1 ("review round 1 - honesty-rule fixes for relay
# windowing and correction scope") then moved the researcher's value again,
# ``bdc3400c3cec`` -> ``90f3fa33d7da``: its import block dropped the now-unused
# ``ATTRIBUTION_CUE_PATTERN`` (the relay-windowing fix moved that pattern's
# only remaining use into ``evidence.py`` itself) and reordered the
# ``_ENERGY_UNIT``/``_POWER_UNIT`` imports from ``utils.types``. Module-source
# drift only — no prompt string was edited, and the other five pins are
# unchanged.
# Evidence Verifier plan, Task 4.7: the harness follows the pipeline to its
# five agents, so the Fact Checker's, Synthesizer's and Critic's pins are gone
# with their case files and ``evaluation_verifier``/``report_writer`` join the
# table. The values are computed on this branch with the Task 1.5 command;
# Task 4.10 re-pins them once every parallel task has landed.
# Evidence Verifier plan, Task 4.10: agent set and shared prompts changed.
# Evidence Verifier plan, Task 5.4 (D10, D11): planner, researcher, source
# evaluator, evidence verifier and report writer prompts and the shared prompts
# module changed; one re-pin for the phase.
# Task 5.6c fix round 2 (same round, PD-17): the Report Writer's restatement
# guard now asks ``verified_facts.subject_names_row`` instead of
# ``subject_named_in``/``subject_context`` directly, so its module source moved
# ``c58bc58d74fe`` -> ``5ed194fd4a41``; no prompt string was edited and the other
# four pins are unchanged.
# Task FF1 ("the review's evidence-core findings", final review slice 1): the
# Evidence Verifier's own module source moved
# ``21c413e91f94`` -> ``25e9124b78a1`` — the Context Check now clears a recorded
# period or subject the reply answers null on, corroborates a relative period
# against the years the words state, checks the finding's statement date against
# the read before using it as the page-date basis, halts on a provider
# configuration error, and judges a re-extraction whose target binding grew; its
# relay rule no longer falls back to the finding's admitted issuer for a
# different proposed body.
# No prompt string was edited, and ``agents.prompts`` was untouched, so the
# other four target pins and the Judge pin are unchanged.
# Task FF2 (the final review's report-path fix round, same rule, PD-17): the
# writer's restatement guard now asks ``report._carried_rows`` -- the one rule
# the reader's labels ask too -- and ``_figure_label_for`` moved to
# ``report.py`` beside ``figure_label``, so the Report Writer's module source
# moved ``5ed194fd4a41`` -> ``6dbc0f346565``. No prompt string in the writer was
# edited (the reviewer's own prompt, which is not a pinned target, was), and the
# other four pins are unchanged.
# The final-review fix round for the planning and research slice moved two
# pins, and only those two. planner ``46a9cddd0896`` -> ``89b638901396``: the
# plan instruction now states that an organisation is a body's own name and
# never a description of its role, ``apply_answer_contract`` empties a value
# that only describes one (the live P3/P4 defect that pre-failed a target), the
# first plan review is guarded like the confirming one, the widening lint needs
# a pairing rather than one domain word, the word-limit pattern needs a length
# frame, and the dead answer-form classifier block was deleted. researcher
# ``f9ad12ff184b`` -> ``c490b869021b``: the dates contract routes a relative
# phrase ("this year") through the page-date resolution path instead of
# recording it as a period, ``statement_date`` is admitted against the page the
# way ``release_date`` is, the provenance contract says where the attribution's
# words may sit (the excerpt's own passage or its neighbour), an output-limit
# truncation is a per-sub-topic failure rather than a provider outage, and an
# extra pass whose sub-topics have spent their acquisition budget is not
# opened. Both moves are module-source changes to the two agents' own modules;
# the shared ``agents.prompts`` library was not edited, which the other three
# pins prove (source evaluator, evidence verifier and report writer are
# unchanged), and the judge pin did not move.
# The same task's probe-driven rules, continued: the planner moved again,
# 3a29cacf5d6c -> 07d3633895c1, for a question answered by an argument or a text
# planning no figure target of its own (a why-question's parts are reasons), and
# for the organisation guard's test becoming a *name shape*: a lower-case
# description is emptied while a capitalised or acronym body - including one a
# role-word list used to empty, and including two bodies joined by a capitalised
# conjunction - stays stamped. The plan instruction's two reply examples now
# spell their bodies as names for the same reason. Module source and two
# instruction strings of the planner's own; the researcher, the other three and
# the judge are unchanged.
# other four pins are unchanged. The controller's final follow-up then reworded
# that module's ``_WRITER_ATTEMPT_EFFORTS`` comment (the last "synthesizer"
# mention in it), a comment-only edit that moves the same value again,
# ``6dbc0f346565`` -> ``69b86b8b87a2``; the four other pins stay put.
# FF2 round 4 (the ev-1 audit's A6 and A2/A3 minors, PD-17): two rules were
# added to ``REPORT_WRITER_INSTRUCTION`` -- a section adds something the
# executive summary does not carry, and a judgement is never stated with the
# criterion it is measured by dropped -- so the writer's value moved
# ``69b86b8b87a2`` -> ``a3a73e32c3c0``. This one *is* prompt drift, and the only
# prompt edited; the four other target pins and the Judge pin are unchanged.
# FF2 round 5 (the run-2 audit's improvements 2 and 11, PD-17): the writer's
# instruction now states the point length bound and requires section titles in
# the cited findings' own words with no page metadata as bullets, and the module
# gained ``_split_oversize_point`` -- an over-length drafted point is split at a
# sentence boundary keeping its citations instead of being refused whole
# (improvement 2) -- so the writer's value moved ``a3a73e32c3c0`` ->
# ``24ccad97fb9f``. Prompt drift and the module's own code; the four other pins
# and the Judge pin are unchanged.
# Task FF1 follow-up (reporting cues, the own-page host rule, a publication cue
# for the quoted date; final review slice 1): the Evidence Verifier's own module
# source moved `25e9124b78a1` -> `c24bfe646317` — `_owns_page` now takes PD-18's
# own-page rule first and counts an identity-words match with the Source
# Evaluator's issuer only beside the page's own credit of itself. No prompt
# string was edited, and ``agents.prompts`` was untouched, so the other four
# target pins and the Judge pin are unchanged.
# The probe re-run's regression ruling moved the planner once more,
# 07d3633895c1 -> 8e875d9b2b53: the required-flag word test is gone (it removed
# required from all eleven targets of the headphones question, whose
# measures paraphrase it, so the plan owed nothing), the plan instruction now
# carries the sentence that makes a planner-added aid optional, and a plan whose
# dropped defective target was the whole of what it owed has its first surviving
# obligation required again. The instruction string moved with it; the
# researcher, the other three and the judge are unchanged.
# The final probe's independent grade moved the planner once more,
# 8e875d9b2b53 -> 3e987c934fae: the instruction now says a target the plan
# introduces to organise the research is optional (a sub-category, aspect,
# example, event or list item) and that a product's or drug's performance is
# answered by the body that measured, tested or approved it rather than the
# maker, while a law's authority is the body that adopted it rather than the
# office that publishes it; and it says every figure target of a windowed
# question carries that window as its period. Code moved with it: the question's
# own bounded window is stamped on figure targets that state no period, a
# text-answered question's added quantity target is optional and formless, and a
# plan never owes nothing after either correction. The instruction strings and
# the planner's own module moved; the researcher, the other three and the judge
# are unchanged.
# Task FF1, the capped live pre-flight's two defects (final review slice 1):
# the Evidence Verifier's own module source moved
# `c24bfe646317` -> `7889ed57c042` — `resolve_attribution` takes the figure's
# own evidence words and repairs a verdict that left the attribution
# unresolved from them, and `_cued_name_candidates` skips a cue's article
# ("reporting from the U.S. Energy Information Administration"). No prompt
# string was edited, and ``agents.prompts`` was untouched, so the other four
# target pins and the Judge pin are unchanged.
# FF1 round 3 (the pre-flight dry-run audit's two general defects): the Evidence
# Verifier's own module source moved `7889ed57c042` -> `2a68e627d577` — every
# period test the enforcement makes now asks ``_period_stated``, which reads the
# words literally or in the spelling their own fold agrees on ("Q1'25" is
# "Q1 2025"). No prompt string was edited, and ``agents.prompts`` was untouched,
# so the other four target pins and the Judge pin are unchanged.
# D10 (controller, before the second live run): the planner's instruction
# carried a worked example drawn from one of the eight probe questions (a drug
# maker's name and "the manufacturer of" that drug). The example is now neutral
# ("Siemens"; "the manufacturer of the product"); the planner moved
# `3e987c934fae` -> `852f96eefee7`. The other four target pins and the Judge
# pin are unchanged.
# Run-2 improvement 5 (FF4Orch; the run-2 audit's extra-pass duplicates):
# ``_recorded_findings`` hands the run's findings to each sub-topic's policy so
# the decision packet can list what is already mined, and
# ``_admitted_evidence_keys`` seeds the figure-owed re-extraction with the
# passages the run has already mined rather than with this pass's alone. (On
# this item's own base the researcher hashed `ba937bd6c817`; resolved onto the
# run-2 wave's researcher the merged source is `7314b246b217`, below.)
# Run-2 improvement 6 (FF4Orch; the run-2 audit's "the graph cannot act on
# material defects"): ``ReportWriterTask`` carries the material defects the
# graph's writer re-run was bought for, ``build_task`` reads them off the stored
# review, and ``writer_messages`` renders them as one more bounded section, so a
# re-draft is asked about the defects it exists to fix. (On this item's own base
# the writer hashed `22e4e86d5f42`; resolved onto the wave's writer the merged
# source is `1bfcd866e32f`, below.) Neither item edited a prompt string, and
# ``agents.prompts`` was untouched, so the other three target pins and the Judge
# pin are unchanged.
# ev-1 fix A, follow-up (F6; the expert final review's Minor): the researcher's
# own module source moved `c490b869021b` -> `3853aff7e02c` — ``_policy_for_task``
# resets ``remaining_model_turns`` to the loop's ``max_iterations`` when a
# target's persisted acquisition state is reused for a new loop, so the packet
# cannot print a zero turn count beside "Iteration 1 of N" while the new loop
# has all of them; ``remaining_calls`` stays the run's. No prompt string was
# edited, and ``agents.prompts`` was untouched, so the other four target pins
# and the Judge pin are unchanged.
# FF1 round 7 (the expert final review's F2): the Evidence Verifier's own module
# source moved `2a68e627d577` -> `6145aac27573` — `resolve_attribution` no longer
# returns the host as a named body's own organisation: an "own" verdict the page
# does not evidence is that body's relay when the page cues it, and unattributed
# otherwise (a verdict naming the page's own host still stands). No prompt string
# was edited, and ``agents.prompts`` was untouched, so the other four target pins
# and the Judge pin are unchanged.
# FF1 round 8 (ReRevF2F4's N3): the Evidence Verifier's own module source moved
# `6145aac27573` -> `e8d10a516459` — F2's host guard is widened to the identity,
# so an "own" verdict naming the page's first-party owner (its name or its host)
# keeps the own-page reading while a body the page is not stays unattributed. No
# prompt string was edited, and ``agents.prompts`` was untouched, so the other
# four target pins and the Judge pin are unchanged.
# FF1 run-2 improvement 8: the Statement Check's response contract now names the
# conditions, exceptions and object a reported rule attaches to, and shows the
# bounded passage beside the snippet, so the verifier's prompt moved
# `e8d10a516459` -> `fbad809c4414`. No other agent's prompt text changed, and
# the Judge pin is unchanged.
# Run-2 improvement 3 (passage cutting): the researcher's own module source moved
# `3853aff7e02c` -> `90f46cf12b62` — a finding's snippet is now admitted when the
# read carries it at the finding's locator *or* in the immediate neighbouring
# passage (``_snippet_admitted_at``), because a passage cut at a sentence or
# clause boundary can leave a rule's clause in one passage and the object it
# attaches to in the next, and the excerpt drawn from the read then spans both.
# No prompt string was edited, and ``agents.prompts`` was untouched, so the other
# four target pins and the Judge pin are unchanged.
# Run-2 improvement wave (FixSelection, improvements 1B and 4): the
# Researcher's own module source moved `3853aff7e02c` -> `e5bbfe3ede99` — a
# required target left unanswered after a read that states its own words now
# buys one bounded re-ask, whose packet leads with the passages that state
# that target most; findings bound to a required target are exempt from the
# per-sub-topic caps, and a passage left unmined by that re-ask gets its own
# `unmined_target` disposition. No prompt string was edited, and
# ``agents.prompts`` was untouched, so the other four target pins and the
# Judge pin are unchanged.
# The run-2 wave merged two researcher changes (FixScraper's improvement 3,
# alone `90f46cf12b62`, and FixSelection's 1B+4, alone `e5bbfe3ede99`); the
# merged module is `068c5fe435ef`. FF2's improvements 2 and 11 moved the
# report writer `a3a73e32c3c0` -> `24ccad97fb9f`.
# The prompt-fix wave (Fable's review of the 20 model-read blocks) moved the
# planner, 852f96eefee7 -> 9f201233ac06: every planner block was rewritten to Fable's
# Directions - the scoping loop states what its final answer must contain and
# that it has one tool call; the plan instruction states each rule once in
# field order (planner-chosen bodies, added sub-categories and aids are
# optional; a target never asks for a verdict, a pick or a combination; a rule's
# parameter is not a quantity; a query says where to look and never carries the
# answer; a closed forecast period gets an optional outcome target) and its two
# reply examples now obey it (names or empty organisations, criteria that name
# the evidence and the measurement); the review instruction checks the fields
# that decide Not found and names the list each finding belongs to; a repair
# request prints the plan it repairs and the review request prints geography.
# Module source and prompt strings of the planner's own; the researcher, the
# other three and the judge are unchanged.
# Fable's prompt-fix round 2 (A1-A8, planner.py) moved the planner again,
# 9f201233ac06 -> 7909a3fde554: a repair request now prints the plan under repair above
# its repair list, with both wrappers saying so and the plan prompt tying the
# list to that printed plan; the two reply examples obey the required rule
# (the attributes the example question now names are required, the rest
# optional, and the why-question's required target is the reasons as sources
# state them, unbound to a body); the verdict rule is about who makes the
# judgement; only an uncalled-for sub-topic or a required target widens scope;
# geography comes from the question or the contract; the why/how clause and the
# derived-total wording are in; the review checks a rule parameter's unit/kind
# and a closed period's optional outcome target. Module source and prompt
# strings of the planner's own; the researcher, the other three and the judge
# are unchanged.
# The planner's bounded degraded retry moved the planner, 7909a3fde554 ->
# 646e8c2dbdf3: every plan-side structured request (the draft, its repairs, the
# review and the confirming review) that the output limit truncates is re-asked
# once with the same messages, schema and budget at OUTPUT_LIMIT_RETRY_EFFORT,
# recorded as a planner_output_limit_retry, and a second truncation propagates
# as before. Module source only: no prompt string moved, and the researcher,
# the other three and the judge are unchanged.
# The retry-review P3s (RevPlannerRetry) moved the planner, 646e8c2dbdf3 -> 8879aec67208: a
# retry that fails other than by truncation now reaches the caller as a
# redacted copy (diagnostics kept, provider chain cut), the reviewer's rule.
# Module source only; the judge and the other four are unchanged.
# Fable's round-2 optional planner follow-ups moved it again, 8879aec67208 -> 632bdf446f27:
# the repair header reads "return this plan corrected, not unchanged", the lint
# wrapper names what each problem concerns and says when no plan is printed,
# PLAN-5's verdict check mirrors PLAN-3's run-judgement rule, and example 1
# names the capital cost its required target measures (kind actual). Prompt
# strings and module source of the planner's own; the others are unchanged.
# Run-3 content wave review round moved it twice more, 5e36f9f4cb3b ->
# 7b9532f4fd85: (RevRouteR3 P1) _PLAN_REPLY_EXAMPLES' example 2 contradicted
# the new PLAN-3 ordering sentence (its priority-1 sub-topic was all-optional
# while priority-2 carried the only required target); "outbreak investigation
# findings" (required) is now priority 1 and listed first, "vaccination
# coverage" (all-optional) is priority 2. (Spec 2026-09-25 §4.5 Q2)
# `_CONSTRAINTS_MARKERS` gained "obligation"/"obligations" (regular plural),
# so a "what obligations does <law> place on <party>" question gets the
# constraints answer form. Module source and one marker string; the others
# are unchanged.
PINNED_TARGET_PROMPT_FINGERPRINTS = {
    # Lift the research-content limits (user decision 2026-09-25):
    # ``MAX_SUB_TOPICS`` 7 -> 10 and its "1-7"/"1-10" docstring mentions; the
    # per-topic evidence-target ceiling ``MAX_TARGETS_PER_TOPIC`` 4 -> 6, and
    # ``PLAN_INSTRUCTION``'s "between 1 and 4 evidence_targets" sentence now
    # interpolates ``MIN_TARGETS_PER_TOPIC``/``MAX_TARGETS_PER_TOPIC`` instead
    # of naming the old bound literally. No prompt sentence changed in
    # substance, only the numbers it states; ``agents.prompts`` was untouched,
    # so the other four target pins and the Judge pin are unchanged. Moved
    # `7b9532f4fd85` -> `4d3acb4ce085`.
    "planner": "4d3acb4ce085",
    # Run-2 review F4 (FixSelection) moved the researcher source once more:
    # `7314b246b217` -> `37bb78b1eca8` — the required-target exemption in
    # `bound_sub_topic_findings` is now capped at two findings per required
    # target, so it can no longer make the per-sub-topic cap meaningless. No
    # prompt string was edited, and ``agents.prompts`` was untouched, so the
    # other four target pins and the Judge pin are unchanged.
    # Code-only round (FixSelection, items 3 and 4): `_admitted_figures`
    # refuses a date-shaped figure (`figures.is_a_date`) and the owed-passage
    # re-extraction sends at most MAX_OWED_BATCHES packets of at most
    # MAX_OWED_PASSAGES_PER_BATCH passages, most-owing first, so the researcher
    # source moved `37bb78b1eca8` -> `0acf612e952f`. No prompt string was
    # edited and ``agents.prompts`` was untouched: the only new string a model
    # could ever read is the code-generated drop reason "states a date, not a
    # measure", which travels in the run's error details, not in a request. The
    # other four target pins and the Judge pin are unchanged.
    # The prompt-fix wave (Fable's review, RES-1..RES-8, EXTRA-2, EXTRA-8,
    # and cross-block 6/10/11/12/13) rewrote the researcher's model-read
    # text: the loop is told the rules the policy enforces and stops on the
    # sub-topic's obligations, the extraction request carries the research
    # question and each target's sub-topic and required flag, the registry
    # contract binds in an ordered step and defines confidence and content,
    # the date and provenance contracts state what the page must carry, the
    # owed-passage heading presents its passages as candidates, and the
    # reply examples fill only what their passages state. The module source
    # moved `0acf612e952f` -> `bce3b184f341`.
    # No other agent's module changed and ``agents.prompts`` was untouched
    # (RES-2's fixes are stated by the agents that read the shared contract),
    # so the other four target pins and the Judge pin are unchanged.
    # Fable's re-review round 1 (agent://FablePromptReReview1) closed five
    # items in this module: the relevance line now reads "bears on the
    # research question or on any planned target" so a passage answering
    # another sub-topic's target is still a finding (B1); the guidance's
    # second header is "What the evidence has to establish:" so one request
    # carries one stop rule (B2); the provenance block names the reporting-
    # verb cue family and the heading of the excerpt's own passage rather
    # than a page-wide "section heading" (B3); the figure example's vintage
    # is the edition its passage names verbatim (B4); and the two examples
    # that carried an energy unit are neutral (B5). The module source moved
    # `bce3b184f341` -> `cb4dd2d828c8`.
    # No other agent's module changed and ``agents.prompts`` was untouched,
    # so the other four target pins and the Judge pin are unchanged.
    # Lift the output limits (user decision 2026-09-25): the owed-passage
    # re-extraction now sends its own cap, agents.re_extraction_max_tokens,
    # while every other call keeps the lifted global cap. The researcher's
    # module source moved `cb4dd2d828c8` -> `449114f4b1ef`; no prompt string
    # was edited, and the other four target pins and the Judge pin are
    # unchanged.
    # Run-3 content wave (Fable's run-3 audit, agent://AuditRun3Fable,
    # D3/D4/D7/D8/D12): the selection query is built from the sub-topic's
    # target questions and the research question rather than the title
    # and success criteria; the extraction confidence definition ranks
    # this sub-topic's own obligations first and the per-sub-topic cap
    # ranks by them too, required first; RES-4 asks a judgement snippet
    # to carry its subject (taking the neighbouring sentence when the
    # referent sits there) and a code guard refuses one whose subject is
    # a bare pronoun with no referent; a caption, player title or
    # condition label is furniture, never a judgement; RES-1 asks one
    # more search in a required obligation's own words when the first
    # search returns only general pages. The module source moved
    # `449114f4b1ef` -> `3bbe364fcd0d`; no other agent's text changed, so
    # the other four target pins and the Judge pin are unchanged.
    # RevResearcherR3's review of the run-3 content wave (agent://
    # RevResearcherR3, D7/D12 P3): the bare-pronoun guard now triggers at a
    # clause boundary as well as a sentence start, skips an impersonal "It
    # is/was/has been ... that/to" construction and restricts "They" to
    # true linking verbs, and a referent may come from an internal-capital
    # or Unicode-aware-capital word named in an earlier sentence or from the
    # finding's own content, never from a plain-ASCII word that is merely
    # its own sentence's first word; RES-1 now also asks the loop to read
    # the page the extra search returns, not only search once more. The
    # module source moved `3bbe364fcd0d` -> `e00e6218a22b`; no other agent's
    # text changed, so the other four target pins and the Judge pin are
    # unchanged.
    # ReRevResearcherR3's review (agent://ReRevResearcherR3, P1): content's
    # own sentence-initial word now gets the same exclusion the snippet's
    # does -- an opening "Overall,"/"However,"/"According to reviewers,"/
    # "We" no longer counts as naming a judgement's subject, only an
    # internal-capital or non-ASCII-leading-capital word, or a plain-ASCII
    # leading capital that is not content's own sentence-initial word. The
    # module source moved `e00e6218a22b` -> `0e92c7cf1206`; no other agent's
    # text changed, so the other four target pins and the Judge pin are
    # unchanged.
    # ReRevResearcherR3's final round on this guard (F5, controller
    # decision): content's own sentence-initial word counts as a referent
    # unless it is a closed-class word, the introductory word "According"
    # (followed by "to", not a comma), or immediately followed by a comma
    # ("Overall,", "However,", "Meanwhile,") -- a bare product name opening
    # content's own sentence ("Sony is the model to beat.") now counts,
    # where the previous round wrongly refused it too. Later heuristic edge
    # cases on this guard are accepted residuals; the writer-side fix in
    # the report-format spec is the main protection. The module source
    # moved `0e92c7cf1206` -> `4245163b56a8`; no other agent's text changed,
    # so the other four target pins and the Judge pin are unchanged.
    # Whole-page snippet admission (controller decision, P1): a kept
    # snippet's admission and its finding's own locator no longer trust the
    # model's claimed locator or a fixed one-neighbour window --
    # ``_snippet_admitted_at`` now returns the passage
    # ``evidence.locate_snippet`` finds the snippet's own words in anywhere
    # on the page, and ``build_findings`` stamps every downstream field
    # (attribution, the finding itself, the admitted-evidence key) from that
    # relocated locator. No prompt string moved; only this module's own
    # source did, so the other four target pins and the Judge pin are
    # unchanged. Moved `4245163b56a8` -> `ecb64eeaf882`.
    # S1's whole-page admission rework (passage selection, packet building,
    # and the finding/source caps) landed on the same head and moved the
    # researcher's own module source a second time: `ecb64eeaf882` ->
    # `5472a4abdedf`. Recomputed after the merge, not carried from either
    # parent's own value.
    "researcher": "5472a4abdedf",
    # Lift the research-content limits (user decision 2026-09-25):
    # ``DEFAULT_EXCERPT_CHARS`` 600 -> 2000 and ``_RATIONALE_CHARS`` 400 ->
    # 1000, so the scoring pass sees enough of each page's excerpt and can
    # write a fuller rationale. No prompt sentence names either number
    # literally, so no model-read text moved beyond the module source itself;
    # ``agents.prompts`` was untouched, so the other four target pins and the
    # Judge pin are unchanged. Moved `58c4e7d909ef` -> `72339771d728`.
    "source_evaluator": "72339771d728",
    # Run-2 improvement 9 (a date is not a figure) moved the verifier's own
    # module: `fbad809c4414` -> `9f5515f04833`. The correction branches now read
    # the figure's own unit shape and, for the scope they propose, the reply's
    # verdict.
    # The caller wiring moved both of them once more: the verifier's
    # cited-figure line no longer names a body an unattributed figure cannot
    # claim (`9f5515f04833` -> `a6205ea9c362`), and the writer handed the plan
    # to the coverage gate and the Statement Check its cited findings' bounded
    # passages (`24ccad97fb9f` -> `7972a1f48129`). No other agent's text
    # changed, and the Judge pin is unchanged.
    # FF4Orch's run-2 items 5 and 6 were resolved onto this head: the merged
    # researcher source is `7314b246b217` (the wave's re-ask, caps and
    # neighbour-snippet rule plus the recorded findings and the run-scoped
    # mined keys) and the merged writer source is `1bfcd866e32f` (the wave's
    # oversize-point split and its plan/passage wiring plus the material
    # defects a re-draft is asked about). Neither item edited a prompt string
    # or ``agents.prompts``, so the other three target pins and the Judge pin
    # are unchanged.
    # Review F2's remedy moved the writer once more: its instruction now asks a
    # point in the executive summary to state every required target the packet
    # names a label for (`1bfcd866e32f` -> `d6b723723629`).
    # Prompt-fix wave (Fable's review VER-1..VER-4, EXTRA-3, EXTRA-4) rewrote the
    # Evidence Verifier's four model-read blocks and added the reproduced-document
    # credit: `a6205ea9c362` -> `90e57af30cd8`. No other agent's text changed, and
    # the Judge pin is unchanged.
    # Re-review round 1 (C1..C4): the Statement Check's block prints each cited
    # finding's own page title, its no-issuer line is `read at: <host>`, VER-2's
    # evidence_words is one contiguous span, and VER-4's one rule agrees with its
    # example: `90e57af30cd8` -> `aacdca45c69f`.
    # Fable's round-2 optional follow-ups 4-6 (VER-4's inconsistent list defers
    # to the one rule; corrected_text may take a document name from the page
    # line's title; EXTRA-4 shows the page line): `aacdca45c69f` -> `99faed0d857d`.
    # Run-3 content wave (Fable's audit run3-content-wave-brief.md, S4, D8
    # VER-3/VER-4, D21): the Statement Check's instruction now refuses a
    # page's caption, player title or condition label presented as a rating,
    # and a no-figure finding is labelled `quoted` rather than `verified`
    # (its completion event gained a matching count): `99faed0d857d` ->
    # `8b1ad6b04834`.
    # Lift the research-content limits (user decision 2026-09-25):
    # ``CONTEXT_PASSAGE_CHARS`` 3000 -> 6000, so the Context Check's bounded
    # passage window covers more of the page around a kept figure. No prompt
    # sentence names the character count, so no model-read text moved beyond
    # the module source itself; ``agents.prompts`` was untouched, so the
    # other four target pins and the Judge pin are unchanged. Moved
    # `8b1ad6b04834` -> `725d8e9ef930`.
    # Close the Context Check gap (controller decision, same P1 round):
    # ``context_passage`` (and, through it, the report registry's
    # ``statement_passages`` line) and ``relay_attribution_on_page``'s
    # primary path now read ``evidence.snippet_span_text`` instead of the
    # fixed one-neighbour-either-side ``neighbouring_passage_text``, so a
    # kept snippet spanning three or more passages is windowed by where it
    # actually ends. No prompt sentence named a passage count, so no
    # model-read text moved; only the module's own source did. Moved
    # `725d8e9ef930` -> `c272fd184706`.
    "evidence_verifier": "c272fd184706",
    # FF2 run-6 (RevRun2Wave's F3, the run-2 wave review): a piece cut after a
    # ';' is now printed with the point's own introduction in front of it, so a
    # list's later items no longer stand without their subject and conditions;
    # ``_split_oversize_point`` gained ``_lead_in`` and no prompt string was
    # edited, so the writer's value moved `1bfcd866e32f` -> `11dde57775b2` and
    # the other four pins and the Judge pin are unchanged.
    # FF1 (review F2) and FF2 (review F3) both moved the writer; merged `9387d2a0d77a`.
    # The evidence-log item (review F5) moved the writer once more: the
    # Statement Check's bounded passages are computed once for the items and
    # kept on the published record (`9387d2a0d77a` -> `e81da5da252b`).
    # The prompt-fix wave (Fable's review; WRI-1 to WRI-3 and EXTRA-5, PD-17)
    # rewrote the writer's system prompt, its rules and its two reply examples:
    # a host is where a statement was read, a snippet may end mid-clause, a
    # "not found" line never withholds an answer, an effective date the question
    # asks for is not page housekeeping, the label decides the credit and its own
    # words are never printed, a qualifier travels with its number, and the
    # examples mirror registry entries. Prompt drift only, module code untouched:
    # `e81da5da252b` -> `80de0d1e2168`; the four other target pins and the Judge
    # pin are unchanged.
    # Fable's prompt re-review round 1 (WRI-3/EXTRA-5 and the writer half of its
    # C1): the registry's statement line now prints `attributed to <issuer>` for
    # an admitted issuer and `read at <host>` for a page that names nobody — the
    # same two words the Statement Check's block uses — and the writer's first
    # example drops the energy word ("12 percent more members"), so the writer
    # value moved `80de0d1e2168` -> `08cad3d4d73f`. Code and example text only;
    # the four other target pins and the Judge pin are unchanged.
    "report_writer": "08cad3d4d73f",
}

# The judge half of the same contract. A Judge prompt change moves this value and
# invalidates Judge evidence for every agent, so it is pinned next to the targets
# rather than only inside the judge's own tests.
PINNED_JUDGE_PROMPT_FINGERPRINT = "74b9cddfbbee"

NOW = datetime(2026, 8, 16, 10, 15, 0, tzinfo=timezone.utc)
GIT = GitMetadata(commit="abc1234def", short_sha="abc1234", dirty=False)


def test_the_baseline_efforts_match_the_approved_profile() -> None:
    """Researcher and Source Evaluator at high; everyone else, and the
    judge, at max — DeepSeek Flash supports only those two levels."""
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


def test_the_baseline_models_are_deepseek_flash() -> None:
    config = EvaluationConfig()

    assert config.target_model == "deepseek-flash"
    assert config.judge_model == "deepseek-flash"


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

    S1 (Task 5.8) later moved the judge model to ``deepseek-flash``, so the
    canary documents' ``924caf47aa0d`` now describes the configuration before
    that change; this test still asserts identity only.
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


def test_every_target_prompt_fingerprint_is_pinned_against_prompt_drift() -> None:
    """Step 5: every agent's fingerprint, not one agent's.

    The matrix is a conformance test, so the fingerprints are checked before any
    prompt edit is accepted. Pinning all of them means a change to the shared
    ``agents.prompts`` module — which moves every value at once — is visible in
    one assertion rather than one fifth of it. PD-17: this alarm and the judge
    pin below it stay; only a test that asserts a *past* value for a deleted
    module is deleted.
    """
    assert set(AGENT_NAMES) == set(PINNED_TARGET_PROMPT_FINGERPRINTS)
    assert {
        name: agent_prompt_fingerprint(name) for name in AGENT_NAMES
    } == PINNED_TARGET_PROMPT_FINGERPRINTS


def test_the_judge_fingerprint_is_pinned_beside_the_target_pins() -> None:
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
    moves the recorded fingerprint for every one of them. Verified here rather
    than assumed,
    because it changes how a fingerprint move should be read.
    """
    fingerprints = {
        name: agent_prompt_fingerprint(name) for name in AGENT_NAMES
    }

    assert len(set(fingerprints.values())) == len(fingerprints)
    # Every agent's value is derived from the same shared module, so a change to
    # that module is visible in all of them; the per-agent component is what keeps
    # the values distinct.
    assert fingerprints["report_writer"] == PINNED_TARGET_PROMPT_FINGERPRINTS[
        "report_writer"
    ]


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
    assert llm.model == "deepseek-flash"
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


def test_a_production_declaration_the_harness_cannot_run_is_not_release_evidence() -> (
    None
):
    """The parity claim covers every knob the run sets, thinking mode included.

    Production declares thinking disabled for the researcher; the harness has
    one hard-wired mode and runs it enabled, and the declaration is one
    ``validate_agent_model_configs`` accepts for ``deepseek-v4-flash``. The
    label was computed from the model and the effort alone, so a run that
    differed from the shipped configuration in every call it made was reported
    as production parity and release evidence.
    """
    settings = ConfigSettings(
        llm=LLMConfig(
            reasoning_effort="high",
            model_overrides={
                "researcher": {
                    "thinking_mode": "disabled",
                    "reasoning_effort": "high",
                }
            },
        )
    )
    validate_agent_model_configs(settings.llm, ("researcher",))

    runtime = build(settings=settings, agent_name="researcher")

    assert settings.llm.resolve_for("researcher").thinking_mode == "disabled"
    assert runtime.target_model == settings.llm.resolve_for("researcher").model
    assert runtime.target_reasoning_effort == "high"
    assert runtime.target_profile_source == "production"
    assert runtime.experiment_only is True
    assert runtime.release_evidence is False
    # The mode the run does use stays recorded, so the artifact still says
    # which configuration produced its numbers.
    assert (
        target_llm_config(runtime, settings.llm)
        .resolve_for("researcher")
        .thinking_mode
        == "enabled"
    )
    assert experiment_metadata(runtime, settings)["release_evidence"] is False


def test_production_declaring_another_thinking_mode_is_not_release_evidence() -> (
    None
):
    """Production declares the mode on ``llm``, not only per agent.

    ``thinking_mode`` is a field of ``LLMConfig`` itself, so a production that
    declares no ``model_overrides`` entry for this agent still runs the mode it
    names there — and the harness, which runs one hard-wired mode, still cannot
    reproduce it. Comparing the mode only on the production-sourced path left
    this case labelled release evidence while every call it made differed from
    the shipped configuration.
    """
    settings = ConfigSettings(llm=LLMConfig(thinking_mode="disabled"))

    runtime = build(settings=settings, agent_name="researcher")

    assert settings.llm.resolve_for("researcher").thinking_mode == "disabled"
    assert runtime.target_profile_source == "evaluation"
    assert runtime.experiment_only is True
    assert runtime.release_evidence is False
    assert experiment_metadata(runtime, settings)["release_evidence"] is False


def test_a_frozen_parity_profile_whose_thinking_mode_changed_is_refused() -> None:
    """Fail preflight: the frozen label named a mode the run no longer matches.

    The model and the effort are untouched here, so only the thinking mode can
    account for the refusal — the third knob of the same parity claim.
    """
    frozen = build(
        settings=ConfigSettings(
            llm=LLMConfig(
                reasoning_effort="high",
                model_overrides={"researcher": {"reasoning_effort": "high"}},
            )
        ),
        agent_name="researcher",
    )
    assert frozen.release_evidence is True
    edited = ConfigSettings(
        llm=LLMConfig(
            reasoning_effort="high",
            model_overrides={
                "researcher": {
                    "thinking_mode": "disabled",
                    "reasoning_effort": "high",
                }
            },
        )
    )

    with pytest.raises(ValueError, match="production"):
        target_llm_config(frozen, edited.llm)


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


def test_an_invocation_can_force_parity_off_even_when_declared() -> None:
    """A per-run ``--no-production-parity`` beats config.yaml's own on."""
    settings = ConfigSettings(
        llm=LLMConfig(
            reasoning_effort="high",
            model_overrides={"planner": {"reasoning_effort": "max"}},
        )
    )

    runtime = build(
        settings=settings, agent_name="planner", production_parity=False
    )

    assert runtime.target_profile_source == "evaluation"
    assert runtime.production_parity is False


def test_an_invocation_can_force_parity_on_even_when_configured_off() -> None:
    """A per-run ``--production-parity`` beats config.yaml's own off."""
    settings = ConfigSettings(
        evaluation=EvaluationConfig(production_parity=False),
        llm=LLMConfig(
            reasoning_effort="high",
            model_overrides={"planner": {"reasoning_effort": "max"}},
        ),
    )

    runtime = build(
        settings=settings, agent_name="planner", production_parity=True
    )

    assert runtime.target_profile_source == "production"
    assert runtime.production_parity is True


def test_with_no_invocation_override_the_parity_source_is_configuration() -> None:
    """Neither CLI flag passed -- the run simply inherited config.yaml."""
    runtime = build()

    assert runtime.production_parity_source == "configuration"


def test_an_invocation_override_is_labelled_as_such() -> None:
    """An artifact must be able to tell a forced run from an inherited one,
    not just record the bool that resulted."""
    runtime = build(production_parity=False)

    assert runtime.production_parity_source == "invocation"

    runtime = build(production_parity=True)

    assert runtime.production_parity_source == "invocation"


def test_experiment_metadata_records_the_parity_source() -> None:
    settings = ConfigSettings()

    inherited = experiment_metadata(build(settings=settings), settings)
    overridden = experiment_metadata(
        build(settings=settings, production_parity=False), settings
    )

    assert inherited["production_parity_source"] == "configuration"
    assert overridden["production_parity_source"] == "invocation"


def test_the_target_llm_config_is_accepted_by_the_capability_registry() -> None:
    """Fail-closed: the baseline profile must be a combination DeepSeek
    actually supports, checked against the local table, not assumed."""
    from deep_research.providers import resolve_request_settings

    for agent_name in AGENT_NAMES:
        llm = target_llm_config(build(agent_name=agent_name), ConfigSettings().llm)
        resolved = resolve_request_settings(llm.provider, llm.resolve_for(None))
        assert resolved.reasoning_effort in ("high", "max")
        assert resolved.include_temperature is False


def test_the_judge_llm_config_is_independent_of_the_target() -> None:
    llm = judge_llm_config(
        build(agent_name="researcher"), ConfigSettings().llm
    )

    assert llm.provider == "deepseek"
    assert llm.model == "deepseek-flash"
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
    (
        "evidence_verifier",
        "cross-agent-planner-fix-parity-baseline-evidence-verifier",
    ),
    ("report_writer", "cross-agent-planner-fix-parity-baseline-report-writer"),
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


def test_the_case_registry_version_has_exactly_one_source() -> None:
    """The artifact records the registry's version, not a copy of it.

    ``config`` used to carry its own ``_CASE_REGISTRY_VERSION = 1`` while
    ``cases`` carried the canonical constant, and the metadata emitted the
    private copy. The two agreed only by luck; the moment a round bumped
    the registry, every artifact would have recorded the old version, and
    a recorded version that is wrong is worse than none.
    """
    from deep_research.evaluation.cases import CASE_REGISTRY_VERSION

    metadata = experiment_metadata(build(agent_name="planner"), ConfigSettings())

    assert metadata["case_registry_version"] == CASE_REGISTRY_VERSION
    assert not hasattr(evaluation_config, "_CASE_REGISTRY_VERSION")


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

