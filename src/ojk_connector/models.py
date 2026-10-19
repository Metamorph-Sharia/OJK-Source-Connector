"""Records shared by sources, identity rules and the pipeline.

Nothing in this module knows about a particular source. Source-specific
details travel in the ``metadata`` / ``extra`` dicts and in raw snapshots.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
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
    title: str
    published: str | None  # ISO date as reported by the source (not necessarily the issue date)
    source_url: str | None  # the source's page for this document
    official_url: str | None  # OJK's own page for the document, when the source knows it
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


class Standing(StrEnum):
    """Whether a regulation is in force, as a source states it."""

    IN_FORCE = "in_force"
    AMENDED = "amended"  # in force, with amended provisions
    PARTLY_REVOKED = "partly_revoked"  # in force, with revoked provisions
    REVOKED = "revoked"
    NOT_YET_IN_FORCE = "not_yet_in_force"


class RelationKind(StrEnum):
    """How a catalogue entry relates its document to another one."""

    REVOKES = "revokes"  # this document revokes the other
    PARTLY_REVOKES = "partly_revokes"  # … some of the other's provisions
    AMENDS = "amends"
    LEGAL_BASIS = "legal_basis"  # the other document is this one's legal basis
    REVOKED_BY = "revoked_by"  # the other document revokes this one
    PARTLY_REVOKED_BY = "partly_revoked_by"
    AMENDED_BY = "amended_by"

    @property
    def outgoing(self) -> bool:
        """This document's own claim about the other (incoming ones are the other document's claim)."""
        return not self.value.endswith("_by")


@dataclass(frozen=True)
class EntryRelation:
    kind: RelationKind
    target: str  # how the source names the other document, e.g. "POJK Nomor 40 Tahun 2024"
    articles: str | None = None  # the provisions concerned, worded as the source words them


@dataclass(frozen=True)
class FileLink:
    role: str  # main | annex | abstract | faq | other
    name: str  # the file name the source gives
    url: str


@dataclass(frozen=True)
class CatalogEntry:
    """A document's full entry in its source's catalogue (e.g. a detail page), read without fetching its files.

    Interpreted fields are source-neutral; ``fields`` keeps the source's own wording.
    """

    title: str | None
    standing: Standing | None
    dates: dict[str, str]  # enacted / promulgated / effective, ISO dates
    relations: tuple[EntryRelation, ...]
    files: tuple[FileLink, ...]
    fields: dict[str, Any]  # the source's own fields, unchanged (never counters such as page views)
    snapshot: Snapshot | None = None  # the raw page (kind "entry…"), kept for re-parsing; None when read back

    def facts(self) -> dict[str, Any]:
        """Everything the entry says, as plain JSON values (not the raw page)."""
        return json.loads(json.dumps({k: v for k, v in asdict(self).items() if k != "snapshot"}))

    @property
    def fingerprint(self) -> str:
        """Changes whenever what the entry says changes."""
        return hashlib.sha256(json.dumps(self.facts(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    @classmethod
    def from_facts(cls, facts: dict[str, Any]) -> CatalogEntry:
        return cls(
            title=facts.get("title"),
            standing=Standing(facts["standing"]) if facts.get("standing") else None,
            dates=dict(facts.get("dates") or {}),
            relations=tuple(EntryRelation(RelationKind(r["kind"]), r["target"], r.get("articles"))
                            for r in facts.get("relations") or []),
            files=tuple(FileLink(**f) for f in facts.get("files") or []),
            fields=dict(facts.get("fields") or {}),
        )
