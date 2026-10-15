"""The pure mapping from a held OJK document to a contract v2 document."""

from __future__ import annotations

from dataclasses import replace

from metamorph_contract import Kind, Relation, Rights, Status, TextOrigin, problems

from ojk_connector.contract_v2 import (
    HeldDocument,
    HeldText,
    indonesian_subject,
    national_law_id,
    normalized_document,
    old_style_number,
)

HEADER = (
    "OTORITAS JASA KEUANGAN\nREPUBLIK INDONESIA\nSALINAN\nPERATURAN OTORITAS JASA KEUANGAN\nNOMOR 5/POJK.05/2014\n"
    "TENTANG\nPERIZINAN USAHA DAN KELEMBAGAAN LEMBAGA\nPENJAMINAN\nDENGAN RAHMAT TUHAN YANG MAHA ESA\n..."
)
LAW = "Undang-Undang Nomor 21 Tahun 2011 tentang Otoritas Jasa Keuangan (Law No. 21 of 2011 on the FSA)"


def held(**changes) -> HeldDocument:
    document = HeldDocument(
        source_id="regalert:172f8b0a",
        source_url="https://regalert.today/document/172f8b0a",
        fetched_at="2026-10-13T02:34:23+00:00",
        canonical_id="ojk:pojk:2014:5",
        id_confidence="high",
        id_evidence="text_header='PERATURAN OTORITAS JASA KEUANGAN NOMOR 5/POJK.05/2014'",
        review_reason=None,
        title="POJK on Business Licensing and Institutional Framework of Guarantee Institutions",
        identifier="5/POJK.05/2014",
        standing=None,
        standing_checked_at=None,
        lineage={"status": "active", "based_on": [{"relation": "issued_under", "title": LAW, "uid": "law-uid"}]},
        texts=(HeldText("id", TextOrigin.SOURCE_TEXT, Rights.PUBLIC, HEADER),),
        source_fields={"uid": "172f8b0a"},
    )
    return replace(document, **changes)


def normalize(document: HeldDocument, targets: dict[str, str] | None = None):
    return normalized_document(document, connector_version="0.1.0", target_of=(targets or {}).get)


def test_a_resolved_regulation_meets_the_contract() -> None:
    document = normalize(held())

    assert problems(document) == []
    info = document.document
    assert (info.id, info.kind, info.citation) == ("ojk:pojk:2014:5", Kind.REGULATION, "POJK 5/POJK.05/2014")
    assert info.title == "PERIZINAN USAHA DAN KELEMBAGAAN LEMBAGA PENJAMINAN"  # the regulation's own words
    assert info.aliases == ("5/POJK.05/2014", "POJK 5/2014", "POJK Nomor 5 Tahun 2014")


def test_new_style_numbering_is_cited_with_tahun() -> None:
    document = normalize(held(canonical_id="ojk:padk:2026:11", identifier=None, texts=()))  # no text: no document

    assert document is None
    padk = normalize(held(canonical_id="ojk:padk:2026:11", identifier=None,
                          texts=(HeldText("id", TextOrigin.SOURCE_TEXT, Rights.PUBLIC, "PADK ..."),)))
    assert padk.document.citation == "PADK 11 Tahun 2026"
    assert padk.document.kind is Kind.IMPLEMENTING_RULE


def test_status_prefers_a_standing_check_over_the_lineage_read_with_the_text() -> None:
    assert normalize(held()).document.status.value is Status.IN_FORCE
    checked = normalize(held(standing="superseded", standing_checked_at="2026-10-12T09:00:00+00:00")).document.status
    assert (checked.value, checked.as_of, checked.source) == (Status.REVOKED, "2026-10-12", "regalert")
    assert normalize(held(lineage={})).document.status.value is Status.UNKNOWN


def test_a_document_to_review_loads_under_an_unresolved_id() -> None:
    document = normalize(held(canonical_id=None, id_confidence="none", review_reason="no confident canonical id"))

    assert problems(document) == []
    assert document.document.id.startswith("unresolved:ojk:")
    assert document.document.id_confidence.value == "none"


def test_an_id_collision_is_reported_as_a_conflict() -> None:
    collided = held(canonical_id=None, id_confidence="high", review_reason="canonical path already used by regalert:x")

    document = normalize(collided)

    assert document.document.id_confidence.value == "conflict"
    assert document.document.id_evidence.startswith("canonical path already used by regalert:x")


def test_lineage_becomes_citations_with_targets_we_can_name() -> None:
    lineage = {
        "based_on": [{"relation": "issued_under", "title": LAW, "uid": "law-uid", "date": "2011-11-22"}],
        "supersedes": [{"relation": "supersedes", "title": "POJK on P2P lending", "uid": "held-uid"}],
        "amends": [{"relation": "amends", "title": "Some regulation we do not hold", "uid": "unknown-uid"}],
    }

    citations = normalize(held(lineage=lineage), targets={"held-uid": "ojk:pojk:2016:77"}).citations

    assert [(c.relation, c.target_id) for c in citations] == [
        (Relation.LEGAL_BASIS, "id:uu:2011:21"),
        (Relation.AMENDS, None),
        (Relation.SUPERSEDES, "ojk:pojk:2016:77"),
    ]


def test_without_a_header_or_source_title_the_citation_is_the_title() -> None:
    untitled = held(title="", texts=(HeldText("id", TextOrigin.SOURCE_TEXT, Rights.PUBLIC, "a captured web page"),))

    assert normalize(untitled).document.title == "POJK 5/POJK.05/2014"


def test_the_subject_comes_from_a_circular_letter_too() -> None:
    letter = "SURAT EDARAN OTORITAS JASA KEUANGAN\nNOMOR 1 /SEOJK.04/2020\nTENTANG\nTATA CARA PENYAMPAIAN\nDalam rangka ..."

    assert indonesian_subject(letter) == "TATA CARA PENYAMPAIAN"
    assert old_style_number(letter) == "1/SEOJK.04/2020"
    assert indonesian_subject("A captured web page without a header") is None


def test_national_laws_get_issuer_namespaced_ids() -> None:
    assert national_law_id(LAW) == "id:uu:2011:21"
    assert national_law_id("Peraturan Pemerintah Nomor 9 Tahun 2018 tentang ...") == "id:pp:2018:9"
    assert national_law_id("Peraturan Bank Indonesia Nomor 8 Tahun 2026") is None


def test_a_law_named_after_an_english_title_is_still_recognised() -> None:
    title = "Capital Market Law, Law No. 8 of 1995 (Undang-Undang Nomor 8 Tahun 1995 tentang Pasar Modal)"

    assert national_law_id(title) == "id:uu:1995:8"
