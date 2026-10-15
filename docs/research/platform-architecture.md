# Platform architecture: from four connectors to one legal knowledge base

**Date:** 2026-10-14
**Scope:** the whole team's system, not only OJK. This document reviews the four connector repositories (BI, OJK, BPK, DSN-MUI) as they stand today, comments on the draft system diagram, and proposes the next steps: a shared document contract, a PostgreSQL loader, and Airflow orchestration.
**Belongs in:** a shared platform repository once one exists. It lives here for now because the team interface also started here.

## 1. Where the four connectors stand

All four repositories hold an **identical copy of interface v1.0** (same checksum for `CONNECTOR_INTERFACE.md` and `source_connector.py`). That is a good base. Beyond the interface, they diverge in everything a loader cares about.

| | OJK | BPK | BI | DSN-MUI |
|---|---|---|---|---|
| Source | RegAlert API (official ojk.go.id / JDIH planned) | `peraturan.bpk.go.id`, a curated list of 130 targets | `bi.go.id` (SharePoint) | `dsnmui.or.id` listing + Google Drive PDFs |
| Covers | POJK, SEOJK, PADK | National laws and ministerial rules (UU, PP, Perpres, PMK …). OJK and BI rules explicitly excluded | PBI, PADG, SEBI | Fatwa (≈166), pedoman, ta'limat: three connectors |
| Interface v1.0 class | `OJKConnector` (`ojk`) | `BPKConnector` | `BankIndonesiaSourceConnector` (`bi-website`) | `DsnFatwaConnector`, `DsnPedomanConnector`, `DsnTalimatConnector` |
| Standalone store | SQLite + blobs + `data/corpus/` | SQLite + blobs + `data/corpus/` | **Its own PostgreSQL** + PDFs + JSON export | SQLite + blobs + text files + JSONL deltas |
| Volume | 973 documents, 70 MB text | 194 files, 1.03 GB, 31,093 pages | No data in the local checkout | ≈166 fatwas, ≈1 GB PDFs |
| Identity | Canonical `ojk:pojk:2014:5` from the document header | `bpk:{record_id}`; no canonical ID by decision (D4) | Source URL; database integer | `162-DSN-MUI-VII-2025` |
| Text | Indonesian text from RegAlert, **no page numbers** | Self-extracted, page markers, OCR | Per page, OCR fallback | Latin text per page + Arabic OCR per page (≈80–90% accurate) |
| Structure (Pasal …) | None yet | None | **Yes**: Bab, Bagian, Pasal, Ayat, Huruf with page spans | None (fatwas have their own format) |
| Relations | RegAlert lineage + standing checks | BPK relations with record IDs and citations | Table defined, **never written** | None (ta'limat and pedoman relate to fatwas, but not recorded) |
| Status | `standing` (amended, superseded) | `Berlaku` / `in_force` | Default `UNVERIFIED` | None |
| Python | ≥ 3.11 | ≥ 3.11 | **≥ 3.13** | ≥ 3.11 |
| Tests | pytest, offline | pytest, offline + architecture test | One script | pytest, offline fixtures |

**What this tells us:**
- **The interface moves bytes, not meaning.** `discover`/`fetch` carry raw files and pass-through metadata, by design ("We'll design a common schema once we've compared what all our sources hold"). Everything a search system needs lives *outside* the interface, in each repo's standalone pipeline: OJK's canonical IDs, BPK's relations and status, BI's provisions, DSN's cleaned text and Arabic OCR. A loader fed only through v1.0 would get PDFs and raw fields and would have to redo all of that. **The missing piece is the common document schema, and now is the time to define it.**
- **Ownership by issuer already works out.** BPK's `watch.toml` ignores OJK and BI rules ("which their own connectors collect"). Each issuer's documents have exactly one owning connector.
- **Every repo has its own output shape**, including two contract documents in DSN-MUI (`CONTRACT.md` for the CLI, the interface for Python).

### Issues to fix before anything is loaded

| Repo | Issue | Why it matters | Fix |
|---|---|---|---|
| BI | `httpx.Client(verify=False)` | TLS checks are off: a man-in-the-middle could feed us forged regulations | Remove; pin the CA bundle if BI's chain is broken |
| BI | Browser User-Agent (`Mozilla/5.0 … Chrome/122`); README says "Bypass F5 BIG-IP, Anti-bot Cookies" | Breaks interface rule 5 ("never get around CAPTCHAs or bot checks") and is a legal/reputational risk for a compliance product | Identify honestly (`metamorph-ingest/0.x (+contact)`). If BI blocks us, ask BI for access |
| BI | `discover(None)` stops after `max_pages` (default 2) | Breaks the interface rule "`discover(None)` lists everything", so a backfill silently misses most of BI | Page until the listing ends when the cursor is `None` |
| BI | `version` = URL slug | Never changes when a PDF is replaced, so changes go undetected | Use file ETag/Last-Modified or the page's modified date |
| BI | `fetch` logs and skips a failed PDF, returning success with just the HTML | Silent data loss: the document looks fetched but has no text | Raise `ItemFailed` when the main document fails |
| BI | Owns a multi-source PostgreSQL schema (`sources` table: "BI, OJK, DSN-MUI") | Two competing "central" databases | Keep BI's database private to BI (or move to SQLite like the others); the central schema belongs to the platform |
| DSN-MUI | `version` = PDF link; byte changes need `sync --all` | A silently replaced fatwa PDF is only seen on a full pass | Acceptable (Drive gives no ETag); schedule a monthly full pass |
| DSN-MUI | Absolute paths in its SQLite (`raw_path`) | Breaks when moved into a container | Relative paths, as its own doc suggests |
| OJK | Text has no page numbers; depends on RegAlert | Citations can't point to a PDF page | Official PDFs + self-extraction (OJK roadmap M3) |
| BPK | No canonical ID (by decision D4) | Can't link to it across sources | Derive `id:{form}:{year}:{number}` in its normalizer (below) |

## 2. Review of the draft diagram

The draft gets the big shape right: connectors are isolated, an orchestrator runs them, a curated database sits in the middle, retrieval is separate from storage, and the chatbot sits on top. The **human verify/edit/delete/add loop** is especially valuable for a legal product. Seven changes would make it buildable:

1. **Add a "Normalize and link" stage between the orchestrator and the database.** Raw data can't go straight into embeddings. In between come: canonical IDs, merging sources per document, parsing documents into provisions, and turning citations into links across sources (an OJK article citing a DSN-MUI fatwa). This is where the cross-source graph is built, and no single connector can build it.
2. **The orchestrator controls the flow; data doesn't pass through it.** Airflow calls connectors and moves *references*. Bytes go to object storage (the `FileStore` the interface requires), and records go to PostgreSQL. Draw an object-storage box. It is currently missing, and every connector needs it.
3. **Rename "Data Ingestion" (the RAG box) to "Knowledge and retrieval".** Ingestion ends at the database. That box serves queries.
4. **Put a retrieval API in front of the RAG.** The chatbot and agents call `search`, `get_provision`, `expand` and `as_of`, never the vector or graph store directly. Then the storage behind the API can change without touching the agents.
5. **The citation system is data, not a separate system.** Citations come from provision addresses and page numbers stored in the registry. The API formats them.
6. **Start with vector search and graph inside PostgreSQL** (`pgvector` + link tables). At our scale (a few thousand documents, a few hundred thousand chunks) a separate vector database and graph database add two sync pipelines and buy nothing yet. Behind the API, either can be split out later if measurement shows a need.
7. **Edits by the Metamorph team are overlays, never in-place changes.** "Edit" and "delete" on source data would be undone by the next ingestion, or worse, would block it. Store corrections as `curation` records (who, why, when) on top of source values. Delete becomes "suppress"; add becomes a document with `source = manual`. Raise a conflict when the source later changes a field someone corrected.

(The vertical line leaving the RAG box at the bottom is cut off in the draft. If it leads to an evaluation or feedback loop, keep it: see step 6 of the plan.)

### Revised diagram

```
DATA / SOURCE MANAGEMENT
┌─────────────────────────────────────────────────────────────────────────────┐
│ Connectors: one repo and one container image each                           │
│   BI · OJK · DSN-MUI (fatwa, pedoman, ta'limat) · BPK (national laws) · …   │
│   interface: discover · fetch · normalize                                   │
└──────────────┬──────────────────────────────────────────────────────────────┘
               │ Airflow runs each connector in its own container
               ▼
  Object storage (raw bytes, write-once)      Postgres `ingest` (items, cursors, files, runs)
               │ normalized document packages (contract v2)
               ▼
  Loader (one, shared) ───────► Postgres `registry` ◄──── Postgres `curation`
               │                 documents, files,        Metamorph team UI:
               ▼                 texts, provisions,       verify · correct · suppress · add
  Resolver ─────────────────►    links, status
  citations → links, identity merge, validity in time
               │
KNOWLEDGE AND RETRIEVAL
               ▼
  Index build ─► Postgres `serving`: chunks + lexical index + pgvector + link projection
               ▼
  Retrieval API: search(q, filters, as_of) · get_provision · expand(links) · citation format
               │
USER INTERFACE ▼
  Chatbot and task agents (law search, market search) for international clients
```

## 3. The key decision: a shared document contract (v2)

**Modular by source for acquisition, shared for meaning.** Each repo keeps what only it knows: how its site works, how its documents are numbered, its quirks. It outputs one shared shape. One loader, owned by the platform, writes that shape into PostgreSQL.

### Interface v2: add `normalize`

```python
class SourceConnector(ABC):
    def discover(self, cursor) -> Discovery: ...                  # unchanged
    def fetch(self, item, store) -> Fetched: ...                  # unchanged
    def normalize(self, item, fetched, store) -> NormalizedDocument: ...   # new
        # offline and deterministic: reads only stored bytes, so it can be re-run
        # for every stored document whenever the rules improve
```

A `NormalizedDocument` holds pointers, not payloads (DSN-MUI's good idea): texts and files are blobs in object storage, referenced by sha256.

```jsonc
{
  "contract_version": "2.0",
  "source":     { "connector": "ojk", "connector_version": "0.4.0", "source_id": "regalert:172f…",
                  "url": "…", "fetched_at": "…" },
  "document": {
    "id": "ojk:pojk:2014:5",                       // canonical, issuer-namespaced (see below)
    "id_confidence": "high", "id_evidence": "text_header='… NOMOR 5/POJK.05/2014'",
    "issuer": "ojk", "family": "pojk", "number": "5", "year": 2014,
    "citation": "POJK 5/POJK.05/2014", "aliases": ["5/POJK.05/2014", "POJK 5/2014"],
    "title": "…", "language": "id",
    "kind": "regulation",                          // law | regulation | implementing_rule | fatwa
                                                   // | guideline | circular | explanatory
    "dates":  { "enacted": null, "promulgated": null, "effective": "2014-04-08" },
    "status": { "value": "in_force", "as_of": "2026-10-14", "source": "regalert" }
  },
  "files":  [ { "sha256": "…", "role": "main", "media_type": "application/pdf",
                "source_url": "…", "pages": 41, "rights": "public" } ],
              // role: main | annex | abstract | faq | page_html | api_payload
  "texts":  [ { "sha256": "…", "of_file": "…", "language": "id", "origin": "self_extracted",
                "method": "text_layer", "paged": true, "rights": "public" } ],
              // origin: self_extracted | source_text | ocr | source_translation
  "provisions": [],                                // optional: BI can fill it today
  "citations": [                                   // outgoing references, as found
    { "relation": "legal_basis", "target_text": "Undang-Undang Nomor 21 Tahun 2011 …",
      "target_id": "id:uu:2011:21", "from": "mengingat/angka-1", "to": null,
      "evidence": "…", "found_in": "text", "confidence": "high" }
  ],
  "source_fields": { }                             // the source's own fields, unchanged
}
```

Ship v2 as a **small installable package** (`metamorph-contract`: the interface, the dataclasses, a JSON Schema and a validator) instead of a copied file. Each repo pins a version, and CI checks packages against the schema.

### Canonical IDs: named by issuer, not by source

| Issuer | Families | ID example | Owning connector |
|---|---|---|---|
| OJK | `pojk`, `seojk`, `padk` | `ojk:pojk:2019:10` | OJK |
| Bank Indonesia | `pbi`, `padg`, `sebi` | `bi:pbi:2021:6` (from `23/6/PBI/2021`): uniqueness per year to confirm with the BI owner | BI |
| DSN-MUI | `fatwa`, `pedoman`, `talimat` | `dsnmui:fatwa:117`: fatwa numbers run continuously across years, so the number alone is unique and matches how fatwas are cited | DSN-MUI |
| Government of Indonesia | `uu`, `pp`, `perpres`, `pmk`, `permenkum` … | `id:uu:2011:21`, `id:pmk:2017:23` | BPK (BPK is the *source*; the issuer is the state) |

Rules:
- **One owner per issuer.** If a non-owning source also lists a document, it is attached as a second source of the same document, never as a new one.
- **Aliases are first-class:** `5/POJK.05/2014`, `POJK 5/2014`, `Fatwa DSN-MUI No. 117/DSN-MUI/II/2018`. The resolver needs them to turn citation text into links.
- **Low confidence never blocks a load.** The document loads with `id_confidence = low | none` and goes to the curation queue.

### Link vocabulary

`amends`, `revokes`, `partly_revokes`, `supersedes`, `implements` (PADK/SEOJK → POJK article; pedoman → fatwa), `legal_basis` (Mengingat), `adopts_fatwa` (POJK/PBI → DSN-MUI fatwa: **the link specific to the sharia market**), `changes_fatwa` (ta'limat → fatwa), `refers_to`. Every link records source, evidence, confidence and an effective date.

**Connectors report citations; the platform resolves them.** OJK writes "cites *Fatwa DSN-MUI No. 117*" with `target_id` filled in if it can parse it. The resolver links it once `dsnmui:fatwa:117` exists, and that may happen in a later run, after DSN-MUI loads it. No connector ever needs another connector's data.

### What is shared by function, not by source

OJK, BI and BPK documents all follow Indonesia's statutory drafting format (UU 12/2011): Menimbang, Mengingat, BAB, Pasal, ayat, Penjelasan, Lampiran. **The structure parser should be one shared library**, not three copies. BI's parser is the best starting point. It runs centrally after loading, for every text that has no `provisions`. DSN-MUI fatwas have a different form (Menimbang, Mengingat, Memperhatikan, MEMUTUSKAN, then numbered "Ketentuan"), so they get their own grammar in the same library.

## 4. PostgreSQL: four schemas, one owner

| Schema | Written by | Holds | Read by |
|---|---|---|---|
| `ingest` | Airflow tasks | Discovered items, cursors, fetched files, runs, item errors (the orchestrator's state under the interface) | Airflow, monitoring |
| `registry` | Loader + resolver only | Documents, sources per document, aliases, files, texts (versions), provisions, links, status history: the **system of record** | Index build, retrieval API, curation UI |
| `curation` | Metamorph team UI | Overrides, suppressions, manual documents, review decisions, conflicts | Loader (to respect them), registry views |
| `serving` | Index build only | Chunks with context headers, lexical index, `pgvector` embeddings, link projection, index versions | Retrieval API only |

The **effective view** of a document is the registry values with curation overrides applied. Agents never read `registry` tables directly, only through the API. `serving` can be dropped and rebuilt from `registry` at any time, and can later move to its own cluster.

### The loader

One shared component (it can live in the platform repo as a library plus a CLI):

1. **Validate** each package against the v2 JSON Schema. If invalid, put it in `ingest.quarantine` and raise an alert. Never load half a document.
2. **Make it idempotent.** Key on (connector, source_id, package hash): the same package twice is a no-op.
3. **Upsert** `registry.document_source`, then the document by canonical ID (merging sources). Add a new `text_version` when a text's sha256 changes. Old versions are kept, and `current` moves.
4. **Respect curation.** Never overwrite an overridden field. If the source value changed underneath an override, write a `curation.conflict`.
5. **Store citations unresolved,** with their evidence, and leave resolution to the resolver.
6. **Use one transaction per document** and emit an event: an Airflow Dataset update that triggers the central DAG.

## 5. Airflow

- **Run each connector in its own container image** (`DockerOperator` / `KubernetesPodOperator`), not by importing it into the Airflow environment. The repos need different Python versions (BI ≥ 3.13) and system tools (Tesseract, Ghostscript, Arabic language data). Images keep the modularity you want, at runtime as well as in source code.
- **One DAG per connector:** `discover → select new/changed → fetch (dynamic task mapping, pool size = max_concurrency) → normalize → load`. Schedule: OJK, BI and BPK daily; DSN-MUI weekly, plus a monthly full pass.
- **Error mapping (already in interface v1.0):**
  - `SourceUnavailable`: stop the DAG run cleanly and retry at the next schedule.
  - `AuthFailed`: fail and page a person.
  - `ItemFailed(retryable)`: retried next run.
  - `ItemFailed(retryable=False)`: parked until the source version changes.
- **One central DAG `knowledge_build`**, triggered by the loader's Datasets: resolve links → recompute validity → update the serving index → run the evaluation smoke test → publish the index version.

### A pragmatic first stage

Three repos already produce rich standalone output, and none implements `normalize` yet. So migrate in two stages:

- **Stage A (now):** Airflow runs each connector's own pipeline in its container (`ojk-connector run`, `bpk-connector run`, `dsn sync`, BI `sync`), then its new `export --format v2` command. The loader reads the packages. Each connector's SQLite state lives on a persistent volume. Data reaches PostgreSQL in weeks, with little rewriting.
- **Stage B (later):** Move to `discover/fetch/normalize` tasks, so Airflow owns state, retries and concurrency. The normalizer written in Stage A becomes the `normalize` method.

## 6. Plan

| Step | Deliverable | Owner | Depends on |
|---|---|---|---|
| 0 | **Fix the BI issues** (TLS, User-Agent, full discovery, version, silent PDF loss) | BI | — |
| 1 | **Agree contract v2:** `NormalizedDocument`, ID scheme per issuer, link vocabulary, rights values. Publish `metamorph-contract` with the JSON Schema and validator | Whole team (one session, then a PR) | — |
| 2 | **Platform repo:** Docker Compose with PostgreSQL 16 + `pgvector` and MinIO (S3-compatible). Migrations for `ingest`, `registry`, `curation` (Alembic or plain SQL) | Platform | 1 |
| 3 | **`export --format v2` per connector** (Stage A). Order: OJK (canonical IDs exist), BPK (relations exist), DSN-MUI (simple), BI (provisions exist) | Each owner | 1 |
| 4 | **Loader** with validation, idempotency, curation respect and quarantine, tested with real packages from step 3 | Platform | 2, 3 |
| 5 | **Resolver + shared structure parser:** citations → links via aliases; provisions for texts without them; validity from links and status | Platform | 4 |
| 6 | **Airflow:** one image per connector, per-connector DAGs (Stage A), `knowledge_build` DAG, alerts | Platform | 3, 4 |
| 7 | **Curation UI (MVP):** unresolved IDs, quarantine, conflicts, suppress/override | Platform | 4 |
| 8 | **Serving index + retrieval API + evaluation set** (about 100 real questions on sharia multifinance, P2P lending, BNPL and fatwa requirements, with the correct provisions) | Platform | 5 |
| 9 | **Stage B:** interface v2 `normalize` in each repo; Airflow drives discover/fetch/normalize | Each owner | 6 |

**Exit criteria for steps 1–4:** all four connectors' documents in `registry`, each with a canonical ID or a curation item, and its files, texts, sources and unresolved citations. Loading the same packages twice changes nothing.

## 7. Decisions needed

| # | Decision | Recommendation |
|---|---|---|
| P1 | Interface v2 with `normalize`, or keep v1.0 and normalize centrally? | v2. Source knowledge stays with each source's owner |
| P2 | IDs named by issuer (`id:uu:…` for BPK records)? | Yes |
| P3 | Vector and graph in PostgreSQL first? | Yes, behind a retrieval API |
| P4 | Curation as overlays? | Yes. Never edit source rows |
| P5 | Connectors as container images under Airflow? | Yes |
| P6 | Stage A (export from standalone pipelines) before Stage B? | Yes |
| P7 | Where the platform code lives | A new `Metamorph-Knowledge-Platform` repo: contract package, loader, resolver, parser, schemas, DAGs, API |
