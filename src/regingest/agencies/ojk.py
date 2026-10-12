"""Identity rules for OJK (Otoritas Jasa Keuangan) instruments.

Families:
  pojk   Peraturan OJK                                  77/POJK.01/2016, POJK 1 Tahun 2026
  seojk  Surat Edaran OJK                               35/SEOJK.06/2025
  padk   Peraturan Anggota Dewan Komisioner OJK (PADK)  45/PADK.06/2025, PADK 11 Tahun 2026

PADK and POJK share numbers within a year (PADK 11/2026 is not POJK 11/2026), so they
are separate families. The sector code of old-style citations (".03") is evidence
only, not part of the key.

Evidence, most to least reliable: the Indonesian header of the document itself, the
source identifier, the official URL slug, the English title, RegAlert's instrument_no.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from regingest.agencies.base import AgencyProfile, CanonicalId, Confidence, IdResolution, TextKind
from regingest.models import DiscoveredDocument

_FAM = r"(?P<fam>POJK|SEOJK|PADK)"
_NUM = r"(?P<num>\d{1,3})"
_YEAR = r"(?P<year>(?:19|20)\d{2})"
_ANY_FAM = r"(?:POJK|SEOJK|PADK)"

_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class _Candidate:
    family: str | None
    number: int
    year: int
    field: str
    confidence: Confidence
    family_hint: str | None = None  # weak guess, used only when nothing better names the family

    def label(self) -> str:
        fam = self.family or (f"{self.family_hint}?" if self.family_hint else "?")
        return f"{self.field}={fam} {self.number}/{self.year}"


def _candidate(
    match: re.Match[str],
    field: str,
    confidence: Confidence,
    family: str | None = None,
    family_hint: str | None = None,
) -> _Candidate:
    groups = match.groupdict()
    fam = family or groups.get("fam")
    year = groups.get("year") or groups.get("year2")
    return _Candidate(
        family=fam.lower() if fam else None,
        number=int(groups["num"]),
        year=int(year),
        field=field,
        confidence=confidence,
        family_hint=family_hint,
    )


# --- source identifier: "34/POJK.05/2015", "10.SEOJK.03/2014", "PADK 11 Tahun 2026"
_IDENTIFIER = (
    re.compile(rf"^{_NUM}\s*[./]\s*{_FAM}\s*\.?\s*\d{{2}}\s*[./]\s*{_YEAR}$", re.I),
    re.compile(rf"^{_FAM}\s+(?:Nomor\s+|No\.?\s*)?{_NUM}\s+Tahun\s+{_YEAR}$", re.I),
)


def _from_identifier(value: object) -> _Candidate | None:
    if not isinstance(value, str) or not value.strip():
        return None
    for pattern in _IDENTIFIER:
        if m := pattern.match(value.strip()):
            return _candidate(m, "identifier", "high")
    return None


# --- official URL slug, e.g. ojk.go.id/id/regulasi/Pages/POJK-1-Tahun-2026-....aspx
# Only the start or the end of a slug names the document itself; the middle often
# names another instrument (the one being amended), so it is never used.
_SLUG_START: tuple[tuple[re.Pattern[str], str | None], ...] = (
    # PADK-45-PADK06-2025, 48-PADK06-2025, SEOJK-14-SEOJK032025, 1SEOJK062025, SEOJK-15-SEOJK.03-2024
    (re.compile(rf"^(?:{_ANY_FAM}-(?:Nomor-)?)?{_NUM}-?{_FAM}\.?\d{{2}}[-.]?{_YEAR}(?:-|$)", re.I), None),
    # PADK-11-Tahun-2026, POJK-Nomor-16-Tahun-2026, POJK-25-2025, POJK-Tahun-14-2024
    (re.compile(rf"^{_FAM}-(?:Nomor-|Tahun-)?{_NUM}-(?:Tahun-)?{_YEAR}(?:-|$)", re.I), None),
    # Peraturan-Otoritas-Jasa-Keuangan-Nomor-12-Tahun-2023-tentang-...
    (re.compile(rf"^Peraturan-Otoritas-Jasa-Keuangan-Nomor-{_NUM}-Tahun-{_YEAR}(?:-|$)", re.I), "pojk"),
    # 28-Tahun-2024-... (no family; it must come from other evidence)
    (re.compile(rf"^{_NUM}-Tahun-{_YEAR}(?:-|$)", re.I), None),
)
_SLUG_END = (
    # ...-POJK-16-2024, ...(POJK-Nomor-20-POJK.04-2021), ...(POJK-19-Tahu-2023), ...-SEOJK-13-04-2023
    re.compile(
        rf"[-(]{_FAM}-(?:Nomor-)?{_NUM}-(?:{_ANY_FAM}\.?\d{{2}}-|\d{{2}}-|Tahu?n?-)?{_YEAR}\)?$", re.I
    ),
    # ...-10SEOJK052024, ...-11-SEOJK01-2024
    re.compile(rf"-{_NUM}-?{_FAM}\.?\d{{2}}[-.]?{_YEAR}\)?$", re.I),
)
_AMENDMENT_SLUG = re.compile(r"(?:^|-)(?:Perubahan|Pencabutan)(?:-|$)", re.I)


def slug_of(url: str | None) -> str | None:
    if not url:
        return None
    last = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    last = re.sub(r"\.aspx$", "", last, flags=re.I).strip(" -")
    return last or None


def _from_slug(url: str | None) -> _Candidate | None:
    slug = slug_of(url)
    if not slug:
        return None
    for pattern, family in _SLUG_START:
        if m := pattern.search(slug):
            return _candidate(m, "url_slug", "high", family=family)
    if _AMENDMENT_SLUG.search(slug):
        return None  # an amendment's slug ends with the instrument it amends
    for pattern in _SLUG_END:
        if m := pattern.search(slug):
            return _candidate(m, "url_slug", "high")
    return None


# --- English title (RegAlert): "… Number 77/POJK.01/2016 …", "… No. 3 of 2026 …"
# A citation preceded by these words names the instrument being amended or revoked,
# e.g. "POJK on Amendment to … Regulation Number 11/POJK.03/2016 …".
_AMENDING_WORDS = re.compile(r"\b(?:amend\w*|chang\w*|revo\w*|repeal\w*|perubahan|pencabutan)\b", re.I)
_TITLE_OLD = re.compile(rf"\b{_NUM}\s*[/.]\s*{_FAM}\s*\.?\s*\d{{2}}\s*[/.]\s*{_YEAR}\b", re.I)
_TITLE_NEW = re.compile(rf"\b(?:Number|No\.?|Nomor)\s*{_NUM}\s+(?:of|Year|Tahun)\s+{_YEAR}\b", re.I)


def _title_family(prefix: str) -> str | None:
    if re.search(r"board of commissioners|commissioner|\bPADK\b", prefix, re.I):
        return "padk"
    if re.search(r"circular|\bSEOJK\b", prefix, re.I):
        return "seojk"
    if re.search(r"regulation|\bPOJK\b", prefix, re.I):
        return "pojk"
    return None


def _from_title(title: str | None) -> _Candidate | None:
    if not title:
        return None
    old = _TITLE_OLD.search(title)
    new = _TITLE_NEW.search(title)
    first = old if old and (not new or old.start() <= new.start()) else new
    if first is None or _AMENDING_WORDS.search(title[: first.start()]):
        return None  # the first citation is the instrument being amended, not this one
    if first is old:
        return _candidate(old, "title", "medium")
    family = _title_family(title[: first.start()])
    return _candidate(first, "title", "medium", family=family) if family else None


# --- RegAlert instrument_no: "ojk:pojk.05-regulation:46:2024", "ojk:regulation:11:2026"
_INSTRUMENT = re.compile(r"^ojk:(?P<prefix>[a-z0-9.-]+):(?P<num>\d{1,3}):(?P<year>(?:19|20)\d{2})$", re.I)


def _from_instrument(value: object) -> _Candidate | None:
    if not isinstance(value, str) or not (m := _INSTRUMENT.match(value.strip())):
        return None
    prefix = m["prefix"].lower()
    for family in ("pojk", "seojk", "padk"):
        if prefix.startswith(family):
            return _candidate(m, "instrument_no", "low", family=family)
    if prefix.startswith("regulation"):
        # Used for both POJK and PADK, so it can only hint at the family.
        return _candidate(m, "instrument_no", "low", family_hint="pojk")
    return None


# --- the document's own Indonesian header
# "NOMOR 1 TAHUN 2026", "NOMOR 77 /POJK.01/2016", and older variants seen in RegAlert's
# texts: "NOMOR: 1/POJK.07/2013", "NOMOR 6 POJK.04/2017", "NOMOR.116 /SEOJK.04/2016".
_NOMOR = r"NOMOR ?[:.]? ?(?P<num>\d{1,3})"


def _old_style(family: str) -> str:
    return rf" ?/? ?{family}\. ?\d{{2}} ?/ ?(?P<year>(?:19|20)\d{{2}})"


_NEW_STYLE = r" TAHUN (?P<year2>(?:19|20)\d{2})"
_HEADERS = (
    ("padk", re.compile(r"PERATURAN ANGGOTA DEWAN KOMISIONER OTORITAS JASA KEUANGAN (?:REPUBLIK INDONESIA )?"
                        + _NOMOR + rf"(?:{_old_style('PADK')}|{_NEW_STYLE})")),
    ("seojk", re.compile(r"SURAT EDARAN OTORITAS JASA KEUANGAN (?:REPUBLIK INDONESIA )?" + _NOMOR + _old_style("SEOJK"))),
    ("pojk", re.compile(r"PERATURAN OTORITAS JASA KEUANGAN (?:REPUBLIK INDONESIA )?"
                        + _NOMOR + rf"(?:{_old_style('POJK')}|{_NEW_STYLE})")),
)
_HEADER_WINDOW = 3000  # the header is near the top (after "SALINAN" or a letter's addressee)
_LOOSE_HEADER_WINDOW = 400  # for texts with odd casing, only a header at the very start counts

# --- an ojk.go.id regulation page captured as text (a summary page, not the instrument):
#     "Jenis Regulasi : Surat Edaran OJK  Nomor Regulasi : 8/SEOJK.04/2023"
_PAGE = re.compile(r"Jenis Regulasi\s*:\s*(?P<jenis>.+?)\s+Nomor Regulasi\s*:\s*(?P<nomor>\S+(?:\s+Tahun\s+\d{4})?)", re.I)
_PAGE_NUMBER = re.compile(rf"^{_NUM}\s*(?:[./]\s*{_FAM}\s*\.?\s*\d{{2}}\s*[./]\s*|\s+Tahun\s+|/){_YEAR}$", re.I)


def _page_family(jenis: str) -> str | None:
    if re.search(r"anggota dewan komisioner|PADK", jenis, re.I):
        return "padk"
    if re.search(r"surat edaran|SEOJK", jenis, re.I):
        return "seojk"
    if re.search(r"^peraturan (?:OJK|otoritas jasa keuangan)|POJK", jenis, re.I):
        return "pojk"
    return None


def _find_header(text: str) -> tuple[str, re.Match[str]] | None:
    """The earliest instrument header. Headers are in capitals; citations in the body are not."""
    head = " ".join(text[:_HEADER_WINDOW].split())
    matches = [(m.start(), family, m) for family, pattern in _HEADERS if (m := pattern.search(head))]
    if not matches:
        # Odd casing (e.g. OCR): accept a header only if the text starts with it.
        start = re.sub(r"^SALINAN ", "", " ".join(text[:_LOOSE_HEADER_WINDOW].split()).upper())
        matches = [(m.start(), family, m) for family, pattern in _HEADERS if (m := pattern.match(start))]
    if not matches:
        return None
    _, family, m = min(matches, key=lambda item: item[0])
    return family, m


class OJKProfile(AgencyProfile):
    kind = "ojk"
    primary_language = "id"
    assesses_text = True

    def resolve_id(self, document: DiscoveredDocument) -> IdResolution:
        meta = document.metadata
        candidates = [
            c
            for c in (
                _from_identifier(meta.get("identifier")),
                _from_slug(document.official_url),
                _from_title(document.title),
                _from_instrument(meta.get("instrument_no")),
            )
            if c is not None
        ]
        return self._decide(candidates)

    def resolve_id_from_text(self, text: str) -> IdResolution:
        if found := _find_header(text):
            family, m = found
            c = _candidate(m, "text_header", "high", family=family)
            return IdResolution(CanonicalId(self.code, family, c.year, c.number), "high", f"text_header={m.group(0)!r}")
        page = _PAGE.search(" ".join(text[:_HEADER_WINDOW].split()))
        if page and (family := _page_family(page["jenis"])) and (m := _PAGE_NUMBER.match(page["nomor"])):
            c = _candidate(m, "ojk_page", "high", family=family)
            return IdResolution(CanonicalId(self.code, family, c.year, c.number), "high", f"ojk_page={page.group(0)!r}")
        return IdResolution.unresolved("no OJK header or regulation-page fields at the top of the text")

    def assess_text(self, text: str) -> TextKind:
        if _find_header(text):
            return "instrument"
        if _PAGE.search(" ".join(text[:_HEADER_WINDOW].split())):
            return "page"
        return "unknown"

    def _decide(self, candidates: list[_Candidate]) -> IdResolution:
        if not candidates:
            return IdResolution.unresolved("no OJK citation in identifier, URL slug, title or instrument_no")
        best = candidates[0]
        same = [c for c in candidates[1:] if (c.number, c.year) == (best.number, best.year)]
        confidence: Confidence = best.confidence
        family = best.family
        if family is None:
            borrowed = next((c.family for c in same if c.family), None)
            if borrowed:
                family = borrowed
                confidence = "medium" if _RANK[confidence] > _RANK["medium"] else confidence
            else:
                family = next((c.family_hint for c in [best, *same] if c.family_hint), None)
                confidence = "low"
        evidence = ", ".join(c.label() for c in candidates)
        if family is None:
            return IdResolution(None, "none", f"family unknown: {evidence}")

        disagreeing = [
            c
            for c in candidates[1:]
            if (c.number, c.year) != (best.number, best.year) or (c.family and c.family != family)
        ]
        if any(_RANK[c.confidence] >= _RANK["medium"] for c in disagreeing):
            confidence = "conflict"
        if disagreeing:
            evidence += " | disagrees: " + ", ".join(c.label() for c in disagreeing)
        return IdResolution(CanonicalId(self.code, family, best.year, best.number), confidence, evidence)
