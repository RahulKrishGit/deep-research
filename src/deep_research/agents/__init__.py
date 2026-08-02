"""The shared agent base class and its bounded ReAct runtime."""

from deep_research.agents.base import AgentRun, BaseAgent, StructuredCompleter
from deep_research.agents.errors import (
    AgentConfigurationError,
    AgentError,
    agent_error,
)
from deep_research.agents.prompts import (
    REACT_RESPONSE_CONTRACT,
    AgentTask,
    render_react_messages,
    render_scratchpad,
    render_tool_catalog,
)
from deep_research.agents.react import (
    DecideCallback,
    StepCallback,
    SufficiencyCallback,
    run_react_loop,
)
from deep_research.agents.steps import (
    DEFAULT_SUMMARY_LIMIT,
    ReActActionType,
    ReActDecision,
    ReActObservation,
    ReActRun,
    ReActStep,
    StopReason,
    parse_tool_input,
    summarize_text,
)
from deep_research.agents.toolset import AgentToolset, ToolDescriptor

__all__ = [
    "DEFAULT_SUMMARY_LIMIT",
    "REACT_RESPONSE_CONTRACT",
    "AgentConfigurationError",
    "AgentError",
    "AgentRun",
    "AgentTask",
    "AgentToolset",
    "BaseAgent",
    "DecideCallback",
    "ReActActionType",
    "ReActDecision",
    "ReActObservation",
    "ReActRun",
    "ReActStep",
    "StepCallback",
    "StopReason",
    "StructuredCompleter",
    "SufficiencyCallback",
    "ToolDescriptor",
    "agent_error",
    "parse_tool_input",
    "render_react_messages",
    "render_scratchpad",
    "render_tool_catalog",
    "run_react_loop",
    "summarize_text",
]
