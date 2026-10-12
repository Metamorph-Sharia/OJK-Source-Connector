"""The contract every source connector implements.

A connector knows *where* documents come from and how to talk to that source. It
knows nothing about storage, canonical identity or other sources: it turns a
source's listings and payloads into source-agnostic records (see ``models``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, ClassVar

from regingest.models import DiscoveredDocument, DocumentContent, StatusCheck


class SourceError(Exception):
    """The source failed; retrying later may succeed."""


class PermanentSourceError(SourceError):
    """The source failed in a way retrying will not fix (e.g. the document is gone)."""


class SourceAuthError(SourceError):
    """Credentials are missing, expired or rejected."""


class QuotaExhausted(SourceError):
    """The source has paused us (fair use, rate cap). Stop for now; resume on a later run."""


@dataclass(frozen=True)
class ConnectorContext:
    """Environment the pipeline hands to every connector."""

    source_name: str  # the [sources.<name>] key in config
    credentials_dir: Path
    user_agent: str


@dataclass(frozen=True)
class AgencyBinding:
    """Which agency to ingest, with the source-specific parameters from config.

    E.g. for RegAlert and OJK: ``AgencyBinding("ojk", {"regulator": "OJK", "jurisdiction": "ID"})``.
    """

    agency: str
    params: Mapping[str, Any] = field(default_factory=dict)


class Connector(ABC):
    """Base class for source connectors."""

    #: Built-in name of the connector type, e.g. "regalert".
    kind: ClassVar[str]
    #: Metadata keys whose change means the content may have changed (triggers a re-read).
    content_fields: ClassVar[tuple[str, ...]] = ()
    #: Metadata keys whose fill rate is monitored across full discoveries.
    monitored_fields: ClassVar[tuple[str, ...]] = ()
    #: Whether ``check_status`` is implemented (cheap standing checks for change detection).
    supports_status_checks: ClassVar[bool] = False

    def __init__(self, settings: Mapping[str, Any], context: ConnectorContext) -> None:
        self.settings = settings
        self.context = context

    @property
    def name(self) -> str:
        """The configured source name; recorded on every document."""
        return self.context.source_name

    @abstractmethod
    def discover(
        self, binding: AgencyBinding, since: date | None = None, until: date | None = None
    ) -> Iterator[DiscoveredDocument]:
        """List the agency's documents published in ``[since, until]`` (open-ended when None)."""

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
        raise NotImplementedError(f"source type {self.kind!r} cannot rebuild content from snapshots")

    def check_status(self, document: DiscoveredDocument) -> StatusCheck:
        """Report what amends or replaces the document. Must not consume rationed reads."""
        raise NotImplementedError(f"source type {self.kind!r} has no status checks")

    # Optional: sources that need sign-in override these.
    @classmethod
    def login(cls, settings: Mapping[str, Any], context: ConnectorContext, *, method: str | None = None) -> str:
        raise NotImplementedError(f"source type {cls.kind!r} does not need sign-in")

    @classmethod
    def auth_status(cls, settings: Mapping[str, Any], context: ConnectorContext) -> dict[str, Any]:
        return {"auth": "not required"}

    def close(self) -> None:
        """Release network resources."""

    def __enter__(self) -> Connector:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
