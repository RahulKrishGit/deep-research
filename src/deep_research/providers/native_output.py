"""Provider-text shape detection shared by both native ReAct boundaries.

One deterministic detector, used by every provider parser, so "this text is a
tool protocol, not an answer" is decided identically for DeepSeek Chat
Completions and OpenAI Responses.

It returns only a bounded literal. The text it read never travels back out
with the name, which keeps a rejection content-free like every other public
provider failure: a consumer learns which shape was refused, never what the
model wrote.
"""

from __future__ import annotations

import json
from typing import Literal, TypeAlias

NativeTextViolation: TypeAlias = Literal[
    "dsml_markup",
    "tool_markup",
    "markdown_fence",
    "legacy_action_json",
]

# The three keys of the retired prompt-encoded ReAct decision envelope. Any of
# them in a parsed object means the model answered with the old protocol, not
# with a final answer.
LEGACY_ACTION_KEYS = frozenset({"action", "tool_name", "tool_input_json"})


def native_text_violation(text: str) -> NativeTextViolation | None:
    """Name the prohibited shape ``text`` is, or ``None`` when it is prose.

    The checks run cheapest and most explicit first, and the JSON branch is
    reached only when nothing else matched, so ordinary prose that happens to
    contain braces is never mistaken for an envelope.
    """
    stripped = text.strip()
    lowered = stripped.casefold()
    if "<|dsml|" in lowered:
        return "dsml_markup"
    if "<tool_call" in lowered or "<invoke" in lowered:
        return "tool_markup"
    if "```" in stripped:
        return "markdown_fence"
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and LEGACY_ACTION_KEYS.intersection(payload):
        return "legacy_action_json"
    return None
