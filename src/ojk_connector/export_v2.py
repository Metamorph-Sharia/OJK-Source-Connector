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

from ojk_connector.contract_v2 import CONNECTOR, HeldDocument, HeldText, normalized_document
from ojk_connector.export import SelectedDocument, choose_texts, select_documents
from ojk_connector.store import Store, doc_key


@dataclass
class BundleStats:
    documents: int = 0
    unresolved: int = 0
    texts: int = 0
    citations: int = 0
    citations_with_target: int = 0
    without_content: int = 0  # no exportable text yet: left out, the contract requires content

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
            bundle.add(normalized)
            _count(stats, normalized)
    return stats


def held_document(store: Store, selected: SelectedDocument) -> HeldDocument:
    primary = selected.primary
    texts, _withheld = choose_texts(store, selected.members)
    extra = json.loads(primary["content_extra_json"]) if primary["content_extra_json"] else {}
    metadata = json.loads(primary["metadata_json"])
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
        texts=tuple(
            HeldText(
                language=text.language,
                origin=TextOrigin(text.origin.value),
                rights=Rights(text.rights.value),
                content=store.blobs.get(text.blob_sha256).decode("utf-8"),
            )
            for _language, text in sorted(texts.items())
        ),
        source_fields=metadata,
    )


def _canonical_ids_by_source_id(selected: list[SelectedDocument]) -> dict[str, str]:
    """A lineage edge names a document by its source ID; duplicates point to their primary's canonical ID."""
    return {
        member["source_id"]: document.canonical_id
        for document in selected
        if document.canonical_id
        for member in document.members
    }


def _count(stats: BundleStats, document: NormalizedDocument) -> None:
    stats.documents += 1
    stats.unresolved += is_unresolved(document.document.id)
    stats.texts += len(document.texts)
    stats.citations += len(document.citations)
    stats.citations_with_target += sum(c.target_id is not None for c in document.citations)

