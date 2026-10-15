# 02 — Document anatomy

Part of the [OJK publishing and storage report](README.md). Based on all 973 Indonesian texts in `data/corpus/` (2026-10-14) and the official PDFs they come from.

OJK documents follow Indonesia's drafting rules for regulations (UU 12/2011 and its amendments), so their structure is predictable. But it exists only as headings and numbering in a PDF. There are **three distinct shapes**, plus a variant for amending regulations.

## POJK (and the new PADK body)

```
OTORITAS JASA KEUANGAN REPUBLIK INDONESIA
SALINAN                                              ← certified copy
PERATURAN OTORITAS JASA KEUANGAN NOMOR 5/POJK.05/2014 TENTANG …   ← identity + title
DENGAN RAHMAT TUHAN YANG MAHA ESA
DEWAN KOMISIONER OTORITAS JASA KEUANGAN,
Menimbang : a. bahwa …  b. bahwa …                   ← considerations (why)
Mengingat : 1. Undang-Undang Nomor 21 Tahun 2011 …    ← legal basis (links to higher law)
MEMUTUSKAN: Menetapkan : PERATURAN … TENTANG …
BAB I  KETENTUAN UMUM                                 ← chapter
  Pasal 1  Dalam Peraturan … yang dimaksud dengan:     ← definitions, scoped to this regulation
           1. Penjaminan adalah …  10. Prinsip Syariah adalah …
BAB II …
  Bagian Kesatu …                                     ← part
    Paragraf 1 …                                      ← paragraph group
      Pasal 5
        (1) …                                         ← ayat
            a. …                                      ← huruf
               1. …                                   ← angka
BAB … SANKSI ADMINISTRATIF
BAB … KETENTUAN PERALIHAN                             ← transitional rules
BAB … KETENTUAN PENUTUP                               ← revocations, entry into force
Ditetapkan di Jakarta pada tanggal …  /  Diundangkan di Jakarta pada tanggal …
LEMBARAN NEGARA REPUBLIK INDONESIA TAHUN … NOMOR …
PENJELASAN ATAS PERATURAN …                           ← official elucidation
  I. UMUM
  II. PASAL DEMI PASAL
      Pasal 1  Cukup jelas.                           ← "self-explanatory"
      Pasal 5  Ayat (1) … Ayat (2) Cukup jelas.
LAMPIRAN I …                                          ← annexes: forms, tables, technical rules
```

**What this means for search:**
- **The Pasal is the natural citation unit.** Its ayat, huruf and angka are the sub-addresses lawyers cite: "Pasal 5 ayat (1) huruf a".
- **The Penjelasan is part of the law's meaning.** It repeats the Pasal numbering, so each elucidation can be joined to its article. "Cukup jelas" entries carry no content and should be dropped from the index, not shown as hits.
- **Definitions are scoped to the regulation.** "Prinsip Syariah" or "Perusahaan Pembiayaan Syariah" is defined in each regulation's Pasal 1, and the definitions differ between regulations. Capitalized defined terms in later articles refer back to them.
- **`Mengingat` is a typed, machine-extractable link to the legal basis.**
- **Chapter titles (BAB / Bagian) give context the Pasal text lacks.** Many articles say "as referred to in paragraph (1)" and make sense only with their headings.

## Amending regulations

An amending POJK doesn't restate the regulation. It contains **editing instructions**:

```
Pasal I
  Beberapa ketentuan dalam POJK 35/POJK.05/2018 … diubah sebagai berikut:
  1. Di antara Pasal 82 dan Pasal 83 disisipkan 1 (satu) pasal, yakni Pasal 82A sehingga berbunyi: …
  2. Ketentuan ayat (1) dan ayat (3) Pasal 115 diubah, sehingga Pasal 115 berbunyi sebagai berikut: …
Pasal II
  Peraturan … ini mulai berlaku pada tanggal diundangkan.
```
(POJK 7/POJK.05/2022, amending POJK 35/POJK.05/2018 on financing companies)

- Articles of an amending regulation are numbered in **Roman numerals** (Pasal I, Pasal II). Inserted articles take a **letter suffix** (Pasal 82A).
- The instructions are formulaic: *diubah* (changed), *disisipkan* (inserted), *dihapus* (deleted), *ditambah* (added). This makes them parseable with good, though not perfect, accuracy.
- The **current wording of POJK 35/2018 Pasal 115 exists nowhere as one text.** It is the 2018 original, edited by 7/2022, possibly edited again later. Neither OJK channel publishes consolidated text.
- Some regulations revoke only some articles of another (partial revocation, e.g. "Pasal Terkait: Pasal 187" on JDIH).

## SEOJK

```
Yth. 1. Direksi Manajer Investasi; 2. Direksi Perusahaan Efek; … di tempat.    ← addressees
SALINAN
SURAT EDARAN OTORITAS JASA KEUANGAN NOMOR 1/SEOJK.04/2020 TENTANG …
Dalam rangka pelaksanaan amanat ketentuan Pasal 20 ayat (4)
Peraturan Otoritas Jasa Keuangan Nomor 28/POJK.04/2016 …       ← which POJK article it implements
I.   KETENTUAN UMUM
     1. Dalam Surat Edaran … yang dimaksud dengan: …
II.  PENGGUNAAN S-INVEST …
     A. …  1. …  a. …
III. KETENTUAN PERALIHAN
IV.  PENUTUP
Ditetapkan di Jakarta …
LAMPIRAN …                                                        ← forms and report templates (most SEOJKs)
```

- **No Pasal, no Menimbang/Mengingat, no Penjelasan.** The hierarchy is Roman section › capital letter › number › lower-case letter.
- **The opening sentence names the POJK article it implements.** This is an article-level link worth extracting: SEOJK 1/2020 → POJK 28/2016 Pasal 20 ayat (4).
- Addressees ("Yth. Direksi …") tell us which kind of institution the circular is for. That is useful as a filter, and it is metadata, not interpretation.

## PADK (since October 2025)

```
PERATURAN ANGGOTA DEWAN KOMISIONER OTORITAS JASA KEUANGAN REPUBLIK INDONESIA NOMOR 1 TAHUN 2026 TENTANG …
Menimbang : bahwa untuk melaksanakan ketentuan Pasal 10, Pasal 11, … Pasal 65 POJK Nomor 11 Tahun 2022 …
Mengingat : 1. UU 21/2011 …  2. POJK 11/2022 …
Pasal 1 … Pasal 5                                   ← thin body: principles only
LAMPIRAN I
  (table of contents with dotted leaders and page numbers)
  BAB I PENDAHULUAN
  BAB II TATA KELOLA TI BANK
  …                                                  ← the technical substance, with its own BAB tree
```

- The body is POJK-shaped but short (PADK 1/2026 has 5 Pasal). **The rules live in the annex**, which has its own chapter hierarchy.
- `Menimbang` names every POJK article implemented, which gives article-level links again.
- The annex's table of contents (dotted leaders, page numbers) must be recognized and skipped. Otherwise it creates fake headings.

## Measurements across the corpus

| Feature (regex over `text.id.md`) | POJK (573) | SEOJK (377) | PADK (22) |
|---|---|---|---|
| `Menimbang` / `Mengingat` / `MEMUTUSKAN` | 564 | 0 | 18 |
| `BAB` headings | 507 | 39 | 8 |
| `Bagian` / `Paragraf` levels | 300 / 63 | 0 / 0 | 0 / 0 |
| Arabic `Pasal N` | 565 | 6 | 18 |
| Roman `Pasal I` (amending regulation) | 63 | 0 | 0 |
| Inserted `Pasal NA` | 48 | 0 | 0 |
| Numbered ayat `(1)` | 563 | 163 | 14 |
| `PENJELASAN` | 531 | 2 | 0 |
| `LAMPIRAN` | 142 | 256 | 18 |
| `Diundangkan` / Lembaran Negara | 560 | 0 | 0 |
| Amendment wording (*diubah … berbunyi*, *disisipkan*) | 57 | 17 | 0 |
| Revocation (*dicabut dan dinyatakan tidak berlaku*) | 225 | 91 | 5 |
| `KETENTUAN PERALIHAN` | 273 | 70 | 0 |
| `SANKSI ADMINISTRATIF` | 100 | 6 | 0 |
| Mentions *syariah* | 364 | 263 | 13 |

| Size | Value |
|---|---|
| Total Indonesian text | 70.3 MB (about 70 million characters) |
| Median / 90th percentile / largest document | 38k / 186k / 695k characters |
| Documents over 150k characters | 126 |
| `Pasal` headings | 42,478, including those repeated in the Penjelasan. Roughly 20–25k distinct body articles |
| Share of text before Penjelasan/Lampiran (the body) | 42% |
| Share of text in Lampiran | **48%** |

Two numbers drive the design:
- **40% of documents have no Pasal structure** (SEOJK). A Pasal-only parser fails on them.
- **About half of all text sits in annexes.** For SEOJK and PADK, the annex is where the rules are.

## Text-quality issues

These are issues in the text we hold today (RegAlert's extraction of the official PDF):

| Issue | Extent | Effect on search | Fix |
|---|---|---|---|
| Page-break markers (a line ending `…` or `...`, then the next page repeats the opening words) | 15,627 markers in 394 texts | Duplicated fragments; broken sentences across chunks | Detect and join during structure parsing |
| No page numbers | All texts | We can't cite "page 12 of the official PDF" | Self-extract from the official PDF with page offsets (M3) |
| Tables flattened into lines | 0 texts contain table rows; every table is plain lines | Report templates, thresholds and rates in annexes lose their columns | Table-aware PDF extraction for annexes; keep the PDF page as the authority |
| Hyphenation joins (`UndangUndang`) | Common | Lexical misses | Normalize known compounds; index both forms |
| Table of contents with dotted leaders (PADK annexes) | Most PADK | Fake headings | Recognize and skip ToC blocks |
| `Salinan sesuai dengan aslinya` certification blocks | 297 texts | Noise | Strip |
| Running headers and page numbers (`- 5 -`) | Common | Noise inside articles | Strip |

## What this means for the design

1. **Parse into a provision tree, with three grammars:** POJK/PADK body, SEOJK sections, and Lampiran headings. Keep each node's character offsets (and, after M3, page numbers) so every search hit points back to the official PDF.
2. **Join each Penjelasan entry to its Pasal and ayat.** Drop "Cukup jelas".
3. **Extract definitions from Pasal 1** (and SEOJK section I.1) into a per-regulation glossary.
4. **Extract links from the text itself:** `Mengingat` (legal basis), SEOJK/PADK opening lines (which POJK article is implemented), Ketentuan Penutup (what is revoked), and amending `Pasal I` instructions (which article was changed, inserted or deleted).
5. **Treat annexes as first-class content,** chunked by their own headings.
