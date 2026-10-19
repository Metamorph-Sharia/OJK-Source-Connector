"""JDIH OJK in the pipeline, beside records another source (RegAlert) already holds.

The official catalogue completes held documents (dates, standing, history) without re-reading them, and
supplies the documents nobody holds yet, with their official files.
"""

from __future__ import annotations

from datetime import date

import pytest
from fake_jdih import FakeJdih, Regulation, pdf
from fake_regalert import FakeRegAlert, row
from metamorph_contract import Bundle, Relation, Status
from test_jdih_source import HEADER, regulation
from test_pipeline import make_pipeline

from ojk_connector.export_v2 import export_bundle
from ojk_connector.sources import SourceContext
from ojk_connector.sources.jdih.source import JdihOjkSource

JDIH_CONFIG = """
[sources.jdih_ojk]
requests_per_second = 0
max_content_reads_per_run = 5
"""
ONLY_IN_JDIH = "11111111-2222-3333-4444-555555555555"
POJK_9_2024 = ("PERATURAN OTORITAS JASA KEUANGAN\nREPUBLIK INDONESIA\nNOMOR 9 TAHUN 2024\nTENTANG\n"
               "PENERAPAN TATA KELOLA BAGI BANK PEREKONOMIAN RAKYAT\nDENGAN RAHMAT TUHAN YANG MAHA ESA")


@pytest.fixture
def regalert() -> FakeRegAlert:
    # RegAlert held POJK 8/2026 under an English title, with its Indonesian text.
    return FakeRegAlert(
        rows=[row("pojk-8-2026", "2026-07-06", "POJK-8-Tahun-2026-Pelaporan",
                  "Reporting and Requesting Funding Transaction Data by P2P Lending Providers")],
        texts={"pojk-8-2026": ("FINANCIAL SERVICES AUTHORITY REGULATION NUMBER 8 OF 2026", HEADER)},
    )


@pytest.fixture
def site() -> FakeJdih:
    return FakeJdih([
        regulation(),
        Regulation(guid=ONLY_IN_JDIH, number="9", status="Berlaku (Dicabut Sebagian)",
                   title="Peraturan Otoritas Jasa Keuangan Republik Indonesia Nomor 9 Tahun 2024 tentang Penerapan "
                         "Tata Kelola bagi Bank Perekonomian Rakyat",
                   enacted="25-03-2024", promulgated="27-03-2024",
                   files=[("Peraturan", "2024pojk009.pdf", pdf(POJK_9_2024, "Pasal 1")),
                          ("FAQ", "2024faqpojk009.pdf", pdf("FAQ"))]),
    ])


def pipeline(tmp_path, regalert: FakeRegAlert, site: FakeJdih):
    jdih = JdihOjkSource({"requests_per_second": 0}, SourceContext("jdih_ojk", tmp_path, "test"),
                         transport=site.transport(), sleep=lambda s: None, today=lambda: date(2026, 10, 16))
    return make_pipeline(tmp_path, regalert, JDIH_CONFIG, jdih_ojk=jdih)


def test_held_documents_are_completed_by_their_entry_and_never_downloaded_again(tmp_path, regalert, site) -> None:
    with pipeline(tmp_path, regalert, site) as p:
        report = p.run()
        held = p.store.row("jdih_ojk", site.regulations["009e5e55-ce9b-5f02-6b0b-2fdb55e5675a"].guid)
        stats = export_bundle(p.store, tmp_path / "bundle", run_id="r1")

    # The official record of POJK 8/2026 is the same document as RegAlert's (its title is the text's subject).
    assert held["content_status"] == "duplicate" and held["duplicate_of"] == "regalert:pojk-8-2026"
    assert not any("DownloadDokumen/009e5e55" in url for url in site.requests)
    assert report["entries"][0]["read"] == 2 and report["entries"][0]["failed"] == 0

    documents = {e.document.document.id: e.document for e in Bundle(tmp_path / "bundle").entries()}
    assert all(e.is_valid for e in Bundle(tmp_path / "bundle").entries())
    pojk_8 = documents["ojk:pojk:2026:8"]
    assert pojk_8.source.source_id == "regalert:pojk-8-2026"  # the platform keeps its record: no churn
    assert (pojk_8.document.status.value, pojk_8.document.status.source) == (Status.IN_FORCE, "jdih_ojk")
    assert pojk_8.document.dates.promulgated == "2026-07-06"
    assert [(c.relation, c.target_id, c.to_address) for c in pojk_8.citations] == [
        (Relation.PARTLY_REVOKES, "ojk:pojk:2024:40", "pasal-187"),
        (Relation.LEGAL_BASIS, "id:uu:2011:21", None),
    ]
    assert pojk_8.source_fields["catalog_entry"]["fields"]["Nomor Peraturan"] == "8"
    assert (stats.documents, stats.with_dates, stats.status_from_entry) == (2, 2, 2)


def test_a_document_only_jdih_lists_arrives_with_its_official_files(tmp_path, regalert, site) -> None:
    with pipeline(tmp_path, regalert, site) as p:
        p.run()
        export_bundle(p.store, tmp_path / "bundle", run_id="r1")

    bundle = Bundle(tmp_path / "bundle")
    pojk_9 = next(e.document for e in bundle.entries() if e.document.document.id == "ojk:pojk:2024:9")
    assert pojk_9.source.source_id == f"jdih_ojk:{ONLY_IN_JDIH}"
    assert pojk_9.document.status.value == Status.PARTLY_REVOKED
    assert pojk_9.document.title == "PENERAPAN TATA KELOLA BAGI BANK PEREKONOMIAN RAKYAT"
    assert [(f.role, f.title) for f in pojk_9.files] == [("main", "2024pojk009.pdf"), ("faq", "2024faqpojk009.pdf")]
    [text] = pojk_9.texts
    assert text.paged and text.of_file == pojk_9.files[0].sha256 and text.origin == "self_extracted"
    assert bundle.blob_path(pojk_9.files[0].sha256).read_bytes().startswith(b"%PDF")


def test_an_entry_that_changes_is_an_event_and_its_page_is_kept_only_then(tmp_path, regalert, site) -> None:
    with pipeline(tmp_path, regalert, site) as p:
        p.discover()
        p.read_entries()
        p.read_entries()  # the page's view counter changed; what it says did not
        site.regulations[ONLY_IN_JDIH].status = "Tidak Berlaku"
        [stats] = p.read_entries()
        pages = p.store.db.execute(
            "SELECT COUNT(*) FROM snapshots WHERE source = 'jdih_ojk' AND source_id = ? AND kind = 'entry_html'",
            (ONLY_IN_JDIH,)).fetchone()[0]
        events = p.store.events(kinds=["entry_changed"])

    assert pages == 2
    assert [c["standing"] for c in stats.changed] == [["partly_revoked", "revoked"]]
    assert [(e["severity"], e["source_id"]) for e in events] == [("warning", ONLY_IN_JDIH)]


def test_entries_are_read_within_the_budget_never_read_first(tmp_path, regalert, site) -> None:
    with pipeline(tmp_path, regalert, site) as p:
        p.discover()
        [first] = p.read_entries(budget=1)
        [second] = p.read_entries(budget=1)
        [third] = p.read_entries(budget=1)
    assert (first.first_reads, second.first_reads, third.first_reads) == (1, 1, 0)


def test_a_firewall_refusal_stops_the_stage_and_raises_a_critical_alert(tmp_path, regalert, site) -> None:
    with pipeline(tmp_path, regalert, site) as p:
        p.discover()
        site.firewall = True
        report = p.run(sources=["jdih_ojk"])

    assert report["discovery"][0]["error_kind"] == "forbidden"
    assert report["entries"][0]["error_kind"] == "skipped"  # not asked again in the same run
    assert [a["code"] for a in report["alerts"] if a["severity"] == "critical"] == ["access_forbidden"]


def test_reparse_applies_parser_changes_to_stored_entries_without_network(tmp_path, regalert, site) -> None:
    with pipeline(tmp_path, regalert, site) as p:
        p.discover()
        p.read_entries()
        p.store.db.execute("UPDATE source_documents SET entry_json = '{}', entry_fingerprint = 'old' "
                           "WHERE source = 'jdih_ojk'")  # as if read by an older parser
        p.store.db.commit()
        read_at = p.store.row("jdih_ojk", ONLY_IN_JDIH)["entry_read_at"]
        site.requests.clear()
        counts = p.reparse()
        row = p.store.row("jdih_ojk", ONLY_IN_JDIH)

    assert site.requests == []
    assert (counts["entries_rebuilt"], counts["entries_changed"]) == (2, 2)
    assert p.store.entry_of(row).standing == "partly_revoked" and row["entry_read_at"] == read_at


def test_a_fetch_can_be_limited_to_named_documents(tmp_path, regalert, site) -> None:
    with pipeline(tmp_path, regalert, site) as p:
        p.discover()
        [stats] = p.fetch_content(sources=["jdih_ojk"], documents=["ojk:pojk:2024:9"])
        statuses = {r["canonical_id"]: r["content_status"]
                    for r in p.store.db.execute("SELECT * FROM source_documents WHERE source = 'jdih_ojk'")}

    assert stats.fetched == 1 and stats.remaining == 1
    assert statuses == {"ojk:pojk:2024:9": "fetched", "ojk:pojk:2026:8": "pending"}  # the other was never looked at


def test_a_file_naming_another_regulation_is_a_conflict_not_a_new_id(tmp_path, regalert) -> None:
    """Seen 2026-10-16: JDIH serves POJK 14/POJK.05/2020 as the file of POJK 14/POJK.05/2016."""
    other = ("PERATURAN OTORITAS JASA KEUANGAN\nREPUBLIK INDONESIA\nNOMOR 14 /POJK.05/2020\nTENTANG\n"
             "KEBIJAKAN COUNTERCYCLICAL\nDENGAN RAHMAT TUHAN YANG MAHA ESA")
    site = FakeJdih([Regulation(guid=ONLY_IN_JDIH, number="14",
                                title="Peraturan Otoritas Jasa Keuangan Republik Indonesia Nomor 14/POJK.05/2016 "
                                      "tentang Pengesahan Pendirian Dana Pensiun",
                                files=[("Peraturan", "POJK 14-2016.pdf", pdf(other, "Pasal 1"))])])
    with pipeline(tmp_path, regalert, site) as p:
        p.discover()
        p.fetch_content(sources=["jdih_ojk"])
        row = p.store.row("jdih_ojk", ONLY_IN_JDIH)

    assert row["id_confidence"] == "conflict"
    assert "metadata says ojk:pojk:2016:14" in row["id_evidence"]
