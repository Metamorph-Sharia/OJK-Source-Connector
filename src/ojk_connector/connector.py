"""The OJK connector as the orchestrator sees it: the team's ``SourceConnector`` interface.

The orchestrator calls ``discover`` and ``fetch`` and never sees RegAlert, the local
pipeline or its store. Every source in config is offered: today RegAlert; when the
official-source fallback (ojk.go.id) is added as another ``Source``, its documents
appear here too, without changes to this module. Merging documents from several
sources stays out of the connector, as the interface requires.

    connector = OJKConnector({"config": "config/pipeline.toml"})
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ojk_connector import __version__
from ojk_connector.config import DEFAULT_CONFIG_PATH, Config, load_config
from ojk_connector.models import DiscoveredDocument
from ojk_connector.source_connector import (
    AuthFailed,
    Discovered,
    Discovery,
    Fetched,
    FileStore,
    ItemFailed,
    SourceConnector,
    SourceUnavailable,
    StoredFile,
)
from ojk_connector.sources import (
    DEFAULT_LOOKBACK_DAYS,
    PermanentSourceError,
    QuotaExhausted,
    Source,
    SourceAuthError,
    SourceContext,
    SourceError,
    SourceForbidden,
    source_class,
)

TEXT_MEDIA_TYPE = "text/plain; charset=utf-8"


def _today() -> date:
    return datetime.now(UTC).date()


class OJKConnector(SourceConnector):
    name = "ojk"
    version = __version__
    max_concurrency = 1  # RegAlert's full-text reads are rationed: one at a time

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        sources: Mapping[str, Source] | None = None,
        today: Callable[[], date] = _today,
    ) -> None:
        """``config["config"]``: path to the pipeline config (default ``config/pipeline.toml``)."""
        super().__init__(config)
        self._config: Config = load_config(config.get("config", DEFAULT_CONFIG_PATH))
        self._sources: dict[str, Source] = dict(sources or {})
        self._today = today

    def discover(self, cursor: str | None) -> Discovery:
        until = self._today()
        since = None if cursor is None else date.fromisoformat(cursor) - timedelta(days=self._lookback_days())
        items: list[Discovered] = []
        for name in self._config.sources:
            with _translated_errors(item_failure=False):
                source = self._source(name)
                items += [_to_discovered(source, doc) for doc in source.discover(since, until)]
        return Discovery(items=items, cursor=until.isoformat())

    def fetch(self, item: Discovered, store: FileStore) -> Fetched:
        document = _to_document(item)
        with _translated_errors(item_failure=True):
            content = self._source(document.source).fetch_content(document)
        files = [store.put(s.content, media_type=s.media_type, source_url=item.source_url) for s in content.snapshots]
        texts = []
        for text in content.texts:
            stored = store.put(text.content.encode("utf-8"), media_type=TEXT_MEDIA_TYPE, source_url=item.source_url)
            files.append(stored)
            texts.append(_text_entry(stored, text.language, text.origin, text.rights, text.complete))
        return Fetched(files=files, metadata={"texts": texts, **content.extra})

    def close(self) -> None:
        for source in self._sources.values():
            source.close()

    def _source(self, name: str) -> Source:
        if name not in self._sources:
            if name not in self._config.sources:
                raise ItemFailed(f"source {name!r} is no longer configured", retryable=False)
            context = SourceContext(name, self._config.credentials_dir, self._config.user_agent)
            self._sources[name] = source_class(name)(self._config.sources[name].settings, context)
        return self._sources[name]

    def _lookback_days(self) -> int:
        return max(int(s.settings.get("lookback_days", DEFAULT_LOOKBACK_DAYS)) for s in self._config.sources.values())


@contextmanager
def _translated_errors(*, item_failure: bool) -> Iterator[None]:
    """Turn our source errors into the ones the orchestrator understands."""
    try:
        yield
    except QuotaExhausted as e:
        raise SourceUnavailable(str(e)) from e
    except SourceAuthError as e:
        raise AuthFailed(str(e)) from e
    except SourceForbidden as e:  # refused, not broken: stop the whole run, never retry item by item
        raise SourceUnavailable(str(e)) from e
    except SourceError as e:
        if not item_failure:
            raise SourceUnavailable(str(e)) from e
        raise ItemFailed(str(e), retryable=not isinstance(e, PermanentSourceError)) from e


def _to_discovered(source: Source, doc: DiscoveredDocument) -> Discovered:
    return Discovered(
        source_id=f"{doc.source}:{doc.source_id}",  # unique across our sources
        source_url=doc.source_url or doc.official_url or "",
        version=_version(source, doc),
        metadata={
            "source": doc.source,
            "id": doc.source_id,
            "title": doc.title,
            "published": doc.published,
            "official_url": doc.official_url,
            "fields": doc.metadata,  # the source's own fields, unchanged
        },
    )


def _to_document(item: Discovered) -> DiscoveredDocument:
    meta = item.metadata
    return DiscoveredDocument(
        source=meta["source"],
        source_id=meta["id"],
        title=meta["title"],
        published=meta["published"],
        source_url=item.source_url or None,
        official_url=meta["official_url"],
        metadata=dict(meta["fields"]),
    )


def _version(source: Source, doc: DiscoveredDocument) -> str:
    """Changes when a field that signals new content changes (e.g. RegAlert's file URL)."""
    fields = {"title": doc.title, **{k: doc.metadata.get(k) for k in source.content_fields}}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _text_entry(stored: StoredFile, language: str, origin: str, rights: str, complete: bool | None) -> dict[str, Any]:
    return {"sha256": stored.sha256, "language": language, "origin": origin, "rights": rights, "complete": complete}
