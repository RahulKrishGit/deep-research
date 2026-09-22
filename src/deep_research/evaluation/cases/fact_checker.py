"""Fact Checker evaluation cases: verdicts, independence, and recovery."""

from __future__ import annotations

from deep_research.evaluation.cases import (
    build_case,
    claim,
    evaluation_state,
    finding,
    metrics,
    rubric,
    scored_source,
)
from deep_research.evaluation.models import CaseExpectations, EvaluationCase

# Every case carries its own JudgeRubric instance: build_case stores the
# rubric by reference, so sharing one module constant across cases would
# let one case's mutations leak into the others. The two verification
# dimensions are shared as plain tuples — rubric() copies them into a fresh
# JudgeRubric per call — so the dimension text is written once, not
# verbatim in all four rubrics.

_VERIFICATION_DIMENSIONS = (
    (
        "evidence_grounding",
        "Verdicts rest on evidence the verification loop actually retrieved.",
        "Every claim is judged on retrieved, independent evidence; nothing "
        "is marked verified on its own sources alone.",
        "Verdicts are asserted with no retrieved evidence behind them.",
    ),
    (
        "claim_fidelity",
        "The claim is verified as stated, not a softened version.",
        "Verification targets the claim's actual assertion and reports the "
        "verdict on it.",
        "The claim is silently reworded into something easier to verify.",
    ),
)

_MIXED_RUBRIC = rubric(
    "fact-checker-mixed",
    *_VERIFICATION_DIMENSIONS,
    (
        "verdict_discipline",
        "Each verdict class is used exactly and only for what it means.",
        "Verified claims are corroborated, contradicted claims have "
        "contradicting evidence, and unverifiable claims are marked "
        "insufficient_evidence.",
        "Verdicts are interchanged — insufficient_evidence for anything "
        "not clearly true, or verified without corroboration.",
    ),
    (
        "evidence_linkage",
        "Judged claims carry bounded passages tied to read source URLs.",
        "Every judged claim names a structured passage and the URL it came "
        "from.",
        "Claims are judged without naming any read-bearing evidence.",
    ),
)

_DEPENDENT_RUBRIC = rubric(
    "fact-checker-dependent-domains",
    *_VERIFICATION_DIMENSIONS,
    (
        "independence_rigor",
        "Corroboration counts only across independent registrable domains.",
        "A claim is verified only on evidence from at least two "
        "independent registrable domains; same-family hosts count once.",
        "Multiple pages from one publisher or domain family are treated "
        "as corroboration.",
    ),
)

_FAILURE_RUBRIC = rubric(
    "fact-checker-search-failure",
    *_VERIFICATION_DIMENSIONS,
    (
        "conservatism_under_failure",
        "A failed verification search yields a conservative verdict, never "
        "an invented one.",
        "Claims whose verification search failed are recorded as "
        "unverified or insufficient_evidence, and the failure is surfaced.",
        "A claim is marked verified despite its verification search "
        "failing.",
    ),
)

_LIVE_RUBRIC = rubric(
    "fact-checker-live-verification", *_VERIFICATION_DIMENSIONS
)

# The claim texts are shared with the dependency scenarios: the cases
# declare them as expected verdicts (or failing-query anchors) and in their
# references, and dependencies.py scripts the verification searches under
# those same strings. The case tests pin both sides to the same literals.

_SUPPORTED_CLAIM = (
    "Small modular reactor designs must satisfy the same international "
    "safety standards as large reactors."
)
_REFUTED_CLAIM = "No small modular reactor has operated commercially."
_THIN_CLAIM = (
    "Small modular reactors will be cheaper to build than large reactors "
    "at scale."
)
_OUTAGE_CLAIM = "The 2025 grid upgrade reduced outage minutes by 40 percent."
_HEAT_CLAIM = (
    "Ocean heat content is still rising at the rate reported in 2023."
)
_ATTRIBUTION_CLAIM = (
    "The recent acceleration in ocean heat content is driven primarily by "
    "greenhouse gas forcing."
)

_IAEA_URL = "https://iaea.org/smr-safety-assessment"
_WNA_URL = "https://world-nuclear.org/smr-safety-standards"
_POLICY_URL = "https://energypolicy.example.org/smr-commercialization"
_TRADE_URL = "https://nuclearindustry.example.com/pevek-klt-40s"
_VENDOR_URL = "https://reactorvendor.example.com/smr-cost-advantage"
_NEWSLETTER_URL = "https://industrynewsletter.example.com/smr-cost-projections"

# Scripted search results for the mixed case: two corroborating results
# across independent domains for the supportable claim, one contradicting
# result for the refutable claim, and an empty set for the thin claim. All
# URLs are written in the normalized form the agent records (no ``www.``,
# no trailing slash), so a gate comparison can never byte-mismatch.
_NRC_URL = "https://nrc.gov/smr-licensing-framework"
_ANS_URL = "https://ans.org/smr-safety-assessment"
_NEI_URL = "https://nei.org/pevek-floating-plant"
_MIXED_KNOWN_URLS = (
    _IAEA_URL,
    _WNA_URL,
    _POLICY_URL,
    _TRADE_URL,
    _VENDOR_URL,
    _NEWSLETTER_URL,
    _NRC_URL,
    _ANS_URL,
    _NEI_URL,
)

_NEWS_URL = "https://news.example.com/outage-coverage"
# Deliberately the ``www.`` spelling: the case's point is that the same
# registrable domain wears three hats. The agent normalizes it to
# ``news.example.com`` when it records the claim's sources.
_WWW_NEWS_URL = "https://www.news.example.com/outage-verification"
_SYNDICATION_URL = "https://syndication.news.example.com/outage-syndication"
_REGULATOR_URL = "https://regulator.example.gov/outage-report"
_DEPENDENT_KNOWN_URLS = (
    _NEWS_URL,
    "https://news.example.com/outage-verification",
    _SYNDICATION_URL,
    _REGULATOR_URL,
    "https://news.example.com/outage-minutes-fall",
    "https://syndication.news.example.com/outage-minutes-fall",
)

_NOAA_URL = "https://noaa.gov/ohc-2025-update"
_COPERNICUS_URL = "https://copernicus.eu/ocean-heat-content-2025"
_NATURE_URL = "https://nature.com/ocean-heat-attribution"
_BLOG_URL = "https://climateblog.example.com/ocean-heat-plateau"
_FAILURE_KNOWN_URLS = (
    _NOAA_URL,
    _COPERNICUS_URL,
    _NATURE_URL,
    _BLOG_URL,
    "https://agu.org/ocean-heat-attribution",
    "https://gcos.wmo.int/ocean-heat-bulletin",
)

_IEA_URL = "https://iea.org/solar-pv-installed-capacity"
_IRENA_URL = "https://irena.org/solar-capacity-statistics"

_MIXED = build_case(
    case_id="mixed-verdicts",
    agent_name="fact_checker",
    tier="controlled",
    title="Return the verdict each claim's evidence actually supports",
    purpose=(
        "Verify three claims from six findings: a supportable claim "
        "corroborated across two independent authorities, a refutable "
        "claim contradicted by one operating SMR, and a thin claim with "
        "no corroborating evidence anywhere. The scripted verification "
        "searches mirror that: corroboration for the first, one "
        "contradicting result for the second, an empty set for the "
        "third."
    ),
    state=evaluation_state(
        case_id="mixed-verdicts",
        question=(
            "What is known about the safety record of small modular "
            "reactors?"
        ),
        findings=(
            finding(
                "IAEA safety assessment reports apply the same safety "
                "objectives to small modular reactor designs as to large "
                "reactors, covering defence in depth, accident management, "
                "and emergency preparedness.",
                url=_IAEA_URL,
                title="IAEA: safety assessment of small modular reactors",
                sub_topic_title="SMR safety standards",
            ),
            finding(
                "The World Nuclear Association notes that SMR designs are "
                "reviewed against established IAEA safety standards and "
                "that several designs are completing pre-licensing "
                "assessments in their home countries.",
                url=_WNA_URL,
                title="WNA: SMR safety and licensing review",
                sub_topic_title="SMR safety standards",
            ),
            finding(
                "An energy policy brief asserts that no small modular "
                "reactor has operated commercially anywhere in the world, "
                "citing the absence of operating SMR units in its country "
                "surveys.",
                url=_POLICY_URL,
                title="Policy brief: SMR commercialization status",
                sub_topic_title="Commercial SMR operation",
            ),
            finding(
                "A nuclear industry trade report documents that the "
                "Akademik Lomonosov floating nuclear plant at Pevek has "
                "supplied power commercially since 2020, using two KLT-40S "
                "small modular reactor units.",
                url=_TRADE_URL,
                title="Trade report: Pevek floating plant operations",
                sub_topic_title="Commercial SMR operation",
            ),
            finding(
                "A reactor vendor brochure claims that factory fabrication "
                "will make small modular reactors cheaper to build than "
                "large reactors.",
                url=_VENDOR_URL,
                title="Vendor brochure: SMR cost advantage",
                sub_topic_title="SMR cost projections",
            ),
            finding(
                "An industry newsletter repeats the claim that SMR "
                "construction costs will fall below large-reactor costs "
                "once designs reach serial production.",
                url=_NEWSLETTER_URL,
                title="Industry newsletter: SMR cost projections",
                sub_topic_title="SMR cost projections",
            ),
        ),
        sources=(
            scored_source(
                _IAEA_URL,
                title="IAEA: safety assessment of small modular reactors",
                authority=0.95,
                recency=0.90,
                relevance=0.95,
                overall=0.91,
                rationale=(
                    "IAEA is the international nuclear safety authority "
                    "and its assessment framework is the benchmark."
                ),
            ),
            scored_source(
                _WNA_URL,
                title="WNA: SMR safety and licensing review",
                authority=0.88,
                recency=0.85,
                relevance=0.90,
                overall=0.86,
                rationale=(
                    "Industry association with technical SMR reviews; "
                    "slightly less authoritative than the regulator."
                ),
            ),
            scored_source(
                _POLICY_URL,
                title="Policy brief: SMR commercialization status",
                authority=0.50,
                recency=0.65,
                relevance=0.70,
                overall=0.54,
                rationale=(
                    "Policy brief without primary operational data; "
                    "uncorroborated by other sources."
                ),
            ),
            scored_source(
                _TRADE_URL,
                title="Trade report: Pevek floating plant operations",
                authority=0.55,
                recency=0.80,
                relevance=0.75,
                overall=0.68,
                rationale=(
                    "Trade reporting with documented operational data on "
                    "an operating SMR unit."
                ),
            ),
            scored_source(
                _VENDOR_URL,
                title="Vendor brochure: SMR cost advantage",
                authority=0.30,
                recency=0.70,
                relevance=0.60,
                overall=0.45,
                rationale=(
                    "Promotional material from the vendor itself; no "
                    "independent evidence of cost advantage."
                ),
                low_confidence=True,
            ),
            scored_source(
                _NEWSLETTER_URL,
                title="Industry newsletter: SMR cost projections",
                authority=0.35,
                recency=0.75,
                relevance=0.55,
                overall=0.48,
                rationale=(
                    "Industry newsletter repeating vendor claims; low "
                    "authority and weak corroboration."
                ),
                low_confidence=True,
            ),
        ),
    ),
    dependency_scenario="fact-checker-mixed",
    expectations=CaseExpectations(
        required_output_fields=["verified_claims"],
        reference={
            "expected_verdicts": {
                _SUPPORTED_CLAIM: "verified",
                _REFUTED_CLAIM: "contradicted",
                _THIN_CLAIM: "insufficient_evidence",
            },
        },
        known_source_urls=list(_MIXED_KNOWN_URLS),
        max_iterations=12,
        max_tool_calls=12,
        deterministic_metrics=metrics(
            (
                "verdict_correctness",
                0.35,
                "Each referenced claim's verdict matches the reference.",
            ),
            (
                "evidence_linked",
                0.25,
                "Every non-`insufficient_evidence` claim carries at least "
                "one bounded verification passage tied to a source URL.",
            ),
            (
                "confidence_calibrated",
                0.20,
                "`insufficient_evidence` claims score confidence <= 0.5 "
                "and `verified` claims >= 0.5.",
            ),
            (
                "sources_known",
                0.20,
                "Every cited `source_url` is among the declared known "
                "sources.",
            ),
        ),
    ),
    judge_rubric=_MIXED_RUBRIC,
    metadata={"scenario": "normal"},
)

_DEPENDENT = build_case(
    case_id="independent-domain-evidence",
    agent_name="fact_checker",
    tier="controlled",
    title="Do not let one publisher corroborate itself",
    purpose=(
        "Verify a single claim whose apparent corroboration is three pages "
        "from the same registrable domain — news.example.com wearing three "
        "hats — plus one genuinely independent regulator finding. The "
        "scripted verification search returns two more news-family "
        "results and nothing else, so the claim resolves to fewer than "
        "two independent registrable domains and must not be marked "
        "verified."
    ),
    state=evaluation_state(
        case_id="independent-domain-evidence",
        question=(
            "Did the 2025 grid upgrade reduce outage minutes by 40%?"
        ),
        findings=(
            finding(
                "A news report on news.example.com says the 2025 grid "
                "upgrade cut outage minutes by 40 percent, citing "
                "preliminary utility estimates.",
                url=_NEWS_URL,
                title="News: outage minutes after the grid upgrade",
                sub_topic_title="Outage minutes after the grid upgrade",
            ),
            finding(
                "A follow-up report on the same outlet's main domain "
                "repeats the 40 percent reduction figure, quoting the "
                "utility's press release.",
                url=_WWW_NEWS_URL,
                title="News: upgrade results verified by the utility",
                sub_topic_title="Outage minutes after the grid upgrade",
            ),
            finding(
                "A syndicated copy of the story on a syndication subdomain "
                "repeats the 40 percent figure without any new reporting.",
                url=_SYNDICATION_URL,
                title="Syndicated story: outage statistics",
                sub_topic_title="Outage minutes after the grid upgrade",
            ),
            finding(
                "The regulator's outage report records a 40 percent "
                "reduction in outage minutes following the 2025 grid "
                "upgrade, based on verified meter data.",
                url=_REGULATOR_URL,
                title="Regulator: outage report 2025",
                sub_topic_title="Outage minutes after the grid upgrade",
            ),
        ),
    ),
    dependency_scenario="fact-checker-dependent-domains",
    expectations=CaseExpectations(
        required_output_fields=["verified_claims"],
        reference={
            "dependent_domain_family": "news.example.com",
            "independent_urls": [_REGULATOR_URL],
            "minimum_independent_domains": 2,
        },
        known_source_urls=list(_DEPENDENT_KNOWN_URLS),
        max_iterations=8,
        max_tool_calls=8,
        deterministic_metrics=metrics(
            (
                "independence_enforced",
                0.45,
                "A claim whose evidence resolves to fewer than two "
                "independent registrable domains is not marked `verified`.",
            ),
            (
                "evidence_linked",
                0.25,
                "Every non-`insufficient_evidence` claim carries at least "
                "one bounded verification passage tied to a source URL.",
            ),
            (
                "sources_known",
                0.15,
                "Every cited `source_url` is among the declared known "
                "sources.",
            ),
            (
                "budget_respected",
                0.15,
                "The merged verification run stays within the case budget.",
            ),
        ),
    ),
    judge_rubric=_DEPENDENT_RUBRIC,
    metadata={"scenario": "challenging"},
)

_FAILURE = build_case(
    case_id="verification-search-failure",
    agent_name="fact_checker",
    tier="controlled",
    title="Stay conservative when a verification search fails",
    purpose=(
        "Verify two claims while the first verification search raises "
        "RuntimeError('search backend unavailable'): that claim must not "
        "be marked verified, the failure must be recorded as recoverable, "
        "and the second claim — whose search succeeds with two "
        "independent corroborating results — must still reach a real "
        "verdict, so bounded, conservative behavior is observable."
    ),
    state=evaluation_state(
        case_id="verification-search-failure",
        question=(
            "Is ocean heat content still rising at the rate reported in "
            "2023?"
        ),
        findings=(
            finding(
                "NOAA's 2025 ocean heat content update reports that the "
                "rate of increase documented in 2023 has continued, with "
                "record annual heat content again in 2024.",
                url=_NOAA_URL,
                title="NOAA: ocean heat content update 2025",
                sub_topic_title="Ocean heat content trend",
            ),
            finding(
                "Copernicus data show global ocean heat content at a "
                "record high in 2025, with the warming rate matching the "
                "2023 level.",
                url=_COPERNICUS_URL,
                title="Copernicus: ocean heat content 2025",
                sub_topic_title="Ocean heat content trend",
            ),
            finding(
                "A Nature study attributes the post-2020 acceleration in "
                "ocean heat content primarily to greenhouse gas forcing, "
                "with internal variability playing a secondary role.",
                url=_NATURE_URL,
                title="Nature: drivers of the ocean heat acceleration",
                sub_topic_title="Drivers of the ocean heat increase",
            ),
            finding(
                "A personal blog claims ocean heat content stopped rising "
                "after mid-2024 and that the 2023 rate is no longer "
                "representative, citing a single observing site.",
                url=_BLOG_URL,
                title="Blog: ocean heat plateau claim",
                sub_topic_title="Ocean heat content trend",
            ),
        ),
    ),
    dependency_scenario="fact-checker-search-failure",
    expectations=CaseExpectations(
        required_output_fields=["verified_claims"],
        reference={
            "failing_query_prefix": "Ocean heat content is still rising "
            "at the rate",
            "conservative_verdicts": ["insufficient_evidence", "unverified"],
        },
        known_source_urls=list(_FAILURE_KNOWN_URLS),
        max_iterations=10,
        max_tool_calls=10,
        deterministic_metrics=metrics(
            (
                "conservative_on_failure",
                0.35,
                "A claim whose verification search failed is not "
                "`verified`.",
            ),
            (
                "partial_verification_present",
                0.25,
                "At least one claim reached a non-`insufficient_evidence` "
                "verdict.",
            ),
            (
                "failure_recorded",
                0.25,
                "The run records the search failure as a recoverable "
                "error.",
            ),
            (
                "budget_respected",
                0.15,
                "The merged verification run stays within the case budget.",
            ),
        ),
        must_record_recoverable_error=True,
    ),
    judge_rubric=_FAILURE_RUBRIC,
    metadata={"scenario": "failure-recovery"},
)

# Two claims about one water authority, and the two ways a claim can look
# corroborated without being corroborated. The trap claim's apparent support
# is the commission's own order plus the commission's own press release —
# one publisher saying one thing twice — with a policy lab's page as the
# third "source", which vouches only for who said the number. The control
# claim is carried by two separate works from two unrelated publishers, so
# it does reach a pair. Counting domains alone passes the second flavour;
# the case declares which claim may not come back verified and the metric
# reads that declaration.

_AGENCY_URL = "https://commission.example.gov/order-2026-14"
_AGENCY_PRESS_URL = "https://commission.example.gov/press/losses-target-2030"
_LAB_URL = "https://leakagelab.example.org/commission-target"
_WIRE_URL = "https://waterdesk.example.com/commission-losses-order"
_UPSTREAM_URLS = (_AGENCY_URL, _AGENCY_PRESS_URL, _LAB_URL, _WIRE_URL)

_MONITOR_URL = "https://lossesmonitor.example.net/2025-audit"
_UNIVERSITY_URL = "https://audits.example.edu/2025-losses-study"

_UPSTREAM_TRAP_CLAIM = (
    "Non-revenue water in Northfield's network measured 12 percent of "
    "supply in 2025."
)
_UPSTREAM_CONTROL_CLAIM = (
    "Northfield Water Authority replaced 41 kilometres of leaking mains "
    "in 2025."
)

_UPSTREAM_RUBRIC = rubric(
    "fact-checker-upstream-independence",
    *_VERIFICATION_DIMENSIONS,
    (
        "upstream_independence",
        "Two accounts of one publisher's number are one account.",
        "A claim is judged on the publishers and works behind its passages, "
        "not the number of pages: an agency's order and that agency's own "
        "press release count once, and the verdict says so.",
        "One publisher's repeated figure is treated as independent "
        "corroboration.",
    ),
    (
        "dimension_specific_support",
        "A source that vouches for who said it has not vouched for the "
        "number.",
        "Every supporting passage carries the value and the period the claim "
        "asserts; a page that only attributes the figure to someone is read "
        "as attribution and never as support.",
        "Attribution-only pages are counted as support for the claim's "
        "value.",
    ),
)

_UPSTREAM = build_case(
    case_id="upstream-independent-pair",
    agent_name="fact_checker",
    tier="controlled",
    title="Corroboration is two publishers, not two pages",
    purpose=(
        "Verify two claims about a water authority's network. The trap claim "
        "— 12 percent non-revenue water in 2025 — is carried by a wire story "
        "and apparently corroborated by the commission's own order, that same "
        "commission's press release repeating the figure, and a lab's page "
        "that vouches only for who said the number. That is one publisher "
        "saying one thing twice, so the claim must not come back verified "
        "however many domains its passages span. The control claim is "
        "supported by two separate works from two unrelated publishers and "
        "must reach a verified pair, so a run that abstains on everything "
        "collects nothing."
    ),
    state=evaluation_state(
        case_id="upstream-independent-pair",
        question=(
            "How much water does the Northfield Water Authority lose, and "
            "what is it doing about it?"
        ),
        findings=(
            finding(
                "Order 2026-14 records non-revenue water of 12 percent of "
                "supply in 2025 in the Northfield Water Authority's network "
                "and sets a target of 8 percent of supply by 2030.",
                url=_AGENCY_URL,
                title="Commission order 2026-14",
                sub_topic_title="Non-revenue water in the network",
            ),
            finding(
                "The commission's press release for order 2026-14 repeats "
                "that non-revenue water measured 12 percent of supply in "
                "2025 and restates the 8 percent target.",
                url=_AGENCY_PRESS_URL,
                title="Commission press release: losses target 2030",
                sub_topic_title="Non-revenue water in the network",
            ),
            finding(
                "A leakage lab's commentary credits the commission's order "
                "2026-14 as the source of the 12 percent figure and presents "
                "no measurement of its own.",
                url=_LAB_URL,
                title="Leakage lab: the commission's 12 percent",
                sub_topic_title="Attribution of the 12 percent figure",
            ),
            finding(
                "A water desk's story on the commission's order reports that "
                "non-revenue water reached 12 percent of supply in 2025.",
                url=_WIRE_URL,
                title="Water desk: commission losses order",
                sub_topic_title="Non-revenue water in the network",
            ),
        ),
        sources=(
            scored_source(
                _AGENCY_URL,
                title="Commission order 2026-14",
                authority=0.85,
                recency=0.80,
                relevance=0.90,
                overall=0.85,
                rationale=(
                    "The commission's own order, stating the measured figure "
                    "and the period it applies to."
                ),
            ),
            scored_source(
                _AGENCY_PRESS_URL,
                title="Commission press release: losses target 2030",
                authority=0.60,
                recency=0.80,
                relevance=0.85,
                overall=0.70,
                rationale=(
                    "The same commission restating its own order's figure; "
                    "one publisher with the order, not a second source."
                ),
            ),
            scored_source(
                _LAB_URL,
                title="Leakage lab: the commission's 12 percent",
                authority=0.55,
                recency=0.75,
                relevance=0.60,
                overall=0.60,
                rationale=(
                    "An independent lab's commentary that attributes the "
                    "figure to the commission without carrying a measurement."
                ),
            ),
            scored_source(
                _WIRE_URL,
                title="Water desk: commission losses order",
                authority=0.45,
                recency=0.80,
                relevance=0.75,
                overall=0.60,
                rationale=(
                    "Trade reporting carrying the commission's figure; no "
                    "measurement of its own."
                ),
                low_confidence=True,
            ),
        ),
        claims=(
            # The trap as the state holds it: taken from the wire story, not
            # verified, and carrying no passage — the shape an
            # ``insufficient_evidence`` claim always has until independent
            # evidence is actually read.
            claim(
                _UPSTREAM_TRAP_CLAIM,
                urls=[_WIRE_URL],
                verdict="insufficient_evidence",
                confidence=0.40,
            ),
            # The control as a correct run records it: two passages, two
            # unrelated publishers, neither of them one of the claim's own
            # origins, so the independent pair is what the badge rests on.
            claim(
                _UPSTREAM_CONTROL_CLAIM,
                urls=[_AGENCY_PRESS_URL],
                verdict="verified",
                confidence=0.85,
                evidence=[
                    "The 2025 network audit records 41 kilometres of leaking "
                    "mains replaced by the authority.",
                    "The regional study records 41 kilometres of leaking "
                    "mains replaced in the authority's network during 2025.",
                ],
                verification_urls=[_MONITOR_URL, _UNIVERSITY_URL],
            ),
        ),
    ),
    dependency_scenario="fact-checker-upstream-independent-pair",
    expectations=CaseExpectations(
        required_output_fields=["verified_claims"],
        reference={
            "expected_verdicts": {
                _UPSTREAM_TRAP_CLAIM: "insufficient_evidence",
                _UPSTREAM_CONTROL_CLAIM: "verified",
            },
            "forbidden_verified_claims": [_UPSTREAM_TRAP_CLAIM],
            "required_verified_claims": [_UPSTREAM_CONTROL_CLAIM],
            "same_publisher_urls": [_AGENCY_URL, _AGENCY_PRESS_URL],
            "dimension_only_urls": [_LAB_URL],
            "corroborating_urls": [_MONITOR_URL, _UNIVERSITY_URL],
            "claim_origin_urls": {
                _UPSTREAM_TRAP_CLAIM: [_WIRE_URL],
                _UPSTREAM_CONTROL_CLAIM: [_AGENCY_PRESS_URL],
            },
            "minimum_independent_domains": 2,
        },
        known_source_urls=[
            *_UPSTREAM_URLS,
            _MONITOR_URL,
            _UNIVERSITY_URL,
        ],
        max_iterations=12,
        max_tool_calls=12,
        deterministic_metrics=metrics(
            (
                "verdict_correctness",
                0.30,
                "The trap claim is not `verified` and the control claim is.",
            ),
            (
                "no_false_independent_pair",
                0.30,
                "No claim the case forbids comes back `verified` or carries "
                "the independent-pair badge, and every `verified` claim "
                "resolves to at least two independent publishers.",
            ),
            (
                "independence_enforced",
                0.20,
                "A claim whose evidence resolves to fewer than two "
                "independent registrable domains is not marked `verified`.",
            ),
            (
                "sources_known",
                0.20,
                "Every cited `source_url` is among the declared known "
                "sources.",
            ),
        ),
    ),
    judge_rubric=_UPSTREAM_RUBRIC,
    metadata={"scenario": "challenging"},
)

_LIVE = build_case(
    case_id="fact-checker-live-verification",
    agent_name="fact_checker",
    tier="live",
    title="Verify a current energy claim with live searches",
    purpose=(
        "Verify whether global installed solar capacity has passed 2 "
        "terawatts using two current findings from IEA and IRENA, live "
        "web searches, and memory recall. A live run legitimately "
        "discovers URLs the case cannot pre-declare, so sources_known is "
        "dropped; independence_enforced still requires at least two "
        "independent registrable domains in the retrieved evidence."
    ),
    state=evaluation_state(
        case_id="fact-checker-live-verification",
        question=(
            "Has global installed solar capacity passed 2 terawatts?"
        ),
        findings=(
            finding(
                "IEA data report that global installed solar PV capacity "
                "surpassed 2 terawatts during 2025.",
                url=_IEA_URL,
                title="IEA: solar PV installed capacity",
                sub_topic_title="Global installed solar capacity",
            ),
            finding(
                "IRENA's renewable capacity statistics record global "
                "installed solar PV capacity above 2 terawatts by the end "
                "of 2025.",
                url=_IRENA_URL,
                title="IRENA: renewable capacity statistics",
                sub_topic_title="Global installed solar capacity",
            ),
        ),
    ),
    dependency_scenario="live",
    expectations=CaseExpectations(
        required_output_fields=["verified_claims"],
        # Live-run note for Task 18's evaluator: the Fact Checker verifies
        # with live web_search (tavily), read-bearing page/document calls, or
        # query_memory reads. Search hits alone are never evidence.
        # Sources_known is intentionally absent: a live run discovers
        # URLs the case cannot pre-declare.
        reference={
            "minimum_independent_domains": 2,
        },
        known_source_urls=[],
        max_iterations=10,
        max_tool_calls=10,
        deterministic_metrics=metrics(
            (
                "evidence_linked",
                0.30,
                "Every non-`insufficient_evidence` claim carries at least "
                "one bounded verification passage tied to a source URL.",
            ),
            (
                "independence_enforced",
                0.30,
                "A claim whose evidence resolves to fewer than two "
                "independent registrable domains is not marked `verified`.",
            ),
            (
                "confidence_calibrated",
                0.25,
                "`insufficient_evidence` claims score confidence <= 0.5 "
                "and `verified` claims >= 0.5.",
            ),
            (
                "budget_respected",
                0.15,
                "The merged verification run stays within the case budget.",
            ),
        ),
        required_live_dependencies=["tavily", "memory"],
    ),
    judge_rubric=_LIVE_RUBRIC,
    metadata={"scenario": "live"},
)

# Append new cases; never prepend. ``conftest.controlled_case_for``
# takes ``cases_for(agent, "controlled")[0]``, so the first case here
# is the one every conftest-driven gate test exercises.
CONTROLLED_CASES: tuple[EvaluationCase, ...] = (
    _MIXED,
    _DEPENDENT,
    _FAILURE,
    _UPSTREAM,
)
LIVE_CASES: tuple[EvaluationCase, ...] = (_LIVE,)
