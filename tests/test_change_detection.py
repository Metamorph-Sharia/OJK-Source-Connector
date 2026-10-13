"""Change detection: metadata and text changes, documents disappearing, regulations being amended."""

from __future__ import annotations

import json
import sqlite3

from fake_regalert import FakeRegAlert
from test_pipeline import make_pipeline, row

from ojk_connector.store import Store

POJK_1 = "PERATURAN OTORITAS JASA KEUANGAN\nREPUBLIK INDONESIA\nNOMOR 1 TAHUN 2026\nTENTANG\nPENGGUNAAN TENAGA KERJA ASING"
POJK_37 = "PERATURAN OTORITAS JASA KEUANGAN\nNOMOR 37 /POJK.03/2017\nTENTANG\nPEMANFAATAN TENAGA KERJA ASING"


def fake_two() -> FakeRegAlert:
    return FakeRegAlert(
        rows=[
            row("new", "2026-02-23", "POJK-1-Tahun-2026-Penggunaan-Tenaga-Kerja-Asing", "Use of Foreign Workers",
                instrument_no="ojk:regulation:1:2026"),
            row("old", "2017-07-12", "Pemanfaatan-Tenaga-Kerja-Asing", "Utilization of Foreign Workers",
                identifier="37/POJK.03/2017"),
        ],
        texts={"new": ("en", POJK_1), "old": ("en", POJK_37)},
    )


def kinds(p) -> list[str]:
    return [e["kind"] for e in p.store.events(limit=1000)]


def test_metadata_change_queues_a_reread_and_text_change_is_alerted(tmp_path) -> None:
    fake = fake_two()
    with make_pipeline(tmp_path, fake) as p:
        p.run()
        assert fake.reads == 2
        # The source corrects the identifier and the text.
        fake.rows[1]["identifier"] = "37/POJK.03/2017 "
        fake.texts["old"] = ("en", POJK_37 + "\nPasal 1 (corrected)")
        report = p.run(full=True)
        d = report["discovery"][0]
        assert d["changed"] == 1 and d["refetch_queued"] == 1
        assert report["content"][0]["refetched"] == 1
        assert report["content"][0]["content_changed"][0]["record"] == "regalert:old"
        assert "content_changed" in [a["code"] for a in report["alerts"]]
        assert {"metadata_changed", "refetch_queued", "content_changed"} <= set(kinds(p))


def test_a_cosmetic_metadata_change_does_not_spend_a_read(tmp_path) -> None:
    fake = fake_two()
    with make_pipeline(tmp_path, fake) as p:
        p.run()
        fake.rows[1]["topics"] = ["banking"]  # not a content field
        report = p.run(full=True)
        assert report["discovery"][0]["changed"] == 1 and report["discovery"][0]["refetch_queued"] == 0
        assert fake.reads == 2


def test_documents_missing_from_a_full_discovery_are_flagged_and_can_reappear(tmp_path) -> None:
    fake = fake_two()
    with make_pipeline(tmp_path, fake) as p:
        p.run()
        hidden = fake.rows.pop()
        report = p.run(full=True)
        assert [m["record"] for m in report["discovery"][0]["missing"]] == ["regalert:old"]
        assert "documents_missing" in [a["code"] for a in report["alerts"]]
        assert p.store.row("regalert", "old")["missing_since"] is not None

        # Incremental runs never mark anything missing.
        report = p.run()
        assert report["discovery"][0]["missing"] == []

        fake.rows.append(hidden)
        report = p.run(full=True)
        assert report["discovery"][0]["reappeared"] == 1
        assert p.store.row("regalert", "old")["missing_since"] is None


def test_amendment_is_detected_through_standing_checks(tmp_path) -> None:
    fake = fake_two()
    with make_pipeline(tmp_path, fake) as p:
        first = p.run()
        assert first["status_checks"][0]["baselined"] == 2 and first["status_checks"][0]["changed"] == []

        fake.incoming["old"] = [{"relation": "supersedes", "uid": "new", "title": "POJK 1/2026", "date": "2026-02-23"}]
        report = p.run()
        changed = report["status_checks"][0]["changed"]
        assert changed[0]["record"] == "regalert:old" and changed[0]["summary"] == "superseded"
        assert changed[0]["added"][0]["canonical_id"] == "ojk:pojk:2026:1"
        assert "replaced by ojk:pojk:2026:1" in changed[0]["reason"]
        alert = next(a for a in report["alerts"] if a["code"] == "regulation_status_changed")
        assert alert["severity"] == "info" and alert["transient"]

        meta = json.loads((tmp_path / "data/corpus/documents/ojk/pojk/2017/37/metadata.json").read_text())
        assert meta["standing"]["summary"] == "superseded"


def test_a_new_document_prioritises_checks_on_what_it_amends(tmp_path) -> None:
    fake = fake_two()
    with make_pipeline(tmp_path, fake) as p:
        p.discover()
        # As done when a fetched document's lineage names the instrument it supersedes.
        p.store.prioritize_status_checks("regalert", ["old"])
        queue = p.store.status_check_queue("regalert", 1)
        assert queue[0].source_id == "old"


def test_full_discovery_happens_every_n_days(tmp_path) -> None:
    fake = fake_two()
    with make_pipeline(tmp_path, fake) as p:
        assert p.discover()[0].full  # first time
        assert not p.discover()[0].full  # just done
        p.store.db.execute("UPDATE discovery_state SET last_full_at = '2026-01-01T00:00:00+00:00'")
        p.store.db.commit()
        assert p.discover()[0].full  # due again


def test_store_migrates_a_version_1_database(tmp_path) -> None:
    db_path = tmp_path / "db" / "ojk_connector.sqlite"
    db_path.parent.mkdir(parents=True)
    old = sqlite3.connect(db_path)
    old.executescript(
        "CREATE TABLE source_documents (source TEXT NOT NULL, source_id TEXT NOT NULL, agency TEXT NOT NULL, "
        "title TEXT, published TEXT, source_url TEXT, official_url TEXT, metadata_json TEXT NOT NULL, "
        "canonical_id TEXT, id_confidence TEXT NOT NULL DEFAULT 'none', id_evidence TEXT, id_origin TEXT, "
        "content_status TEXT NOT NULL DEFAULT 'pending', content_attempts INTEGER NOT NULL DEFAULT 0, "
        "content_error TEXT, content_fetched_at TEXT, content_extra_json TEXT, first_seen_at TEXT NOT NULL, "
        "last_seen_at TEXT NOT NULL, metadata_changed_at TEXT NOT NULL, PRIMARY KEY (source, source_id));"
        "INSERT INTO source_documents (source, source_id, agency, metadata_json, first_seen_at, last_seen_at, "
        "metadata_changed_at) VALUES ('regalert', 'x', 'ojk', '{}', 't', 't', 't');"
    )
    old.close()
    store = Store(tmp_path)
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert store.row("regalert", "x")["missing_since"] is None and store.row("regalert", "x")["status_priority"] == 0
    store.record_event("test", "info", "ok")
    Store(tmp_path)  # idempotent
