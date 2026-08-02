"""Source identity, grouping, and corroboration — pure, offline helpers.

``Finding.source_url`` is whatever a model reported, so two findings can
name the same page three different ways. Everything downstream keys on
``normalize_source_url``'s output instead, which is the canonical URL that
lands in ``ScoredSource.url``.

Nothing here performs I/O, reads a clock, or calls a provider, so grouping
and corroboration are deterministic functions of ``state.raw_findings``.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field

from deep_research.utils.types import ContractModel, Finding

_DEFAULT_PORTS = {"http": "80", "https": "443"}


def normalize_source_url(url: str) -> str:
    """Return a canonical form of ``url``, or the collapsed input verbatim.

    Total by design: a model may report a source that is not a URL at all
    (a book title, a file name). Those are returned whitespace-collapsed
    rather than rejected, so no finding is ever dropped for having an
    unusual source.
    """
    collapsed = " ".join(url.split())
    parts = urlsplit(collapsed)
    if not parts.scheme or not parts.hostname:
        return collapsed

    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    port = parts.port
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def source_domain(url: str) -> str:
    """Return the registrable-ish host for ``url``, or the normalized input.

    Not a public-suffix parse: ``a.example.co.uk`` and ``b.example.co.uk``
    are treated as different domains. That is deliberately conservative —
    it can only *under*-count corroboration, never invent it.
    """
    normalized = normalize_source_url(url)
    parts = urlsplit(normalized)
    return parts.hostname or normalized


class SourceGroup(ContractModel):
    """Every finding this research pass drew from one canonical source."""

    url: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    title: str = Field(min_length=1)
    sub_topics: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)


def group_findings_by_url(findings: Sequence[Finding]) -> list[SourceGroup]:
    """Fold findings into one group per canonical URL, first-seen order.

    ``title`` is the first non-blank ``source_title`` in the group, falling
    back to the URL: ``SourceGroup.title`` and ``ScoredSource.title`` both
    require a non-blank string, and a model that returned a blank title
    must not be able to fail validation for the whole run.
    """
    grouped: dict[str, SourceGroup] = {}
    for finding in findings:
        url = normalize_source_url(finding.source_url)
        group = grouped.get(url)
        if group is None:
            group = SourceGroup(url=url, domain=source_domain(url), title=url)
            grouped[url] = group
        if group.title == url and finding.source_title.strip():
            group.title = finding.source_title.strip()
        if finding.related_sub_topic not in group.sub_topics:
            group.sub_topics.append(finding.related_sub_topic)
        group.findings.append(finding)
    return list(grouped.values())


def corroboration_score(
    group: SourceGroup,
    groups: Sequence[SourceGroup],
) -> float:
    """Fraction of ``group``'s sub-topics another domain also covered.

    In ``[0.0, 1.0]`` by construction, and ``0.0`` for a group covering no
    sub-topic at all. A second page on the same domain is not corroboration.
    """
    if not group.sub_topics:
        return 0.0
    covered = sum(
        1
        for sub_topic in group.sub_topics
        if any(
            other.domain != group.domain and sub_topic in other.sub_topics
            for other in groups
        )
    )
    return covered / len(group.sub_topics)
