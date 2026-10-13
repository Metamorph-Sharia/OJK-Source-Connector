# Source Connector Interface

**Version 1.0 · 2026-10-13**

Every source connector we build implements one small Python interface. Then the orchestrator in the next phase (probably Airflow) can run any of our connectors the same way, without knowing how each one talks to its source.

**What you implement:** one class, two methods: `discover` and `fetch`.
**What you don't implement:** scheduling, retries, storage layout, cursors, run reports or Airflow code. The orchestrator owns all of that.
**What isn't defined yet:** the document schema. Pass the source's fields through unchanged in `metadata`. We'll design a common schema once we've compared what all our sources hold.

## How it fits

```
Orchestrator (Airflow, later) ──depends on──▶ source_connector.py ◀──depends on── Your connector
```

Both sides depend only on the interface file, never on each other:

- The orchestrator never imports your internals.
- Your connector never imports Airflow.
- You can change anything inside your connector, such as the HTTP library, PDF links or page parsing. As long as the two methods behave as described, nothing else changes.

## The interface

Copy this file into your repository as `source_connector.py` and don't edit it. If you need a change, propose it to the team so everyone stays on the same version.

```python
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
```

## The rules

The types already reject empty IDs, metadata that isn't JSON, and fetches that save no files. These rules are the ones a type can't check:

1. **`source_id` never changes.** Use the source's own ID or a permanent URL, never a position in a list or a page number. It's how the same document is recognised across runs.
2. **Save the original bytes.** Pass every PDF, HTML page or API response you use through `store.put()`, unchanged, before you parse it. Then we can re-parse later without downloading again.
3. **Pass fields through unchanged.** Put the source's fields in `metadata` as they are. Don't rename them, clean them, merge duplicates or assign our own IDs; that comes later, the same way for every source.
4. **`fetch()` stands alone.** It may run in another process, on another machine, or be retried. Use only `item` and `self.config`: stash what `fetch` needs in `item.metadata`, never in `self`.
5. **Be polite to the source.** Pace your own requests, send a User-Agent naming the project, follow the site's terms and robots.txt, and never get around CAPTCHAs or bot checks.
6. **Raise the right error.** Use `ItemFailed` when one document fails, `SourceUnavailable` when the whole source is down or rate-limiting you, and `AuthFailed` when credentials are bad. Anything else counts as a bug.
7. **Secrets come from environment variables.** Never put them in `config`, files, metadata or logs.
8. **Log with `logging`, never `print`.**

**PDF text extraction is optional inside a connector.** If you parse PDFs to find the document's fields, that's fine. Still save the PDF itself through `store.put()`, so text extraction can later run as a shared step over every source's files.

## Example: an official website with PDFs

```python
import logging
import time

import httpx

from source_connector import (
    Discovered, Discovery, Fetched, ItemFailed, SourceConnector, SourceUnavailable,
)

log = logging.getLogger(__name__)


class ExampleSiteConnector(SourceConnector):
    name = "example-site"
    version = "0.1.0"

    def __init__(self, config):
        super().__init__(config)
        self.http = httpx.Client(
            headers={"User-Agent": "metamorph-ingest/0.1 (+contact@example.com)"}, timeout=30
        )

    def _get(self, url: str) -> httpx.Response:
        time.sleep(1.0)  # pace requests
        try:
            response = self.http.get(url)
        except httpx.TransportError as exc:
            raise SourceUnavailable(str(exc)) from exc
        if response.status_code in (429, 503) or response.status_code >= 500:
            raise SourceUnavailable(f"{url}: HTTP {response.status_code}")
        return response

    def discover(self, cursor):
        items = []
        for row in parse_listing(self._get(self.config["listing_url"]).text):  # your parser
            items.append(Discovered(
                source_id=row["detail_url"],
                source_url=row["detail_url"],
                version=row.get("published"),
                metadata=row,  # e.g. {"title": ..., "number": ..., "pdf_url": ...}
            ))
        newest = max((i.version for i in items if i.version), default=cursor)
        return Discovery(items=items, cursor=newest)

    def fetch(self, item, store):
        page = self._get(item.source_url)
        if page.status_code == 404:
            raise ItemFailed("detail page gone", retryable=False)
        files = [store.put(page.content, media_type="text/html", source_url=item.source_url)]

        pdf_url = item.metadata["pdf_url"]
        pdf = self._get(pdf_url)
        if pdf.status_code != 200:
            raise ItemFailed(f"PDF returned HTTP {pdf.status_code}")
        files.append(store.put(pdf.content, media_type="application/pdf", source_url=pdf_url))

        return Fetched(files=files, metadata=parse_detail(page.text))  # your parser
```

## Test it locally

The orchestrator will provide the real `FileStore`. For your own tests, this in-memory one is enough:

```python
import hashlib

from source_connector import StoredFile


class MemoryStore:
    def __init__(self):
        self.files = {}

    def put(self, data, *, media_type, source_url=None):
        sha = hashlib.sha256(data).hexdigest()
        self.files[sha] = data
        return StoredFile(sha256=sha, media_type=media_type, source_url=source_url)


def test_connector_follows_the_interface():
    connector = ExampleSiteConnector({"listing_url": "https://example.com/regulations"})
    discovery = connector.discover(cursor=None)
    assert discovery.items, "found nothing"

    store = MemoryStore()
    fetched = connector.fetch(discovery.items[0], store)
    assert all(f.sha256 in store.files for f in fetched.files)
```

Run this test against recorded responses (for example with `respx` or saved HTML/PDF fixtures) rather than the live site, so it stays fast and polite.

## How the orchestrator will use it (for context)

You don't write any of this; it shows why the rules matter.

| Orchestrator step | Calls | Your error becomes |
|---|---|---|
| Discover | `discover(saved_cursor)`, then saves the items and the new cursor | `SourceUnavailable`: retry later · `AuthFailed`: alert a person |
| Fetch | `fetch(item, store)` for new or changed items, at most `max_concurrency` at once | `ItemFailed`: recorded, next item · `SourceUnavailable`: stop, retry later |

Items are new or changed when their `source_id` is new or their `version` differs from the stored one.

## Checklist before you hand it over

- [ ] Uses the unmodified `source_connector.py`, version 1.0.
- [ ] `name` is set and unique across the team.
- [ ] `discover(None)` lists everything; `discover(cursor)` lists only changes, or everything again if your source can't tell.
- [ ] `source_id` is the same on every run for the same document.
- [ ] Every downloaded file goes through `store.put()`.
- [ ] `fetch()` works in a fresh process with only `item` and the config.
- [ ] Errors are `ItemFailed`, `SourceUnavailable` or `AuthFailed`.
- [ ] Requests are paced and identified, and secrets come from environment variables.
- [ ] Tests run offline against recorded responses.
