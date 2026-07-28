"""Verify all packages import correctly."""

import deep_research
from deep_research import (
    agents,
    graph,
    memory,
    observability,
    providers,
    tools,
    utils,
)


def test_package_version() -> None:
    """Package exposes a version string."""
    assert deep_research.__version__ == "0.1.0"


def test_all_subpackages_import() -> None:
    """All sub-packages import without error."""
    assert agents is not None
    assert graph is not None
    assert memory is not None
    assert tools is not None
    assert providers is not None
    assert observability is not None
    assert utils is not None


def test_shared_research_types_import_from_utils_package() -> None:
    from deep_research.utils import (  # noqa: F401
        AwareISOString,
        Claim,
        ClaimVerdict,
        CriticScore,
        Critique,
        Finding,
        MemorySnapshot,
        ResearchError,
        ResearchEvent,
        ResearchState,
        ResearchStateUpdate,
        ScoredSource,
        SubTopic,
        UnitScore,
        advance_research_iteration,
        merge_research_state,
    )
