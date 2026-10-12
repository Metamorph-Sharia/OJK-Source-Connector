"""Local storage: SQLite for metadata, a content-addressed blob store for raw bytes.

Layout under ``data_dir``::

    raw/{sha256[:2]}/{sha256}     immutable blobs (API payloads, texts, files)
    db/regingest.sqlite           everything else

The schema is versioned with ``PRAGMA user_version``; ``_migrate`` upgrades older
databases in place.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from regingest.agencies.base import IdResolution
from regingest.models import DiscoveredDocument, DocumentContent, Origin, Rights, StatusCheck

SCHEMA_VERSION = 2

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS source_documents (
    source              TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    agency              TEXT NOT NULL,
    title               TEXT,
    published           TEXT,
    source_url          TEXT,
    official_url        TEXT,
    metadata_json       TEXT NOT NULL,
    canonical_id        TEXT,
    id_confidence       TEXT NOT NULL DEFAULT 'none',
    id_evidence         TEXT,
    id_origin           TEXT,                 -- metadata | text
    content_status      TEXT NOT NULL DEFAULT 'pending',  -- pending | fetched | failed | duplicate
    content_attempts    INTEGER NOT NULL DEFAULT 0,
    content_error       TEXT,
    content_fetched_at  TEXT,
    content_extra_json  TEXT,
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    metadata_changed_at TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS ix_source_documents_canonical ON source_documents (canonical_id);
CREATE INDEX IF NOT EXISTS ix_source_documents_queue ON source_documents (source, content_status, published);

CREATE TABLE IF NOT EXISTS texts (
    source       TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    language     TEXT NOT NULL,
    origin       TEXT NOT NULL,
    rights       TEXT NOT NULL,
    blob_sha256  TEXT NOT NULL,
    chars        INTEGER NOT NULL,
    fetched_at   TEXT NOT NULL,
    PRIMARY KEY (source, source_id, language, origin)
);

CREATE TABLE IF NOT EXISTS snapshots (
    source       TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    media_type   TEXT NOT NULL,
    blob_sha256  TEXT NOT NULL,
    fetched_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snapshots_doc ON snapshots (source, source_id);

CREATE TABLE IF NOT EXISTS discovery_state (
    source       TEXT NOT NULL,
    agency       TEXT NOT NULL,
    last_until   TEXT NOT NULL,
    last_run_at  TEXT NOT NULL,
    PRIMARY KEY (source, agency)
);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    command      TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT,
    report_json  TEXT
);
"""

# Version 2: deduplication, change detection, standing checks, alert state.
_V2_COLUMNS = {
    "source_documents": {
        "duplicate_of": "TEXT",          # "source:source_id" of the record this one duplicates
        "dedup_reason": "TEXT",          # why it was merged, or why it was NOT (id collision)
        "missing_since": "TEXT",         # set when a full discovery no longer lists it
        "refetch_reason": "TEXT",        # why content was queued again
        "status_fingerprint": "TEXT",    # standing at the source (what amends/replaces it)
        "status_edges_json": "TEXT",
        "status_summary": "TEXT",
        "status_checked_at": "TEXT",
        "status_priority": "INTEGER NOT NULL DEFAULT 0",
    },
    "texts": {
        "assessment": "TEXT",            # instrument | page | unknown
        "complete": "INTEGER",           # 1, 0, or NULL when the source does not say
    },
    "discovery_state": {
        "last_full_at": "TEXT",
        "fill_rates_json": "TEXT",       # field fill rates at the last full discovery
    },
}
_V2_TABLES = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            TEXT NOT NULL,
    run_id        INTEGER,
    kind          TEXT NOT NULL,
    severity      TEXT NOT NULL,
    agency        TEXT,
    source        TEXT,
    source_id     TEXT,
    canonical_id  TEXT,
    message       TEXT NOT NULL,
    details_json  TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_at ON events (at);
CREATE INDEX IF NOT EXISTS ix_events_run ON events (run_id);

CREATE TABLE IF NOT EXISTS alert_state (
    fingerprint       TEXT PRIMARY KEY,
    code              TEXT NOT NULL,
    scope             TEXT NOT NULL,
    severity          TEXT NOT NULL,
    message           TEXT NOT NULL,
    details_json      TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    last_notified_at  TEXT,
    resolved_at       TEXT,
    notify_count      INTEGER NOT NULL DEFAULT 0
);
"""

Outcome = Literal["new", "changed", "unchanged"]


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def doc_key(source: str, source_id: str) -> str:
    return f"{source}:{source_id}"


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256

    def put(self, data: bytes) -> str:
        sha256 = hashlib.sha256(data).hexdigest()
        target = self.path(sha256)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.replace(tmp, target)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        return sha256

    def get(self, sha256: str) -> bytes:
        return self.path(sha256).read_bytes()


@dataclass(frozen=True)
class TextRow:
    source: str
    source_id: str
    language: str
    origin: Origin
    rights: Rights
    blob_sha256: str
    chars: int
    fetched_at: str
    assessment: str | None = None
    complete: bool | None = None


@dataclass
class UpsertResult:
    outcome: Outcome
    changed_fields: list[str] = field(default_factory=list)
    reappeared: bool = False
    refetch_queued: bool = False


@dataclass
class SaveResult:
    first_fetch: bool
    changed_texts: list[dict[str, Any]] = field(default_factory=list)  # text replaced by a different one
    id_change: tuple[str | None, str] | None = None  # (old, new) when the text gave a different id


def _metadata_diff(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.blobs = BlobStore(data_dir / "raw")
        db_path = data_dir / "db" / "regingest.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA_V1)
        self._migrate()

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        with self.db:
            for table, columns in _V2_COLUMNS.items():
                existing = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
                for name, decl in columns.items():
                    if name not in existing:
                        self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            self.db.executescript(_V2_TABLES)
            self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self.db.close()

    def row(self, source: str, source_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM source_documents WHERE source = ? AND source_id = ?", (source, source_id)
        ).fetchone()

    # --- discovery -------------------------------------------------------

    def upsert_discovered(
        self, doc: DiscoveredDocument, resolution: IdResolution, *, content_fields: Iterable[str] = ()
    ) -> UpsertResult:
        now = utcnow()
        meta = canonical_json(doc.metadata)
        cid = str(resolution.id) if resolution.id else None
        with self.db:
            row = self.row(doc.source, doc.source_id)
            if row is None:
                self.db.execute(
                    "INSERT INTO source_documents (source, source_id, agency, title, published, source_url, "
                    "official_url, metadata_json, canonical_id, id_confidence, id_evidence, id_origin, "
                    "first_seen_at, last_seen_at, metadata_changed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'metadata', ?, ?, ?)",
                    (doc.source, doc.source_id, doc.agency, doc.title, doc.published, doc.source_url,
                     doc.official_url, meta, cid, resolution.confidence, resolution.evidence, now, now, now),
                )
                return UpsertResult("new")

            result = UpsertResult("unchanged")
            top = {"agency": doc.agency, "title": doc.title, "published": doc.published,
                   "source_url": doc.source_url, "official_url": doc.official_url}
            changed = [k for k, v in top.items() if row[k] != v]
            if row["metadata_json"] != meta:
                changed += [f"metadata.{k}" for k in _metadata_diff(json.loads(row["metadata_json"]), doc.metadata)]
            self.db.execute(
                "UPDATE source_documents SET last_seen_at = ?, missing_since = NULL WHERE source = ? AND source_id = ?",
                (now, doc.source, doc.source_id),
            )
            result.reappeared = row["missing_since"] is not None
            if changed:
                result.outcome, result.changed_fields = "changed", changed
                self.db.execute(
                    "UPDATE source_documents SET agency = ?, title = ?, published = ?, source_url = ?, "
                    "official_url = ?, metadata_json = ?, metadata_changed_at = ? "
                    "WHERE source = ? AND source_id = ?",
                    (doc.agency, doc.title, doc.published, doc.source_url, doc.official_url, meta, now,
                     doc.source, doc.source_id),
                )
                relevant = sorted({f"metadata.{f}" for f in content_fields} & set(changed))
                if relevant and row["content_status"] in ("fetched", "duplicate"):
                    self.db.execute(
                        "UPDATE source_documents SET content_status = 'pending', content_attempts = 0, "
                        "refetch_reason = ? WHERE source = ? AND source_id = ?",
                        (f"metadata changed: {', '.join(relevant)}", doc.source, doc.source_id),
                    )
                    result.refetch_queued = True
            # An id taken from the document's own text outranks one derived from metadata.
            if row["id_origin"] != "text":
                self.db.execute(
                    "UPDATE source_documents SET canonical_id = ?, id_confidence = ?, id_evidence = ?, "
                    "id_origin = 'metadata' WHERE source = ? AND source_id = ?",
                    (cid, resolution.confidence, resolution.evidence, doc.source, doc.source_id),
                )
            return result

    def mark_missing(self, source: str, agency: str, seen_ids: Iterable[str]) -> list[sqlite3.Row]:
        """After a full discovery: flag records the source no longer lists. Returns the newly missing."""
        now = utcnow()
        with self.db:
            self.db.execute("CREATE TEMP TABLE IF NOT EXISTS _seen (source_id TEXT PRIMARY KEY)")
            self.db.execute("DELETE FROM _seen")
            self.db.executemany("INSERT OR IGNORE INTO _seen VALUES (?)", ((s,) for s in seen_ids))
            newly = self.db.execute(
                "SELECT * FROM source_documents WHERE source = ? AND agency = ? AND missing_since IS NULL "
                "AND source_id NOT IN (SELECT source_id FROM _seen)", (source, agency),
            ).fetchall()
            self.db.executemany(
                "UPDATE source_documents SET missing_since = ? WHERE source = ? AND source_id = ?",
                ((now, r["source"], r["source_id"]) for r in newly),
            )
            self.db.execute("DELETE FROM _seen")
        return newly

    def discovery_state(self, source: str, agency: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM discovery_state WHERE source = ? AND agency = ?", (source, agency)
        ).fetchone()
        if row is None:
            return None
        return {
            "last_until": date.fromisoformat(row["last_until"]),
            "last_full_at": row["last_full_at"],
            "fill_rates": json.loads(row["fill_rates_json"]) if row["fill_rates_json"] else None,
        }

    def set_discovery_state(
        self, source: str, agency: str, until: date, *, full: bool, fill_rates: Mapping[str, float] | None
    ) -> None:
        now = utcnow()
        with self.db:
            self.db.execute(
                "INSERT INTO discovery_state (source, agency, last_until, last_run_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (source, agency) DO UPDATE SET last_until = excluded.last_until, "
                "last_run_at = excluded.last_run_at",
                (source, agency, until.isoformat(), now),
            )
            if full:
                self.db.execute(
                    "UPDATE discovery_state SET last_full_at = ?, fill_rates_json = ? WHERE source = ? AND agency = ?",
                    (now, canonical_json(dict(fill_rates or {})), source, agency),
                )

    def count_documents(self, source: str, agency: str | None = None, *, present_only: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM source_documents WHERE source = ?"
        args: list[Any] = [source]
        if agency:
            sql += " AND agency = ?"
            args.append(agency)
        if present_only:
            sql += " AND missing_since IS NULL"
        return self.db.execute(sql, args).fetchone()[0]

    # --- content queue ---------------------------------------------------

    def pending_content(self, source: str) -> list[DiscoveredDocument]:
        """Documents needing content: never-fetched first (newest first), then re-reads."""
        rows = self.db.execute(
            "SELECT * FROM source_documents WHERE source = ? AND content_status = 'pending' "
            "AND missing_since IS NULL "
            "ORDER BY content_fetched_at IS NOT NULL, published IS NULL, published DESC, source_id",
            (source,),
        ).fetchall()
        return [self.to_document(r) for r in rows]

    def count_pending(self, source: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM source_documents WHERE source = ? AND content_status = 'pending' "
            "AND missing_since IS NULL", (source,),
        ).fetchone()[0]

    def latest_snapshots(self, source: str, source_id: str) -> list[tuple[str, bytes]]:
        """The raw payloads of the most recent fetch, as ``(kind, bytes)``."""
        rows = self.db.execute(
            "SELECT kind, blob_sha256 FROM snapshots WHERE source = ? AND source_id = ? AND fetched_at = "
            "(SELECT MAX(fetched_at) FROM snapshots WHERE source = ? AND source_id = ?) ORDER BY rowid",
            (source, source_id, source, source_id),
        ).fetchall()
        return [(r["kind"], self.blobs.get(r["blob_sha256"])) for r in rows]

    def save_content(
        self,
        doc: DiscoveredDocument,
        content: DocumentContent,
        text_resolution: IdResolution | None,
        assessments: Mapping[tuple[str, Origin], str] | None = None,
        *,
        rebuilt: bool = False,  # re-assembled from stored snapshots: not a new fetch
    ) -> SaveResult:
        now = utcnow()
        assessments = assessments or {}
        snapshot_rows = [(s, self.blobs.put(s.content)) for s in content.snapshots]
        text_rows = [(t, self.blobs.put(t.content.encode("utf-8"))) for t in content.texts]
        with self.db:
            row = self.row(doc.source, doc.source_id)
            result = SaveResult(first_fetch=row is None or row["content_fetched_at"] is None)
            previous = {(t.language, t.origin): t for t in self.texts_for(doc.source, doc.source_id)}
            for snapshot, sha in snapshot_rows:
                self.db.execute(
                    "INSERT INTO snapshots (source, source_id, kind, media_type, blob_sha256, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (doc.source, doc.source_id, snapshot.kind, snapshot.media_type, sha, now),
                )
            for text, sha in text_rows:
                old = previous.get((text.language, text.origin))
                if old is not None and old.blob_sha256 != sha:
                    result.changed_texts.append({
                        "language": text.language, "origin": text.origin.value,
                        "old_sha256": old.blob_sha256, "new_sha256": sha,
                        "old_chars": old.chars, "new_chars": len(text.content),
                    })
                self.db.execute(
                    "INSERT INTO texts (source, source_id, language, origin, rights, blob_sha256, chars, fetched_at, "
                    "assessment, complete) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (source, source_id, language, origin) DO UPDATE SET rights = excluded.rights, "
                    "blob_sha256 = excluded.blob_sha256, chars = excluded.chars, fetched_at = excluded.fetched_at, "
                    "assessment = excluded.assessment, complete = excluded.complete",
                    (doc.source, doc.source_id, text.language, text.origin.value, text.rights.value, sha,
                     len(text.content), now, assessments.get((text.language, text.origin)),
                     None if text.complete is None else int(text.complete)),
                )
            if rebuilt:
                self.db.execute(
                    "UPDATE source_documents SET content_extra_json = ? WHERE source = ? AND source_id = ?",
                    (canonical_json(content.extra), doc.source, doc.source_id),
                )
            else:
                self.db.execute(
                    "UPDATE source_documents SET content_status = 'fetched', content_fetched_at = ?, "
                    "content_error = NULL, content_extra_json = ?, content_attempts = content_attempts + 1, "
                    "refetch_reason = NULL WHERE source = ? AND source_id = ?",
                    (now, canonical_json(content.extra), doc.source, doc.source_id),
                )
            if text_resolution is not None and text_resolution.id is not None:
                new_id = str(text_resolution.id)
                if row is not None and row["canonical_id"] != new_id:
                    result.id_change = (row["canonical_id"], new_id)
                self.db.execute(
                    "UPDATE source_documents SET canonical_id = ?, id_confidence = ?, id_evidence = ?, "
                    "id_origin = 'text' WHERE source = ? AND source_id = ?",
                    (new_id, text_resolution.confidence, text_resolution.evidence, doc.source, doc.source_id),
                )
        return result

    def set_identity(self, source: str, source_id: str, resolution: IdResolution, origin: str) -> str | None:
        """Replace a record's canonical id (used when re-parsing). Returns the previous id."""
        with self.db:
            row = self.row(source, source_id)
            self.db.execute(
                "UPDATE source_documents SET canonical_id = ?, id_confidence = ?, id_evidence = ?, id_origin = ? "
                "WHERE source = ? AND source_id = ?",
                (str(resolution.id) if resolution.id else None, resolution.confidence, resolution.evidence, origin,
                 source, source_id),
            )
        return row["canonical_id"] if row else None

    def set_assessment(self, source: str, source_id: str, language: str, origin: Origin, assessment: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE texts SET assessment = ? WHERE source = ? AND source_id = ? AND language = ? AND origin = ?",
                (assessment, source, source_id, language, origin.value),
            )

    def mark_duplicate(self, doc: DiscoveredDocument, duplicate_of: str, reason: str) -> None:
        """Another record is the same document and has content; don't spend a read on this one."""
        with self.db:
            self.db.execute(
                "UPDATE source_documents SET content_status = 'duplicate', duplicate_of = ?, dedup_reason = ? "
                "WHERE source = ? AND source_id = ?",
                (duplicate_of, reason, doc.source, doc.source_id),
            )

    def mark_content_failed(self, doc: DiscoveredDocument, error: str, *, permanent: bool, max_attempts: int) -> None:
        with self.db:
            self.db.execute(
                "UPDATE source_documents SET content_attempts = content_attempts + 1, content_error = ?, "
                "content_status = CASE WHEN ? OR content_attempts + 1 >= ? THEN 'failed' ELSE 'pending' END "
                "WHERE source = ? AND source_id = ?",
                (error[:2000], permanent, max_attempts, doc.source, doc.source_id),
            )

    # --- deduplication -----------------------------------------------------

    def set_dedup(self, source: str, source_id: str, duplicate_of: str | None, reason: str | None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE source_documents SET duplicate_of = ?, dedup_reason = ?, "
                "content_status = CASE "
                "  WHEN ? IS NULL AND content_status = 'duplicate' THEN 'pending' "   # decision undone: read it
                "  WHEN ? IS NOT NULL AND content_status = 'pending' THEN 'duplicate' "  # no read needed
                "  ELSE content_status END "
                "WHERE source = ? AND source_id = ?",
                (duplicate_of, reason, duplicate_of, duplicate_of, source, source_id),
            )

    # --- standing checks ---------------------------------------------------

    def status_check_queue(self, source: str, limit: int) -> list[DiscoveredDocument]:
        """Prioritized first, then never checked, then least recently checked."""
        rows = self.db.execute(
            "SELECT * FROM source_documents WHERE source = ? AND missing_since IS NULL "
            "AND content_status != 'duplicate' "
            "ORDER BY status_priority DESC, status_checked_at IS NOT NULL, status_checked_at, published DESC "
            "LIMIT ?", (source, limit),
        ).fetchall()
        return [self.to_document(r) for r in rows]

    def prioritize_status_checks(self, source: str, source_ids: Iterable[str]) -> int:
        with self.db:
            cur = self.db.executemany(
                "UPDATE source_documents SET status_priority = 1 WHERE source = ? AND source_id = ?",
                ((source, s) for s in source_ids),
            )
        return cur.rowcount

    def save_status(self, doc: DiscoveredDocument, check: StatusCheck) -> tuple[str | None, list[dict[str, Any]]]:
        """Store a standing check; returns the previous fingerprint and edges."""
        with self.db:
            row = self.row(doc.source, doc.source_id)
            previous = (row["status_fingerprint"], json.loads(row["status_edges_json"] or "[]")) if row else (None, [])
            self.db.execute(
                "UPDATE source_documents SET status_fingerprint = ?, status_edges_json = ?, status_summary = ?, "
                "status_checked_at = ?, status_priority = 0 WHERE source = ? AND source_id = ?",
                (check.fingerprint, canonical_json(check.edges), check.summary, utcnow(), doc.source, doc.source_id),
            )
        return previous

    def canonical_ids(self, source: str, source_ids: Iterable[str]) -> dict[str, str | None]:
        ids = list(source_ids)
        if not ids:
            return {}
        rows = self.db.execute(
            f"SELECT source_id, canonical_id FROM source_documents WHERE source = ? "
            f"AND source_id IN ({','.join('?' * len(ids))})", (source, *ids),
        ).fetchall()
        return {r["source_id"]: r["canonical_id"] for r in rows}

    # --- events ------------------------------------------------------------

    def record_event(
        self, kind: str, severity: str, message: str, *, run_id: int | None = None,
        row: Mapping[str, Any] | DiscoveredDocument | None = None, details: Mapping[str, Any] | None = None,
    ) -> int:
        agency = source = source_id = canonical_id = None
        if isinstance(row, DiscoveredDocument):
            agency, source, source_id = row.agency, row.source, row.source_id
            stored = self.row(row.source, row.source_id)
            canonical_id = stored["canonical_id"] if stored else None
        elif row is not None:
            agency, source, source_id, canonical_id = row["agency"], row["source"], row["source_id"], row["canonical_id"]
        with self.db:
            cur = self.db.execute(
                "INSERT INTO events (at, run_id, kind, severity, agency, source, source_id, canonical_id, message, "
                "details_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (utcnow(), run_id, kind, severity, agency, source, source_id, canonical_id, message,
                 canonical_json(dict(details)) if details else None),
            )
        return int(cur.lastrowid)

    def events(
        self, *, run_id: int | None = None, since: str | None = None, kinds: Iterable[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM events WHERE 1 = 1", []
        if run_id is not None:
            sql += " AND run_id = ?"
            args.append(run_id)
        if since:
            sql += " AND at >= ?"
            args.append(since)
        kinds = list(kinds or ())
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        out = []
        for r in self.db.execute(sql, args):
            item = dict(r)
            item["details"] = json.loads(item.pop("details_json") or "null")
            out.append(item)
        return out

    def event_counts(self, run_id: int) -> dict[str, int]:
        return {r["kind"]: r["n"] for r in self.db.execute(
            "SELECT kind, COUNT(*) AS n FROM events WHERE run_id = ? GROUP BY kind", (run_id,)
        )}

    # --- alert state -------------------------------------------------------

    def alert_state(self, fingerprint: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM alert_state WHERE fingerprint = ?", (fingerprint,)).fetchone()

    def open_alerts(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM alert_state WHERE resolved_at IS NULL ORDER BY first_seen_at").fetchall()

    def save_alert_state(self, values: Mapping[str, Any]) -> None:
        columns = ", ".join(values)
        updates = ", ".join(f"{k} = excluded.{k}" for k in values if k != "fingerprint")
        with self.db:
            self.db.execute(
                f"INSERT INTO alert_state ({columns}) VALUES ({', '.join('?' * len(values))}) "
                f"ON CONFLICT (fingerprint) DO UPDATE SET {updates}",
                tuple(values.values()),
            )

    # --- reading -----------------------------------------------------------

    def iter_documents(self) -> Iterator[sqlite3.Row]:
        yield from self.db.execute("SELECT * FROM source_documents ORDER BY agency, canonical_id, source, source_id")

    def texts_for(self, source: str, source_id: str) -> list[TextRow]:
        rows = self.db.execute(
            "SELECT * FROM texts WHERE source = ? AND source_id = ? ORDER BY language, origin", (source, source_id)
        ).fetchall()
        return [
            TextRow(r["source"], r["source_id"], r["language"], Origin(r["origin"]), Rights(r["rights"]),
                    r["blob_sha256"], r["chars"], r["fetched_at"], r["assessment"],
                    None if r["complete"] is None else bool(r["complete"]))
            for r in rows
        ]

    def set_rights(self, source: str, origin: Origin, rights: Rights) -> int:
        with self.db:
            cur = self.db.execute(
                "UPDATE texts SET rights = ? WHERE source = ? AND origin = ?", (rights.value, source, origin.value)
            )
        return cur.rowcount

    def stats(self) -> dict[str, Any]:
        def grouped(sql: str) -> list[dict[str, Any]]:
            return [dict(r) for r in self.db.execute(sql).fetchall()]

        return {
            "documents": grouped(
                "SELECT agency, source, content_status, COUNT(*) AS n FROM source_documents "
                "GROUP BY agency, source, content_status ORDER BY agency, source, content_status"
            ),
            "identity": grouped(
                "SELECT agency, id_confidence, id_origin, COUNT(*) AS n FROM source_documents "
                "GROUP BY agency, id_confidence, id_origin ORDER BY agency, id_confidence"
            ),
            "texts": grouped(
                "SELECT source, language, origin, rights, assessment, COUNT(*) AS n, SUM(chars) AS chars FROM texts "
                "GROUP BY source, language, origin, rights, assessment"
            ),
            "missing_from_source": self.db.execute(
                "SELECT COUNT(*) FROM source_documents WHERE missing_since IS NOT NULL").fetchone()[0],
            "status_checked": self.db.execute(
                "SELECT COUNT(*) FROM source_documents WHERE status_checked_at IS NOT NULL").fetchone()[0],
        }

    def review_items(self) -> dict[str, list[dict[str, Any]]]:
        """Records a person should look at."""
        def rows(sql: str) -> list[dict[str, Any]]:
            return [dict(r) for r in self.db.execute(sql).fetchall()]

        base = "SELECT agency, source, source_id, canonical_id, title, official_url"
        return {
            "id_unresolved": rows(f"{base}, id_confidence, id_evidence FROM source_documents "
                                  "WHERE id_confidence IN ('conflict', 'none') ORDER BY agency, published DESC"),
            "id_collisions": rows(f"{base}, dedup_reason FROM source_documents "
                                  "WHERE dedup_reason LIKE 'id collision%' ORDER BY canonical_id"),
            "content_failed": rows(f"{base}, content_attempts, content_error FROM source_documents "
                                   "WHERE content_status = 'failed'"),
            "missing_from_source": rows(f"{base}, missing_since FROM source_documents WHERE missing_since IS NOT NULL"),
            "text_not_instrument": rows(
                "SELECT d.agency, d.source, d.source_id, d.canonical_id, d.title, d.official_url, t.language, "
                "t.assessment FROM texts t JOIN source_documents d USING (source, source_id) "
                "WHERE t.origin != 'source_translation' AND t.assessment IN ('page', 'unknown')"),
            "text_incomplete": rows(
                "SELECT d.agency, d.source, d.source_id, d.canonical_id, d.title, t.language, t.origin, t.chars "
                "FROM texts t JOIN source_documents d USING (source, source_id) WHERE t.complete = 0"),
        }

    # --- runs --------------------------------------------------------------

    def start_run(self, command: str) -> int:
        with self.db:
            cur = self.db.execute("INSERT INTO runs (command, started_at) VALUES (?, ?)", (command, utcnow()))
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, report: dict[str, Any]) -> None:
        with self.db:
            self.db.execute(
                "UPDATE runs SET finished_at = ?, status = ?, report_json = ? WHERE id = ?",
                (utcnow(), status, canonical_json(report), run_id),
            )

    def last_run(self, *, statuses: Iterable[str] | None = None, before_id: int | None = None) -> dict[str, Any] | None:
        sql, args = "SELECT * FROM runs WHERE finished_at IS NOT NULL", []
        statuses = list(statuses or ())
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            args += statuses
        if before_id is not None:
            sql += " AND id < ?"
            args.append(before_id)
        row = self.db.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["report"] = json.loads(item.pop("report_json") or "null")
        return item

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def to_document(row: sqlite3.Row) -> DiscoveredDocument:
        return DiscoveredDocument(
            source=row["source"],
            source_id=row["source_id"],
            agency=row["agency"],
            title=row["title"] or "",
            published=row["published"],
            source_url=row["source_url"],
            official_url=row["official_url"],
            metadata=json.loads(row["metadata_json"]),
        )
