"""RegAlert connector: official publications of financial regulators, via RegAlert's API.

API reference: https://mcp.regalert.today/llms.txt and /openapi.json. RegAlert permits
reading through this API only, never by scraping its website.

* Discovery uses ``/v1/recent`` (free, unlimited) in adaptive date windows: a window
  holding more than one page is split in half, so we rarely depend on offset paging,
  which is unstable when many documents share a date.
* Content uses ``/v1/documents/{uid}`` with ``include_original=true`` (rationed by fair
  use). ``429 fair_use`` and a paused account become ``QuotaExhausted``.

* Standing checks use ``/v1/documents/{uid}/lineage`` (free, unlimited): incoming
  ``amends``/``supersedes`` edges tell us when a regulation is amended or replaced.

Document responses were confirmed on the first live run (2026-10-12): ``text`` and
``original_text`` page independently (``text_next_offset`` / ``original_text_next_offset``,
``*_total_chars``), and every response carries ``account`` and ``charged_usd``. Lineage
edges carry ``relation``, ``uid``, ``title``, ``date``. Note: RegAlert sometimes labels a
replacement as ``amends`` (POJK 1/2026 → 37/POJK.03/2017).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from regingest.connectors.base import AgencyBinding, Connector, ConnectorContext, QuotaExhausted, SourceError
from regingest.connectors.regalert.auth import RegAlertAuth, TokenStore, browser_login, device_login
from regingest.connectors.regalert.client import RegAlertClient
from regingest.models import (
    DiscoveredDocument,
    DocumentContent,
    Origin,
    Rights,
    Snapshot,
    StatusCheck,
    TextArtifact,
)

DEFAULT_BASE_URL = "https://mcp.regalert.today"
MAX_PAGE_LIMIT = 100  # API maximum for /v1/recent
MAX_CHARS = 400_000  # API maximum for /v1/documents/{uid}
EARLIEST = date(1900, 1, 1)
DISCOVERY_FIELDS = ",".join((
    "uid", "title", "published", "regulator", "regulator_name", "identifier", "instrument_no", "type",
    "language", "source", "file_url", "url", "jurisdiction", "topics",
))
MAX_TEXT_PAGES = 50
STATUS_RELATIONS = ("amends", "supersedes")  # incoming edges that change a document's standing


def _usd(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and (m := re.search(r"\d+(?:\.\d+)?", value)):
        return float(m.group(0))
    return 0.0


@dataclass
class _TextPart:
    """One of the document's texts, assembled across pages.

    Offsets are RegAlert's own units, which differ slightly from Python's character
    count (a 400000-unit page can hold 399992 characters), so pages are joined by the
    offsets the API reports, and completeness comes from its ``*_truncated`` flag.
    """

    key: str  # "text" or "original_text"
    chunks: list[str] = field(default_factory=list)
    expected_offset: int | None = 0  # the API offset of the next page we need; None when done
    truncated: bool | None = None  # as reported by the last page we took

    def take(self, doc: Mapping[str, Any], requested_offset: int) -> None:
        if self.expected_offset is None:
            return
        chunk = doc.get(self.key)
        if not isinstance(chunk, str):
            self.expected_offset = None
            return
        offset = doc.get(f"{self.key}_offset", requested_offset)
        if offset != self.expected_offset:
            return  # this response is positioned for the other text
        self.chunks.append(chunk)
        truncated = doc.get(f"{self.key}_truncated")
        self.truncated = truncated if isinstance(truncated, bool) else None
        nxt = doc.get(f"{self.key}_next_offset")
        self.expected_offset = nxt if isinstance(nxt, int) and nxt > offset else None

    @property
    def text(self) -> str:
        return "".join(self.chunks).strip()

    @property
    def complete(self) -> bool | None:
        if self.truncated is None:
            return None
        return not self.truncated and self.expected_offset is None


def _token_store(context: ConnectorContext) -> TokenStore:
    return TokenStore(context.credentials_dir / f"{context.source_name}.json")


class RegAlertConnector(Connector):
    kind = "regalert"
    content_fields = ("title", "identifier", "instrument_no", "source", "file_url")
    monitored_fields = ("identifier", "instrument_no", "source", "file_url")
    supports_status_checks = True

    def __init__(
        self,
        settings: Mapping[str, Any],
        context: ConnectorContext,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(settings, context)
        self._page_limit = max(1, min(int(settings.get("page_limit", MAX_PAGE_LIMIT)), MAX_PAGE_LIMIT))
        self._max_chars = max(1000, min(int(settings.get("max_chars", MAX_CHARS)), MAX_CHARS))
        self._original_rights = Rights(settings.get("original_text_rights", Rights.PUBLIC))
        self._translation_rights = Rights(settings.get("translation_rights", Rights.RESTRICTED))
        static = os.environ.get(settings.get("token_env", "REGALERT_ACCESS_TOKEN"))
        auth = RegAlertAuth(
            _token_store(context), static_token=static, login_hint=f"regingest auth login {context.source_name}"
        )
        self._client = RegAlertClient(
            settings.get("base_url", DEFAULT_BASE_URL),
            auth=auth,
            user_agent=context.user_agent,
            requests_per_second=float(settings.get("requests_per_second", 1.0)),
            transport=transport,
            sleep=sleep,
        )
        self._max_charge_usd = float(settings.get("max_charge_usd_per_read", 0.0))
        self._reads_checked = False
        self._stop_reason: str | None = None  # set when a response says further reads would pause or cost money

    def close(self) -> None:
        self._client.close()

    # --- discovery -----------------------------------------------------------

    def discover(
        self, binding: AgencyBinding, since: date | None = None, until: date | None = None
    ) -> Iterator[DiscoveredDocument]:
        regulator = binding.params.get("regulator")
        if not regulator:
            raise SourceError(f"[agencies.{binding.agency}.sources.{self.name}] needs 'regulator' (e.g. \"OJK\")")
        jurisdiction = binding.params.get("jurisdiction")
        seen: set[str] = set()
        for row in self._walk(regulator, jurisdiction, since or EARLIEST, until or datetime.now(UTC).date()):
            uid = row.get("uid")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            yield self._to_document(row, binding.agency)

    def _recent(self, regulator: str, jurisdiction: str | None, start: date, end: date, offset: int = 0) -> dict[str, Any]:
        body, _ = self._client.get("/v1/recent", {
            "regulator": regulator,
            "jurisdiction": jurisdiction,
            "since": start.isoformat(),
            "until": end.isoformat(),
            "limit": self._page_limit,
            "offset": offset,
            "fields": DISCOVERY_FIELDS,
        })
        return body

    def _walk(self, regulator: str, jurisdiction: str | None, start: date, end: date) -> Iterator[dict[str, Any]]:
        windows = [(start, end)]
        while windows:
            a, b = windows.pop()
            page = self._recent(regulator, jurisdiction, a, b)
            rows = page.get("documents") or []
            total = int(page.get("total", len(rows)))
            if total <= len(rows):
                yield from rows
                continue
            if a < b:
                mid = a + (b - a) // 2
                windows.append((a, mid))
                windows.append((mid + timedelta(days=1), b))  # popped first: newest first
                continue
            # A single day holds more than a page: offset paging is the only option here.
            yield from rows
            offset = len(rows)
            while offset < total:
                more = self._recent(regulator, jurisdiction, a, b, offset).get("documents") or []
                if not more:
                    break
                yield from more
                offset += len(more)

    def _to_document(self, row: dict[str, Any], agency: str) -> DiscoveredDocument:
        uid = str(row["uid"])
        return DiscoveredDocument(
            source=self.name,
            source_id=uid,
            agency=agency,
            title=row.get("title") or "",
            published=row.get("published"),
            source_url=row.get("url") or f"https://regalert.today/document/{uid}",
            official_url=row.get("source"),
            metadata=row,
        )

    # --- content -------------------------------------------------------------

    def account(self) -> dict[str, Any]:
        body, _ = self._client.get("/v1/account")
        return body

    def _ensure_reads_available(self) -> None:
        state = self.account().get("full_text_reads")
        if state not in (None, True, "available"):
            raise QuotaExhausted(f"RegAlert full-text reads are {state!r}; they resume the next day (UTC)")

    def _note_account(self, body: Mapping[str, Any]) -> float:
        """Read the account state and charge that come with every document response."""
        state = (body.get("account") or {}).get("full_text_reads")
        if state not in (None, True, "available"):
            self._stop_reason = f"RegAlert full-text reads are {state!r}; they resume the next day (UTC)"
        charged = _usd(body.get("charged_usd"))
        if charged > self._max_charge_usd:
            self._stop_reason = (
                f"RegAlert charged ${charged:.2f} for one read (allowed: ${self._max_charge_usd:.2f}); "
                "stopping reads until max_charge_usd_per_read is raised"
            )
        return charged

    def fetch_content(self, document: DiscoveredDocument) -> DocumentContent:
        if self._stop_reason:
            raise QuotaExhausted(self._stop_reason)
        if not self._reads_checked:
            self._ensure_reads_available()
            self._reads_checked = True

        pages: list[tuple[int, dict[str, Any]]] = []
        snapshots: list[Snapshot] = []
        charged = 0.0
        english, original = _TextPart("text"), _TextPart("original_text")
        offset = 0
        for _ in range(MAX_TEXT_PAGES):
            body, raw = self._client.get(
                f"/v1/documents/{document.source_id}",
                {"max_chars": self._max_chars, "include_original": True, "offset": offset},
            )
            snapshots.append(Snapshot(kind=f"document_json@{offset}", content=raw))
            charged += self._note_account(body)
            pages.append((offset, body))
            doc = body["document"] if isinstance(body.get("document"), dict) else body
            english.take(doc, offset)
            original.take(doc, offset)
            pending = [p.expected_offset for p in (english, original) if p.expected_offset is not None]
            if not pending:
                break
            offset = min(pending)
        content = self._assemble(document, pages)
        content.extra["charged_usd"] = charged
        return DocumentContent(texts=content.texts, snapshots=snapshots, extra=content.extra)

    def rebuild_content(self, document: DiscoveredDocument, snapshots: list[tuple[str, bytes]]) -> DocumentContent:
        """Re-assemble content from stored ``document_json@{offset}`` payloads, without network."""
        pages = []
        for kind, raw in snapshots:
            if kind.startswith("document_json@"):
                pages.append((int(kind.rsplit("@", 1)[1]), json.loads(raw)))
        if not pages:
            raise ValueError("no document_json snapshots to rebuild from")
        content = self._assemble(document, sorted(pages, key=lambda p: p[0]))
        content.extra["rebuilt_from_snapshots"] = True
        return content

    def _assemble(self, document: DiscoveredDocument, pages: list[tuple[int, dict[str, Any]]]) -> DocumentContent:
        english, original = _TextPart("text"), _TextPart("original_text")
        first: dict[str, Any] | None = None
        for offset, body in pages:
            doc = body["document"] if isinstance(body.get("document"), dict) else body
            first = first or doc
            english.take(doc, offset)
            original.take(doc, offset)

        assert first is not None
        language = str(
            first.get("original_language") or first.get("language") or document.metadata.get("language") or ""
        ).lower() or None
        texts: list[TextArtifact] = []
        if language == "en":
            if english.text:
                texts.append(TextArtifact("en", Origin.SOURCE_TEXT, self._original_rights, english.text, english.complete))
        else:
            if original.text and language:
                texts.append(TextArtifact(language, Origin.SOURCE_TEXT, self._original_rights, original.text,
                                          original.complete))
            if english.text:
                texts.append(TextArtifact("en", Origin.SOURCE_TRANSLATION, self._translation_rights, english.text,
                                          english.complete))
        lineage = first.get("lineage") if isinstance(first.get("lineage"), dict) else {}
        affects = [
            str(edge["uid"])
            for relation in STATUS_RELATIONS
            for edge in lineage.get(relation) or []
            if isinstance(edge, dict) and edge.get("uid")
        ]
        extra = {
            "original_language": language,
            "original_text_found": bool(original.text) or language == "en",
            "text_complete": {"en": english.complete, "original": original.complete},
            "lineage": lineage or None,
            "enclosures": first.get("enclosures"),
            "affects": affects,
            "text_pages": len(pages),
        }
        return DocumentContent(texts=texts, snapshots=[], extra=extra)

    # --- standing (change detection) -------------------------------------------

    def check_status(self, document: DiscoveredDocument) -> StatusCheck:
        edges: list[dict[str, Any]] = []
        for relation in STATUS_RELATIONS:
            offset = 0
            while True:
                body, _ = self._client.get(
                    f"/v1/documents/{document.source_id}/lineage",
                    {"direction": "incoming", "relation": relation, "limit": 100, "offset": offset},
                )
                batch =[e for e in body.get("edges") or [] if isinstance(e, dict)]
                edges += [
                    {"relation": e.get("relation", relation), "source_id": str(e.get("uid")),
                     "title": e.get("title"), "date": e.get("date")}
                    for e in batch if e.get("uid")
                ]
                if not batch or not body.get("has_more"):
                    break
                offset += len(batch)
        keys = sorted(f"{e['relation']}:{e['source_id']}" for e in edges)
        fingerprint = hashlib.sha256("\n".join(keys).encode()).hexdigest()
        relations = {e["relation"] for e in edges}
        summary = "superseded" if "supersedes" in relations else "amended" if "amends" in relations else "unchanged"
        return StatusCheck(fingerprint=fingerprint, edges=edges, summary=summary)

    # --- sign-in ---------------------------------------------------------------

    @classmethod
    def login(
        cls,
        settings: Mapping[str, Any],
        context: ConnectorContext,
        *,
        method: str | None = None,
        transport: httpx.BaseTransport | None = None,
        notify: Callable[[str], None] = print,
    ) -> str:
        store = _token_store(context)
        base_url = settings.get("base_url", DEFAULT_BASE_URL)
        with httpx.Client(transport=transport, timeout=30.0, headers={"User-Agent": context.user_agent}) as http:
            if method in (None, "browser"):
                browser_login(http, base_url, store, notify=notify)
            elif method == "device":
                device_login(http, base_url, store, notify=notify)
            else:
                raise ValueError(f"unknown sign-in method {method!r}; use 'browser' or 'device'")
        return f"Signed in to RegAlert. Token saved to {store.path}"

    @classmethod
    def auth_status(cls, settings: Mapping[str, Any], context: ConnectorContext) -> dict[str, Any]:
        store = _token_store(context)
        token = store.load()
        env_var = settings.get("token_env", "REGALERT_ACCESS_TOKEN")
        info: dict[str, Any] = {
            "token_file": str(store.path),
            "signed_in": token is not None,
            "token_from_env": bool(os.environ.get(env_var)),
        }
        if token is not None:
            info["refreshable"] = bool(token.refresh_token)
            if token.expires_at:
                info["expires_at"] = datetime.fromtimestamp(token.expires_at, UTC).isoformat(timespec="seconds")
        if token is not None or info["token_from_env"]:
            try:
                with cls(settings, context) as connector:
                    info["account"] = connector.account()
            except SourceError as e:
                info["error"] = str(e)
        return info
