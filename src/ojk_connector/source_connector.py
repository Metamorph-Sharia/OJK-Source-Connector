"""Source connector interface, version 1.0. Standard library only, Python 3.11+."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

INTERFACE_VERSION = "1.0"


# --- Data crossing the boundary: plain, JSON-safe values --------------------


@dataclass(frozen=True)
class Discovered:
    """One document the source lists. No content downloaded yet."""

    source_id: str  # stable across runs: the source's own ID or permanent URL
    source_url: str  # where a person can see it at the source
    version: str | None = None  # changes when the document changes (updated_at, ETag, hash), if known
    metadata: Mapping[str, Any] = field(default_factory=dict)  # the source's own fields, unchanged

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id must not be empty")
        _require_json(self.metadata)


@dataclass(frozen=True)
class Discovery:
    """Returned by `discover()`."""

    items: list[Discovered]
    cursor: str | None  # pass back to the next `discover()` to get only what changed


@dataclass(frozen=True)
class StoredFile:
    """Returned by `FileStore.put()`."""

    sha256: str
    media_type: str  # e.g. "application/pdf", "text/html"
    source_url: str | None = None  # where the bytes were downloaded from


@dataclass(frozen=True)
class Fetched:
    """Returned by `fetch()`."""

    files: list[StoredFile]  # at least one
    metadata: Mapping[str, Any] = field(default_factory=dict)  # fields found while fetching, unchanged

    def __post_init__(self) -> None:
        if not self.files:
            raise ValueError("fetch() must store at least one file")
        _require_json(self.metadata)


def _require_json(value: Mapping[str, Any]) -> None:
    # Values cross process boundaries (Airflow tasks, retries, storage).
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"metadata must be JSON-serializable: {exc}") from None


# --- Port the orchestrator provides -----------------------------------------


class FileStore(Protocol):
    """Where a connector saves downloaded bytes. Provided by the orchestrator.

    Content-addressed and write-once: saving the same bytes twice is harmless.
    """

    def put(self, data: bytes, *, media_type: str, source_url: str | None = None) -> StoredFile: ...


# --- Errors the orchestrator understands -------------------------------------
# Raise one of these for expected problems. Anything else is treated as a bug.


class ConnectorError(Exception):
    """Base class."""


class ItemFailed(ConnectorError):
    """This one document failed. The run continues with the next one."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable  # False: don't try this document again until it changes


class SourceUnavailable(ConnectorError):
    """The source is down, rate-limited or changed. Stop now and retry later."""


class AuthFailed(ConnectorError):
    """Credentials are missing, expired or rejected. A person must fix them."""


# --- The interface ------------------------------------------------------------


class SourceConnector(ABC):
    """Implement `discover` and `fetch`, and set `name`."""

    name: ClassVar[str]  # unique across our connectors, e.g. "regalert", "ojk-website"
    version: ClassVar[str] = "0.1.0"  # your connector's version; recorded with what it fetches
    max_concurrency: ClassVar[int] = 1  # how many `fetch()` calls the source tolerates at once

    def __init__(self, config: Mapping[str, Any]) -> None:
        """`config` holds your settings; secrets come from environment variables."""
        self.config = config

    @abstractmethod
    def discover(self, cursor: str | None) -> Discovery:
        """List documents without downloading their content.

        `cursor=None`: list everything. Otherwise: list what changed since the
        cursor you returned last time.
        """

    @abstractmethod
    def fetch(self, item: Discovered, store: FileStore) -> Fetched:
        """Download one document and save every file through `store.put()`.

        May run in a different process from `discover()`, and may be retried:
        use only `item` and `self.config`.
        """
