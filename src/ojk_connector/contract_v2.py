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
    DocumentInfo,
    ExtractionMethod,
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

from ojk_connector.identity import AGENCY, PRIMARY_LANGUAGE, CanonicalId

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
SOURCE_METHODS = {TextOrigin.SOURCE_TEXT, TextOrigin.SOURCE_TRANSLATION}
UNSURE = {"none", "conflict"}

_HEADER_WINDOW = 3000
_SUBJECT = re.compile(
    r"\bTENTANG\s+(?P<subject>.+?)\s+(?:DENGAN RAHMAT|Dalam rangka|Sehubungan dengan|Menimbang|MEMUTUSKAN)", re.S
)
_OLD_STYLE_NUMBER = re.compile(r"\b(?P<number>\d{1,3}\s*/\s*(?:POJK|SEOJK|PADK)\s*\.\s*\d{2}\s*/\s*(?:19|20)\d{2})\b")
_NATIONAL_LAW = re.compile(
    r"\b(?P<form>Undang-Undang|Peraturan Pemerintah Pengganti Undang-Undang|Peraturan Pemerintah|Peraturan Presiden)"
    r"\s+(?:Republik Indonesia\s+)?Nomor\s+(?P<number>\d+)\s+Tahun\s+(?P<year>\d{4})\b",
    re.I,
)
_NATIONAL_FAMILY = {
    "undang-undang": "uu",
    "peraturan pemerintah pengganti undang-undang": "perppu",
    "peraturan pemerintah": "pp",
    "peraturan presiden": "perpres",
}


@dataclass(frozen=True)
class HeldText:
    language: str
    origin: TextOrigin
    rights: Rights
    content: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


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


def normalized_document(
    held: HeldDocument, *, connector_version: str, target_of: Callable[[str], str | None]
) -> NormalizedDocument | None:
    """None when the document has no exportable text yet: the contract requires content.

    `target_of(uid)` gives the canonical ID of a document a lineage edge points to, if we hold it.
    """
    if not held.texts:
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
        files=(),
        texts=tuple(_text_ref(text) for text in held.texts),
        citations=tuple(_citations(held.lineage, target_of)),
        source_fields=dict(held.source_fields),
    )


def _document_info(held: HeldDocument) -> DocumentInfo:
    primary_text = next((t.content for t in held.texts if t.language == PRIMARY_LANGUAGE), "")
    subject = indonesian_subject(primary_text)
    if held.canonical_id is None:
        return _unresolved_info(held, subject or held.title.strip() or held.identifier or held.source_id)
    cid = CanonicalId.parse(held.canonical_id)
    numbering = old_style_number(held.identifier or "") or old_style_number(primary_text[:_HEADER_WINDOW])
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
        method=ExtractionMethod.SOURCE if text.origin in SOURCE_METHODS else ExtractionMethod.TEXT_LAYER,
        rights=text.rights,
        paged=False,  # RegAlert's text has no page boundaries; the official PDF (roadmap M3) will
    )


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


def indonesian_subject(text: str) -> str | None:
    """The "TENTANG …" subject from a regulation's own header, whitespace collapsed."""
    found = _SUBJECT.search(text[:_HEADER_WINDOW])
    return " ".join(found["subject"].split()).rstrip(".") if found else None


def old_style_number(text: str) -> str | None:
    """'5/POJK.05/2014' from an identifier or header, spaces removed; None for new-style numbering."""
    found = _OLD_STYLE_NUMBER.search(text)
    return re.sub(r"\s+", "", found["number"]) if found else None


def national_law_id(title: str) -> str | None:
    """id:uu:2011:21 from "… Undang-Undang Nomor 21 Tahun 2011 …"; laws themselves come from the BPK connector.

    Searched anywhere: lineage titles often lead with an English name and give the Indonesian one in brackets.
    """
    found = _NATIONAL_LAW.search(title)
    if not found:
        return None
    family = _NATIONAL_FAMILY[found["form"].lower()]
    return canonical_id("id", family, int(found["number"]), int(found["year"]))
