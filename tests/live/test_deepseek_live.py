from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from deep_research.observability import (
    LangSmithRuntimeConfig,
    TokenUsageMetric,
    Tracker,
)
from deep_research.providers import ChatMessage, DeepSeekChatProvider
from deep_research.utils.config import LLMConfig


class LiveTinyAnswer(BaseModel):
    value: int


@pytest.mark.live
@pytest.mark.asyncio
async def test_deepseek_structured_adapter_live() -> None:
    if os.getenv("RUN_DEEPSEEK_LIVE_TESTS") != "1":
        pytest.skip("set RUN_DEEPSEEK_LIVE_TESTS=1 to opt in")
    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        pytest.skip("DEEPSEEK_API_KEY is required for the live smoke test")

    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    provider = DeepSeekChatProvider(
        LLMConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            thinking_mode="enabled",
            reasoning_effort="high",
            timeout=30.0,
            retry_count=0,
            max_tokens=256,
        ),
        tracker,
    )

    async with tracker.session_span("deepseek-live-smoke", "bounded adapter check"):
        result = await provider.complete_structured(
            [
                ChatMessage(
                    role="user",
                    content="Return a JSON object with value equal to 7.",
                )
            ],
            LiveTinyAnswer,
        )

    assert result == LiveTinyAnswer(value=7)
    metric = next(
        metric for metric in tracker.metrics if isinstance(metric, TokenUsageMetric)
    )
    assert metric.input_tokens >= 0
    assert metric.output_tokens > 0
    assert metric.total_tokens == metric.input_tokens + metric.output_tokens
