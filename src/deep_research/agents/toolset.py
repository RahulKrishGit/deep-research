"""The validated, ordered view one agent has over the shared tool registry."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field, JsonValue, model_validator

from deep_research.agents.errors import AgentConfigurationError
from deep_research.providers import ToolDefinition
from deep_research.tools.base import BaseTool
from deep_research.utils.types import ContractModel

# The finite compact-type vocabulary ``BaseTool.input_schema`` speaks. Kept
# exact rather than guessed at: a member outside this map is an assembly
# mistake, and a wrong provider schema is worse than a loud failure.
_JSON_TYPES: dict[str, dict[str, JsonValue]] = {
    "string": {"type": "string"},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "object": {"type": "object"},
    "array": {"type": "array"},
}


def _provider_type_schema(compact: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(compact, str):
        raise AgentConfigurationError("tool input types must be compact strings")
    members = compact.split("|")
    # A lone "null" would produce an unsatisfiable parameter: a nullable type
    # must pair with a real one.
    if members == ["null"]:
        raise AgentConfigurationError(
            "a compact tool input type must not be 'null' alone"
        )
    schemas = [
        {"type": "null"} if member == "null" else _JSON_TYPES.get(member)
        for member in members
    ]
    if any(schema is None for schema in schemas):
        raise AgentConfigurationError(f"unsupported compact tool input type: {compact}")
    retained = [schema for schema in schemas if schema is not None]
    return retained[0] if len(retained) == 1 else {"anyOf": retained}


class ToolDescriptor(ContractModel):
    """The prompt-facing projection of a tool's class metadata."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)
    required_arguments: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_compact_schema(self) -> "ToolDescriptor":
        """Fail at construction, before any provider request exists.

        Both halves of this projection are checked here rather than lazily in
        ``provider_definition``: an agent whose tool metadata cannot be
        converted should never reach a provider call at all.
        """
        for name in self.required_arguments:
            if name not in self.input_schema:
                raise AgentConfigurationError(
                    f"required tool argument {name!r} is absent from "
                    f"{self.name} input_schema"
                )
        for compact in self.input_schema.values():
            _provider_type_schema(compact)
        return self

    @classmethod
    def from_tool(cls, tool: BaseTool) -> "ToolDescriptor":
        return cls(
            name=tool.name,
            description=tool.description,
            input_schema=dict(tool.input_schema),
            required_arguments=tuple(tool.required_arguments),
        )

    def provider_definition(self) -> ToolDefinition:
        """This tool as a provider-native function definition."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    name: _provider_type_schema(compact)
                    for name, compact in self.input_schema.items()
                },
                "required": list(self.required_arguments),
                "additionalProperties": False,
            },
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

    def provider_definitions(self) -> tuple[ToolDefinition, ...]:
        """Allowed tools as provider-native function definitions, in order."""
        return tuple(
            descriptor.provider_definition() for descriptor in self.descriptors()
        )

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def without(self, *names: str) -> "AgentToolset":
        """A toolset missing ``names``, validated like any other.

        The one legitimate way to narrow a toolset after construction, for a
        decision that depends on run state rather than on the agent's
        declaration — the planner offering no ``query_memory`` once the
        session's startup recall has already supplied its procedural
        guidance. A name that is not in the set is ignored: this narrows, it
        never adds, so an unknown name cannot widen anything.
        """
        dropped = {name for name in names}
        retained = [name for name in self._tools if name not in dropped]
        return AgentToolset(
            [tool for tool in self._tools.values() if tool.name not in dropped],
            allowed=retained,
        )
