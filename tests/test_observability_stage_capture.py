"""A live run's stage inputs, written only when bound."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from deep_research.agents.evidence_verifier import (
    StatementCheckDraft,
    StatementCheckItem,
    StatementVerdictDraft,
    check_statements,
)
from deep_research.observability import (
    bind_stage_capture,
    capture_node_input,
    capture_page_extraction,
    capture_statement_check,
)
from deep_research.providers import ChatMessage
from deep_research.utils.types import ResearchState, SubTopic
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import figure, make_finding, make_read


def _state(question: str = "How much storage was added?") -> ResearchState:
    return ResearchState.model_validate(
        {"session_id": "session-1", "original_question": question}
    )


def _items() -> list[StatementCheckItem]:
    read = make_read("Wood Mackenzie added 18.9 GW of storage in 2025.")
    finding = make_finding(
        read,
        "Wood Mackenzie added 18.9 GW of storage in 2025.",
        figures=[figure("18.9", "GW", "2025", "actual")],
    )
    return [
        StatementCheckItem(
            label=f"S{index:02d}",
            text=f"Storage grew by 18.9 GW ({index}).",
            findings=[finding],
            labels=["F01"],
        )
        for index in range(1, 4)
    ]


def _consistent(messages: list, schema: type) -> StatementCheckDraft:
    del schema
    return StatementCheckDraft(
        statements=[
            StatementVerdictDraft(label=label, verdict="consistent", reason="As cited.")
            for label in re.findall(r"## (S\d+)", messages[1].content)
        ]
    )


def test_nothing_is_written_while_no_capture_is_bound(tmp_path: Path) -> None:
    capture_node_input("evidence_verifier", _state())
    capture_statement_check("How much?", _items())

    assert list(tmp_path.iterdir()) == []


def test_a_bound_capture_writes_each_named_nodes_input_numbered(tmp_path: Path) -> None:
    first, second = _state("First question?"), _state("Second question?")

    with bind_stage_capture(tmp_path, nodes=["evidence_verifier"]):
        capture_node_input("evidence_verifier", first)
        capture_node_input("researcher", _state())
        capture_node_input("evidence_verifier", second)
    capture_node_input("evidence_verifier", _state("After the block?"))

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "evidence_verifier-01.json",
        "evidence_verifier-02.json",
    ]
    payload = json.loads((tmp_path / "evidence_verifier-02.json").read_text("utf-8"))
    assert payload["node"] == "evidence_verifier"
    assert ResearchState.model_validate(payload["state"]) == second


@pytest.mark.asyncio
async def test_every_statement_check_call_is_written_with_its_question_and_items(
    tmp_path: Path,
) -> None:
    items = _items()
    completer = ScriptedCompleter(outputs=[_consistent])

    with bind_stage_capture(tmp_path, nodes=[]):
        results, errors = await check_statements(
            completer, items, question="How much storage was added?"
        )

    assert errors == [] and len(results) == 3
    payload = json.loads((tmp_path / "statement_check-001.json").read_text("utf-8"))
    assert payload["question"] == "How much storage was added?"
    assert [StatementCheckItem.model_validate(item) for item in payload["items"]] == items


def _sub_topic() -> SubTopic:
    return SubTopic(
        coverage_id="topic-01",
        title="Battery storage",
        rationale="How much storage was added.",
        search_queries=["battery storage additions 2025"],
        success_criteria=["Names the capacity added."],
        priority=1,
    )


def _request() -> list[ChatMessage]:
    return [
        ChatMessage(role="developer", content="Extract findings."),
        ChatMessage(role="user", content="Retrieved evidence: Wood Mackenzie added 18.9 GW."),
    ]


def test_a_bound_capture_writes_each_page_extraction_request(tmp_path: Path) -> None:
    read = make_read("Wood Mackenzie added 18.9 GW of storage in 2025.")
    request = _request()

    with bind_stage_capture(tmp_path, nodes=()):
        capture_page_extraction(
            request,
            sub_topic=_sub_topic(),
            known_reads={read.read_id: read},
            valid_target_ids=["t1"],
        )
        capture_page_extraction(
            request, sub_topic=_sub_topic(), known_reads={}, valid_target_ids=None
        )

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "page_extraction-001.json",
        "page_extraction-002.json",
    ]
    payload = json.loads((tmp_path / "page_extraction-001.json").read_text("utf-8"))
    assert payload["valid_target_ids"] == ["t1"]
    assert set(payload["known_reads"]) == {read.read_id}
    assert [ChatMessage.model_validate(message) for message in payload["messages"]] == request
    assert SubTopic.model_validate(payload["sub_topic"]) == _sub_topic()
    assert type(read).model_validate(payload["known_reads"][read.read_id]) == read
    unplanned = json.loads((tmp_path / "page_extraction-002.json").read_text("utf-8"))
    assert unplanned["valid_target_ids"] is None
    assert unplanned["known_reads"] == {}


def test_an_unbound_page_extraction_capture_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No capture bound: the hook returns without error and leaves no file,
    not in a capture directory (there is none) and not in the working one."""
    monkeypatch.chdir(tmp_path)
    read = make_read("Wood Mackenzie added 18.9 GW of storage in 2025.")

    returned = capture_page_extraction(
        _request(),
        sub_topic=_sub_topic(),
        known_reads={read.read_id: read},
        valid_target_ids=["t1"],
    )

    assert returned is None
    assert list(tmp_path.iterdir()) == []
