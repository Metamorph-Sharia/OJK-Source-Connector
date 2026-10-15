# 01 — How OJK publishes its regulations

Part of the [OJK publishing and storage report](README.md). Research date: 2026-10-14.

## Instrument types and their legal weight

OJK issues instruments at several levels. For search, the level matters: a POJK binds the public, while a PADK or SEOJK implements a POJK. An answer has to know which one it is quoting.

| Instrument | What it is | Promulgated in the State Gazette? | In our corpus | Notes |
|---|---|---|---|---|
| **POJK**: Peraturan OJK | OJK's main regulation; binding on the public. Issued under UU 21/2011 (OJK) and, since 2023, UU 4/2023 (P2SK) | **Yes.** `Diundangkan di …`, with an LN and TLN number. Recent ones use an OJK series, e.g. `LN Tahun 2022 Nomor 5/OJK` (JDIH shows `2026 (16/OJK)`) | 573 | Has an official elucidation (`PENJELASAN`) in nearly every case |
| **SEOJK**: Surat Edaran OJK | Circular implementing a POJK ("Dalam rangka pelaksanaan amanat ketentuan Pasal 20 ayat (4) POJK …") | No (0 of 377 texts carry `Diundangkan`) | 377 | Discontinued in October 2025. Existing SEOJKs **remain in force and are read as PADK** until replaced |
| **PADK**: Peraturan Anggota Dewan Komisioner | Successor to SEOJK since the Board regulation PDK 7/PDK.02/2025 (enacted 13 Oct 2025). Formatted like a POJK, with the technical substance in the annex | No (0 of 22) | 22 | Its own number series from 2026 (PADK 11/2026 ≠ POJK 11/2026) |
| **PDK**: Peraturan Dewan Komisioner | Internal Board regulations (e.g. PDK 7/PDK.02/2025 on how OJK makes regulations) | No | 0 | Mostly internal. Collect only those that change how public instruments work |
| UU, PP, Perpres, PBI, Bapepam-LK rules | Laws and older instruments OJK administers or relies on | — | 0 | JDIH lists them beside OJK's own. POJKs cite them in `Mengingat` (legal basis) |
| RPOJK: draft POJK | Consultation drafts ("Permintaan Tanggapan …") with a deadline and a contact | — | 0 | Not law. An early-warning signal for monitoring |
| Abstract, FAQ | OJK's own summary and Q&A for a regulation, published as PDFs on JDIH | — | 0 | Official explanation. Not binding, but very useful for search |
| English pages (`/en/regulasi/…`) | Unofficial English pages for some regulations | — | 0 | Informational only; the Indonesian text is authoritative |

## Numbering and naming

How OJK numbers its instruments changed twice. Canonical identity must handle every form.

| Period | POJK | SEOJK / PADK |
|---|---|---|
| 2013 – early 2022 | `5/POJK.05/2014` (number / family.sector code / year) | `1/SEOJK.04/2020` |
| From 2022 (POJK) | `Nomor 1 Tahun 2023` (number and year only) | SEOJK keeps the sector-code form until it ends |
| Oct–Dec 2025 | unchanged | **PADK continues the SEOJK sequence of the year:** SEOJK 1–36/2025, then `37/PADK.08/2025` … `48/PADK.06/2025` |
| From 2026 | unchanged | `PADK Nomor 1 Tahun 2026`, a fresh series |

Consequences:
- **The sector code (`.03`, `.04`, `.05` …) is not part of identity.** POJK and SEOJK numbers are unique per family and year. Our corpus agrees: 972 of 973 records resolved without a collision.
- **SEOJK and PADK in 2025 share one number space;** in 2026 PADK has its own. Keeping `seojk` and `padk` as separate canonical families, as we do now, is correct. Search should treat them as one *kind* of instrument (implementing rules).
- **JDIH sometimes relabels old SEOJKs as PADK.** For example, JDIH lists `padk:2016:39` and `padk:2017:20` where the document is SEOJK 39/2016 and SEOJK 20/2017. This follows OJK's "read as PADK" rule. We should keep the label printed on the document and record the relabel as an alias.

## The lifecycle of a regulation, and its dates

```
 RPOJK draft ──► Penetapan ──► Pengundangan ──► Berlaku ──► (Diubah) ──► (Dicabut Sebagian) ──► Dicabut / Tidak Berlaku
 consultation    enacted        promulgated      in force    amended      partly revoked           revoked or replaced
                 "Ditetapkan    "Diundangkan     may be later
                  di Jakarta"    di Jakarta"     or staged by article
```

| Date | Where it appears | Example (POJK 8/2026) | Trap |
|---|---|---|---|
| Tanggal Penetapan (enacted) | Signature block; JDIH | 12-06-2026 | — |
| Tanggal Pengundangan (promulgated) | Signature block (POJK only); JDIH | 06-07-2026 | — |
| Berlaku (in force) | Ketentuan Penutup; JDIH status "Berlaku Sejak Tanggal …" | 06-07-2026 | Some provisions come into force later than others; 8 SEOJK/PADK in the JDIH list carry 2027 dates |
| ojk.go.id "Tanggal Berlaku" | Detail page | `4/8/2014` | **`M/D/YYYY`**: this is 8 April 2014, not 4 August |
| JDIH list date | List JSON | `06-07-2026` | `DD-MM-YYYY`; **blank for 441 of 565 POJK** |
| RegAlert `published` | API | — | Sometimes the date RegAlert ingested the document ([feasibility study](../regalert-feasibility.md)) |

**Status values on JDIH** (2026-10-14, from the list endpoint):

| Status | POJK | SEOJK/PADK |
|---|---|---|
| Berlaku (in force) | 410 | 295 |
| Tidak Berlaku (not in force) | 139 | 113 |
| Berlaku (Dicabut Sebagian): partly revoked | 11 | 0 |
| Berlaku (Perubahan) (Diubah): in force, amended | 4 | 0 |
| Berlaku (Perubahan) (Mengubah): in force, amends another | 1 | 0 |

"Partly revoked" is the case that matters most for search. The regulation is in force, but some of its articles are not. JDIH's history block records this at article level. For example, POJK 8/2026 → "Mencabut: POJK 40/2024 … Pasal Terkait: Pasal 187".

## What OJK publishes for one regulation

Taking POJK 8/2026 (P2P lending data reporting, a regulation central to our market) on JDIH OJK:

| Artifact | Form | Collected today? |
|---|---|---|
| Regulation (`2026pojk008.pdf`) | Certified copy PDF (`SALINAN`) | Text only, via RegAlert. The PDF itself comes in M3 |
| Abstract (`2026abspojk008.pdf`) | PDF | **No** |
| FAQ (`2026faqpojk008.pdf`) | PDF | **No** |
| Metadata | Issuing body, number, type and abbreviation, sector, classification, sub-classification, place, enacted and promulgated dates, LN/TLN source, status, subject, language, legal field, **legal basis (Landasan Hukum)**, judicial review (Uji Materi) | Partly (RegAlert fields) |
| History (Riwayat Peraturan) | Typed links (`Mencabut`, `Mengubah` …) to other regulations, **with article references** | No (RegAlert lineage only, known to be incomplete) |
| Legal-basis files | `downloadfilelandasan/{id}` | No |

On ojk.go.id the same regulation has a page with Sektor, SubSektor, Jenis Regulasi, Nomor Regulasi and Tanggal Berlaku, plus one or more attachments (`Lampiran 1`, `Lampiran 2` …). Annexes sometimes arrive as separate files.

## Publication channels

| Channel | Role | Strengths | Weaknesses |
|---|---|---|---|
| **JDIH OJK** (`jdih.ojk.go.id`) | OJK's legal documentation portal, part of the national JDIHN network | Richest metadata; status; article-level history; abstract and FAQ; JSON list endpoint; 10 sectors × 14 types, including UU/PP/PBI | Title typos; blank dates; misses some 2024 instruments in its sector listings; a firewall that has rejected plain clients before |
| **ojk.go.id** (`/id/regulasi/…`) | Public website (SharePoint) | Direct PDF downloads with ETag and Last-Modified; separate annex files; draft-regulation (RPOJK) pages; English pages | No API (REST returns `401`), no sitemap or `robots.txt`; listings paged by ASP.NET postback; test pages in production navigation |
| BPK Database Peraturan | National regulation database (secondary) | Clean POJK metadata and status | Only about 25 SEOJK; no PADK ([01-sources](../../plan/01-sources.md)) |
| RegAlert | Third-party tracker (secondary, our current primary) | Indonesian text already extracted; lineage hints | Not authoritative; some dates are ingestion dates; English text licence pending |

**OJK itself publishes no consolidated texts, no structured text (XML/HTML) and no change feed.** Everything above the PDF is ours to build.
