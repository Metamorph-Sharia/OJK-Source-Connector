from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fake_regalert import FakeRegAlert, row

from ojk_connector.config import load_config
from ojk_connector.models import DiscoveredDocument, DocumentContent, Origin, Rights, TextArtifact
from ojk_connector.pipeline import Pipeline
from ojk_connector.sources import Source, SourceContext
from ojk_connector.sources.regalert.auth import Token, TokenStore
from ojk_connector.sources.regalert.source import RegAlertSource

TODAY = date(2026, 10, 12)

CONFIG = """
[pipeline]
data_dir = "data"
credentials_dir = "creds"

[sources.regalert]
requests_per_second = 0
max_content_reads_per_run = 2
"""

POJK_1_2026 = "PERATURAN OTORITAS JASA KEUANGAN\nREPUBLIK INDONESIA\nNOMOR 1 TAHUN 2026\nTENTANG\nPENGGUNAAN TENAGA KERJA ASING"


def header(family_line: str, number: str) -> str:
    return f"{family_line}\nNOMOR {number}\nTENTANG\n..."


@pytest.fixture
def fake() -> FakeRegAlert:
    return FakeRegAlert(
        rows=[
            row("pojk-1-2026", "2026-02-23", "POJK-1-Tahun-2026-Penggunaan-Tenaga-Kerja-Asing",
                "Use of Foreign Workers and Knowledge Transfer Programs by Commercial Banks",
                instrument_no="ojk:regulation:1:2026"),
            row("padk-11-2026", "2026-09-23", "PADK-11-Tahun-2026-Pelaksanaan-Penawaran-Umum",
                identifier="PADK 11 Tahun 2026", instrument_no="ojk:regulation:11:2026"),
            row("pojk-11-2026", "2026-09-15", "POJK-11-Tahun-2026-Laporan-Berkala-Lembaga-Penjamin",
                instrument_no="ojk:regulation:11:2026"),
            # RegAlert lists POJK 28/POJK.05/2014 twice (real titles).
            row("dup-a", "2014-11-25", "POJK-tentang-Perizinan-Usaha-dan-Kelembagaan-Perusahaan-Pembiayaan",
                "POJK on Business Licensing and Institutional Structure of Financing Companies",
                identifier="28/POJK.05/2014"),
            row("dup-b", "2014-11-19", "Perizinan-Usaha-dan-Kelembagaan-Perusahaan-Pembiayaan",
                "Financial Services Authority Regulation Number 28/POJK.05/2014 Concerning Business Licensing and "
                "Institutional Aspects of Financing Companies",
                identifier="28/POJK.05/2014"),
        ],
        texts={
            "pojk-1-2026": ("FINANCIAL SERVICES AUTHORITY REGULATION NUMBER 1 OF 2026", POJK_1_2026),
            "padk-11-2026": ("en", header("PERATURAN ANGGOTA DEWAN KOMISIONER OTORITAS JASA KEUANGAN", "11 TAHUN 2026")),
            "pojk-11-2026": ("en", header("PERATURAN OTORITAS JASA KEUANGAN", "11 TAHUN 2026")),
            "dup-a": ("en", header("PERATURAN OTORITAS JASA KEUANGAN", "28/POJK.05/2014")),
            "dup-b": ("en", header("PERATURAN OTORITAS JASA KEUANGAN", "28/POJK.05/2014")),
        },
    )


def make_pipeline(
    tmp_path: Path, fake: FakeRegAlert, extra_config: str = "", *, notifiers=None, **sources: Source
) -> Pipeline:
    path = tmp_path / "pipeline.toml"
    path.write_text(CONFIG + extra_config)
    config = load_config(path)
    TokenStore(config.credentials_dir / "regalert.json").save(Token("good-token", "r", None, "c", "https://a/t"))
    regalert = RegAlertSource(
        config.sources["regalert"].settings,
        SourceContext("regalert", config.credentials_dir, config.user_agent),
        transport=fake.transport(),
        sleep=lambda s: None,
    )
    return Pipeline(config, sources={"regalert": regalert, **sources}, notifiers=notifiers, today=lambda: TODAY)


def test_end_to_end_paced_backfill(tmp_path, fake) -> None:
    with make_pipeline(tmp_path, fake) as p:
        first = p.run()
        assert first["status"] == "ok"
        assert first["discovery"][0]["new"] == 5
        assert first["content"][0]["fetched"] == 2  # capped per run
        assert first["content"][0]["remaining"] == 3

        second = p.run()
        assert second["discovery"][0]["new"] == 0
        third = p.run()
        content = third["content"][0]
        assert content["remaining"] == 0
        assert fake.reads == 4  # five documents, one is a duplicate and never read

        corpus = tmp_path / "data" / "corpus"
        pojk = corpus / "documents/ojk/pojk/2026/1"
        assert (pojk / "text.id.md").read_text().startswith("PERATURAN OTORITAS JASA KEUANGAN")
        assert not (pojk / "text.en.md").exists()  # RegAlert's translation is restricted by default
        meta = json.loads((pojk / "metadata.json").read_text())
        assert meta["id"] == "ojk:pojk:2026:1" and meta["id_confidence"] == "high"
        assert meta["texts"][0]["origin"] == "source_text" and meta["texts"][0]["rights"] == "public"

        # PADK and POJK 11/2026 stay apart; the duplicate POJK 28/2014 is merged.
        assert (corpus / "documents/ojk/padk/2026/11/text.id.md").exists()
        assert (corpus / "documents/ojk/pojk/2026/11/text.id.md").exists()
        dup = json.loads((corpus / "documents/ojk/pojk/2014/28/metadata.json").read_text())
        assert sorted(s["source_id"] for s in dup["sources"]) == ["dup-a", "dup-b"]
        index = [json.loads(line) for line in (corpus / "regulations.jsonl").read_text().splitlines()]
        assert len(index) == 4
        assert third["export"]["texts_withheld"] == 4


def test_quota_pause_stops_cleanly_and_resumes(tmp_path, fake) -> None:
    fake.fair_use_after = 1
    with make_pipeline(tmp_path, fake) as p:
        report = p.run()
        assert report["status"] == "paused"
        assert report["content"][0]["fetched"] == 1 and report["content"][0]["paused"]
        assert report["content"][0]["remaining"] == 4
        fake.fair_use_after = None  # next day
        p.run()
        # Two read this run; the second POJK 28/2014 listing was matched by title, so it is never read.
        assert p.store.count_pending("regalert") == 1


def test_id_from_the_document_header_overrides_metadata(tmp_path, fake) -> None:
    # Metadata says POJK 1/2026; the document's own header says otherwise: the header wins.
    fake.texts["pojk-1-2026"] = ("en", header("PERATURAN OTORITAS JASA KEUANGAN", "2 TAHUN 2026"))
    with make_pipeline(tmp_path, fake) as p:
        p.discover()
        p.fetch_content(max_reads=3)  # newest first: PADK 11, POJK 11, then this one
        r = p.store.db.execute("SELECT canonical_id, id_origin FROM source_documents WHERE source_id = 'pojk-1-2026'").fetchone()
        assert (r["canonical_id"], r["id_origin"]) == ("ojk:pojk:2026:2", "text")
        p.discover(full=True)  # rediscovery must not overwrite an id taken from the text
        r = p.store.db.execute("SELECT canonical_id FROM source_documents WHERE source_id = 'pojk-1-2026'").fetchone()
        assert r["canonical_id"] == "ojk:pojk:2026:2"


def test_incremental_discovery_uses_cursor_and_lookback(tmp_path, fake) -> None:
    with make_pipeline(tmp_path, fake) as p:
        full = p.discover()[0]
        assert full.full and full.since is None
        again = p.discover()[0]
        assert not again.full and again.since == "2026-08-28"  # 2026-10-12 minus 45 days
        assert again.seen == 2 and again.new == 0


class OfficialSiteSource(Source):
    """Stands in for the planned fallback: the official PDF from ojk.go.id, extracted by us."""

    def discover(self, since=None, until=None) -> Iterator[DiscoveredDocument]:
        yield DiscoveredDocument(self.name, "pojk-1-2026", "Peraturan OJK Nomor 1 Tahun 2026", "2026-02-23", None,
                                 "https://www.ojk.go.id/id/regulasi/Pages/POJK-1-Tahun-2026-Tenaga-Kerja-Asing.aspx", {})

    def fetch_content(self, document: DiscoveredDocument) -> DocumentContent:
        return DocumentContent(
            texts=[TextArtifact("id", Origin.SELF_EXTRACTED, Rights.PUBLIC, POJK_1_2026 + "\n(from the official PDF)")],
            snapshots=[],
        )


def test_a_fallback_source_plugs_in_beside_regalert_and_merges_by_canonical_id(tmp_path, fake) -> None:
    official = OfficialSiteSource({}, SourceContext("ojk_website", tmp_path / "creds", "test"))
    with make_pipeline(tmp_path, fake, "\n[sources.ojk_website]\n", ojk_website=official) as p:
        report = p.run(max_reads=10)
        assert report["status"] == "ok"
        meta = json.loads((tmp_path / "data/corpus/documents/ojk/pojk/2026/1/metadata.json").read_text())
        assert {s["source"] for s in meta["sources"]} == {"regalert", "ojk_website"}
        # Our own extraction of the official file outranks RegAlert's copy of the text.
        assert [(t["language"], t["origin"]) for t in meta["texts"]] == [("id", "self_extracted")]


def test_relabelling_rights_needs_no_refetch(tmp_path, fake) -> None:
    with make_pipeline(tmp_path, fake) as p:
        p.run(max_reads=10)
        pojk = tmp_path / "data/corpus/documents/ojk/pojk/2026/1"
        assert not (pojk / "text.en.md").exists()  # RegAlert's translation is restricted by default
        assert p.store.set_rights("regalert", Origin.SOURCE_TRANSLATION, Rights.LICENSED) == 4
        p.export()
        assert (pojk / "text.en.md").exists()


def test_ctrl_c_during_standing_checks_keeps_data_and_writes_a_partial_report(tmp_path, fake) -> None:
    # As on 2026-10-12 (run 3): reads finished, then Ctrl-C during the standing checks.
    with make_pipeline(tmp_path, fake) as p:
        p.store.save_alert_state({"fingerprint": "backlog_stalled:regalert", "code": "backlog_stalled",
                                  "scope": "regalert", "severity": "warning", "message": "old",
                                  "first_seen_at": "2026-10-11T00:00:00+00:00",
                                  "last_seen_at": "2026-10-11T00:00:00+00:00"})
        calls = {"n": 0}
        original = p.source("regalert").check_status

        def interrupt_after_one(doc):
            calls["n"] += 1
            if calls["n"] > 1:
                raise KeyboardInterrupt
            return original(doc)

        p.source("regalert").check_status = interrupt_after_one
        with pytest.raises(KeyboardInterrupt):
            p.run()

        run = p.store.last_run()
        report = run["report"]
        assert run["status"] == "interrupted"
        assert report["stages_completed"] == ["discover", "fetch_content"]
        assert report["content"][0]["fetched"] == 2 and not report["content"][0]["partial"]
        assert report["status_checks"][0]["checked"] == 1 and report["status_checks"][0]["partial"]
        assert "interrupted during check_status" in report["problems"][0]
        assert p.store.count_pending("regalert") == 3  # the two reads were kept
        assert len(p.store.open_alerts()) == 1  # partial data resolves nothing
        latest = (tmp_path / "data/reports/latest.md").read_text()
        assert "INTERRUPTED" in latest and "stopped early" in latest

        # The next run picks up where it stopped.
        p.source("regalert").check_status = original
        assert p.run()["content"][0]["fetched"] == 2


def test_header_disagreeing_with_a_trusted_identifier_is_a_conflict(tmp_path, fake) -> None:
    # Real: SEOJK 16/SEOJK.04/2016's own header reads "NOMOR.116 /SEOJK.04/2016".
    fake.rows.append(row("seojk-16", "2016-05-20", "SEOJK-tentang-Pengakuan-terhadap-Asosiasi-Wakil-Manajer-Investasi",
                         "Recognition of Investment Manager Representative Associations", identifier="16/SEOJK.04/2016"))
    fake.texts["seojk-16"] = ("en", "Yth. Wakil Manajer Investasi di tempat. SALINAN SURAT EDARAN OTORITAS JASA "
                                    "KEUANGAN NOMOR.116 /SEOJK.04/2016.. TENTANG PENGAKUAN")
    with make_pipeline(tmp_path, fake) as p:
        p.discover()
        p.fetch_content(max_reads=10)
        r = p.store.row("regalert", "seojk-16")
        assert (r["canonical_id"], r["id_confidence"]) == ("ojk:seojk:2016:116", "conflict")
        assert "metadata says ojk:seojk:2016:16" in r["id_evidence"]
        assert any(i["source_id"] == "seojk-16" for i in p.store.review_items()["id_unresolved"])


def test_a_source_that_refuses_us_is_left_alone_for_the_rest_of_the_run(tmp_path, fake) -> None:
    """HTTP 403: credentials are fine but access is refused. Asking 900 more times won't change that."""
    with make_pipeline(tmp_path, fake) as p:
        p.run()
        fake.forbidden = True
        before = len(fake.calls)

        report = p.run()

    assert len(fake.calls) - before == 1  # the first refusal ends every stage's use of RegAlert
    assert report["discovery"][0]["error_kind"] == "forbidden"
    assert report["status_checks"][0]["error_kind"] == "skipped"  # not asked again
    refused = [a for a in report["alerts"] if a["code"] == "access_forbidden"]
    assert [a["severity"] for a in refused] == ["critical"]
    assert "refused" in refused[0]["message"]
