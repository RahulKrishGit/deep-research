"""Tests for the per-agent tool selection path."""

from __future__ import annotations

import pytest

from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.toolset import AgentToolset, ToolDescriptor
from deep_research.observability import Tracker
from tests.agent_fakes import BoomTool, EchoTool


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
