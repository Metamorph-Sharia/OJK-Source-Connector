"""JDIH OJK's listing and detail pages, parsed. Fixtures are real pages (fetched 2026-10-16)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ojk_connector.models import EntryRelation, RelationKind, Standing
from ojk_connector.sources.jdih.catalog import (
    UnexpectedPage,
    file_role,
    iso_date,
    parse_entry,
    parse_listing,
    standing_of,
    unknown_relation_labels,
)

FIXTURES = Path(__file__).parent / "fixtures" / "jdih"
TODAY = date(2026, 10, 16)


def entry(name: str):
    return parse_entry((FIXTURES / f"detail-{name}.html").read_bytes(), today=TODAY)


def test_the_listing_gives_every_regulation_of_a_sector_and_type() -> None:
    rows = parse_listing((FIXTURES / "listing-09-06.json").read_bytes())

    assert len(rows) == 19
    first = rows[0]
    assert first.guid == "009e5e55-ce9b-5f02-6b0b-2fdb55e5675a"
    assert first.url == "https://jdih.ojk.go.id/Web/ViewPeraturan/Detail/009e5e55-ce9b-5f02-6b0b-2fdb55e5675a/09/06"
    assert first.title.startswith("Peraturan Otoritas Jasa Keuangan Republik Indonesia Nomor 8 Tahun 2026 tentang")
    assert first.published == "2026-07-06"
    assert first.fields | {"sector": "…"} == {
        "number": "8", "sector": "…", "sector_code": "09", "classification": "", "sub_classification": "",
        "type": "Peraturan OJK", "type_code": "06", "date": "06-07-2026", "status": "Berlaku",
    }


def test_a_listing_that_is_not_json_is_an_unexpected_page() -> None:
    with pytest.raises(UnexpectedPage):
        parse_listing(b"<html>Request Rejected</html>")


def test_a_recent_regulation_gives_dates_standing_history_legal_basis_and_files() -> None:
    pojk = entry("pojk-8-2026")

    assert pojk.title.startswith("Peraturan Otoritas Jasa Keuangan Republik Indonesia Nomor 8 Tahun 2026")
    assert pojk.standing is Standing.IN_FORCE
    assert pojk.dates == {"enacted": "2026-06-12", "promulgated": "2026-07-06", "effective": "2026-07-06"}
    # Revoking one article of POJK 40/2024 is a partial revocation.
    assert pojk.relations[0] == EntryRelation(
        RelationKind.PARTLY_REVOKES,
        "Peraturan Otoritas Jasa Keuangan Republik Indonesia Nomor 40 Tahun 2024 tentang Layanan Pendanaan "
        "Bersama Berbasis Teknologi Informasi",
        "Pasal 187",
    )
    # The last Landasan Hukum link is left unclosed by JDIH: it is still read once.
    assert [r.target for r in pojk.relations if r.kind is RelationKind.LEGAL_BASIS] == [
        "UU Nomor 21 Tahun 2011 tentang Otoritas Jasa Keuangan",
        "UU Nomor 4 Tahun 2023 tentang Pengembangan dan Penguatan Sektor Keuangan",
        "UU Nomor 4 Tahun 2026 tentang Perubahan atas Undang-Undang Nomor 4 Tahun 2023 tentang Pengembangan dan "
        "Penguatan Sektor Keuangan",
    ]
    assert [(f.role, f.name) for f in pojk.files] == [
        ("main", "2026pojk008.pdf"), ("abstract", "2026abspojk008.pdf"), ("faq", "2026faqpojk008.pdf")]
    assert pojk.files[0].url == "https://jdih.ojk.go.id/Web/ViewPeraturan/DownloadDokumen/ec82dc08-aced-3cee-8a4e-c3a06e01d085"
    assert pojk.fields["Sumber LN"] == "2026 (16/OJK) : 18 HLM."
    assert pojk.fields["Subjek"].startswith("PVML - PELAPORAN")
    assert unknown_relation_labels(pojk) == set()


def test_a_revoked_regulation_lists_what_it_revoked_and_what_revoked_it() -> None:
    pojk = entry("pojk-62-2020")

    assert pojk.standing is Standing.REVOKED
    assert pojk.dates == {"enacted": "2020-12-16", "promulgated": "2020-12-18"}  # no "Berlaku Sejak": no effective date
    kinds = [(r.kind, r.target) for r in pojk.relations if r.kind is not RelationKind.LEGAL_BASIS]
    assert kinds == [
        (RelationKind.REVOKES, "POJK Nomor 20/POJK.03/2014"),
        (RelationKind.REVOKES, "SEOJK 16/SEOJK.03/2015"),
        (RelationKind.PARTLY_REVOKES, "POJK Nomor 12/POJK.03/2016"),
        (RelationKind.REVOKES, "PBI Nomor 10/9/PBI/2008"),
        (RelationKind.REVOKES, "Surat Edaran Bank Indonesia Nomor 12/36/DPNP"),
        (RelationKind.REVOKED_BY, "POJK Nomor 7 Tahun 2024"),
    ]
    # Older entries leave the file label blank: the name says it is the summary.
    assert [(f.role, f.name) for f in pojk.files] == [("main", "POJK 62-2020.pdf"),
                                                      ("abstract", "Ringkasan POJK 62-2020.pdf")]


def test_partial_revocation_and_amendment_of_this_regulation_name_the_articles() -> None:
    partly = entry("pojk-21-2014")
    amended = entry("pojk-27-2015")

    assert partly.standing is Standing.PARTLY_REVOKED
    assert EntryRelation(RelationKind.PARTLY_REVOKED_BY, "POJK Nomor 12 Tahun 2019",
                         "Pasal 42 ayat (3) dan Pasal 42 ayat (5)") in partly.relations
    assert amended.standing is Standing.AMENDED
    assert EntryRelation(RelationKind.AMENDED_BY, "POJK Nomor 25 Tahun 2016", "Pasal 20 ayat (1) huruf a") in amended.relations
    assert [f.role for f in amended.files] == ["main", "annex", "annex"]


def test_the_view_counter_never_changes_the_fingerprint() -> None:
    page = (FIXTURES / "detail-pojk-8-2026.html").read_bytes()
    recounted = page.replace(b"diakses 113 kali", b"diakses 114 kali")

    first, second = parse_entry(page, today=TODAY), parse_entry(recounted, today=TODAY)
    assert first.snapshot.content != second.snapshot.content
    assert first.fingerprint == second.fingerprint


def test_a_page_that_is_not_a_detail_page_is_unexpected() -> None:
    with pytest.raises(UnexpectedPage):
        parse_entry(b"<html><body>The requested URL was rejected.</body></html>", today=TODAY)


IN_FORCE_HISTORY: frozenset[RelationKind] = frozenset()


@pytest.mark.parametrize(
    ("status", "incoming", "expected"),
    [
        ("Berlaku Sejak Tanggal 06-07-2026", IN_FORCE_HISTORY, Standing.IN_FORCE),
        ("Berlaku Sejak Tanggal 01-01-2027", IN_FORCE_HISTORY, Standing.NOT_YET_IN_FORCE),
        ("Tidak Berlaku", {RelationKind.REVOKED_BY}, Standing.REVOKED),
        ("Tidak Berlaku Sejak Tanggal 01-07-2024", {RelationKind.REVOKED_BY}, Standing.REVOKED),
        ("Berlaku (Dicabut Sebagian)", IN_FORCE_HISTORY, Standing.PARTLY_REVOKED),
        ("Berlaku (Perubahan) (Diubah)", IN_FORCE_HISTORY, Standing.AMENDED),
        ("Berlaku (Perubahan) (Mengubah) Sejak Tanggal 01-12-2024", IN_FORCE_HISTORY, Standing.IN_FORCE),
        # JDIH's status field is coarser than its own history (94 entries, 2026-10-16): the history decides.
        ("Berlaku", {RelationKind.AMENDED_BY}, Standing.AMENDED),
        ("Berlaku", {RelationKind.PARTLY_REVOKED_BY}, Standing.PARTLY_REVOKED),
        ("Berlaku", {RelationKind.AMENDED_BY, RelationKind.PARTLY_REVOKED_BY}, Standing.PARTLY_REVOKED),
        ("Berlaku", {RelationKind.REVOKED_BY}, Standing.IN_FORCE),  # 2 entries: the status field is the claim
        ("Ditangguhkan", IN_FORCE_HISTORY, None),  # unknown wording: never guessed
    ],
)
def test_standing(status: str, incoming: frozenset[RelationKind], expected: Standing | None) -> None:
    assert standing_of(status, incoming, TODAY) is expected


def test_the_date_a_regulation_stopped_being_in_force_is_not_its_effective_date() -> None:
    page = (FIXTURES / "detail-pojk-62-2020.html").read_bytes().replace(
        b"Tidak Berlaku&nbsp;</label>", b"Tidak Berlaku&nbsp;Sejak Tanggal 01-07-2024</label>")
    assert "effective" not in parse_entry(page, today=TODAY).dates


@pytest.mark.parametrize(
    ("label", "name", "role"),
    [
        ("Peraturan", "2026pojk008.pdf", "main"),
        ("Abstrak", "2026abspojk008.pdf", "abstract"),
        ("FAQ", "2026faqpojk008.pdf", "faq"),
        ("Peraturan", "Lampiran POJK 5-2014.pdf", "annex"),
        ("", "ringkasan seojk 8 - lapbul BPR.pdf", "abstract"),
        ("", "2024abspojk023.pdf", "abstract"),
        ("", "lampiran1pojktrust.pdf", "annex"),
        ("", "seojk 8-2019.pdf", "main"),
        # Older entries label nothing; summaries and FAQs are named in many ways (real names, 2026-10-16).
        ("", "summary pojk 42 - LPIP.pdf", "abstract"),
        ("", "Ringsek Tata Kelola Remunerasi BUS dan UUS R1.pdf", "abstract"),
        ("", "Ringkasan Eksekutif POJK 11 - 2020.pdf", "abstract"),
        ("", "Frequently Asked Question POJK Nomor 9 Tahun 2019.pdf", "faq"),
        ("", "FAQ R1.pdf", "faq"),
    ],
)
def test_file_role(label: str, name: str, role: str) -> None:
    assert file_role(label, name) == role


@pytest.mark.parametrize(("value", "expected"), [("06-07-2026", "2026-07-06"), ("", None), ("31-02-2024", None)])
def test_iso_date(value: str, expected: str | None) -> None:
    assert iso_date(value) == expected
