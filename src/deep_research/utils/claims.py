"""Pure projections for the effective claim judgment in a research pass."""

from __future__ import annotations

from collections.abc import Sequence

from deep_research.utils.types import Claim


def normalize_claim_text(text: str) -> str:
    """Return the stable identity used for a factual claim."""
    return " ".join(text.split()).casefold()


def latest_claims(claims: Sequence[Claim]) -> list[Claim]:
    """Keep the last append-ordered judgment for each claim text.

    ``ResearchState.verified_claims`` remains an append-only history. This
    projection is the effective view consumed by presentation and synthesis.
    Evidence URLs are deliberately absent from the identity because they are
    mutable attributes of a claim judgment.
    """
    projected: dict[str, Claim] = {}
    for claim in claims:
        projected[normalize_claim_text(claim.text)] = claim
    return list(projected.values())


__all__ = ["latest_claims", "normalize_claim_text"]
