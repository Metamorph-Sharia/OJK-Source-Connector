"""Deduplication: which records are the same document?

Two records are the same document when they share a trusted canonical id (high or
medium confidence) AND one of: identical instrument text, largely matching titles, or
both texts' own headers name the instrument. While either text is missing the pair is
*undecided* (both get fetched, no alert). Once both texts exist and still disagree, it
is an *id collision*: one of the ids is probably wrong (a source error, or a citation
picked up from the body). Collisions are kept apart and listed for review.

Seen in RegAlert's OJK data: POJK 28/POJK.05/2014 and 24/POJK.04/2014 are listed twice
(true duplicates); a SEOJK 8/2023 record whose text is a summary page citing POJK
35/2017 once collided with POJK 35/2017 (an id error, not a duplicate).
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from regingest.agencies.base import TRUSTED_CONFIDENCE
from regingest.models import DiscoveredDocument, Origin
from regingest.store import Store, doc_key

DEFAULT_TITLE_OVERLAP = 0.8

# Words that say nothing about which document it is (English titles from sources, Indonesian titles).
STOPWORDS = frozenset("""
the and for with from into under over about regarding concerning amendment amendments amending
regulation regulations circular letter number financial services authority ojk pojk seojk padk
board commissioners commissioner member republic indonesia year implementation
peraturan otoritas jasa keuangan surat edaran nomor tahun tentang dan atas yang bagi dalam
anggota dewan komisioner perubahan republik
""".split())

_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2, "conflict": 3, "none": 4}


def title_tokens(title: str | None) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z]+", (title or "").lower()) if len(w) > 2 and w not in STOPWORDS)


def title_overlap(a: str | None, b: str | None) -> float:
    """Share of the shorter title's distinctive words that the other title also has."""
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _key(row: sqlite3.Row) -> str:
    return doc_key(row["source"], row["source_id"])


def _primary_order(row: sqlite3.Row) -> tuple[Any, ...]:
    return (
        row["content_fetched_at"] is None,
        row["id_origin"] != "text",
        _CONFIDENCE_RANK.get(row["id_confidence"], 9),
        row["first_seen_at"],
        row["source_id"],
    )


def _instrument_shas(store: Store, row: sqlite3.Row) -> set[str]:
    return {t.blob_sha256 for t in store.texts_for(row["source"], row["source_id"])
            if t.origin != Origin.SOURCE_TRANSLATION}


Verdict = str  # "duplicate" | "collision" | "undecided"


def compare(store: Store, a: sqlite3.Row, b: sqlite3.Row, threshold: float) -> tuple[Verdict, str]:
    """Are two records with the same canonical id the same document?"""
    shas_a, shas_b = _instrument_shas(store, a), _instrument_shas(store, b)
    if shas_a & shas_b:
        return "duplicate", "identical text"
    overlap = title_overlap(a["title"], b["title"])
    if overlap >= threshold:
        return "duplicate", f"title overlap {overlap:.2f}"
    if a["id_origin"] == "text" and b["id_origin"] == "text":
        return "duplicate", "both texts identify themselves as this instrument"
    if not shas_a or not shas_b:
        return "undecided", f"title overlap {overlap:.2f}; not comparable until both texts are fetched"
    return "collision", f"title overlap {overlap:.2f} and the texts differ"


def find_fetched_duplicate(store: Store, doc: DiscoveredDocument, threshold: float) -> tuple[str, str] | None:
    """Before spending a read: is this record the same document as one that already has content?"""
    row = store.row(doc.source, doc.source_id)
    if row is None or row["canonical_id"] is None or row["id_confidence"] not in TRUSTED_CONFIDENCE:
        return None
    others = store.db.execute(
        "SELECT * FROM source_documents WHERE canonical_id = ? AND content_status = 'fetched' "
        "AND id_confidence IN ('high', 'medium') AND NOT (source = ? AND source_id = ?)",
        (row["canonical_id"], doc.source, doc.source_id),
    ).fetchall()
    for other in sorted(others, key=_primary_order):
        overlap = title_overlap(row["title"], other["title"])
        if overlap >= threshold:
            return _key(other), f"same document as {_key(other)}: same id, title overlap {overlap:.2f}"
    return None


@dataclass
class DedupOutcome:
    groups: int = 0  # canonical ids held by more than one record
    duplicates: int = 0
    collisions: int = 0
    undecided: int = 0  # same id, but not comparable until both texts are fetched
    new_duplicates: list[dict[str, Any]] = field(default_factory=list)
    new_collisions: list[dict[str, Any]] = field(default_factory=list)
    same_text_different_ids: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "groups": self.groups,
            "duplicates": self.duplicates,
            "collisions": self.collisions,
            "undecided": self.undecided,
            "new_duplicates": self.new_duplicates,
            "new_collisions": self.new_collisions,
            "same_text_different_ids": self.same_text_different_ids,
        }


def resolve_duplicates(store: Store, threshold: float = DEFAULT_TITLE_OVERLAP) -> DedupOutcome:
    """Recompute every duplicate/collision decision from the current ids, titles and texts."""
    outcome = DedupOutcome()
    rows = list(store.iter_documents())
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        if row["canonical_id"] and row["id_confidence"] in TRUSTED_CONFIDENCE:
            groups[row["canonical_id"]].append(row)

    decisions: dict[str, tuple[str | None, str | None]] = {}
    for canonical_id, members in groups.items():
        if len(members) < 2:
            continue
        outcome.groups += 1
        members.sort(key=_primary_order)
        primary = members[0]
        for member in members[1:]:
            verdict, why = compare(store, primary, member, threshold)
            if verdict == "duplicate":
                decisions[_key(member)] = (_key(primary), f"same document as {_key(primary)}: same id, {why}")
                outcome.duplicates += 1
            elif verdict == "collision":
                decisions[_key(member)] = (None, f"id collision with {_key(primary)} on {canonical_id}: {why}")
                outcome.collisions += 1
            else:
                decisions[_key(member)] = (None, f"same id as {_key(primary)}; {why}")
                outcome.undecided += 1

    for row in rows:
        target = decisions.get(_key(row), (None, None))
        if (row["duplicate_of"], row["dedup_reason"]) == target:
            continue
        store.set_dedup(row["source"], row["source_id"], *target)
        entry = {"record": _key(row), "canonical_id": row["canonical_id"], "title": row["title"], "reason": target[1]}
        if target[0] is not None:
            outcome.new_duplicates.append(entry)
        elif target[1] is not None and target[1].startswith("id collision"):
            outcome.new_collisions.append(entry)

    # The same text under different ids means at least one id is wrong.
    by_text: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in store.db.execute(
        "SELECT t.blob_sha256, d.* FROM texts t JOIN source_documents d USING (source, source_id) "
        "WHERE t.origin != ?", (Origin.SOURCE_TRANSLATION.value,),
    ):
        by_text[r["blob_sha256"]].append(r)
    for sha, members in by_text.items():
        ids = {m["canonical_id"] for m in members}
        if len(ids) > 1:
            outcome.same_text_different_ids.append(
                {"sha256": sha, "records": [_key(m) for m in members], "canonical_ids": sorted(map(str, ids))}
            )
    return outcome
