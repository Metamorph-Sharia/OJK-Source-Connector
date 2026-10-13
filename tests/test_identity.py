"""OJK identity rules, tested on real RegAlert records (listed 2026-10-12) unless marked otherwise."""

from __future__ import annotations

import pytest

from ojk_connector import identity
from ojk_connector.identity import _from_slug, _from_title
from ojk_connector.models import DiscoveredDocument

PAGES = "https://www.ojk.go.id/id/regulasi/Pages/"


def doc(title: str = "", slug: str | None = None, identifier: str | None = None, instrument_no: str | None = None):
    return DiscoveredDocument(
        source="regalert", source_id="x", title=title, published=None, source_url=None,
        official_url=f"{PAGES}{slug}.aspx" if slug else None,
        metadata={"identifier": identifier, "instrument_no": instrument_no},
    )


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("PADK-11-Tahun-2026-Pelaksanaan-Penawaran-Umum-Efek-Bersifat-Utang-danatau-Sukuk-secara-Elektronik", ("padk", 11, 2026)),
        ("POJK-Nomor-16-Tahun-2026-tentang-Penyelenggaraan-Bursa-Mineral-dan-Komoditas-Strategis", ("pojk", 16, 2026)),
        ("POJK-10-Tahun-2026-Perubahan-Atas-POJK-14-Tahun-2023-tentang-Perdagangan-Karbon-Melalui-Bursa-Karbon", ("pojk", 10, 2026)),
        ("POJK-35-Tahun-2025-Perubahan-POJK-46-Tahun-2024", ("pojk", 35, 2025)),
        ("48-PADK06-2025-Penerapan-Manajemen-Risiko-Bagi-Lembaga-PVML", ("padk", 48, 2025)),
        ("PADK-45-PADK06-2025-Laporan-Bulanan-Perusahaan-Pembiayaan-dan-Perusahaan-Pembiayaan-Syariah", ("padk", 45, 2025)),
        ("PADK-Nomor-3-Tahun-2026-Penyelenggaraan-Perdagangan-Aset-Keuangan-Digital-termasuk-Aset-Kripto", ("padk", 3, 2026)),
        ("SEOJK-35-SEOJK05-2025-Penilaian-Tingkat-Kesehatan-Perusahaan-Pergadaian-dan-Perusahaan-Pergadaian-Syariah", ("seojk", 35, 2025)),
        ("33-SEOJK03-2025-Pelaporan-BUS-dan-UUS-melalui-Sistem-Pelaporan-OJK", ("seojk", 33, 2025)),
        ("SEOJK-4SEOJK072025-Pelaporan-Penyelenggara-Inovasi-Teknologi-Sektor-Keuangan-Yang-Memiliki-Izin-Usaha-di-OJK", ("seojk", 4, 2025)),
        ("1SEOJK062025-Laporan-Keuangan-Lembaga-Keuangan-Mikro", ("seojk", 1, 2025)),
        ("12SEOJK052025-Sertifikasi-Kompetensi-Kerja-bagi-PPDP", ("seojk", 12, 2025)),
        ("SEOJK-14-SEOJK032025-Penerapan-Tata-Kelola-Bagi-Bank-Umum", ("seojk", 14, 2025)),
        ("SEOJK-15-SEOJK.03-2024-Penerapan-Tata-Kelola-Syariah-bagi-Bank-Umum-Syariah-dan-Unit-Usaha-Syariah", ("seojk", 15, 2024)),
        ("POJK-Tahun-14-2024-Satuan-Tugas-Penanganan-Kegiatan-Usaha-Tanpa-Izin-di-Sektor-Keuangan", ("pojk", 14, 2024)),
        ("POJK-1-2025-Derivatif-Keuangan-dengan-Aset-yang-Mendasari-Berupa-Efek", ("pojk", 1, 2025)),
        ("Peraturan-Otoritas-Jasa-Keuangan-Nomor-12-Tahun-2023-tentang-Unit-Usaha-Syariah-(POJK-UUS)", ("pojk", 12, 2023)),
        # end-anchored
        ("Pengawasan-PT-Sarana-Multi-Infrastruktur-Persero-POJK-16-2024", ("pojk", 16, 2024)),
        ("Penyusunan-Laporan-Keuangan-Perusahaan-Efek-(POJK-Nomor-20-POJK.04-2021)", ("pojk", 20, 2021)),
        ("Pengembangan-Kualitas-Sumber-Daya-Manusia-Bank-Perekonomian-Rakyat-dan-Bank-Perekonomian-Rakyat-Syariah-(POJK-19-Tahu-2023)", ("pojk", 19, 2023)),
        ("Lembaga-Pengelola-Informasi-Perkreditan-SEOJK-27-2022", ("seojk", 27, 2022)),
        ("Pembelian-Kembali-Saham-Perusahaan-Terbuka-Sebagai-Akibat-Dibatalkannya-Pencatatan-Efek-oleh-Bursa-Efek-SEOJK-13-04-2023", ("seojk", 13, 2023)),
        ("Mekanisme-dan-Tata-Cara-Pemisahan-Unit-Syariah-Perusahaan-Asuransi-dan-Perusahaan-Reasuransi-10SEOJK052024", ("seojk", 10, 2024)),
        ("Pelaporan-dan-Permintaan-Informasi-Debitur-Melalui-Sistem-Layanan-Informasi-Keuangan-11-SEOJK01-2024", ("seojk", 11, 2024)),
    ],
)
def test_slug(slug: str, expected: tuple[str, int, int]) -> None:
    c = _from_slug(PAGES + slug + ".aspx")
    assert c is not None and (c.family, c.number, c.year) == expected


@pytest.mark.parametrize(
    "slug",
    [
        # Amendment slugs end with the instrument being amended, not the document itself.
        "Perubahan-Atas-Surat-Edaran-Otoritas-Jasa-Keuangan-Nomor-20-SEOJK-04-2021",  # is SEOJK 4/2022
        "Perubahan-atas-SEOJK-Nomor-19.SEOJK.04.2018-tentang-Laporan-Penerapan-Tata-Kelola-Manajer-Investasi",  # SEOJK 9/2023
        "SEOJK-20325-Kewajiban-Penyediaan-Modal-Minimum-dan-Pemenuhan-Modal-Inti-Minimum-Bagi-BPR",  # ambiguous
        "POJK-tentang-Pedoman-Transaksi-Repurchase-Agreement-bagi-Lembaga-Jasa-Keuangan",
        "Penyelenggaraan-Usaha-Dana-Pensiun",
    ],
)
def test_slug_without_reliable_id(slug: str) -> None:
    assert _from_slug(PAGES + slug + ".aspx") is None


def test_slug_without_family_gets_it_from_instrument_no() -> None:
    r = identity.resolve_id(doc(
        title="Management of Track Record Information of Actors through the Actor Information System",
        slug="28-Tahun-2024-Pengelolaan-Informasi-Rekam-Jejak-Pelaku-Melalui-Sistem-Informasi-Pelaku",
        instrument_no="ojk:regulation:28:2024",
    ))
    assert str(r.id) == "ojk:pojk:2024:28" and r.confidence == "low"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Financial Services Authority Regulation Number 77/POJK.01/2016 Concerning Information Technology-Based Money Lending Services", ("pojk", 77, 2016)),
        ("OJK Regulation No. 45/PADK.06/2025 on Monthly Reports of Financing Companies and Shariah Financing Companies", ("padk", 45, 2025)),
        ("OJK Circular No. 36/SEOJK.06/2025 on the Implementation of the Micro Waqf Program", ("seojk", 36, 2025)),
        ("Regulation of the Board of Commissioners of the Financial Services Authority Number 2 of 2026 Concerning the Implementation of Buy Now Pay Later for Financing Companies and Sharia Financing Companies", ("padk", 2, 2026)),
        ("OJK Regulation No. 3 of 2026 on the Conduct of Securities Company Business Activities as Underwriters and Securities Trading Intermediaries", ("pojk", 3, 2026)),
        ("POJK No. 41 of 2025 on Representative Offices of Financing Institutions, Venture Capital Companies, and Other Financial Service Institutions Headquartered Abroad", ("pojk", 41, 2025)),
        ("Financial Services Authority Regulation Number 63/POJK.05/2016 Regarding Amendments to Financial Services Authority Regulation Number 11/POJK.05/2014 Regarding Direct Examinations of Non-Bank Financial Institutions", ("pojk", 63, 2016)),
        ("Bank Accounting Guidelines for Rural Banks (SEOJK No. 21/SEOJK.03/2024)", ("seojk", 21, 2024)),
        ("Circular Letter of the Financial Services Authority Number 10/SEOJK.03/2014 Regarding the Assessment of the Health Level of Sharia Commercial Banks and Sharia Business Units", ("seojk", 10, 2014)),
    ],
)
def test_title(title: str, expected: tuple[str, int, int]) -> None:
    c = _from_title(title)
    assert c is not None and (c.family, c.number, c.year) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Amendment to OJK Circular No. 25/SEOJK.05/2019 on Monthly Reports of Venture Capital and Sharia Venture Capital Companies",
        "Second Amendment to Financial Services Authority Regulation Number 4/POJK.04/2014 on the Procedure for Collecting Administrative Sanctions",
        "Use of Foreign Workers and Knowledge Transfer Programs by Commercial Banks",
    ],
)
def test_title_without_own_id(title: str) -> None:
    assert _from_title(title) is None


def test_padk_and_pojk_with_the_same_number_get_different_ids() -> None:
    # RegAlert gives both the same instrument_no "ojk:regulation:11:2026".
    padk = identity.resolve_id(doc(
        title="Implementation of Electronic Public Offering of Debt Securities and/or Sukuk",
        slug="PADK-11-Tahun-2026-Pelaksanaan-Penawaran-Umum-Efek-Bersifat-Utang-danatau-Sukuk-secara-Elektronik",
        identifier="PADK 11 Tahun 2026", instrument_no="ojk:regulation:11:2026",
    ))
    pojk = identity.resolve_id(doc(
        title="Financial Services Authority Regulation Number 11 of 2026 Concerning Periodic Reports of Guarantee Institutions",
        slug="POJK-11-Tahun-2026-Laporan-Berkala-Lembaga-Penjamin", instrument_no="ojk:regulation:11:2026",
    ))
    assert str(padk.id) == "ojk:padk:2026:11" and padk.confidence == "high"
    assert str(pojk.id) == "ojk:pojk:2026:11" and pojk.confidence == "high"


def test_amendment_resolves_to_itself_not_the_amended_instrument() -> None:
    r = identity.resolve_id(doc(
        title="Amendment to Circular Letter Number 20/SEOJK.04/2021",  # title form assumed; slug and instrument_no are real
        slug="Perubahan-Atas-Surat-Edaran-Otoritas-Jasa-Keuangan-Nomor-20-SEOJK-04-2021",
        instrument_no="ojk:seojk-circular-letter:4:2022",
    ))
    assert str(r.id) == "ojk:seojk:2022:4" and r.confidence == "low"


def test_identifier_wins_and_agreeing_evidence_keeps_high_confidence() -> None:
    r = identity.resolve_id(doc(
        title="Financial Services Authority Regulation Number 34/POJK.05/2015 on Business Licensing and Institutional Organization of Venture Capital Companies",
        identifier="34/POJK.05/2015", instrument_no="ojk:pojk.05-regulation:34:2015",
    ))
    assert str(r.id) == "ojk:pojk:2015:34" and r.confidence == "high"


def test_disagreeing_medium_evidence_is_a_conflict() -> None:
    # Constructed: slug and title name different documents.
    r = identity.resolve_id(doc(
        title="OJK Regulation No. 33 of 2025 on the Health Assessment of Insurance Companies",
        slug="POJK-32-Tahun-2025-Penyelenggaraan-Beli-Sekarang-Bayar-Nanti-BNPL",
    ))
    assert str(r.id) == "ojk:pojk:2025:32" and r.confidence == "conflict"
    assert "disagrees" in r.evidence


def test_old_identifier_with_dot_separator() -> None:
    r = identity.resolve_id(doc(identifier="10.SEOJK.03/2014"))
    assert str(r.id) == "ojk:seojk:2014:10"


def test_unknown_family_is_unresolved() -> None:
    r = identity.resolve_id(doc(title="Securities Financing", instrument_no="ojk:bl-decision:258:2008"))
    assert r.id is None and r.confidence == "none"


# --- the document's own header -------------------------------------------------

POJK_1_2026_HEADER = """PERATURAN OTORITAS JASA KEUANGAN
REPUBLIK INDONESIA
NOMOR 1 TAHUN 2026
TENTANG
PENGGUNAAN TENAGA KERJA ASING DAN PROGRAM ALIH
PENGETAHUAN OLEH BANK UMUM
DENGAN RAHMAT TUHAN YANG MAHA ESA
DEWAN KOMISIONER OTORITAS JASA KEUANGAN,
Menimbang : a. bahwa untuk meningkatkan ketangguhan,
c. bahwa Peraturan Otoritas Jasa Keuangan
Nomor 37/POJK.03/2017 tentang Pemanfaatan"""  # real (RegAlert original text of POJK 1/2026)


def test_header_new_style() -> None:
    r = identity.resolve_id_from_text(POJK_1_2026_HEADER)
    assert str(r.id) == "ojk:pojk:2026:1" and r.confidence == "high"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("PERATURAN OTORITAS JASA KEUANGAN\nNOMOR 77 /POJK.01/2016\nTENTANG\nLAYANAN PINJAM MEMINJAM", "ojk:pojk:2016:77"),
        ("SURAT EDARAN OTORITAS JASA KEUANGAN\nNOMOR 35 /SEOJK.06/2025\nTENTANG\nPENILAIAN TINGKAT KESEHATAN", "ojk:seojk:2025:35"),
        ("PERATURAN ANGGOTA DEWAN KOMISIONER\nOTORITAS JASA KEUANGAN\nNOMOR 2 TAHUN 2026\nTENTANG\n"
         "Mengingat Peraturan Otoritas Jasa Keuangan Nomor 32 Tahun 2025", "ojk:padk:2026:2"),
        ("SURAT EDARAN OTORITAS JASA KEUANGAN\nNOMOR 9 /SEOJK.04/2023\nTENTANG\nPERUBAHAN ATAS SURAT EDARAN "
         "OTORITAS JASA KEUANGAN NOMOR 19/SEOJK.04/2018", "ojk:seojk:2023:9"),
    ],
)
def test_header_other_forms(header: str, expected: str) -> None:
    # Constructed from the standard header layout; to be checked against real texts after the first live run.
    assert str(identity.resolve_id_from_text(header).id) == expected


def test_no_header() -> None:
    assert identity.resolve_id_from_text("Lampiran I\nTabel 1").id is None


# --- found on the first live run (2026-10-12) -------------------------------------

@pytest.mark.parametrize(
    "title",
    [
        # The citation names the instrument being amended or revoked, mid-title.
        "POJK on Amendment to Financial Services Authority Regulation Number 11/POJK.03/2016 on Minimum Capital "
        "Provision Requirements for Commercial Banks",
        "SEOJK Regarding the Revocation of SEOJK Number 27/SEOJK.04/2015 on Accounting Treatment for Leased "
        "Telecommunication Tower Assets",
        "POJK on Amendments to Financial Services Authority Regulation Number 27/POJK.03/2015 on Bank Business "
        "Activities Involving Trusts",
    ],
)
def test_title_citing_the_amended_instrument_is_ignored(title: str) -> None:
    assert _from_title(title) is None


def test_amendment_with_identifier_is_no_longer_a_conflict() -> None:
    r = identity.resolve_id(doc(
        title="POJK on Amendment to Financial Services Authority Regulation Number 11/POJK.03/2016 on Minimum "
              "Capital Provision Requirements for Commercial Banks",
        slug="POJK-tentang-Perubahan-atas-POJK-Nomor-11POJK032016-tentang-Kewajiban-Penyediaan-Modal-Minimum-Bank-Umum",
        identifier="34/POJK.03/2016", instrument_no="ojk:pojk.03-regulation:34:2016",
    ))
    assert str(r.id) == "ojk:pojk:2016:34" and r.confidence == "high"


# Real RegAlert "original text" for SEOJK 8/2023: a capture of the ojk.go.id page, not the instrument.
SEOJK_8_2023_PAGE = (
    "Regulasi  /\n                        \n                            Pencarian Regulasi  /\n"
    "Penyampaian Informasi oleh Emiten dan Perusahaan Publik dalam Rangka Penyusunan Daftar Efek Syariah\n\n"
    "Sektor :\nPasar Modal\n\nSubSektor :\nPasar Modal Syariah; Emiten dan Perusahaan Publik\n\n"
    "Jenis Regulasi :\nSurat Edaran OJK\n\nNomor Regulasi :\n8/SEOJK.04/2023\n\nTanggal Berlaku :\n3/2/2023\n\n"
    "Lampiran 1\n​\nSEOJK Nomor 8 SEOJK.04 2023.pdf\nRingkasan SEOJK Nomor 8 SEOJK.04 2023.pdf\n\n"
    "Page Content\n​\nRINGKASAN\nSURAT EDARAN OTORITAS JASA KEUANGAN\nTENTANG\n"
    "PENYAMPAIAN INFORMASI OLEH EMITEN ATAU PERUSAHAAN PUBLIK\nDALAM RANGKA PENYUSUNAN DAFTAR EFEK SYARIAH\n"
    "Surat Edaran Otoritas Jasa K\neuangan (SEOJK) tentang Penyampaian Informasi Oleh Emiten Atau Perusahaan "
    "Publik Dalam Rangka Penyusunan Daftar Efek Syariah diterbitkan dalam rangka:\npelaksanaan ketentuan "
    "Peraturan Otoritas Jasa Keuangan Nomor 35/POJK.04/2017 tentang Kriteria dan Penerbitan Daftar Efek Syariah"
)


def test_page_capture_is_recognised_and_not_mistaken_for_the_cited_regulation() -> None:
    r = identity.resolve_id_from_text(SEOJK_8_2023_PAGE)
    assert str(r.id) == "ojk:seojk:2023:8"  # not ojk:pojk:2017:35, which the summary cites
    assert r.evidence.startswith("ojk_page=")
    assert identity.assess_text(SEOJK_8_2023_PAGE) == "page"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Real openings: a circular letter starts with its addressee; a PADK with "SALINAN".
        ("Yth.\nDireksi Penyelenggara Bursa Karbon\ndi tempat.\nSALINAN\nSURAT EDARAN OTORITAS JASA KEUANGAN\n"
         "REPUBLIK INDONESIA\nNOMOR 12/SEOJK.04/2023\nTENTANG\nTATA CARA PENYELENGGARAAN PERDAGANGAN KARBON MELALUI "
         "BURSA\nKARBON\nDalam rangka melaksanakan ketentuan Peraturan Otoritas Jasa Keuangan\nNomor 14 Tahun 2023",
         "ojk:seojk:2023:12"),
        ("SALINAN\nPERATURAN ANGGOTA DEWAN KOMISIONER\nOTORITAS JASA KEUANGAN\nREPUBLIK INDONESIA\nNOMOR 11 TAHUN 2026\n"
         "TENTANG\nPELAKSANAAN PENAWARAN UMUM EFEK BERSIFAT UTANG\nDAN/ATAU SUKUK SECARA ELEKTRONIK\n"
         "Menimbang : a. bahwa untuk melaksanakan amanat Pasal 33 ayat (3),\nPeraturan Otoritas Jasa Keuangan Nomor\n"
         "41/POJK.04/2020 tentang Pelaksanaan Kegiatan", "ojk:padk:2026:11"),
    ],
)
def test_real_headers(text: str, expected: str) -> None:
    assert str(identity.resolve_id_from_text(text).id) == expected
    assert identity.assess_text(text) == "instrument"


def test_citation_in_body_text_is_not_a_header() -> None:
    body = "Lampiran\nDalam rangka melaksanakan ketentuan Peraturan Otoritas Jasa Keuangan Nomor 14 Tahun 2023 " * 3
    assert identity.resolve_id_from_text(body).id is None
    assert identity.assess_text(body) == "unknown"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Real header variants in older documents (RegAlert original texts, 2026-10-12 backfill).
        ("OTORITAS JASA KEUANGAN REPUBLIK INDONESIA SALINAN PERATURAN OTORITAS JASA KEUANGAN NOMOR: 1/POJK.07/2013 "
         "TENTANG PERLINDUNGAN KONSUMEN SEKTOR JASA KEUANGAN", "ojk:pojk:2013:1"),
        ("SALINAN PERATURAN OTORITAS JASA KEUANGAN NOMOR 6 POJK.04/2017 TENTANG PERLAKUAN AKUNTANSI", "ojk:pojk:2017:6"),
        ("Yth. Direksi Emiten dan Perusahaan Publik di tempat. SALINAN SURAT EDARAN OTORITAS JASA KEUANGAN "
         "NOMOR: 3/SEOJK.04/2020 TENTANG KONDISI LAIN", "ojk:seojk:2020:3"),
        ("Yth. Wakil Manajer Investasi di tempat. SALINAN SURAT EDARAN OTORITAS JASA KEUANGAN NOMOR.116 "
         "/SEOJK.04/2016.. TENTANG PENGAKUAN TERHADAP ASOSIASI", "ojk:seojk:2016:116"),
    ],
)
def test_older_header_variants(text: str, expected: str) -> None:
    assert str(identity.resolve_id_from_text(text).id) == expected
    assert identity.assess_text(text) == "instrument"


@pytest.mark.parametrize(
    "text",
    [
        # Real: texts that start with an annex or a guide, not the instrument.
        "Lampiran I SEOJK Nomor: 2/SEOJK.05/2013 Tanggal: 27 Agustus 2013 Halaman i K e p a d a Yth. Otoritas Jasa",
        "BAGIAN II LAPORAN KEUANGAN BANK SYARIAH II.1. KETENTUAN UMUM A. Tujuan Laporan Keuangan",
        "PEMISAHAN PANDUAN UNIT USAHA SYARIAH Direktorat Pengaturan dan Perizinan Perbankan Syariah",
    ],
)
def test_annex_first_texts_stay_unknown(text: str) -> None:
    assert identity.assess_text(text) == "unknown"
