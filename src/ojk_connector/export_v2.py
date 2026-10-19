"""`ojk-connector export --format v2`: the store as one contract v2 bundle (a full snapshot).

Uses the same document selection and text choice as the corpus export (export.py), so the two
never disagree about which documents exist or which text is authoritative.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path

from metamorph_contract import BundleWriter, NormalizedDocument, Rights, TextOrigin, is_unresolved

from ojk_connector.contract_v2 import CONNECTOR, HeldDocument, HeldEntry, HeldFile, HeldText, normalized_document
from ojk_connector.export import SelectedDocument, choose_texts, select_documents
from ojk_connector.models import Origin
from ojk_connector.store import Store, TextRow, doc_key

FILE_KIND = "file:"  # content snapshots holding an official file: "file:{role}:{name}"


@dataclass
class BundleStats:
    documents: int = 0
    unresolved: int = 0
    texts: int = 0
    citations: int = 0
    citations_with_target: int = 0
    without_content: int = 0  # no exportable text or file yet: left out, the contract requires content
    files: int = 0
    with_dates: int = 0
    status_from_entry: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def export_bundle(store: Store, out_dir: Path, *, run_id: str) -> BundleStats:
    selected = select_documents(store)
    targets = _canonical_ids_by_source_id(selected)
    connector_version = version("ojk-connector")
    stats = BundleStats()
    with BundleWriter(out_dir, connector=CONNECTOR, connector_version=connector_version, run_id=run_id) as bundle:
        for document in selected:
            held = held_document(store, document)
            normalized = normalized_document(held, connector_version=connector_version, target_of=targets.get)
            if normalized is None:
                stats.without_content += 1
                continue
            for text in held.texts:
                bundle.put_text(text.content)
            for file in held.files:
                bundle.put_blob(store.blobs.get(file.sha256))
            bundle.add(normalized)
            _count(stats, normalized, held)
    return stats


def held_document(store: Store, selected: SelectedDocument) -> HeldDocument:
    primary = selected.primary
    texts, _withheld = choose_texts(store, selected.members)
    extra = json.loads(primary["content_extra_json"]) if primary["content_extra_json"] else {}
    metadata = json.loads(primary["metadata_json"])
    files = _held_files(store, selected.members)
    return HeldDocument(
        source_id=doc_key(primary["source"], primary["source_id"]),
        source_url=primary["source_url"] or primary["official_url"],
        fetched_at=primary["content_fetched_at"] or primary["last_seen_at"],
        canonical_id=selected.canonical_id,
        id_confidence=primary["id_confidence"],
        id_evidence=primary["id_evidence"] or "",
        review_reason=selected.review_reason,
        title=primary["title"],
        identifier=metadata.get("identifier") or None,
        standing=primary["status_summary"],
        standing_checked_at=primary["status_checked_at"],
        lineage=extra.get("lineage") or {},
        texts=tuple(_held_text(store, text, files) for _language, text in sorted(texts.items())),
        source_fields=metadata,
        entry=_held_entry(selected),
        files=tuple(file for file, _record in files),
    )


def _held_text(store: Store, text: TextRow, files: list[tuple[HeldFile, str]]) -> HeldText:
    """A text we extracted ourselves is paged, and comes from its record's main file."""
    record = doc_key(text.source, text.source_id)
    extracted = text.origin == Origin.SELF_EXTRACTED
    main = next((file.sha256 for file, owner in files if owner == record and file.role == "main"), None)
    return HeldText(
        language=text.language,
        origin=TextOrigin(text.origin.value),
        rights=Rights(text.rights.value),
        content=store.blobs.get(text.blob_sha256).decode("utf-8"),
        paged=extracted,
        of_file=main if extracted else None,
    )


def _held_files(store: Store, members: list) -> list[tuple[HeldFile, str]]:
    """Every official file the document's records hold, with the record that holds it. The same bytes once."""
    held: dict[str, tuple[HeldFile, str]] = {}
    for member in members:
        extra = json.loads(member["content_extra_json"]) if member["content_extra_json"] else {}
        described = {f"{FILE_KIND}{f['role']}:{f['name']}": f for f in extra.get("files") or []}
        for kind, sha, media_type in store.content_files(member["source"], member["source_id"], FILE_KIND):
            about = described.get(kind, {})
            _, role, name = kind.split(":", 2)
            held.setdefault(sha, (HeldFile(sha, role, media_type, about.get("url"), name, about.get("pages")),
                                  doc_key(member["source"], member["source_id"])))
    return list(held.values())


def _held_entry(selected: SelectedDocument) -> HeldEntry | None:
    """The most recently read catalogue entry among the document's records."""
    readers = [m for m in selected.members if m["entry_read_at"]]
    if not readers:
        return None
    record = max(readers, key=lambda m: m["entry_read_at"])
    return HeldEntry(Store.entry_of(record), record["source"], record["source_id"], record["source_url"],
                     record["entry_read_at"])


def _canonical_ids_by_source_id(selected: list[SelectedDocument]) -> dict[str, str]:
    """A lineage edge names a document by its source ID; duplicates point to their primary's canonical ID."""
    return {
        member["source_id"]: document.canonical_id
        for document in selected
        if document.canonical_id
        for member in document.members
    }


def _count(stats: BundleStats, document: NormalizedDocument, held: HeldDocument) -> None:
    stats.documents += 1
    stats.unresolved += is_unresolved(document.document.id)
    stats.texts += len(document.texts)
    stats.files += len(document.files)
    stats.with_dates += any((document.document.dates.enacted, document.document.dates.promulgated,
                             document.document.dates.effective))
    stats.status_from_entry += held.entry is not None and held.entry.entry.standing is not None
    stats.citations += len(document.citations)
    stats.citations_with_target += sum(c.target_id is not None for c in document.citations)

