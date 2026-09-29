"""API test guard: no API test reaches the live one-time check (live-briefs spec §4.4).

``create_app`` picks ``live_clarity_check`` for a live-mode app built without a
``clarity_checker``, and every live-mode test app here is built that way. The
live checker is replaced for every test in this package by one that asks
nothing and records the question it was given, so no test can build a provider,
and a test can still see whether the check ran (``live_check_calls``).
"""

from __future__ import annotations

import importlib

import pytest

from deep_research.api.clarify import NO_QUESTIONS, ClarityCheck

# ``deep_research.api`` re-exports the module-level FastAPI ``app``, which shadows
# the ``app`` submodule as a package attribute, so the module is fetched by name.
app_module = importlib.import_module("deep_research.api.app")


@pytest.fixture(autouse=True)
def live_check_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def asks_nothing(question: str, settings: object) -> ClarityCheck:
        del settings
        calls.append(question)
        return NO_QUESTIONS

    monkeypatch.setattr(app_module, "live_clarity_check", asks_nothing)
    return calls
