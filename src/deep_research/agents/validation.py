"""Shared helpers for turning Pydantic validation failures into safe text.

Both the Planner and the Researcher validate model-produced drafts against
domain types and need to report which fields failed without ever copying
provider-generated text into a message: provider text can carry secrets,
prompt-injected instructions, or anything else the model chose to emit.
"""

from __future__ import annotations

from pydantic import ValidationError


def _invalid_fields(error: ValidationError) -> str:
    """Name the fields a validation failure touched, without provider text.

    Field names come from ``error.errors()[i]["loc"]``, which pydantic
    derives from the model's own schema — never from the invalid value
    itself — so this is safe to place in a repair prompt or a user-facing
    message. Falls back to ``"unknown"`` when no error carries a location.
    """
    fields = sorted(
        {str(detail["loc"][0]) for detail in error.errors() if detail["loc"]}
    )
    return ", ".join(fields) or "unknown"
