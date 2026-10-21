"""JDIH OJK source: OJK's own legal database (``jdih.ojk.go.id``), the primary source since 2026-10-16.

* Discovery sweeps the listing: every sector × {POJK, SEOJK/PADK}, 20 requests, the whole catalogue
  each time (the listing has no date filter). ``since``/``until`` are therefore ignored.
* ``read_entry`` reads one detail page: official title, dates, standing, history, legal basis, file list.
* ``fetch_content`` reads the detail page, downloads every file it lists, and extracts the regulation's
  text from its main PDF (``self_extracted``, public: an official instrument).

No sign-in, no quota. Paced at one request per second by default.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any

import httpx
from metamorph_contract import wants_printed_lines
from metamorph_pdf_text import from_environment, wants_mineru

from ojk_connector.identity import PRIMARY_LANGUAGE
from ojk_connector.models import (
    CatalogEntry,
    DiscoveredDocument,
    DocumentContent,
    FileLink,
    Origin,
    Rights,
    Snapshot,
    TextArtifact,
)
from ojk_connector.pdf_text import NotAPdf, PdfText, extract_text, extract_text_with, is_pdf
from ojk_connector.sources.base import Source, SourceContext, SourceError, SourceForbidden
from ojk_connector.sources.jdih.catalog import (
    DOCUMENT_TYPES,
    ENTRY_SNAPSHOT,
    SECTORS,
    ListingRow,
    UnexpectedPage,
    listing_url,
    parse_entry,
    parse_listing,
)
from ojk_connector.sources.jdih.client import JdihClient

log = logging.getLogger(__name__)

FILE_SNAPSHOT = "file"  # snapshot kinds: "file:{role}:{name}"
PDF = "application/pdf"


def file_snapshot_kind(link: FileLink) -> str:
    return f"{FILE_SNAPSHOT}:{link.role}:{link.name}"


def _today() -> date:
    return datetime.now(UTC).date()


class JdihOjkSource(Source):
    content_fields = ("number", "type")
    monitored_fields = ("number", "sector", "type", "status", "date")
    supports_entries = True

    def __init__(
        self,
        settings: Mapping[str, Any],
        context: SourceContext,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        today: Callable[[], date] = _today,
    ) -> None:
        super().__init__(settings, context)
        self._printed_lines = wants_printed_lines(os.environ)  # METAMORPH_PDF_LINES=extracted: pymupdf's own lines
        self._mineru = from_environment(os.environ) if wants_mineru(os.environ) else None  # METAMORPH_PDF_ENGINE
        self._client = JdihClient(
            user_agent=context.user_agent,
            requests_per_second=float(settings.get("requests_per_second", 1.0)),
            transport=transport,
            sleep=sleep,
        )
        self._today = today

    def close(self) -> None:
        self._client.close()

    # --- discovery -----------------------------------------------------------

    def discover(self, since: date | None = None, until: date | None = None) -> Iterator[DiscoveredDocument]:
        seen: set[str] = set()
        for sector in SECTORS:
            for kind in DOCUMENT_TYPES:
                for row in self._listing(sector, kind):
                    if row.guid in seen:
                        continue
                    seen.add(row.guid)
                    yield DiscoveredDocument(
                        source=self.name,
                        source_id=row.guid,
                        title=row.title,
                        published=row.published,
                        source_url=row.url,
                        official_url=row.url,
                        metadata=row.fields,
                    )

    def _listing(self, sector: str, kind: str) -> list[ListingRow]:
        try:
            return parse_listing(self._client.get(listing_url(sector, kind)).content)
        except UnexpectedPage as e:
            raise SourceError(f"JDIH OJK listing {sector}/{kind}: {e}") from e

    # --- catalogue entry -------------------------------------------------------

    def read_entry(self, document: DiscoveredDocument) -> CatalogEntry:
        return self._parse_entry(self._client.get(self._detail_url(document)).content)

    def rebuild_entry(self, document: DiscoveredDocument, page: bytes) -> CatalogEntry:
        return self._parse_entry(page)

    def _parse_entry(self, page: bytes) -> CatalogEntry:
        try:
            return parse_entry(page, today=self._today())
        except UnexpectedPage as e:
            raise SourceError(f"JDIH OJK detail page: {e}") from e

    @staticmethod
    def _detail_url(document: DiscoveredDocument) -> str:
        if not document.source_url:
            raise SourceError(f"JDIH OJK {document.source_id}: no detail page URL")
        return document.source_url

    # --- content -------------------------------------------------------------

    def fetch_content(self, document: DiscoveredDocument) -> DocumentContent:
        """Only the regulation itself is required: a broken abstract, FAQ or annex is recorded, not fatal
        (POJK 37/2019's FAQ answers HTTP 500 every time, 2026-10-16)."""
        entry = self.read_entry(document)
        files, missing = [], []
        for link in entry.files:
            try:
                files.append((link, self._client.get(link.url).content))
            except SourceForbidden:
                raise
            except SourceError as e:
                if link.role == "main":
                    raise
                missing.append({"role": link.role, "name": link.name, "url": link.url, "error": str(e)})
        assert entry.snapshot is not None
        snapshots = [entry.snapshot, *(Snapshot(file_snapshot_kind(link), data, _media_type(link, data))
                                       for link, data in files)]
        content = self._content(entry, files, snapshots)
        content.extra["files_missing"] = missing
        return content

    def rebuild_content(self, document: DiscoveredDocument, snapshots: list[tuple[str, bytes]]) -> DocumentContent:
        """Re-parse the stored detail page and re-extract text from the stored files, without network."""
        pages = [data for kind, data in snapshots if kind == ENTRY_SNAPSHOT]
        if not pages:
            raise ValueError("no stored detail page to rebuild from")
        entry = self._parse_entry(pages[-1])
        stored = {kind: data for kind, data in snapshots}
        files = [(link, stored[kind]) for link in entry.files if (kind := file_snapshot_kind(link)) in stored]
        content = self._content(entry, files, [])
        content.extra["rebuilt_from_snapshots"] = True
        return content

    def _content(self, entry: CatalogEntry, files: list[tuple[FileLink, bytes]],
                 snapshots: list[Snapshot]) -> DocumentContent:
        texts, described = [], []
        for link, data in files:
            item: dict[str, Any] = {"role": link.role, "name": link.name, "url": link.url, "bytes": len(data),
                                    "media_type": _media_type(link, data)}
            if link.role == "main" and is_pdf(data) and not texts:
                try:
                    extracted = self._extracted(data)
                except NotAPdf as e:
                    item["error"] = str(e)
                else:
                    item |= {"pages": extracted.pages, "pages_without_text": extracted.pages_without_text,
                             "text": True}
                    if extracted.extraction:  # read by MinerU: kept with the file's description, for the export
                        item |= {"method": str(extracted.method), "extraction": asdict(extracted.extraction)}
                    texts.append(TextArtifact(PRIMARY_LANGUAGE, Origin.SELF_EXTRACTED, Rights.PUBLIC,
                                              extracted.text, extracted.complete))
            described.append(item)
        if not texts:
            log.warning("JDIH OJK %s: no main PDF with text among %s files", entry.title, len(files))
        return DocumentContent(texts=texts, snapshots=snapshots,
                               extra={"entry": entry.facts(), "files": described, "paged": True})


    def _extracted(self, data: bytes) -> PdfText:
        if self._mineru is not None:
            return extract_text_with(data, self._mineru)
        return extract_text(data, printed_lines=self._printed_lines)


def _media_type(link: FileLink, data: bytes) -> str:
    if is_pdf(data):
        return PDF
    return mimetypes.guess_type(link.name)[0] or "application/octet-stream"
