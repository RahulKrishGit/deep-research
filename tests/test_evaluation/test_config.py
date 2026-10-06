"""Effort precedence, fingerprints, naming, and secret handling."""

from __future__ import annotations

import ntpath
from datetime import datetime, timezone

import pytest

from deep_research.agents.report_reviewer import REPORT_REVIEW_PROMPT_VERSION
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
    SERVICE_ROLE_NAMES,
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
# Every target agent's ``target_prompt_fingerprint``. All five are pinned
# together because ``agent_prompt_fingerprint`` hashes the shared
# ``agents.prompts`` module: one sentence changed there moves every agent's
# value at once, so a single-agent pin cannot say whether a change was
# intended. The hash also covers the agent module's full source, so a
# behavioural change to a function that lives in an agent module moves that
# agent's value even when its prompt text is byte-identical. Re-pin
# deliberately, from the value the pin test reports, rather than silently
# invalidating it.
PINNED_TARGET_PROMPT_FINGERPRINTS = {
    "planner": "8f277236da6e",
    "researcher": "51009ba36176",
    "source_evaluator": "ea32fd463f65",
    "evidence_verifier": "95abe2306b9f",
    "report_writer": "f76cccddf882",
}

# The reviewer is a service role (``SERVICE_ROLE_NAMES``), not an agent
# target: it carries no slot in ``AGENT_NAMES`` and so no place in the matrix
# above or in the judge's own module-source hash below. Its own versioned
# contract -- ``REPORT_REVIEW_PROMPT_VERSION`` -- is pinned here instead,
# beside the other two, so a change to its packet or prompt is visible the
# same way a target or judge prompt change is.
PINNED_REPORT_REVIEWER_PROMPT_VERSION = "report-review-7"

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


def test_identical_runtime_configs_share_a_judge_configuration_fingerprint() -> None:
    """The judge fingerprint is a function of the judge's settings alone.

    ``judge_configuration_fingerprint`` covers provider, structured
    transport, judge model, judge reasoning effort, judge temperature,
    thinking mode, and rubric version. It is asserted by identity between two
    builds rather than by a literal, so a deliberate change to the judge
    model does not need a re-pin here.
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
    """Every agent's fingerprint, not one agent's.

    The matrix is a conformance test, so the fingerprints are checked before any
    prompt edit is accepted. Pinning all of them means a change to the shared
    ``agents.prompts`` module — which moves every value at once — is visible in
    one assertion rather than one fifth of it.
    """
    assert set(AGENT_NAMES) == set(PINNED_TARGET_PROMPT_FINGERPRINTS)
    assert {
        name: agent_prompt_fingerprint(name) for name in AGENT_NAMES
    } == PINNED_TARGET_PROMPT_FINGERPRINTS


def test_the_judge_fingerprint_is_pinned_beside_the_target_pins() -> None:
    """Both halves of the structured contract, pinned in one place.

    The judge fingerprint is a distinct identity from every target's, because it
    covers the judge system prompt, template, schema, weights, and rubric version
    rather than an agent prompt module.
    """
    judge = judge_prompt_fingerprint(rubric_version=1)

    assert judge == PINNED_JUDGE_PROMPT_FINGERPRINT
    assert judge not in set(PINNED_TARGET_PROMPT_FINGERPRINTS.values())


def test_the_reviewer_prompt_version_is_pinned_beside_the_target_and_judge_pins() -> None:
    """The reviewer is a service role, not an agent target or the judge: its
    own versioned contract is pinned on its own.
    """
    assert SERVICE_ROLE_NAMES == ("report_reviewer",)
    assert "report_reviewer" not in PINNED_TARGET_PROMPT_FINGERPRINTS
    assert REPORT_REVIEW_PROMPT_VERSION == PINNED_REPORT_REVIEWER_PROMPT_VERSION


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
    """The measured divergence this closes.

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
    """The CLI flag turns production parity off; off is not the default."""
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


RUNTIME_IDENTITIES = (
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
    RUNTIME_IDENTITIES,
    ids=[prefix for _, prefix in RUNTIME_IDENTITIES],
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
    """Return the host-independent string contract for the extended-path helper."""
    normalized = ntpath.normpath(value)
    if normalized.startswith("\\\\?\\"):
        return normalized
    if normalized.startswith("\\\\"):
        return "\\\\?\\UNC\\" + normalized[2:]
    return "\\\\?\\" + normalized


def test_experiment_metadata_records_every_identifying_field() -> None:
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

    A private copy of the version in ``config`` would drift from the
    canonical constant in ``cases``: the moment the registry was bumped,
    every artifact would record the old version, and a recorded version
    that is wrong is worse than none.
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



def test_a_target_thinking_mode_reaches_the_target_and_never_the_judge() -> None:
    """One invocation can run its target with thinking disabled; the judge
    keeps thinking, and the run is an experiment."""
    settings = ConfigSettings()

    runtime = build(settings=settings, target_thinking_mode="disabled")

    assert runtime.target_thinking_mode == "disabled"
    assert runtime.thinking_mode == "enabled"
    assert target_llm_config(runtime, settings.llm).thinking_mode == "disabled"
    assert judge_llm_config(runtime, settings.llm).thinking_mode == "enabled"
    assert runtime.experiment_only is True
    assert runtime.configuration_fingerprint != build(settings=settings).configuration_fingerprint


def test_without_the_toggle_a_runtime_and_its_fingerprint_are_unchanged() -> None:
    settings = ConfigSettings()

    default = build(settings=settings)
    explicit = build(settings=settings, target_thinking_mode="enabled")

    assert default.target_thinking_mode == "enabled"
    assert explicit == default
