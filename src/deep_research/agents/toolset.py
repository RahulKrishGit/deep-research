"""The validated, ordered view one agent has over the shared tool registry."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field, JsonValue

from deep_research.agents.errors import AgentConfigurationError
from deep_research.tools.base import BaseTool
from deep_research.utils.types import ContractModel


class ToolDescriptor(ContractModel):
    """The prompt-facing projection of a tool's class metadata."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)

    @classmethod
    def from_tool(cls, tool: BaseTool) -> "ToolDescriptor":
        return cls(
            name=tool.name,
            description=tool.description,
            input_schema=dict(tool.input_schema),
        )


class AgentToolset:
    """The subset of injected tools one agent is permitted to call.

    Construction fails loudly: an agent that declares a tool nobody injected
    is a wiring mistake, not a runtime condition to be recovered from.
    """

    def __init__(
        self,
        tools: Sequence[BaseTool] = (),
        *,
        allowed: Sequence[str] = (),
    ) -> None:
        registry: dict[str, BaseTool] = {}
        for tool in tools:
            if tool.name in registry:
                raise AgentConfigurationError(
                    f"duplicate tool name in the registry: {tool.name}"
                )
            registry[tool.name] = tool

        selected: dict[str, BaseTool] = {}
        missing: list[str] = []
        for name in allowed:
            if name in selected or name in missing:
                raise AgentConfigurationError(f"duplicate allowed tool name: {name}")
            tool = registry.get(name)
            if tool is None:
                missing.append(name)
                continue
            selected[name] = tool
        if missing:
            names = ", ".join(missing)
            raise AgentConfigurationError(f"allowed tools were not injected: {names}")

        self._tools = selected

    @property
    def names(self) -> tuple[str, ...]:
        """Allowed tool names, in the order the agent declared them."""
        return tuple(self._tools)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return tuple(ToolDescriptor.from_tool(tool) for tool in self._tools.values())

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
