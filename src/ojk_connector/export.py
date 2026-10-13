"""Rebuild ``data/corpus/``: the self-contained hand-off for the knowledge database.

* One folder per document. Records the dedup stage marked as duplicates are merged
  into their primary record's folder (all sources are listed in ``metadata.json``).
* Records without a confident id, id collisions, and records whose id path is already
  taken go to ``documents/ojk/_unresolved/…`` with a ``review_reason``.
* Only texts whose rights allow it are written.

The corpus is rebuilt into a temporary directory and swapped in, so readers never see
a half-written one.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ojk_connector.identity import AGENCY, PRIMARY_LANGUAGE, CanonicalId
from ojk_connector.models import EXPORTABLE_RIGHTS, Origin
from ojk_connector.store import Store, TextRow, doc_key, utcnow

#: Which text wins when several exist for one language (lower is better).
ORIGIN_PRIORITY = {Origin.SELF_EXTRACTED: 0, Origin.SOURCE_TEXT: 1, Origin.SOURCE_TRANSLATION: 2}
ASSESSMENT_PRIORITY = {"instrument": 0, None: 1, "unknown": 2, "page": 3}
#: Ids trusted enough to file a document under its canonical path.
RESOLVED_CONFIDENCE = frozenset({"high", "medium", "low"})
_CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class ExportStats:
    documents: int = 0
    unresolved: int = 0
    texts_written: int = 0
    texts_withheld: int = 0  # stored, but their rights keep them out of the corpus
    without_primary_text: int = 0
    primary_text_not_instrument: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _source_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "source": row["source"],
        "source_id": row["source_id"],
        "title": row["title"],
        "published": row["published"],
        "source_url": row["source_url"],
        "official_url": row["official_url"],
        "content_status": row["content_status"],
        "id_confidence": row["id_confidence"],
        "id_evidence": row["id_evidence"],
        "duplicate_of": row["duplicate_of"],
        "dedup_reason": row["dedup_reason"],
        "missing_since": row["missing_since"],
        "metadata": json.loads(row["metadata_json"]),
        "extra": json.loads(row["content_extra_json"]) if row["content_extra_json"] else None,
    }


def _review_reason(row: sqlite3.Row) -> str | None:
    if not row["canonical_id"] or row["id_confidence"] not in RESOLVED_CONFIDENCE:
        return f"no confident canonical id ({row['id_confidence']}): {row['id_evidence']}"
    if row["dedup_reason"] and row["dedup_reason"].startswith("id collision"):
        return row["dedup_reason"]
    return None


def _id_path(canonical_id: str) -> Path:
    cid = CanonicalId.parse(canonical_id)
    return Path("documents", AGENCY, cid.family, str(cid.year), str(cid.number))


def _unresolved_path(row: sqlite3.Row) -> Path:
    return Path("documents", AGENCY, "_unresolved", row["source"], row["source_id"])


def _text_order(text: TextRow) -> tuple[int, int]:
    return ORIGIN_PRIORITY[text.origin], ASSESSMENT_PRIORITY.get(text.assessment, 1)


def export_corpus(store: Store, corpus_dir: Path) -> ExportStats:
    rows = list(store.iter_documents())
    merged_into: dict[str, list[sqlite3.Row]] = defaultdict(list)
    primaries = []
    for row in rows:
        if row["duplicate_of"]:
            merged_into[row["duplicate_of"]].append(row)
        else:
            primaries.append(row)
    # Best-identified records claim their canonical path first.
    primaries.sort(key=lambda r: (r["id_origin"] != "text", _CONFIDENCE_ORDER.get(r["id_confidence"], 9),
                                  r["content_fetched_at"] is None, r["first_seen_at"], r["source_id"]))

    stats = ExportStats()
    tmp = corpus_dir.parent / f".{corpus_dir.name}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "documents").mkdir(parents=True)
    exported_at = utcnow()
    claimed: dict[Path, str] = {}

    with (tmp / "regulations.jsonl").open("w", encoding="utf-8") as index:
        for primary in primaries:
            members = [primary, *merged_into.get(doc_key(primary["source"], primary["source_id"]), [])]
            reason = _review_reason(primary)
            path = None if reason else _id_path(primary["canonical_id"])
            if path is not None and path in claimed:
                reason = f"canonical path already used by {claimed[path]}"
                path = None
            if path is None:
                path = _unresolved_path(primary)
                stats.unresolved += 1
            else:
                claimed[path] = doc_key(primary["source"], primary["source_id"])
            stats.documents += 1

            chosen: dict[str, TextRow] = {}
            for member in members:
                for text in store.texts_for(member["source"], member["source_id"]):
                    if text.rights not in EXPORTABLE_RIGHTS:
                        stats.texts_withheld += 1
                        continue
                    current = chosen.get(text.language)
                    if current is None or _text_order(text) < _text_order(current):
                        chosen[text.language] = text

            target = tmp / path
            target.mkdir(parents=True, exist_ok=True)
            text_entries = []
            for language, text in sorted(chosen.items()):
                filename = f"text.{language}.md"
                (target / filename).write_bytes(store.blobs.get(text.blob_sha256))
                stats.texts_written += 1
                text_entries.append({
                    "file": filename, "language": language, "origin": text.origin.value,
                    "rights": text.rights.value, "assessment": text.assessment, "complete": text.complete,
                    "source": text.source, "source_id": text.source_id, "sha256": text.blob_sha256,
                    "chars": text.chars, "fetched_at": text.fetched_at,
                })
            primary_text = chosen.get(PRIMARY_LANGUAGE)
            if primary_text is None:
                stats.without_primary_text += 1
            elif primary_text is not None and primary_text.assessment != "instrument":
                stats.primary_text_not_instrument += 1

            resolved = reason is None
            cid = CanonicalId.parse(primary["canonical_id"]) if resolved else None
            record = {
                "id": primary["canonical_id"] if resolved else None,
                "agency": AGENCY,
                "family": cid.family if cid else None,
                "year": cid.year if cid else None,
                "number": cid.number if cid else None,
                "id_confidence": primary["id_confidence"],
                "id_evidence": primary["id_evidence"],
                "review_reason": reason,
                "title": primary["title"],
                "published": primary["published"],
                "official_url": primary["official_url"],
                "standing": {
                    "summary": primary["status_summary"],
                    "checked_at": primary["status_checked_at"],
                    "edges": json.loads(primary["status_edges_json"] or "[]"),
                } if primary["status_checked_at"] else None,
                "path": str(path),
                "texts": text_entries,
                "sources": [_source_entry(m) for m in members],
                "exported_at": exported_at,
            }
            (target / "metadata.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            summary = {k: record[k] for k in ("id", "agency", "family", "year", "number", "id_confidence",
                                              "review_reason", "title", "published", "official_url", "path")}
            summary["languages"] = sorted(chosen)
            summary["standing"] = primary["status_summary"]
            summary["sources"] = [doc_key(m["source"], m["source_id"]) for m in members]
            index.write(json.dumps(summary, ensure_ascii=False) + "\n")

    if corpus_dir.exists():
        shutil.rmtree(corpus_dir)
    os.replace(tmp, corpus_dir)
    return stats
