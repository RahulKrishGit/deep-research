"""Tests for the per-agent tool selection path."""

from __future__ import annotations

from typing import Any

import pytest

from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.toolset import AgentToolset, ToolDescriptor
from deep_research.observability import Tracker
from deep_research.providers import ToolDefinition
from deep_research.tools import (
    DocumentReaderTool,
    QueryMemoryTool,
    SaveToMemoryTool,
    WebScraperTool,
    WebSearchTool,
    WriteDocumentTool,
)
from deep_research.tools.base import BaseTool, ToolCallContext, ToolExecution
from tests.agent_fakes import BoomTool, EchoTool


class SearchTool(BaseTool):
    """A tool whose compact schema exercises the nullable union case."""

    name = "search"
    description = "Search one index."
    input_schema = {"query": "string", "limit": "integer|null"}
    required_arguments = ("query",)
    output_schema = {"results": "array"}

    async def _execute(
        self, context: ToolCallContext, **kwargs: Any
    ) -> ToolExecution:
        return ToolExecution(data=[], output_summary={"count": 0})


class UnsupportedTypeTool(SearchTool):
    """Declares a compact type outside the supported vocabulary."""

    name = "unsupported"
    input_schema = {"query": "text"}


class DriftedRequiredTool(SearchTool):
    """Declares a required name its own input schema does not carry."""

    name = "drifted"
    required_arguments = ("query", "missing")


def test_descriptor_projects_tool_class_metadata(tracker: Tracker) -> None:
    descriptor = ToolDescriptor.from_tool(EchoTool(tracker))

    assert descriptor.name == "echo"
    assert descriptor.description == "Echo one string back to the agent."
    assert descriptor.input_schema == {"value": "string"}


def test_toolset_exposes_only_the_allowed_tools(tracker: Tracker) -> None:
    toolset = AgentToolset([EchoTool(tracker), BoomTool(tracker)], allowed=["echo"])

    assert toolset.names == ("echo",)
    assert len(toolset) == 1
    assert "echo" in toolset
    assert "boom" not in toolset
    assert toolset.get("boom") is None
    assert isinstance(toolset.get("echo"), EchoTool)


def test_toolset_preserves_the_declared_tool_order(tracker: Tracker) -> None:
    toolset = AgentToolset(
        [EchoTool(tracker), BoomTool(tracker)],
        allowed=["boom", "echo"],
    )

    assert toolset.names == ("boom", "echo")
    assert [descriptor.name for descriptor in toolset.descriptors()] == [
        "boom",
        "echo",
    ]


def test_toolset_rejects_an_allowed_tool_that_was_never_injected(
    tracker: Tracker,
) -> None:
    with pytest.raises(AgentConfigurationError, match="web_search"):
        AgentToolset([EchoTool(tracker)], allowed=["echo", "web_search"])


def test_toolset_rejects_duplicate_registry_names(tracker: Tracker) -> None:
    with pytest.raises(AgentConfigurationError, match="duplicate"):
        AgentToolset([EchoTool(tracker), EchoTool(tracker)], allowed=["echo"])


def test_toolset_rejects_duplicate_allowed_names(tracker: Tracker) -> None:
    with pytest.raises(AgentConfigurationError, match="duplicate"):
        AgentToolset([EchoTool(tracker)], allowed=["echo", "echo"])


def test_an_agent_with_no_allowed_tools_is_valid(tracker: Tracker) -> None:
    toolset = AgentToolset([EchoTool(tracker)], allowed=[])

    assert toolset.names == ()
    assert toolset.descriptors() == ()
    assert len(toolset) == 0


def test_provider_definitions_are_real_object_json_schemas(tracker: Tracker) -> None:
    toolset = AgentToolset([SearchTool(tracker)], allowed=("search",))

    assert toolset.provider_definitions() == (
        ToolDefinition(
            name="search",
            description="Search one index.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}]
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
    )


def test_provider_definitions_keep_the_declared_tool_order(
    tracker: Tracker,
) -> None:
    toolset = AgentToolset(
        [EchoTool(tracker), SearchTool(tracker)],
        allowed=["search", "echo"],
    )

    assert [definition.name for definition in toolset.provider_definitions()] == [
        "search",
        "echo",
    ]


def test_a_tool_with_no_required_arguments_declares_an_empty_required_list(
    tracker: Tracker,
) -> None:
    toolset = AgentToolset([BoomTool(tracker)], allowed=["boom"])

    assert toolset.provider_definitions()[0].parameters["required"] == []


def test_an_unsupported_compact_type_fails_at_descriptor_construction(
    tracker: Tracker,
) -> None:
    with pytest.raises(AgentConfigurationError, match="unsupported compact"):
        ToolDescriptor.from_tool(UnsupportedTypeTool(tracker))


def test_a_required_name_absent_from_the_schema_fails_at_descriptor_construction(
    tracker: Tracker,
) -> None:
    with pytest.raises(AgentConfigurationError, match="absent from"):
        ToolDescriptor.from_tool(DriftedRequiredTool(tracker))


# The declaration half of the projection. `DriftedRequiredTool` above only
# fails when a declared name is missing from the schema, so a wrong-but-present
# list — for example `("query", "max_results")` — would pass every other test.
# This table is what pins the six production declarations themselves.
PRODUCTION_REQUIRED_ARGUMENTS = (
    (WebSearchTool, ("query",)),
    (WebScraperTool, ("url",)),
    (DocumentReaderTool, ("source",)),
    (SaveToMemoryTool, ("content",)),
    (QueryMemoryTool, ("query",)),
    (WriteDocumentTool, ("filename", "content")),
)


@pytest.mark.parametrize(
    ("tool_class", "expected"),
    PRODUCTION_REQUIRED_ARGUMENTS,
)
def test_every_production_tool_declares_exactly_its_required_arguments(
    tool_class: type[BaseTool],
    expected: tuple[str, ...],
) -> None:
    assert tool_class.required_arguments == expected
    assert set(expected) <= set(tool_class.input_schema)
