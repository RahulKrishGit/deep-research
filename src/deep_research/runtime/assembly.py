"""Assemble the providers, tools, agents, and graph one session runs on.

The wiring root. Every collaborator a research session needs is constructed
here from a loaded ``ConfigSettings``, and every external client is
injectable so this module can be tested without an API key or a network.
"""

from __future__ import annotations

import os
from typing import Any

from deep_research.observability import Tracker
from deep_research.runtime.memory_bridge import LongTermMemoryBridge
from deep_research.tools.base import BaseTool
from deep_research.tools.document_reader import DocumentReaderTool
from deep_research.tools.memory_tools import QueryMemoryTool, SaveToMemoryTool
from deep_research.tools.web_scraper import WebScraperTool
from deep_research.tools.web_search import WebSearchTool
from deep_research.tools.write_document import WriteDocumentTool
from deep_research.utils.config import ConfigSettings

TAVILY_API_KEY_VARIABLE = "TAVILY_API_KEY"


def build_tools(
    settings: ConfigSettings,
    *,
    tracker: Tracker,
    memory: LongTermMemoryBridge,
    tavily_api_key: str | None = None,
    search_client: Any | None = None,
    http_client: Any | None = None,
) -> list[BaseTool]:
    """Build every tool any agent declares, in one shared registry.

    One registry for all six agents rather than a per-agent subset:
    ``AgentToolset`` already selects the names an agent declares and
    ignores the rest, and it raises ``AgentConfigurationError`` when a
    declared tool was never injected — so the wiring guard is kept without
    six lists to keep in step.
    """
    return [
        WebSearchTool(
            tracker,
            api_key=tavily_api_key or os.getenv(TAVILY_API_KEY_VARIABLE),
            client=search_client,
            search_depth=settings.tavily.search_depth,
            max_results=settings.tavily.max_results,
        ),
        WebScraperTool(tracker, client=http_client),
        DocumentReaderTool(tracker, client=http_client),
        QueryMemoryTool(tracker, memory),
        SaveToMemoryTool(tracker, memory),
        WriteDocumentTool(tracker, settings.output.directory),
    ]
