"""The JDIH OJK source against a fake site: discovery, catalogue entries, files and extracted text."""

from __future__ import annotations

from datetime import date

import httpx
import pymupdf
import pytest
from fake_jdih import FakeJdih, Regulation, pdf

from ojk_connector.identity import resolve_id, resolve_id_from_text
from ojk_connector.models import Origin, Rights, Standing
from ojk_connector.sources import PermanentSourceError, SourceContext, SourceError, SourceForbidden
from ojk_connector.sources.jdih.source import JdihOjkSource

GUID = "009e5e55-ce9b-5f02-6b0b-2fdb55e5675a"
TITLE = ("Peraturan Otoritas Jasa Keuangan Republik Indonesia Nomor 8 Tahun 2026 tentang Pelaporan dan Permintaan "
         "Data Transaksi Pendanaan")
HEADER = ("PERATURAN OTORITAS JASA KEUANGAN\nREPUBLIK INDONESIA\nNOMOR 8 TAHUN 2026\nTENTANG\n"
          "PELAPORAN DAN PERMINTAAN DATA TRANSAKSI PENDANAAN\nDENGAN RAHMAT TUHAN YANG MAHA ESA")


def regulation(**changes) -> Regulation:
    defaults = dict(
        guid=GUID, title=TITLE, number="8", status="Berlaku Sejak Tanggal 06-07-2026",
        history=[("Mencabut", "POJK Nomor 40 Tahun 2024", "Pasal 187")],
        legal_basis=["UU Nomor 21 Tahun 2011 tentang Otoritas Jasa Keuangan"],
        files=[("Peraturan", "2026pojk008.pdf", pdf(HEADER, "Pasal 1\nDalam Peraturan ini yang dimaksud dengan ...")),
               ("Abstrak", "2026abspojk008.pdf", pdf("ABSTRAK"))],
    )
    return Regulation(**{**defaults, **changes})


@pytest.fixture
def site() -> FakeJdih:
    return FakeJdih([regulation()])


def source(site: FakeJdih, tmp_path) -> JdihOjkSource:
    context = SourceContext("jdih_ojk", tmp_path, "ojk-connector-test")
    return JdihOjkSource({"requests_per_second": 0}, context, transport=site.transport(), sleep=lambda s: None,
                         today=lambda: date(2026, 10, 16))


def test_discovery_sweeps_every_sector_and_type_and_ids_come_from_the_title(site, tmp_path) -> None:
    with source(site, tmp_path) as s:
        found = list(s.discover())

    assert len(site.requests) == 20  # 10 sectors × {POJK, SEOJK/PADK}
    assert [d.source_id for d in found] == [GUID]
    document = found[0]
    assert (document.source, document.title, document.metadata["number"]) == ("jdih_ojk", TITLE, "8")
    assert document.source_url.endswith(f"/Detail/{GUID}/09/06")
    assert str(resolve_id(document).id) == "ojk:pojk:2026:8"


def test_an_entry_is_one_page_and_no_files(site, tmp_path) -> None:
    with source(site, tmp_path) as s:
        document = next(s.discover())
        site.requests.clear()
        entry = s.read_entry(document)

    assert len(site.requests) == 1
    assert entry.standing is Standing.IN_FORCE
    assert entry.dates == {"enacted": "2026-06-12", "promulgated": "2026-07-06", "effective": "2026-07-06"}
    assert [f.role for f in entry.files] == ["main", "abstract"]


def test_content_is_every_file_and_the_paged_text_of_the_main_pdf(site, tmp_path) -> None:
    with source(site, tmp_path) as s:
        document = next(s.discover())
        content = s.fetch_content(document)

    [text] = content.texts
    assert (text.language, text.origin, text.rights, text.complete) == ("id", Origin.SELF_EXTRACTED, Rights.PUBLIC, True)
    assert text.content.startswith("<!-- page 1 -->\nPERATURAN OTORITAS JASA KEUANGAN")
    assert "<!-- page 2 -->" in text.content
    assert str(resolve_id_from_text(text.content).id) == "ojk:pojk:2026:8"
    assert [s.kind for s in content.snapshots] == ["entry_html", "file:main:2026pojk008.pdf",
                                                   "file:abstract:2026abspojk008.pdf"]
    assert all(s.media_type == "application/pdf" for s in content.snapshots[1:])
    assert content.extra["files"][0] | {"url": "…"} == {
        "role": "main", "name": "2026pojk008.pdf", "url": "…", "bytes": len(content.snapshots[1].content),
        "media_type": "application/pdf", "pages": 2, "pages_without_text": 0, "text": True}
    assert content.extra["entry"]["standing"] == "in_force"


def test_a_scanned_page_makes_the_text_incomplete(tmp_path) -> None:
    site = FakeJdih([regulation(files=[("Peraturan", "scan.pdf", pdf(HEADER, ""))])])
    with source(site, tmp_path) as s:
        [text] = s.fetch_content(next(s.discover())).texts
    assert text.complete is False


def set_apart() -> bytes:
    """A justified line set a word at a time, as many official PDFs are."""
    document = pymupdf.open()
    page = document.new_page()
    for n, word in enumerate(["Peraturan", "ini", "mulai", "berlaku", "pada"]):
        page.insert_text((72 + 80 * n, 72), word)
    return document.tobytes()


def test_the_text_is_a_printed_line_to_a_line_unless_pymupdfs_own_lines_are_asked_for(tmp_path, monkeypatch) -> None:
    site = FakeJdih([regulation(files=[("Peraturan", "2026pojk008.pdf", set_apart())])])
    with source(site, tmp_path) as s:
        [printed] = s.fetch_content(next(s.discover())).texts
    monkeypatch.setenv("METAMORPH_PDF_LINES", "extracted")
    with source(site, tmp_path) as s:
        [extracted] = s.fetch_content(next(s.discover())).texts

    assert printed.content == "<!-- page 1 -->\nPeraturan ini mulai berlaku pada"
    assert extracted.content == "<!-- page 1 -->\nPeraturan\nini\nmulai\nberlaku\npada"


def test_content_is_rebuilt_from_stored_snapshots_without_network(site, tmp_path) -> None:
    with source(site, tmp_path) as s:
        document = next(s.discover())
        fetched = s.fetch_content(document)
        site.requests.clear()
        rebuilt = s.rebuild_content(document, [(snapshot.kind, snapshot.content) for snapshot in fetched.snapshots])

    assert site.requests == []
    assert rebuilt.texts == fetched.texts
    assert rebuilt.extra["entry"] == fetched.extra["entry"]


def test_the_firewall_is_a_refusal_never_retried(site, tmp_path) -> None:
    site.firewall = True
    with source(site, tmp_path) as s, pytest.raises(SourceForbidden):
        list(s.discover())
    assert len(site.requests) == 1


def test_a_vanished_detail_page_is_permanent(site, tmp_path) -> None:
    with source(site, tmp_path) as s:
        document = next(s.discover())
        site.regulations.clear()
        with pytest.raises(PermanentSourceError):
            s.read_entry(document)


def test_a_broken_secondary_file_is_recorded_but_a_broken_main_file_fails_the_read(tmp_path) -> None:
    class Broken(FakeJdih):
        def _handle(self, request):
            if request.url.path.endswith(f"{GUID}-1"):  # the abstract
                return httpx.Response(500)
            return super()._handle(request)

    site = Broken([regulation()])
    with source(site, tmp_path) as s:
        content = s.fetch_content(next(s.discover()))
    assert [t.origin for t in content.texts] == [Origin.SELF_EXTRACTED]
    assert [(f["role"], f["name"]) for f in content.extra["files_missing"]] == [("abstract", "2026abspojk008.pdf")]

    site = Broken([regulation(files=[("Abstrak", "a.pdf", b""), ("Peraturan", "x.pdf", b"")])])  # main is #1 now
    with source(site, tmp_path) as s, pytest.raises(SourceError):
        s.fetch_content(next(s.discover()))


class FakeMinerU:
    """ExtractPdf as the source needs it: what MinerU read of the regulation's PDF."""

    def __init__(self) -> None:
        from metamorph_pdf_text import ExtractedPdf, Extraction, OcrMode, Tier

        read = Extraction("mineru", "4.0.12+t1", Tier.STANDARD, OcrMode.TXT, 0, 1)
        pages = (HEADER, "Pasal 1\nDalam Peraturan ini yang dimaksud dengan ...")
        self.read = ExtractedPdf(pages, "text_layer", read, ())

    def run(self, pdf: bytes, role: str):
        return self.read


def test_with_the_mineru_engine_the_text_is_minerus_and_how_it_was_read_is_kept(site, tmp_path, monkeypatch) -> None:
    import ojk_connector.sources.jdih.source as jdih

    monkeypatch.setenv("METAMORPH_PDF_ENGINE", "mineru")
    monkeypatch.setattr(jdih, "from_environment", lambda environment: FakeMinerU())
    with source(site, tmp_path) as s:
        content = s.fetch_content(next(s.discover()))

    [text] = content.texts
    assert text.content.startswith("<!-- page 1 -->\nPERATURAN OTORITAS JASA KEUANGAN")
    main = next(file for file in content.extra["files"] if file["role"] == "main")
    assert (main["method"], main["extraction"]["engine"], main["extraction"]["pages_fallen_back"]) == (
        "text_layer", "mineru", 1,
    )


def test_with_the_default_engine_the_files_description_is_as_before(site, tmp_path) -> None:
    with source(site, tmp_path) as s:
        content = s.fetch_content(next(s.discover()))

    main = next(file for file in content.extra["files"] if file["role"] == "main")
    assert "extraction" not in main and "method" not in main
