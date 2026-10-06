"""API test guard: no API test reaches the live one-time check
or the live note interpreter.

``create_app`` picks ``live_clarity_check`` for a live-mode app built without a
``clarity_checker``, and every live-mode test app here is built that way. The
live checker is replaced for every test in this package by one that asks
nothing and records the question it was given, so no test can build a provider,
and a test can still see whether the check ran (``live_check_calls``). The live
note interpreter is replaced the same way, by one that restates each note as
written and records its text (``live_note_calls``).
"""

from __future__ import annotations

import importlib

import pytest

from deep_research.api.clarify import NO_QUESTIONS, ClarityCheck
from deep_research.api.notes import NoteInterpretation

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


@pytest.fixture(autouse=True)
def live_note_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def restates(text: str, question: str, earlier: object, settings: object) -> NoteInterpretation:
        del question, earlier, settings
        calls.append(text)
        return NoteInterpretation(kinds=["emphasis"], restatement=text)

    monkeypatch.setattr(app_module, "live_note_interpreter", restates)
    return calls
