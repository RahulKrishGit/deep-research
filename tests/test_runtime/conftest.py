import pytest

from deep_research.observability import LangSmithRuntimeConfig, Tracker


@pytest.fixture
def tracker() -> Tracker:
    return Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False,
            project="runtime-tests",
            api_key=None,
        )
    )
