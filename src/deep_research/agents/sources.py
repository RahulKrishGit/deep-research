"""Source identity and grouping — pure, offline helpers.

``Finding.source_url`` is whatever a model reported, so two findings can
name the same page three different ways. Everything downstream keys on
``normalize_source_url``'s output instead, which is the canonical URL that
lands in ``ScoredSource.url``.

Nothing here performs I/O, reads a clock, or calls a provider, so grouping is
deterministic from ``state.raw_findings``.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit

import tldextract
from pydantic import Field

from deep_research.utils.types import ContractModel, Finding, ScoredSource

_DEFAULT_PORTS = {"http": "80", "https": "443"}
# Never refresh public-suffix data at runtime. The bundled suffix snapshot is
# deterministic and keeps publisher identity an offline operation.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())


def normalize_source_url(url: str) -> str:
    """Return a canonical form of ``url``, or the collapsed input verbatim.

    Total by design: a model may report a source that is not a URL at all
    (a book title, a file name), or a malformed URL (an out-of-range or
    non-numeric port, a malformed IPv6 host, ...). Both are returned
    whitespace-collapsed rather than rejected, so no finding is ever
    dropped for having an unusual or malformed source. ``urlsplit`` parses
    eagerly but its ``.hostname``/``.port`` properties can raise
    ``ValueError`` lazily on access for a malformed authority; any such
    failure falls back to the collapsed input, same as a non-URL string.
    """
    collapsed = " ".join(url.split())
    try:
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
    except ValueError:
        return collapsed


def latest_scored_sources(
    sources: Sequence[ScoredSource],
) -> list[ScoredSource]:
    """Keep the last append-ordered score for each normalized source URL."""
    projected: dict[str, ScoredSource] = {}
    for source in sources:
        projected[normalize_source_url(source.url)] = source
    return list(projected.values())


def source_domain(url: str) -> str:
    """Return the registrable-ish host for ``url``, or the normalized input.

    This is a lightweight host extraction used for lookup keys. Publisher
    identity and claim-level independence are handled by the Fact Checker.
    """
    normalized = normalize_source_url(url)
    parts = urlsplit(normalized)
    return parts.hostname or normalized


def publisher_identity(url: str) -> str:
    """Return the registrable publisher identity for ``url``.

    Subdomains from one organisation are one publisher for corroboration.
    Malformed or opaque source strings remain deterministic fallback keys,
    matching :func:`normalize_source_url`'s total contract.
    """
    normalized = normalize_source_url(url)
    try:
        host = urlsplit(normalized).hostname
    except ValueError:
        host = None
    if host is None:
        return normalized.casefold()
    parts = _EXTRACT(host.casefold())
    if not parts.domain or not parts.suffix:
        # ``.test`` and other private/reserved hosts are not in the public
        # suffix list. Keep their full host as the deterministic identity so
        # independent test publishers do not collapse to the bare label.
        return host.casefold()
    identity = ".".join(
        part for part in (parts.domain, parts.suffix) if part
    )
    return identity or host.casefold()


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
