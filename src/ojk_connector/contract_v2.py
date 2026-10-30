"""What one held OJK document looks like as a contract v2 `NormalizedDocument`.

Pure mapping: plain values in, a document out. No store, no files, no network, so every rule
here is tested on its own (tests/test_contract_v2.py). `export_v2.py` feeds it from the store.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from metamorph_contract import (
    Citation,
    Confidence,
    Dates,
    DocumentInfo,
    Extraction,
    ExtractionMethod,
    FileRef,
    FileRole,
    FoundIn,
    Kind,
    NormalizedDocument,
    Relation,
    Rights,
    SourceRef,
    Status,
    StatusClaim,
    TextOrigin,
    TextRef,
    canonical_id,
    unresolved_id,
)

from ojk_connector.identity import AGENCY, PRIMARY_LANGUAGE, CanonicalId, cited_id, indonesian_subject, title_subject
from ojk_connector.models import CatalogEntry, EntryRelation, RelationKind

CONNECTOR = "ojk"
STATUS_SOURCE = "regalert"

KIND_OF_FAMILY = {"pojk": Kind.REGULATION, "seojk": Kind.IMPLEMENTING_RULE, "padk": Kind.IMPLEMENTING_RULE}
# A standing check (fresh) wins over the lineage RegAlert sent with the text (as old as the read).
STATUS_OF_STANDING = {
    "unchanged": Status.IN_FORCE,
    "active": Status.IN_FORCE,
    "amended": Status.AMENDED,
    "superseded": Status.REVOKED,
}
RELATION_OF_LINEAGE = {"based_on": Relation.LEGAL_BASIS, "amends": Relation.AMENDS, "supersedes": Relation.SUPERSEDES}
RELATION_OF_ENTRY = {
    RelationKind.REVOKES: Relation.REVOKES,
    RelationKind.PARTLY_REVOKES: Relation.PARTLY_REVOKES,
    RelationKind.AMENDS: Relation.AMENDS,
    RelationKind.LEGAL_BASIS: Relation.LEGAL_BASIS,
}
SOURCE_METHODS = {TextOrigin.SOURCE_TEXT, TextOrigin.SOURCE_TRANSLATION}
UNSURE = {"none", "conflict"}

_HEADER_WINDOW = 3000
_OLD_STYLE_NUMBER = re.compile(r"\b(?P<number>\d{1,3}\s*/\s*(?:POJK|SEOJK|PADK)\s*\.\s*\d{2}\s*/\s*(?:19|20)\d{2})\b")
_NATIONAL_LAW = re.compile(
    r"\b(?P<form>Undang-Undang|Peraturan Pemerintah Pengganti Undang-Undang|Peraturan Pemerintah|Peraturan Presiden"
    r"|UU|Perppu|PP|Perpres)"
    r"\s+(?:Republik Indonesia\s+)?Nomor\s+(?P<number>\d+)\s+Tahun\s+(?P<year>\d{4})\b",
    re.I,
)
_NATIONAL_FAMILY = {
    "undang-undang": "uu",
    "uu": "uu",
    "peraturan pemerintah pengganti undang-undang": "perppu",
    "perppu": "perppu",
    "peraturan pemerintah": "pp",
    "pp": "pp",
    "peraturan presiden": "perpres",
    "perpres": "perpres",
}
# "Pasal 187", "Pasal 42 ayat (3)", "Pasal 20 ayat (1) huruf a" (the huruf is below what an address names)
_ARTICLE = re.compile(r"\bPasal\s+(?P<pasal>\d+[A-Z]?)(?:\s+ayat\s*\((?P<ayat>\d+[a-z]?)\))?")
_RANGE = re.compile(r"\bsampai dengan\b|\bs\.d\.?|\bhingga\b", re.I)


@dataclass(frozen=True)
class HeldText:
    language: str
    origin: TextOrigin
    rights: Rights
    content: str
    paged: bool = False  # pages marked with <!-- page N -->
    of_file: str | None = None  # sha256 of the file it was extracted from
    method: ExtractionMethod | None = None  # None: as its origin says (the source's own, or the text layer)
    extraction: Extraction | None = None  # how MinerU read it (contract 2.1)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HeldFile:
    """An official file we hold: always public, as an official publication."""

    sha256: str
    role: str  # main | annex | abstract | faq | other
    media_type: str
    source_url: str | None
    name: str
    pages: int | None = None
    method: ExtractionMethod | None = None  # how its text was read, when MinerU read it
    extraction: Extraction | None = None


@dataclass(frozen=True)
class HeldEntry:
    """A catalogue entry, and where it came from."""

    entry: CatalogEntry
    source: str  # e.g. "jdih_ojk"
    source_id: str
    url: str | None
    read_at: str  # ISO datetime


@dataclass(frozen=True)
class HeldDocument:
    """What the OJK store holds about one document, as plain values."""

    source_id: str  # "<source>:<id>", as OJKConnector names it, e.g. "regalert:172f…"
    source_url: str
    fetched_at: str
    canonical_id: str | None  # None when the document must be reviewed
    id_confidence: str
    id_evidence: str
    review_reason: str | None
    title: str  # the source's title (RegAlert's is English)
    identifier: str | None
    standing: str | None
    standing_checked_at: str | None
    lineage: Mapping[str, Any]
    texts: tuple[HeldText, ...]
    source_fields: Mapping[str, Any] = field(default_factory=dict)
    entry: HeldEntry | None = None  # the official catalogue's entry, when one was read
    files: tuple[HeldFile, ...] = ()


def normalized_document(
    held: HeldDocument, *, connector_version: str, target_of: Callable[[str], str | None]
) -> NormalizedDocument | None:
    """None when the document has no exportable text yet: the contract requires content.

    `target_of(uid)` gives the canonical ID of a document a lineage edge points to, if we hold it.
    An official catalogue entry, when there is one, outranks a tracker's lineage for status and links.
    """
    if not held.texts and not held.files:
        return None
    return NormalizedDocument(
        source=SourceRef(
            connector=CONNECTOR,
            connector_version=connector_version,
            source_id=held.source_id,
            url=held.source_url,
            fetched_at=held.fetched_at,
        ),
        document=_document_info(held),
        files=tuple(_file_ref(file) for file in held.files),
        texts=tuple(_text_ref(text) for text in held.texts),
        citations=tuple(_entry_citations(held.entry) if _has_relations(held.entry)
                        else _citations(held.lineage, target_of)),
        source_fields=_source_fields(held),
    )


def _document_info(held: HeldDocument) -> DocumentInfo:
    primary_text = next((t.content for t in held.texts if t.language == PRIMARY_LANGUAGE), "")
    subject = indonesian_subject(primary_text) or title_subject(held.entry.entry.title if held.entry else None)
    if held.canonical_id is None:
        return _unresolved_info(held, subject or held.title.strip() or held.identifier or held.source_id)
    cid = CanonicalId.parse(held.canonical_id)
    numbering = (old_style_number(held.identifier or "", cid.number, cid.year)
                 or old_style_number(primary_text[:_HEADER_WINDOW], cid.number, cid.year))
    family = cid.family.upper()
    citation = f"{family} {numbering}" if numbering else f"{family} {cid.number} Tahun {cid.year}"
    title = subject or held.title.strip() or citation  # the regulation's own words first
    return DocumentInfo(
        id=canonical_id(AGENCY, cid.family, cid.number, cid.year),
        id_confidence=Confidence(held.id_confidence),
        id_evidence=held.id_evidence,
        issuer=AGENCY,
        family=cid.family,
        number=str(cid.number),
        year=cid.year,
        citation=citation,
        title=title,
        language=PRIMARY_LANGUAGE,
        kind=KIND_OF_FAMILY[cid.family],
        status=_status(held),
        dates=Dates(**held.entry.entry.dates) if held.entry else Dates(),
        aliases=_aliases(family, cid, numbering, citation),
    )


def _unresolved_info(held: HeldDocument, title: str) -> DocumentInfo:
    """Loads anyway, under a placeholder ID, so a person can resolve it."""
    confidence = held.id_confidence if held.id_confidence in UNSURE else "conflict"
    return DocumentInfo(
        id=unresolved_id(CONNECTOR, held.source_id),
        id_confidence=Confidence(confidence),
        id_evidence=f"{held.review_reason}; {held.id_evidence}" if held.review_reason else held.id_evidence,
        issuer=AGENCY,
        family="unknown",
        number="unknown",
        year=None,
        citation=held.identifier or title,
        title=title,
        language=PRIMARY_LANGUAGE,
        kind=Kind.REGULATION,
        status=_status(held),
    )


def _status(held: HeldDocument) -> StatusClaim:
    if held.entry and held.entry.entry.standing:
        return StatusClaim(Status(held.entry.entry.standing), source=held.entry.source, as_of=held.entry.read_at[:10])
    checked = held.standing is not None
    value = held.standing if checked else held.lineage.get("status")
    return StatusClaim(
        value=STATUS_OF_STANDING.get(value or "", Status.UNKNOWN),
        source=STATUS_SOURCE,
        as_of=held.standing_checked_at[:10] if checked and held.standing_checked_at else None,
    )


def _aliases(family: str, cid: CanonicalId, numbering: str | None, citation: str) -> tuple[str, ...]:
    forms = {numbering, f"{family} {cid.number}/{cid.year}", f"{family} Nomor {cid.number} Tahun {cid.year}"}
    return tuple(sorted(form for form in forms if form and form != citation))


def _text_ref(text: HeldText) -> TextRef:
    return TextRef(
        sha256=text.sha256,
        language=text.language,
        origin=text.origin,
        method=text.method or _method_of(text.origin),
        rights=text.rights,
        paged=text.paged,  # RegAlert's texts have no page boundaries; texts from official PDFs do
        of_file=text.of_file,
        extraction=text.extraction,
    )


def _method_of(origin: TextOrigin) -> ExtractionMethod:
    """A text's method when nothing else says: the source's own, or the text layer of a PDF read here."""
    return ExtractionMethod.SOURCE if origin in SOURCE_METHODS else ExtractionMethod.TEXT_LAYER


def _file_ref(file: HeldFile) -> FileRef:
    return FileRef(sha256=file.sha256, role=FileRole(file.role), media_type=file.media_type,
                   source_url=file.source_url, rights=Rights.PUBLIC, title=file.name, pages=file.pages)


def _has_relations(held_entry: HeldEntry | None) -> bool:
    return held_entry is not None and any(r.kind in RELATION_OF_ENTRY for r in held_entry.entry.relations)


def _entry_citations(held_entry: HeldEntry | None) -> list[Citation]:
    """What the document does to others, and its legal basis, as the official catalogue lists them.

    Incoming relations ("revoked by …") are the other document's claim: its own entry reports them.
    """
    assert held_entry is not None
    citations = []
    for relation in held_entry.entry.relations:
        if relation.kind not in RELATION_OF_ENTRY:
            continue
        evidence = f"{held_entry.source} catalogue entry: {relation.kind.value}" + (
            f" ({relation.articles})" if relation.articles else "")
        target = cited_id(relation.target)
        for address in _addresses(relation) or [None]:
            citations.append(Citation(
                relation=RELATION_OF_ENTRY[relation.kind],
                target_text=relation.target,
                evidence=evidence,
                found_in=FoundIn.SOURCE_METADATA,
                confidence=Confidence.HIGH,  # the issuer's own catalogue
                target_id=canonical_id(AGENCY, target.family, target.number, target.year) if target
                else national_law_id(relation.target),
                to_address=address,
            ))
    return citations


def _addresses(relation: EntryRelation) -> list[str]:
    """Contract addresses of the provisions a relation names ("pasal-42/ayat-3"); none for a range."""
    if not relation.articles or _RANGE.search(relation.articles):
        return []
    addresses = []
    for found in _ARTICLE.finditer(relation.articles):
        address = f"pasal-{found['pasal'].lower()}" + (f"/ayat-{found['ayat']}" if found["ayat"] else "")
        if address not in addresses:
            addresses.append(address)
    return addresses


def _source_fields(held: HeldDocument) -> dict[str, Any]:
    """The primary record's own fields, plus the catalogue entry's own fields under ``catalog_entry``."""
    fields = dict(held.source_fields)
    if held.entry:
        fields["catalog_entry"] = {"source": held.entry.source, "source_id": held.entry.source_id,
                                   "url": held.entry.url, "read_at": held.entry.read_at,
                                   "fields": held.entry.entry.fields}
    return fields


def _citations(lineage: Mapping[str, Any], target_of: Callable[[str], str | None]) -> list[Citation]:
    citations = []
    for key, relation in RELATION_OF_LINEAGE.items():
        for edge in lineage.get(key) or []:
            title = (edge.get("title") or "").strip()
            if not title:
                continue
            citations.append(
                Citation(
                    relation=relation,
                    target_text=title,
                    evidence=f"RegAlert lineage '{edge.get('relation', key)}' ({edge.get('date') or 'undated'})",
                    found_in=FoundIn.SOURCE_METADATA,
                    confidence=Confidence.MEDIUM,  # RegAlert's lineage is useful but incomplete and sometimes mislabelled
                    target_id=target_of(edge.get("uid") or "") or national_law_id(title),
                )
            )
    return citations


def old_style_number(text: str, number: int | str | None = None, year: int | str | None = None) -> str | None:
    """'5/POJK.05/2014' from an identifier or header, spaces removed; None for new-style numbering.

    Given the document's own number and year, only a number with both: a header also names the regulations it
    amends, revokes or rests on, in the same style."""
    for found in _OLD_STYLE_NUMBER.finditer(text):
        compact = re.sub(r"\s+", "", found["number"])
        own_number, own_year = compact.split("/")[0], compact.split("/")[-1]
        if number is None or (int(own_number), int(own_year)) == (int(number), int(year)):
            return compact
    return None


def national_law_id(title: str) -> str | None:
    """id:uu:2011:21 from "… Undang-Undang Nomor 21 Tahun 2011 …"; laws themselves come from the BPK connector.

    Searched anywhere: lineage titles often lead with an English name and give the Indonesian one in brackets.
    """
    found = _NATIONAL_LAW.search(title)
    if not found:
        return None
    family = _NATIONAL_FAMILY[found["form"].lower()]
    return canonical_id("id", family, int(found["number"]), int(found["year"]))
