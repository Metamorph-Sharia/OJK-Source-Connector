from __future__ import annotations

import json
from datetime import date

import pytest

from fake_regalert import FakeRegAlert, row
from regingest.connectors import AgencyBinding, ConnectorContext, PermanentSourceError, QuotaExhausted, SourceAuthError
from regingest.connectors.regalert.auth import Token, TokenStore
from regingest.connectors.regalert.connector import RegAlertConnector
from regingest.models import Origin, Rights

BINDING = AgencyBinding("ojk", {"regulator": "OJK", "jurisdiction": "ID"})


@pytest.fixture
def context(tmp_path) -> ConnectorContext:
    ctx = ConnectorContext("regalert", tmp_path / "creds", "regingest-test")
    TokenStore(ctx.credentials_dir / "regalert.json").save(
        Token("good-token", "refresh-1", None, "client-1", "https://auth.example/oauth/token")
    )
    return ctx


def connector(fake: FakeRegAlert, context: ConnectorContext, **settings) -> RegAlertConnector:
    return RegAlertConnector(
        {"requests_per_second": 0, **settings}, context, transport=fake.transport(), sleep=lambda s: None
    )


def test_discovery_splits_crowded_windows_and_finds_every_document_once(context) -> None:
    rows = [row(f"u{i:03}", f"2024-{1 + i % 12:02}-{1 + i % 28:02}", f"POJK-{i}-Tahun-2024-x") for i in range(40)]
    rows += [row(f"same{i}", "2025-12-22", f"POJK-{50 + i}-Tahun-2025-x") for i in range(7)]  # one crowded day
    fake = FakeRegAlert(rows=rows)
    with connector(fake, context, page_limit=5) as c:
        found = list(c.discover(BINDING, since=date(2000, 1, 1), until=date(2026, 1, 1)))
    assert sorted(d.source_id for d in found) == sorted(r["uid"] for r in rows)
    first = next(d for d in found if d.source_id == "u000")
    assert first.agency == "ojk" and first.source == "regalert"
    assert first.official_url.endswith("POJK-0-Tahun-2024-x.aspx")
    assert first.metadata["uid"] == "u000"


def test_discovery_requires_a_regulator(context) -> None:
    with connector(FakeRegAlert(), context) as c, pytest.raises(Exception, match="regulator"):
        list(c.discover(AgencyBinding("ojk", {})))


def test_content_pages_through_text_and_keeps_original_and_snapshots(context) -> None:
    english = "E" * 2500
    original = "PERATURAN OTORITAS JASA KEUANGAN " + "I" * 2400
    fake = FakeRegAlert(rows=[row("u1", "2026-02-23", "POJK-1-Tahun-2026-x")], texts={"u1": (english, original)})
    with connector(fake, context, max_chars=1000) as c:
        doc = next(c.discover(BINDING, since=date(2026, 1, 1), until=date(2026, 12, 31)))
        content = c.fetch_content(doc)
    by_origin = {t.origin: t for t in content.texts}
    assert by_origin[Origin.SOURCE_TEXT].language == "id"
    assert by_origin[Origin.SOURCE_TEXT].content == original
    assert by_origin[Origin.SOURCE_TEXT].rights is Rights.PUBLIC
    assert by_origin[Origin.SOURCE_TRANSLATION].content == english
    assert by_origin[Origin.SOURCE_TRANSLATION].rights is Rights.RESTRICTED
    assert len(content.snapshots) == 3  # offsets 0, 1000, 2000
    assert content.extra["lineage"]["status_label"] == "In force"
    assert content.extra["original_text_found"] is True
    assert all(t.complete for t in content.texts)


def test_original_text_longer_than_english_is_paged_to_the_end(context) -> None:
    # The two texts page independently (live shape): the original must not stop when the English does.
    english, original = "E" * 900, "PERATURAN " + "I" * 2990
    fake = FakeRegAlert(rows=[row("u1", "2026-02-23", "x")], texts={"u1": (english, original)})
    with connector(fake, context, max_chars=1000) as c:
        doc = next(c.discover(BINDING, since=date(2026, 1, 1), until=date(2026, 12, 31)))
        content = c.fetch_content(doc)
    by_origin = {t.origin: t for t in content.texts}
    assert by_origin[Origin.SOURCE_TEXT].content == original and by_origin[Origin.SOURCE_TEXT].complete
    assert by_origin[Origin.SOURCE_TRANSLATION].content == english


def test_a_paused_account_in_a_response_stops_the_next_read(context) -> None:
    fake = FakeRegAlert(rows=[row("u1", "2026-02-23", "a"), row("u2", "2026-02-22", "b")],
                        texts={"u1": ("en", "id"), "u2": ("en", "id")}, report_paused_after=1)
    with connector(fake, context) as c:
        docs = list(c.discover(BINDING, since=date(2026, 1, 1), until=date(2026, 12, 31)))
        c.fetch_content(docs[0])  # its response reports the account as paused
        with pytest.raises(QuotaExhausted):
            c.fetch_content(docs[1])
    assert fake.reads == 1


def test_any_charge_stops_reads(context) -> None:
    fake = FakeRegAlert(rows=[row("u1", "2026-02-23", "a"), row("u2", "2026-02-22", "b")],
                        texts={"u1": ("en", "id"), "u2": ("en", "id")}, charged_usd="$0.05")
    with connector(fake, context) as c:
        docs = list(c.discover(BINDING, since=date(2026, 1, 1), until=date(2026, 12, 31)))
        content = c.fetch_content(docs[0])
        assert content.extra["charged_usd"] == pytest.approx(0.05)
        with pytest.raises(QuotaExhausted, match="charged"):
            c.fetch_content(docs[1])


def test_status_check_fingerprints_incoming_amendments(context) -> None:
    fake = FakeRegAlert(rows=[row("old", "2017-07-12", "x"), row("new", "2026-02-23", "y")])
    with connector(fake, context) as c:
        doc = next(d for d in c.discover(BINDING, since=date(2000, 1, 1), until=date(2026, 12, 31)) if d.source_id == "old")
        before = c.check_status(doc)
        fake.incoming["old"] = [{"relation": "supersedes", "uid": "new", "title": "POJK 1/2026", "date": "2026-02-23"}]
        after = c.check_status(doc)
    assert before.summary == "unchanged" and after.summary == "superseded"
    assert before.fingerprint != after.fingerprint
    assert after.edges == [{"relation": "supersedes", "source_id": "new", "title": "POJK 1/2026", "date": "2026-02-23"}]


def test_rights_come_from_settings(context) -> None:
    fake = FakeRegAlert(rows=[row("u1", "2026-02-23", "x")], texts={"u1": ("en", "id")})
    with connector(fake, context, translation_rights="licensed") as c:
        doc = next(c.discover(BINDING, since=date(2026, 1, 1), until=date(2026, 12, 31)))
        content = c.fetch_content(doc)
    assert {t.origin: t.rights for t in content.texts}[Origin.SOURCE_TRANSLATION] is Rights.LICENSED


def test_fair_use_pause_becomes_quota_exhausted(context) -> None:
    fake = FakeRegAlert(
        rows=[row("u1", "2026-02-23", "a"), row("u2", "2026-02-22", "b")],
        texts={"u1": ("en", "id"), "u2": ("en", "id")}, fair_use_after=1,
    )
    with connector(fake, context) as c:
        docs = list(c.discover(BINDING, since=date(2026, 1, 1), until=date(2026, 12, 31)))
        c.fetch_content(docs[0])
        with pytest.raises(QuotaExhausted, match="pause"):
            c.fetch_content(docs[1])


def test_paused_account_stops_before_reading(context) -> None:
    fake = FakeRegAlert(rows=[row("u1", "2026-02-23", "a")], texts={"u1": ("en", "id")}, full_text_reads="paused")
    with connector(fake, context) as c:
        doc = next(c.discover(BINDING, since=date(2026, 1, 1), until=date(2026, 12, 31)))
        with pytest.raises(QuotaExhausted):
            c.fetch_content(doc)
    assert fake.reads == 0


def test_missing_document_is_permanent(context) -> None:
    fake = FakeRegAlert(rows=[row("gone", "2026-02-23", "a")])
    with connector(fake, context) as c:
        doc = next(c.discover(BINDING, since=date(2026, 1, 1), until=date(2026, 12, 31)))
        with pytest.raises(PermanentSourceError):
            c.fetch_content(doc)


def test_not_signed_in(tmp_path) -> None:
    ctx = ConnectorContext("regalert", tmp_path / "empty", "regingest-test")
    with connector(FakeRegAlert(), ctx) as c, pytest.raises(SourceAuthError, match="regingest auth login regalert"):
        list(c.discover(BINDING))


# --- found in the backfill (2026-10-12) ---------------------------------------------------

def _page(offset: int, text: str, original: str, *, total: int, next_offset: int | None) -> tuple[str, bytes]:
    """A stored document_json snapshot, shaped like RegAlert's live responses."""
    doc = {"uid": "u1", "language": "id", "lineage": {},
           "text": text, "text_offset": offset, "text_total_chars": total, "text_next_offset": next_offset,
           "text_truncated": next_offset is not None,
           "original_text": original, "original_text_offset": offset, "original_text_total_chars": total,
           "original_text_next_offset": next_offset, "original_text_truncated": next_offset is not None}
    return f"document_json@{offset}", json.dumps({"document": doc}).encode()


def test_pages_join_by_api_offsets_even_when_character_counts_differ(context) -> None:
    # Real case (SEOJK 31/2025): RegAlert's 400000-unit first page held 399992 Python characters;
    # the second page starts at API offset 400000. That page used to be dropped.
    first, second = "A" * 9992, "B" * 3856
    snapshots = [
        _page(0, first, first, total=13863, next_offset=10000),
        _page(10000, second, second, total=13863, next_offset=None),
    ]
    with connector(FakeRegAlert(), context) as c:
        content = c.rebuild_content(_doc(), snapshots)
    texts = {t.origin: t for t in content.texts}
    assert texts[Origin.SOURCE_TEXT].content == first + second
    assert texts[Origin.SOURCE_TEXT].complete is True


def test_small_total_mismatch_is_not_incomplete_when_not_truncated(context) -> None:
    # Real case (POJK 37/2025): 50961 characters received, total_chars 51261, truncated false.
    text = "PERATURAN " + "x" * 50951
    with connector(FakeRegAlert(), context) as c:
        content = c.rebuild_content(_doc(), [_page(0, text, text, total=51261, next_offset=None)])
    assert all(t.complete for t in content.texts)


def test_truncated_without_a_next_page_is_incomplete(context) -> None:
    text = "x" * 100
    snapshot = _page(0, text, text, total=500, next_offset=None)
    kind, raw = snapshot
    body = json.loads(raw)
    body["document"]["original_text_truncated"] = True
    with connector(FakeRegAlert(), context) as c:
        content = c.rebuild_content(_doc(), [(kind, json.dumps(body).encode())])
    assert {t.origin: t.complete for t in content.texts}[Origin.SOURCE_TEXT] is False


def _doc():
    from regingest.models import DiscoveredDocument

    return DiscoveredDocument("regalert", "u1", "ojk", "t", "2025-11-28", None, None, {"language": "id"})
