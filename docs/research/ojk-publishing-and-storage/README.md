# OJK publishing and storage architecture: research report

**Date:** 2026-10-14
**Question:** How does OJK publish and structure its regulatory documents, and what storage architecture best supports reliable AI-powered legal search over them?
**Method:**
- Live checks of OJK's two official channels: `ojk.go.id` (detail pages, file downloads, listings) and JDIH OJK (list endpoint, detail pages), made at about 1 request per second with an identifying User-Agent.
- Structural analysis of all 973 Indonesian texts already in `data/corpus/`.
- A first reconciliation of the JDIH OJK catalogue against our RegAlert-based corpus.
- OJK's own announcements on the SEOJK → PADK change.

## The answer in short

**OJK publishes each regulation as a certified PDF copy (`SALINAN`), with no structured or machine-readable text.** It does so on two official channels that disagree in places:

- **ojk.go.id** is a SharePoint site. It serves the PDFs directly.
- **JDIH OJK** is the legal documentation portal. It has the richest metadata (gazette numbers, status, article-level history, legal basis), plus an abstract and an FAQ PDF for each regulation. It turns out to have **an open JSON list endpoint**, so the whole POJK and SEOJK/PADK catalogue can be listed in 30 requests.

The documents themselves are highly structured: chapters, articles, paragraphs and points, an official elucidation, and annexes. But that structure exists only as typography in the PDF, so we must rebuild it ourselves.

**For legal search, the unit that matters is the provision, not the document.** An answer must cite "POJK 10/POJK.05/2022, Pasal 15 ayat (2)", know whether that provision is still in force, and pull in its elucidation, the definitions it relies on, and the PADK/SEOJK that implement it. The recommended architecture therefore has four layers:

```
 Layer 0  Immutable raw store         official PDFs, abstracts, FAQs, JDIH/ojk.go.id snapshots, source payloads
             │                         (content-addressed; exists today in data/raw/)
 Layer 1  Regulation registry          one record per instrument: identity, dates, gazette refs, status history,
             │                         files, typed relations between instruments (and between articles)
 Layer 2  Provision tree               each document parsed into addressable nodes:
             │                         BAB › Bagian › Paragraf › Pasal › ayat › huruf/angka; Penjelasan; Lampiran
 Layer 3  Search indexes (derived)     lexical (BM25-style) + vector + metadata filters + relation expansion
```

Layers 1–3 live in **one PostgreSQL database** (relational tables, built-in full-text search with an Indonesian configuration, and `pgvector`). Blobs stay in object storage. At OJK's scale this is small: about 1,000 instruments and 70 MB of text. That rules out the case for a separate vector database, search cluster or graph database at this stage. Layer 3 is always rebuildable from Layers 0–2.

## Key findings

1. **Two official channels, neither complete.** JDIH lists 973 POJK and SEOJK/PADK. Our RegAlert-based corpus has 972. Only **902 match** by canonical ID, and each side has about 70 documents the other lacks. For example, JDIH's sector listings miss most 2024 POJK and SEOJK. Completeness needs the union of sources, reconciled. ([03](03-source-access-findings.md#reconciliation-with-our-corpus))
2. **JDIH OJK is machine-readable after all.** `GET /Web/ViewPeraturan/ListDataPeraturan?sektor=NN&jenisPeraturan=NN&sLanguage=id` returns JSON. Plain HTTP with our User-Agent got `200` (not the firewall rejection recorded on 2026-10-12). This answers open question Q2. ([03](03-source-access-findings.md#jdih-ojk))
3. **ojk.go.id serves official PDFs directly**, with `Last-Modified` and a versioned SharePoint `ETag` (`"{GUID},1"`), which is useful for detecting a replaced file. It has no `robots.txt`, no sitemap, and its SharePoint REST API is closed (`401`). ([03](03-source-access-findings.md#ojkgoid))
4. **Each instrument has more than one artifact.** These are the regulation PDF (sometimes with separate annex files), an **abstract** PDF, an **FAQ** PDF, the JDIH history block, and sometimes an unofficial English page. The abstract and FAQ are OJK's own explanations and highly valuable for search. Today we collect none of them. ([01](01-how-ojk-publishes.md#what-ojk-publishes-for-one-regulation))
5. **The instrument family changed in October 2025.** SEOJK became **PADK** (Peraturan Anggota Dewan Komisioner). In 2025 PADK continued the SEOJK numbering (SEOJK 1–36/2025, then PADK 37–48/2025); in 2026 numbering restarted as `Nomor N Tahun 2026`. Existing SEOJKs remain valid and "are read as PADK". JDIH already relabels some old SEOJKs as PADK. ([01](01-how-ojk-publishes.md#numbering-and-naming))
6. **Three document shapes, not one.**
   - **POJK:** articles (Pasal) with an elucidation (Penjelasan).
   - **SEOJK:** a letter with Roman-numeral sections and no Pasal at all.
   - **New PADK:** a thin body (e.g. 5 Pasal) wrapped around a large annex that has its own chapter tree.

   A single "split on Pasal" parser would fail on about 40% of the corpus. ([02](02-document-anatomy.md))
7. **Annexes (Lampiran) hold about half of all text** (48% of characters). They contain forms, templates, technical rules and, for PADK, the actual substance. Treating them as second-class would hide the operative rules. ([02](02-document-anatomy.md#measurements-across-the-corpus))
8. **Amendments are written as editing instructions, not as consolidated text.** Example: "Ketentuan ayat (1) dan ayat (3) Pasal 115 diubah …", "Di antara Pasal 82 dan Pasal 83 disisipkan … Pasal 82A". JDIH records **partial revocations** (`Berlaku (Dicabut Sebagian)`) and **article-level links** ("Pasal Terkait: Pasal 187"). We found no consolidated versions on either official site. "What does the rule say today?" is therefore something we must derive and label as ours. ([02](02-document-anatomy.md#amending-regulations))
9. **The texts we hold lose page references and tables.** There are 15,627 page-break markers across 394 texts, tables are flattened, and page numbers are gone. A citation that points to a PDF page needs our own extraction from the official PDF (roadmap M3). ([02](02-document-anatomy.md#text-quality-issues))
10. **Official metadata is messy.**
    - JDIH titles contain typos (`Nomor34/POJK.05/2015`, `28/POJK,05/2014`, `12/POJK.05.2014`).
    - JDIH's list date is blank for 441 of 565 POJK.
    - ojk.go.id writes dates as `M/D/YYYY`, while JDIH uses `DD-MM-YYYY`.

    Identity must keep coming from the document's own header, as it does today.

## Reports

| Document | Contents |
|---|---|
| [01 — How OJK publishes](01-how-ojk-publishes.md) | Instrument types and legal weight, numbering history, the regulation lifecycle and its dates, the artifacts per regulation, publication channels |
| [02 — Document anatomy](02-document-anatomy.md) | Internal structure of POJK, amending POJK, SEOJK and PADK; corpus measurements; text-quality issues; what this means for search |
| [03 — Source access findings](03-source-access-findings.md) | Live findings for ojk.go.id and JDIH OJK (endpoints, fields, files, data quality) and the reconciliation against our corpus |
| [04 — Storage architecture](04-storage-architecture.md) | Search requirements, the four-layer design, data model, provision IDs, chunking, temporal validity, retrieval, technology choice, and what to change in this repo now |

## Recommended next steps

In order. Each step fits the existing roadmap.

1. **Add a JDIH OJK source** (`sources/jdih_ojk/`), using the JSON list endpoint for discovery and the detail page for metadata, status, history and file links. JDIH is cheap and authoritative for status, so pull it forward from M4. Run a full listing daily as both the completeness baseline and a status-change monitor.
2. **Download every official artifact:** the regulation PDF, separate annexes, the abstract and the FAQ, from JDIH and ojk.go.id, into the existing blob store with sha256 (M3).
3. **Extract text from the official PDFs ourselves, with page numbers kept** (M3). This becomes the text that citations point to; the RegAlert text becomes a cross-check.
4. **Add a deterministic structure parser** that writes a `structure.json` (the provision tree with character and page offsets) next to `text.id.md` in the corpus. It handles three shapes: POJK/PADK body, SEOJK sections, and Lampiran. This is normalization, not interpretation, so it fits this phase's scope.
5. **Record relations and status as data**, not just events: typed edges from JDIH history (with article references), plus a status history with effective dates.
6. **Build the search database in the next phase**, as a separate service that reads only from `data/corpus/` (Layers 1–3 in Postgres), and evaluate it against a gold set of real questions from sharia multifinance and consumer lending.

## Decisions needed

| # | Decision | Recommendation | Who |
|---|---|---|---|
| D1 | Make JDIH OJK the authority for status and relations? | Yes. RegAlert lineage stays a hint | Team |
| D2 | Collect abstracts and FAQ PDFs as first-class corpus artifacts? | Yes. They are official OJK explanations | Product |
| D3 | Widen collection to UU/PP/PBI/Bapepam-LK instruments that JDIH lists beside OJK's own? | Yes for the instruments OJK regulations cite as legal basis (`Mengingat`); defer the rest | Product |
| D4 | Will the search system offer point-in-time answers ("what applied on 1 March 2024")? | Yes at document level from day one; article-level consolidation later, labelled as derived | Product |
| D5 | Use PostgreSQL + pgvector as the knowledge database? | Yes. Revisit only if relevance tests show the lexical side falls short | Engineering |

## Sources

- OJK press release SP 167, SEOJK renamed to PADK: https://ojk.go.id/id/berita-dan-kegiatan/siaran-pers/Pages/OJK-Sempurnakan-Tata-Cara-Pembentukan-Peraturan-Nomenklatur-SEOJK-Berubah-menjadi-PADK.aspx
- Hukumonline, "OJK Terapkan Format Baru dalam Pembentukan Peraturan": https://www.hukumonline.com/berita/a/ojk-terapkan-format-baru-dalam-pembentukan-peraturan-lt6901ac58a72b2
- ojk.go.id draft regulations (Rancangan Regulasi): https://www.ojk.go.id/id/regulasi/otoritas-jasa-keuangan/rancangan-regulasi/Default.aspx
- ojk.go.id example detail page: https://www.ojk.go.id/id/regulasi/Pages/POJK-tentang-Perizinan-Usaha-dan-Kelembagaan-Lembaga-Penjaminan.aspx
- JDIH OJK: https://jdih.ojk.go.id/ (example detail page: `/Web/ViewPeraturan/Detail/009e5e55-ce9b-5f02-6b0b-2fdb55e5675a/09/06`, POJK 8/2026)
- Earlier project research: [RegAlert feasibility](../regalert-feasibility.md), [01 — Sources](../../plan/01-sources.md)
