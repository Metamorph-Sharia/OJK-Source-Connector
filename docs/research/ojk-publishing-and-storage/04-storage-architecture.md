# 04 — Storage architecture for AI-powered legal search

Part of the [OJK publishing and storage report](README.md). This builds on the current [architecture](../../plan/02-architecture.md). It keeps the connector's ingestion store and the `data/corpus/` hand-off unchanged, and adds what the later search phase needs.

## What legal search needs from storage

| # | Requirement | Why, in OJK terms |
|---|---|---|
| R1 | **Every answer cites a provision**, down to ayat and huruf, and back to the official PDF page | Users act on "POJK 10/POJK.05/2022 Pasal 15 ayat (2)", not on a document. An uncited or wrongly cited answer is worse than none |
| R2 | **Validity in time**, per document *and per article* | 139 POJK are no longer in force; 11 are partly revoked; amending regulations change single articles. "What applies now" and "what applied on date X" are different questions |
| R3 | **Typed relations**: amends, revokes (fully or partly), implements, legal basis, supersedes, refers to | A POJK article is incomplete without the PADK/SEOJK that implements it and the regulation that later amended it |
| R4 | **Context travels with a hit**: headings, Penjelasan, definitions | Articles say "as referred to in paragraph (1)"; defined terms differ by regulation |
| R5 | **Hybrid retrieval**: exact citations, Indonesian legal vocabulary, English questions, metadata filters | Users type "POJK 35/2018", "pembiayaan syariah", and "can a sharia financing company offer BNPL?" |
| R6 | **Provenance and rights**: every text traces to file bytes, extractor and version; only exportable rights are indexed | Auditability for a compliance product; RegAlert's English text stays restricted until licensed |
| R7 | **Change propagation**: new or amended instruments reach the index within 24 hours; old versions are kept | Roadmap definition of done; past answers must stay explainable |
| R8 | **Fits the scale** | About 1,000–1,500 instruments, 70 MB of text, an estimated 40–80k search chunks |

## Recommended architecture

```
                         ojk-connector (this repo)                     │   knowledge database (next phase)
                                                                       │
 sources ──► Layer 0  RAW STORE (content-addressed, immutable)         │
 RegAlert     data/raw/{sha[:2]}/{sha}: PDFs, annexes, abstracts,      │
 JDIH OJK     FAQs, detail-page HTML, API payloads                     │
 ojk.go.id         │                                                   │
 BPK               ▼                                                   │
            ingestion store (SQLite, today) ──► data/corpus/ ──────────┼──► loader ──► PostgreSQL
            source records, texts, events      the hand-off contract   │               Layer 1  registry
                                               metadata.json           │               Layer 2  provisions
                                               text.id.md              │               Layer 3  search (FTS + pgvector)
                                               structure.json   (new)  │                    │
                                               *.pdf            (new)  │                    ▼
                                                                       │               retrieval API → AI assistant
```

- **This repo stays an ingestion connector.** It gains official files, JDIH metadata, relations and a structure parser. All of these are deterministic normalization, within this phase's scope.
- **`data/corpus/` stays the only interface** between ingestion and search. The search side never calls a source.
- **The knowledge database is built from the corpus** and can be rebuilt from it at any time. Layer 3 indexes can be rebuilt from Layers 1–2 alone.
- **The schema is agency-neutral** (`ojk:` prefixes, generic instrument kinds), so the team's other regulator connectors can load into the same database.

## Data model (Layers 1–2)

A simplified FRBR model, the standard way legal-information systems separate *the law* from *its texts* and *its files*:

```
instrument (the regulation as a legal act)
  ├── alias            every way it is cited: "5/POJK.05/2014", "POJK 5/2014", JDIH relabel "padk:2016:39"
  ├── source_record    which sources list it (regalert, jdih_ojk, ojk_website, bpk) and their ids
  ├── file             official PDFs: regulation, annex n, abstract, FAQ (sha256, url, etag, pages)
  ├── text_version     an extracted text: origin, extractor + version, language, authoritative?
  │     └── provision  the tree: BAB › Bagian › Paragraf › Pasal › ayat › huruf › angka,
  │                    Penjelasan per Pasal/ayat, Lampiran sections, SEOJK sections
  ├── definition       term → definition, from Pasal 1 / SEOJK I.1
  ├── relation         typed edge to another instrument, optionally provision → provision
  └── status_event     status over time, for the instrument or a single provision
```

Sketch (PostgreSQL):

```sql
CREATE TABLE instrument (
  id              text PRIMARY KEY,          -- 'ojk:pojk:2018:35'
  agency          text NOT NULL,             -- 'ojk'
  family          text NOT NULL,             -- pojk | seojk | padk | uu | pp | ...
  kind            text NOT NULL,             -- regulation | implementing_rule | law | ...
  number int, year int,
  title_id        text NOT NULL,             -- Indonesian title (authoritative)
  sector          text[],                    -- as given by JDIH / ojk.go.id; not our classification
  addressees      text[],                    -- SEOJK "Yth." lines
  enacted_on date, promulgated_on date, effective_from date, effective_to date,
  gazette_ln text, gazette_tln text,         -- '2026 (16/OJK)'
  status          text NOT NULL,             -- in_force | partly_revoked | revoked | ...
  status_source   text NOT NULL              -- jdih_ojk | bpk | regalert | text
);

CREATE TABLE provision (
  id              text PRIMARY KEY,          -- 'ojk:pojk:2018:35/pasal-115/ayat-1'
  instrument_id   text REFERENCES instrument,
  text_version_id bigint REFERENCES text_version,
  parent_id       text REFERENCES provision,
  kind            text NOT NULL,             -- bab | bagian | paragraf | pasal | ayat | huruf | angka
                                             -- | penjelasan | lampiran | section | menimbang | mengingat
  label           text,                      -- 'Pasal 115', 'ayat (1)', 'II.A'
  heading         text,                      -- BAB/Bagian/section titles
  ordinal         int,
  body            text,
  char_start int, char_end int, page_start int, page_end int,
  valid_from date, valid_to date             -- article-level validity (R2)
);

CREATE TABLE relation (
  id bigserial PRIMARY KEY,
  type            text NOT NULL,             -- amends | revokes | partly_revokes | implements
                                             -- | legal_basis | supersedes | refers_to
  from_instrument text NOT NULL, from_provision text,
  to_instrument   text NOT NULL, to_provision   text,   -- 'Pasal Terkait: Pasal 187'
  effective_on    date,
  source          text NOT NULL,             -- jdih_ojk | text | regalert | bpk
  evidence        text, confidence text
);
```

`file`, `text_version`, `definition`, `alias`, `source_record` and `status_event` follow the same pattern. Every row records its source and evidence, as the connector's identity rules already do.

### Provision addresses

Stable, readable and derived only from the document's own numbering, so the same address comes out on every re-parse:

| Provision | Address |
|---|---|
| POJK article, paragraph, point | `ojk:pojk:2018:35/pasal-115/ayat-1/huruf-a` |
| Inserted article | `ojk:pojk:2018:35/pasal-82a` |
| Elucidation of an article | `ojk:pojk:2018:35/penjelasan/pasal-115/ayat-1` |
| General elucidation | `ojk:pojk:2018:35/penjelasan/umum` |
| Amending instruction | `ojk:pojk:2022:7/pasal-i/angka-2` |
| SEOJK section | `ojk:seojk:2020:1/bagian-ii/huruf-a/angka-3` |
| Annex section | `ojk:padk:2026:1/lampiran-i/bab-ii/…` |
| Legal basis entry | `ojk:pojk:2014:5/mengingat/angka-1` |

## Temporal validity (R2)

Three levels, built in this order:

1. **Instrument status with dates.** This comes from JDIH (authoritative) and the Ketentuan Penutup, and it is available as soon as the JDIH source exists. The default search filter is "in force on the query date". Revoked instruments stay searchable on request, clearly labelled.
2. **Article-level validity.** The `valid_to` of a provision is set from `partly_revokes` and `amends` relations. Those relations come from JDIH's article references and from parsing amending regulations' `Pasal I` instructions (*diubah*, *disisipkan*, *dihapus*). A hit on POJK 35/2018 Pasal 115 then says "amended by POJK 7/POJK.05/2022 Pasal I angka 2" and shows both texts.
3. **Consolidated versions (later).** Apply the amendment instructions to produce "POJK 35/2018 as in force on 2024-01-01". OJK publishes no consolidations, so these are **our derived texts**, stored as their own `text_version` with `origin = consolidated`, never presented as official, and always linked to the source provisions.

## Chunking: what goes into the index

| Content | Chunk unit | Notes |
|---|---|---|
| POJK / PADK body | **One Pasal** | Split at ayat when a Pasal is longer than about 800 tokens, repeating the Pasal's opening words. Don't merge small Pasal, to keep citations precise |
| Penjelasan | Joined to its Pasal/ayat chunk as a separate field | Indexed for recall, shown separately. "Cukup jelas" dropped |
| Pasal 1 definitions | One chunk per definition *and* a `definition` row | Lets the assistant expand a defined term in any article of that regulation |
| SEOJK | One numbered section (e.g. `II.A`) | Same size cap; split at the next numbering level |
| Lampiran | One annex heading section | Size cap; tables also kept as page references to the PDF; ToC blocks skipped |
| Menimbang / Mengingat | Not separate search hits | Stored as provisions and turned into relations (legal basis, implements) |
| Abstract, FAQ | Their own chunks, typed `explanatory` | Ranked below binding text and labelled as OJK guidance, not regulation |

**Each chunk is embedded with a context header**, so a bare "as referred to in paragraph (1)" still retrieves well:

```
POJK 35/POJK.05/2018 tentang Penyelenggaraan Usaha Perusahaan Pembiayaan
› BAB V … › Bagian Kedua … › Pasal 115   [Berlaku · diubah oleh POJK 7/POJK.05/2022]
<article text>
Penjelasan: <elucidation text>
```

Filter columns on every chunk: `agency`, `family`, `kind` (binding / implementing / explanatory), `sector`, `valid_from`, `valid_to`, `rights`, `instrument_status`.

## Retrieval flow (the capability storage must enable)

```
question ──► 1. citation lookup     "POJK 10/2022", "Pasal 15" → alias table → exact provisions
         ──► 2. lexical search      Indonesian full-text; citation tokens in a separate keyword field
         ──► 3. vector search       multilingual embeddings (English or Indonesian question)
                 with filters: in force at date, family, sector, rights
         ──► 4. fuse                reciprocal rank fusion of 1–3
         ──► 5. expand via graph    parent headings, Penjelasan, definitions used, amending provisions,
                                    implementing PADK/SEOJK sections, newer replacing regulation
         ──► 6. rerank              cross-encoder on the expanded set
         ──► answer with provision addresses + PDF page links
```

Step 5 is what separates legal search from generic RAG. It needs relations and provisions as data (Layers 1–2), not just text chunks.

## Technology choice

| Option | Verdict |
|---|---|
| **PostgreSQL + full-text search + `pgvector`** (one database) | **Recommended.** Relational integrity for registry, provisions and relations; recursive queries for relation expansion; a built-in `indonesian` Snowball text-search configuration; HNSW vector index; filters and joins in one query; transactions keep text, chunks and vectors in step. At an estimated 40–80k chunks × 1,024 dimensions (about 160–330 MB of vectors), it is well within one instance |
| OpenSearch / Elasticsearch | Better BM25 scoring and analyzers. Adds a second system to keep in sync. **Add it as a derived index only if** the relevance evaluation shows Postgres's lexical ranking falls short |
| Dedicated vector database (Qdrant, Weaviate, Pinecone …) | Not needed at this scale. It would split the truth across two stores and complicate validity filters |
| Graph database (Neo4j …) | Relations number in the thousands; recursive SQL handles them. Revisit only if a multi-regulator graph grows by orders of magnitude |
| File corpus only (`data/corpus/`, today) | Keep it as the auditable hand-off and the rebuild source. It is not queryable |
| SQLite (the ingestion store, today) | Right for a single-writer connector. Not for a multi-user search service |

Implementation notes:
- **Normalize citations before indexing** (`10/POJK.05/2022`, `POJK 10/2022`, `Nomor 10 Tahun 2022` → `ojk:pojk:2022:10`). Store them in a keyword column, because a text tokenizer breaks them apart.
- **Choose the embedding model by evaluation, not by reputation.** Use a multilingual model (candidates include BGE-M3 and multilingual-E5) for English questions over Indonesian text. Translating the *question* into Indonesian as an extra query is cheaper than translating the corpus, and avoids the open licence question on RegAlert's translations.
- **Version the index build:** record the extractor, parser and embedding versions on every chunk, so re-indexing is a controlled migration.

## What to change in this repository now

In priority order. Each item is ingestion or normalization, inside this phase's scope.

| # | Change | Corpus effect | Roadmap |
|---|---|---|---|
| 1 | **JDIH OJK source**: discovery via `ListDataPeraturan` (30 requests per full sweep), detail-page parser, daily full listing as completeness baseline and status monitor | `metadata.json` gains JDIH GUID, dates, LN/TLN, status, sector, legal basis | Pull forward from M4 |
| 2 | **Official artifacts**: regulation PDF, annex files, abstract, FAQ, from JDIH and ojk.go.id; ETag/Last-Modified change checks | `original.pdf`, `annex-{n}.pdf`, `abstract.pdf`, `faq.pdf` | M3 |
| 3 | **Self-extraction with page numbers** (`pymupdf`, OCR fallback); tables kept from annexes where possible | `text.id.md` carries page markers; RegAlert text kept as a cross-check | M3 |
| 4 | **Structure parser** with three grammars (POJK/PADK body, SEOJK sections, Lampiran) plus page-break repair and ToC skipping | New `structure.json`: provision tree with addresses, headings, char and page spans | New, after M3 |
| 5 | **Relations and status as records**: JDIH Riwayat (with article references), `Mengingat`, SEOJK/PADK "implements Pasal …", Ketentuan Penutup revocations, amending `Pasal I` instructions | `metadata.json` gains `relations[]` and `status_history[]` | M4 |
| 6 | **Structure quality checks in the run report**: Pasal numbering continuity, Penjelasan articles ⊆ body articles, ayat continuity; a `structure_suspect` alert | Per-document structure quality score | With item 4 |

Then, in the next phase: a loader from `data/corpus/` into PostgreSQL (Layers 1–2), the chunk and index build (Layer 3), and **a relevance evaluation set**. That is about 100 real questions from the target market, each with its correct provisions. Seed topics: sharia financing (POJK 10/POJK.05/2019), financing companies (POJK 35/POJK.05/2018 and its amendments), P2P lending (POJK 10/POJK.05/2022 → POJK 40/2024, whose Pasal 187 POJK 8/2026 revokes), and BNPL (POJK 32/2025, PADK 2/2026). Measure recall@10 of the correct provision, citation accuracy, and **validity accuracy** (never presenting revoked or amended text as current).

## Risks for this design

| Risk | Mitigation |
|---|---|
| Structure parsing errors (unusual layouts, OCR text) | Quality checks (item 6); documents that fail fall back to heading-based chunks, flagged and still searchable |
| Amendment instructions misparsed, so wrong article validity | Article-level validity comes from JDIH references first and parsed instructions second; parsed edges carry confidence; consolidations stay labelled as derived |
| Official sites change layout or block us | Raw snapshots allow offline re-parsing; several sources per instrument; existing drift alerts |
| Index drifts from the registry | Layer 3 is derived and versioned; rebuild from Layers 1–2 rather than patching it |
| Explanatory material (FAQ, abstract) quoted as binding law | `kind = explanatory` on chunks; ranked and labelled separately |
