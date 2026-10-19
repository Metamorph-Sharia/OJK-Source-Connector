"""The port every OJK document source implements.

A source knows *where* OJK documents come from and how to talk to that place
(JDIH OJK, OJK's own legal database; RegAlert until 2026-10-16). It knows nothing about
storage, canonical identity or other sources: it turns listings and payloads into
the records in ``models``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, ClassVar

from ojk_connector.models import CatalogEntry, DiscoveredDocument, DocumentContent, StatusCheck

#: Incremental discovery re-lists this many days before the last run, because sources
#: may date documents late (e.g. by when they ingested them). Setting: ``lookback_days``.
DEFAULT_LOOKBACK_DAYS = 45


class SourceError(Exception):
    """The source failed; retrying later may succeed."""


class PermanentSourceError(SourceError):
    """The source failed in a way retrying will not fix (e.g. the document is gone)."""


class SourceAuthError(SourceError):
    """Credentials are missing, expired or rejected."""


class SourceForbidden(SourceError):
    """The source refuses us (HTTP 403) although our credentials are valid. A person must find out why; never
    work around it, and stop asking for the rest of the run."""


class QuotaExhausted(SourceError):
    """The source has paused us (fair use, rate cap). Stop for now; resume on a later run."""


@dataclass(frozen=True)
class SourceContext:
    """Environment the pipeline hands to every source."""

    source_name: str  # the [sources.<name>] key in config
    credentials_dir: Path
    user_agent: str


class Source(ABC):
    """Base class for OJK document sources."""

    #: Metadata keys whose change means the content may have changed (triggers a re-read).
    content_fields: ClassVar[tuple[str, ...]] = ()
    #: Metadata keys whose fill rate is monitored across full discoveries.
    monitored_fields: ClassVar[tuple[str, ...]] = ()
    #: Whether ``check_status`` is implemented (cheap standing checks for change detection).
    supports_status_checks: ClassVar[bool] = False
    #: Whether ``read_entry`` is implemented (the document's full catalogue entry, without its files).
    supports_entries: ClassVar[bool] = False

    def __init__(self, settings: Mapping[str, Any], context: SourceContext) -> None:
        self.settings = settings
        self.context = context

    @property
    def name(self) -> str:
        """The configured source name; recorded on every document."""
        return self.context.source_name

    @abstractmethod
    def discover(self, since: date | None = None, until: date | None = None) -> Iterator[DiscoveredDocument]:
        """List OJK documents published in ``[since, until]`` (open-ended when None)."""

    @abstractmethod
    def fetch_content(self, document: DiscoveredDocument) -> DocumentContent:
        """Fetch texts and raw payloads for one document.

        Raises ``QuotaExhausted`` when the source pauses us, ``SourceAuthError`` when
        credentials fail, ``PermanentSourceError`` for documents that cannot be fetched.
        ``extra["affects"]`` may list source ids whose standing this document changes
        (e.g. instruments it amends), so their status is re-checked first.
        """

    def rebuild_content(self, document: DiscoveredDocument, snapshots: list[tuple[str, bytes]]) -> DocumentContent:
        """Re-assemble content from stored raw snapshots (``(kind, bytes)``), without network.

        Lets parser fixes apply to everything already fetched without spending reads.
        """
        raise NotImplementedError(f"source {self.name!r} cannot rebuild content from snapshots")

    def check_status(self, document: DiscoveredDocument) -> StatusCheck:
        """Report what amends or replaces the document. Must not consume rationed reads."""
        raise NotImplementedError(f"source {self.name!r} has no status checks")

    def read_entry(self, document: DiscoveredDocument) -> CatalogEntry:
        """Read the document's full catalogue entry: official title, dates, standing, relations, file list.

        Cheap (one page, no files), so it runs for every listed document, including those whose content
        another source already supplied.
        """
        raise NotImplementedError(f"source {self.name!r} has no catalogue entries")

    def rebuild_entry(self, document: DiscoveredDocument, page: bytes) -> CatalogEntry:
        """Re-parse a stored entry page, without network, so parser fixes reach every entry already read."""
        raise NotImplementedError(f"source {self.name!r} cannot rebuild catalogue entries")

    # Optional: sources that need sign-in override these.
    @classmethod
    def login(cls, settings: Mapping[str, Any], context: SourceContext, *, method: str | None = None) -> str:
        raise NotImplementedError(f"source {context.source_name!r} does not need sign-in")

    @classmethod
    def auth_status(cls, settings: Mapping[str, Any], context: SourceContext) -> dict[str, Any]:
        return {"auth": "not required"}

    def close(self) -> None:
        """Release network resources."""

    def __enter__(self) -> Source:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
