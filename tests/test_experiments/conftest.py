import pytest

from deep_research.experiments.live_runs import OFF_PEAK_DATES_ENV
from deep_research.observability import LangSmithRuntimeConfig, Tracker


@pytest.fixture(autouse=True)
def _no_declared_off_peak_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner's shell may export the exemption; a test states its own dates."""
    monkeypatch.delenv(OFF_PEAK_DATES_ENV, raising=False)


@pytest.fixture
def tracker() -> Tracker:
    return Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False,
            project="experiment-tests",
            api_key=None,
        )
    )
