"""Deduplication rules, on title pairs from RegAlert's OJK listing (2026-10-12)."""

from __future__ import annotations

import pytest

from ojk_connector.dedup import resolve_duplicates, title_overlap
from ojk_connector.identity import CanonicalId, IdResolution
from ojk_connector.models import DiscoveredDocument, DocumentContent, Origin, Rights, TextArtifact
from ojk_connector.store import Store


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        # Real duplicates (the same instrument listed twice)
        ("POJK on Guidelines for the Implementation of Investment Manager Functions",
         "Implementation Guidelines for Investment Manager Functions", True),
        ("Circular Letter of the Financial Services Authority Number 2/SEOJK.05/2016 Concerning the Financial Health "
         "Level of Sharia Financing", "SEOJK on the Financial Health Level of Sharia Financing", True),
        ("Amendment to OJK Circular No. 4/SEOJK.05/2016 on Monthly Reports of Sharia Financing Companies",
         "Amendment to Financial Services Authority Circular Number 4/SEOJK.05/2016 on Monthly Reports of Sharia "
         "Financing Companies", True),
        # Real collision: SEOJK 8/2023 vs POJK 35/2017 (a wrong id made them share one)
        ("Submission of Information by Issuers and Public Companies in the Framework of Preparing the Sharia "
         "Compliant Securities List", "Criteria and Publication of the Sharia Securities List", False),
    ],
)
def test_title_overlap(a: str, b: str, same: bool) -> None:
    assert (title_overlap(a, b) >= 0.8) is same


def _add(store: Store, sid: str, title: str, cid: str, text: str | None = None) -> DiscoveredDocument:
    doc = DiscoveredDocument("regalert", sid, title, "2024-01-01", None, None, {})
    store.upsert_discovered(doc, IdResolution(CanonicalId.parse(cid), "high", "test"))
    if text is not None:
        content = DocumentContent([TextArtifact("id", Origin.SOURCE_TEXT, Rights.PUBLIC, text)], [])
        store.save_content(doc, content, None)
    return doc


def test_same_id_and_title_is_a_duplicate_and_needs_no_read(tmp_path) -> None:
    store = Store(tmp_path)
    _add(store, "a", "POJK on Guidelines for the Implementation of Investment Manager Functions", "ojk:pojk:2014:24", "X")
    _add(store, "b", "Implementation Guidelines for Investment Manager Functions", "ojk:pojk:2014:24")
    outcome = resolve_duplicates(store)
    assert outcome.duplicates == 1 and not outcome.new_collisions
    b = store.row("regalert", "b")
    assert b["duplicate_of"] == "regalert:a" and b["content_status"] == "duplicate"


def test_different_titles_are_undecided_until_both_texts_exist(tmp_path) -> None:
    store = Store(tmp_path)
    _add(store, "a", "Criteria and Publication of the Sharia Securities List", "ojk:pojk:2017:35", "POJK TEXT")
    _add(store, "b", "Submission of Information by Issuers for the Sharia Securities List", "ojk:pojk:2017:35")
    outcome = resolve_duplicates(store)
    assert outcome.undecided == 1 and not outcome.new_collisions
    assert store.row("regalert", "b")["content_status"] == "pending"  # still gets read

    store.save_content(store.to_document(store.row("regalert", "b")),
                       DocumentContent([TextArtifact("id", Origin.SOURCE_TEXT, Rights.PUBLIC, "SEOJK TEXT")], []), None)
    outcome = resolve_duplicates(store)
    assert outcome.collisions == 1 and outcome.new_collisions[0]["record"] == "regalert:b"
    assert store.row("regalert", "b")["dedup_reason"].startswith("id collision")
    assert len(store.review_items()["id_collisions"]) == 1


def test_identical_text_is_a_duplicate_whatever_the_titles(tmp_path) -> None:
    store = Store(tmp_path)
    _add(store, "a", "Completely different words", "ojk:seojk:2019:27", "SAME TEXT")
    _add(store, "b", "Nothing alike here", "ojk:seojk:2019:27", "SAME TEXT")
    assert resolve_duplicates(store).duplicates == 1


def test_same_text_under_different_ids_is_reported(tmp_path) -> None:
    store = Store(tmp_path)
    _add(store, "a", "One", "ojk:pojk:2020:1", "SAME TEXT")
    _add(store, "b", "Two", "ojk:pojk:2020:2", "SAME TEXT")
    outcome = resolve_duplicates(store)
    assert outcome.same_text_different_ids[0]["canonical_ids"] == ["ojk:pojk:2020:1", "ojk:pojk:2020:2"]


def test_undoing_a_duplicate_decision_requeues_the_record(tmp_path) -> None:
    store = Store(tmp_path)
    _add(store, "a", "Investment Manager Functions Guidelines", "ojk:pojk:2014:24", "X")
    _add(store, "b", "Guidelines for Investment Manager Functions", "ojk:pojk:2014:24")
    resolve_duplicates(store)
    assert store.row("regalert", "b")["content_status"] == "duplicate"
    # Its id turns out to be different (e.g. after a re-parse): it must be read after all.
    store.set_identity("regalert", "b", IdResolution(CanonicalId.parse("ojk:pojk:2014:25"), "high", "t"), "metadata")
    resolve_duplicates(store)
    assert store.row("regalert", "b")["content_status"] == "pending"
