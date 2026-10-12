"""Source-agnostic records shared by connectors, agency profiles and the pipeline.

Nothing in this module knows about a particular source or agency. Source-specific
details travel in the ``metadata`` / ``extra`` dicts and in raw snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Origin(StrEnum):
    """Where a stored text came from."""

    SELF_EXTRACTED = "self_extracted"  # we extracted it from an official file
    SOURCE_TEXT = "source_text"  # original-language text supplied by a source
    SOURCE_TRANSLATION = "source_translation"  # a translation supplied by a source


class Rights(StrEnum):
    """What we may do with a stored artifact."""

    PUBLIC = "public"  # public regulatory instrument
    LICENSED = "licensed"  # third-party content we have permission to use
    RESTRICTED = "restricted"  # third-party content: stored locally, never exported


EXPORTABLE_RIGHTS = frozenset({Rights.PUBLIC, Rights.LICENSED})


@dataclass(frozen=True)
class DiscoveredDocument:
    """A document as listed by a source, before its content is fetched."""

    source: str  # source name from config, e.g. "regalert"
    source_id: str  # stable id within that source
    agency: str  # our agency code, e.g. "ojk"
    title: str
    published: str | None  # ISO date as reported by the source (not necessarily the issue date)
    source_url: str | None  # the source's page for this document
    official_url: str | None  # the issuing agency's own page, when the source knows it
    metadata: dict[str, Any] = field(default_factory=dict)  # raw source metadata


@dataclass(frozen=True)
class TextArtifact:
    language: str  # e.g. "id", "en"
    origin: Origin
    rights: Rights
    content: str
    complete: bool | None = None  # False when the source reported more text than we received


@dataclass(frozen=True)
class StatusCheck:
    """A document's standing at its source (what amends or replaces it), checked without reading its text."""

    fingerprint: str  # changes whenever the set of amending/replacing instruments changes
    edges: list[dict[str, Any]]  # [{"relation", "source_id", "title", "date"}]
    summary: str  # e.g. "superseded", "amended", "unchanged"


@dataclass(frozen=True)
class Snapshot:
    """A raw payload kept so we can re-parse without fetching again."""

    kind: str  # e.g. "document_json"
    content: bytes
    media_type: str = "application/json"


@dataclass(frozen=True)
class DocumentContent:
    texts: list[TextArtifact]
    snapshots: list[Snapshot]
    extra: dict[str, Any] = field(default_factory=dict)  # structured extras, e.g. lineage
