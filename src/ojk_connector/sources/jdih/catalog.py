"""JDIH OJK's catalogue as data: the listing JSON and the detail page, parsed. No network.

The listing (``/Web/ViewPeraturan/ListDataPeraturan``) is legacy DataTables JSON: one row per
regulation of one sector × type, every row at once. The detail page (``/Web/ViewPeraturan/Detail/
{GUID}/{sektor}/{jenis}``) is server-rendered: a label/value table, the regulation's history
(Riwayat Peraturan) and its files. Both were checked on live pages (2026-10-16); the fixtures in
``tests/fixtures/jdih`` are real pages.

JDIH's wording is turned into the source-neutral ``CatalogEntry`` here, and nowhere else.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from bs4 import BeautifulSoup, Tag

from ojk_connector.models import CatalogEntry, EntryRelation, FileLink, RelationKind, Snapshot, Standing

BASE_URL = "https://jdih.ojk.go.id"
#: JDIH's sector codes (``sektor``), 01-10.
SECTORS = tuple(f"{n:02}" for n in range(1, 11))
#: JDIH's type codes (``jenisPeraturan``) for OJK's own instruments: POJK, and SEOJK/PADK.
DOCUMENT_TYPES = ("06", "09")
ENTRY_SNAPSHOT = "entry_html"

_DETAIL_LINK = re.compile(r"/Detail/(?P<guid>[0-9a-f-]{36})/(?P<sector>\d{2})/(?P<type>\d{2})", re.I)
_DATE = re.compile(r"\b(\d{2})-(\d{2})-(\d{4})\b")
_SINCE = re.compile(r"Sejak Tanggal\s+(\d{2}-\d{2}-\d{4})", re.I)
_NUMBERED = re.compile(r"^\s*\d+\s*\.\s*")
_NO_ARTICLES = {"", "-"}
_COUNTER = re.compile(r"telah diakses", re.I)  # "Peraturan ini telah diakses 113 kali": changes on every view

#: The detail table's date labels, and what they are.
DATE_FIELDS = {"Tanggal Penetapan": "enacted", "Tanggal Pengundangan": "promulgated"}

#: Riwayat Peraturan section labels. A revocation that names provisions ("Pasal Terkait …") is partial.
_RELATIONS = {
    "mencabut": (RelationKind.REVOKES, RelationKind.PARTLY_REVOKES),
    "mencabut sebagian": (RelationKind.PARTLY_REVOKES, RelationKind.PARTLY_REVOKES),
    "mengubah": (RelationKind.AMENDS, RelationKind.AMENDS),
    "dicabut": (RelationKind.REVOKED_BY, RelationKind.PARTLY_REVOKED_BY),
    "dicabut sebagian": (RelationKind.PARTLY_REVOKED_BY, RelationKind.PARTLY_REVOKED_BY),
    "diubah": (RelationKind.AMENDED_BY, RelationKind.AMENDED_BY),
}

#: The Dokumen table's labels. Older entries leave the label blank; the file name tells then.
_FILE_LABELS = {"peraturan": "main", "abstrak": "abstract", "faq": "faq", "lampiran": "annex"}
_FILE_NAMES = (
    (re.compile(r"ringkasan|ringsek|abstrak|summary|^\d{4}\s*abs", re.I), "abstract"),
    (re.compile(r"\bfaq|^\d{4}\s*faq|frequently asked", re.I), "faq"),
    (re.compile(r"lampiran", re.I), "annex"),
)


class UnexpectedPage(ValueError):
    """The page is not what we asked for (a firewall notice, an error page, a changed layout)."""


@dataclass(frozen=True)
class ListingRow:
    guid: str
    url: str
    title: str
    published: str | None  # the listing's date, ISO; often blank
    fields: dict[str, Any]  # the listing's own columns


def parse_listing(body: bytes) -> list[ListingRow]:
    try:
        data = json.loads(body)
        rows = data["aaData"]
    except (ValueError, KeyError, TypeError) as e:
        raise UnexpectedPage(f"not a JDIH listing: {e}") from e
    return [_listing_row(row) for row in rows]


def _listing_row(row: list[Any]) -> ListingRow:
    link, number, sector, classification, sub_classification, kind, listed_on, status = (row + [None] * 8)[:8]
    found = _DETAIL_LINK.search(link or "")
    if found is None:
        raise UnexpectedPage(f"listing row without a detail link: {str(link)[:200]}")
    guid = found["guid"].lower()
    return ListingRow(
        guid=guid,
        url=detail_url(guid, found["sector"], found["type"]),
        title=_text(BeautifulSoup(link, "html.parser").get_text()),
        published=iso_date(listed_on),
        fields={
            "number": _text(number), "sector": _text(sector), "sector_code": found["sector"],
            "classification": _text(classification), "sub_classification": _text(sub_classification),
            "type": _text(kind), "type_code": found["type"], "date": _text(listed_on), "status": _text(status),
        },
    )


def detail_url(guid: str, sector: str, kind: str) -> str:
    return f"{BASE_URL}/Web/ViewPeraturan/Detail/{guid}/{sector}/{kind}"


def listing_url(sector: str, kind: str) -> str:
    return f"{BASE_URL}/Web/ViewPeraturan/ListDataPeraturan?sektor={sector}&jenisPeraturan={kind}&sLanguage=id"


def parse_entry(page: bytes, *, today: date) -> CatalogEntry:
    soup = BeautifulSoup(page, "html.parser")
    table = soup.select_one("table.table")
    if table is None or "DETAIL PERATURAN" not in soup.get_text():
        raise UnexpectedPage("not a JDIH detail page")
    fields = _detail_fields(table)
    history = _history(soup)
    status = fields.get("Status Peraturan", "")
    dates = {key: iso for label, key in DATE_FIELDS.items() if (iso := iso_date(fields.get(label)))}
    if effective := _effective_date(status):
        dates["effective"] = effective
    relations = _relations(history)
    return CatalogEntry(
        title=fields.get("Judul") or None,
        standing=standing_of(status, {r.kind for r in relations if not r.kind.outgoing}, today),
        dates=dates,
        relations=(*relations, *_legal_basis(table)),
        files=tuple(_files(soup)),
        fields={**fields, "Riwayat Peraturan": history},
        snapshot=Snapshot(ENTRY_SNAPSHOT, page, "text/html"),
    )


def _detail_fields(table: Tag) -> dict[str, str]:
    fields = {}
    for row in table.find_all("tr"):
        label, cells = row.find("h4"), row.find_all("td")
        if label is None or len(cells) < 2 or _COUNTER.search(row.get_text()):
            continue
        fields[_text(label.get_text())] = _text(cells[-1].get_text(" "))
    return fields


def standing_of(status: str, incoming: Collection[RelationKind], today: date) -> Standing | None:
    """JDIH's "Status Peraturan" (e.g. "Berlaku (Dicabut Sebagian)", "Berlaku Sejak Tanggal 06-07-2026"), made
    precise by the entry's own history: the status field often says only "Berlaku" for a regulation its history
    lists as amended or partly revoked (94 of 973 entries, 2026-10-16)."""
    wording = status.lower()
    if wording.startswith("tidak berlaku"):
        return Standing.REVOKED
    if wording.startswith("belum berlaku"):
        return Standing.NOT_YET_IN_FORCE
    if not wording.startswith("berlaku"):
        return None  # unknown wording: kept in the fields, never guessed
    if "dicabut sebagian" in wording or RelationKind.PARTLY_REVOKED_BY in incoming:
        return Standing.PARTLY_REVOKED
    if "(diubah)" in wording or RelationKind.AMENDED_BY in incoming:
        return Standing.AMENDED
    effective = _effective_date(status)
    if effective and date.fromisoformat(effective) > today:
        return Standing.NOT_YET_IN_FORCE
    return Standing.IN_FORCE


def _effective_date(status: str) -> str | None:
    """"Berlaku Sejak Tanggal …" only: in "Tidak Berlaku Sejak Tanggal …" the date is when it stopped being in force."""
    found = _SINCE.search(status) if status.lower().startswith("berlaku") else None
    return iso_date(found[1]) if found else None


def _history(soup: BeautifulSoup) -> list[dict[str, str | None]]:
    """Riwayat Peraturan as listed: [{label, target, articles}], in page order."""
    box = _section(soup, "Riwayat Peraturan")
    if box is None:
        return []
    items, label = [], None
    for span in box.find_all("span"):
        classes = span.get("class") or []
        if "text-primary" in classes:
            label = _text(span.get_text()).rstrip(" :")
        elif "d-block" in classes and (link := span.find("a")) is not None:
            _, _, articles = span.get_text(" ").partition("Pasal Terkait")
            items.append({"label": label, "target": _text(link.get_text()), "articles": _text(articles) or None})
    return items


def _relations(history: list[dict[str, str | None]]) -> list[EntryRelation]:
    relations = []
    for item in history:
        kinds = _RELATIONS.get((item["label"] or "").lower())
        if kinds is None:
            continue  # an unknown label stays in the fields ("Riwayat Peraturan") for a person to read
        articles = item["articles"] if (item["articles"] or "") not in _NO_ARTICLES else None
        whole, partial = kinds
        relations.append(EntryRelation(partial if articles else whole, item["target"] or "", articles))
    return relations


def unknown_relation_labels(entry: CatalogEntry) -> set[str]:
    return {str(item["label"]) for item in entry.fields.get("Riwayat Peraturan", [])
            if (item.get("label") or "").lower() not in _RELATIONS}


def _legal_basis(table: Tag) -> list[EntryRelation]:
    for row in table.find_all("tr"):
        label = row.find("h4")
        if label is not None and _text(label.get_text()) == "Landasan Hukum":
            return [EntryRelation(RelationKind.LEGAL_BASIS, target)
                    for link in row.find_all("a") if (target := _NUMBERED.sub("", _own_text(link)))]
    return []


def _own_text(link: Tag) -> str:
    """A link's own words. JDIH leaves the last Landasan Hukum link unclosed, so a parser nests nothing in it
    but text; still, never take a nested link's words twice."""
    return _text("".join(str(part) for part in link.find_all(string=True, recursive=False)))


def _files(soup: BeautifulSoup) -> list[FileLink]:
    box = _section(soup, "Dokumen")
    if box is None:
        return []
    files = []
    for row in box.find_all("tr"):
        download = row.find("a", href=re.compile(r"/DownloadDokumen/", re.I))
        if download is None:
            continue
        preview = row.find("a", href=re.compile(r"/PreviewDokumen/", re.I))
        name = _text((preview or download).get_text())
        label = row.find("h4")
        files.append(FileLink(file_role(_text(label.get_text()) if label else "", name),
                              name, BASE_URL + download["href"]))
    return files


def file_role(label: str, name: str) -> str:
    role = _FILE_LABELS.get(label.lower())
    if role == "main" and re.search(r"lampiran", name, re.I):
        return "annex"
    if role:
        return role
    return next((role for pattern, role in _FILE_NAMES if pattern.search(name)), "main")


def _section(soup: BeautifulSoup, heading: str) -> Tag | None:
    """The box under an ``<h2>`` heading."""
    for h2 in soup.find_all("h2"):
        if _text(h2.get_text()) == heading:
            box = h2.find_next_sibling("div", class_="box-holder")
            return box if isinstance(box, Tag) else None
    return None


def iso_date(value: object) -> str | None:
    """JDIH writes DD-MM-YYYY. A date that doesn't exist (e.g. 31-02) is no date."""
    found = _DATE.search(value) if isinstance(value, str) else None
    if found is None:
        return None
    try:
        return datetime(int(found[3]), int(found[2]), int(found[1])).date().isoformat()
    except ValueError:
        return None


def _text(value: object) -> str:
    return " ".join(html.unescape(str(value or "")).replace("\xa0", " ").split())
