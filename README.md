# OJK Source Connector (`ojk-connector`)

Discovers, fetches, normalizes, stores and monitors the regulations of **OJK** (Otoritas Jasa Keuangan, Indonesia's Financial Services Authority).

This repository covers **OJK only**. Each other regulator has its own connector repository. Every connector implements the same [team interface](contract/CONNECTOR_INTERFACE.md), so the orchestrator in the next phase (probably Airflow) runs them all the same way.

It is the data foundation for a later AI-powered market-entry and compliance platform for businesses entering Indonesia's sharia multifinance and consumer lending markets. Every document is **stored locally** as a self-contained corpus that a later phase will turn into a knowledge database.

> **Status (2026-10-16):** the primary source is now **JDIH OJK**, OJK's own legal database. RegAlert was retired when our account was banned; its 977 stored records (973 read) stay and keep exporting.
> - JDIH OJK lists 973 regulations. Its catalogue entry (official dates, standing, history, legal basis, file list) is read for every one of them, including those whose text RegAlert supplied.
> - Official files are downloaded, and the text extracted, only for documents no other record holds yet, or whose held text is not the instrument. 15 were read on 2026-10-16 (7 samples, then the 8 the [registry RCA](../Metamorph-EDA/2026-10-16-registry-failure-rca/reports/RCA-report.md) found holding a web page instead of the regulation: `fetch --document <id>`), then the other 77: the 64 RegAlert never had and 13 held as a web page or annex. 3 stay queued: their held text is already the regulation.
> - Known gap: 67 regulations we hold (mostly POJK 2024 no. 10–48, PADK 2026 and POJK 2026 no. 11–16) are in no JDIH listing. See [risks](docs/plan/04-risks-and-open-questions.md).
>
> See the [roadmap](docs/plan/03-roadmap.md).

## Scope of this phase

The priority is **collecting data reliably and automatically.** We collect OJK regulations broadly and do **not** filter them by business relevance.

**Out of scope for now:** relevance classification (rule-based or AI), legal interpretation or compliance advice, extracting obligations, Q&A or RAG, user-facing apps, and dashboards beyond what's needed to show that ingestion is reliable.

## Quick start

```bash
uv sync                                        # Python ≥ 3.11; installs httpx, beautifulsoup4, pymupdf (+ pytest for dev)
uv run pytest                                  # run the tests

uv run ojk-connector run                       # the whole pipeline, standalone (daily); JDIH OJK needs no sign-in
uv run ojk-connector health                    # for a scheduler: exit 0 ok / 1 warning / 2 critical
uv run ojk-connector review                    # records that need a person's attention
uv run ojk-connector events --since 2026-10-01 # what changed: new, changed, missing, amended, duplicates
uv run ojk-connector export --format v2 --out data/exports/v2/RUN   # a contract v2 bundle for the platform loader
```

What each command does:
- **`run`** goes through every stage:
  1. Discovers documents: JDIH OJK's whole listing (20 requests). A full re-list every 7 days catches removals and format drift.
  2. Reads up to 25 documents, one at a time, newest first: the detail page, every file it lists, and the text of the main PDF. Documents another record already holds are skipped (dedup), so nothing is downloaded twice.
  3. Checks whether stored regulations were amended or replaced (sources with standing checks).
  4. Reads up to 100 catalogue entries (one page each, no files), never-read first. A changed entry is an event; a changed standing is a warning.
  5. Deduplicates.
  6. Rebuilds `data/corpus/`.
  7. Evaluates the alert rules and writes `data/reports/latest.md`.

  Exit code `2` means an error or critical alert.
- **Pacing:** one request per second. JDIH's firewall has refused some clients before: a refusal stops the run and raises a critical `access_forbidden` alert; it is never worked around.
- **Alerts** go to `data/alerts.jsonl`, plus any channels configured under `[monitoring]` in [config/pipeline.toml](config/pipeline.toml) (Slack, webhook, email).

Other commands:
- `discover`, `fetch`, `check-status`, `entries`, `dedupe` and `export`: run one stage on its own. `entries --budget 1000` reads every catalogue entry at once (about 17 minutes).
- `reparse`: re-applies identity and text rules to stored data, with no network calls.
- `status`: counts by source, status, identity and rights.
- `rights`: relabels stored texts after a licence decision.

## For the platform (contract v2)

`export --format v2` writes the store as one [contract v2 bundle](../Metamorph-Knowledge-Platform/packages/metamorph-contract/CONTRACT_V2.md): a full snapshot that `metamorph load` reads into the platform's PostgreSQL. It uses the same document selection and text choice as the corpus export (`select_documents`, `choose_texts`), so the two never disagree.

- **IDs:** `ojk:{pojk|seojk|padk}:{year}:{number}`. A record without a confident ID, or one that lost an ID collision, is exported under `unresolved:ojk:…` and queued for review.
- **Source record:** a document keeps the record that first supplied it (`regalert:…` for the 973 held before 2026-10-16), so the platform sees no churn; documents new from JDIH are `jdih_ojk:{GUID}`.
- **Title:** the "TENTANG …" subject from the regulation's own header, then the subject of JDIH's official title, then the source's title.
- **Dates and status:** from the JDIH catalogue entry (`status.source = "jdih_ojk"`): enacted, promulgated, effective; in force, amended, partly revoked, revoked, not yet in force. Without an entry: the latest standing check, otherwise RegAlert's lineage status.
- **Citations:** the JDIH entry's history (`Mencabut` → `revokes`, or `partly_revokes` with the article addresses it names, e.g. `pasal-187`; `Mengubah` → `amends`) and legal basis (`Landasan Hukum`), with the target's canonical ID (`ojk:…`, `id:uu:…`). Without an entry: RegAlert lineage.
- **Files:** the official files JDIH lists (`main`, `annex`, `abstract`, `faq`), byte for byte. The text extracted from the main PDF is paged (`<!-- page N -->`) and points at it (`of_file`).
- **Texts:** only `public` or `licensed` ones. RegAlert's English translation stays out. When both exist, our own extraction from the official PDF wins over a source's text.
- **`source_fields`:** the source record's own fields, plus the JDIH entry's own fields under `catalog_entry` (sector, subject, LN/TLN, place, …).

**As a container:** the platform's Airflow runs this connector from [Dockerfile](Dockerfile), built from the workspace root, the directory holding every repository: `docker build -f OJK-Source-Connector/Dockerfile -t metamorph/ojk-connector:dev .` (or `docker compose --profile connectors build` in Metamorph-Knowledge-Platform). `data/` is bind-mounted, not copied, so scheduled runs and your own CLI runs share one store. Don't run both at once. If a source refuses access (HTTP 403, or JDIH's firewall notice), the run stops asking at once and raises a critical `access_forbidden` alert.

## For the orchestrator

The orchestrator imports one class, which implements the [team interface](contract/CONNECTOR_INTERFACE.md):

```python
from ojk_connector import OJKConnector

connector = OJKConnector({"config": "config/pipeline.toml"})
discovery = connector.discover(cursor=None)              # every OJK document; returns a cursor for next time
fetched = connector.fetch(discovery.items[0], store)     # raw payloads + texts saved through store.put()
```

`max_concurrency = 1`, because sources are paced. Errors arrive as the interface's `SourceUnavailable` (refusal or outage), `AuthFailed` or `ItemFailed`.

## How it fits together

```
Airflow ──────────► OJKConnector ──┐                 the team interface
ojk-connector CLI ─► Pipeline ─────┤                 discover → fetch → check standing → read entries → dedupe → export → alerts
                                   ├──► Source port ◄── JdihOjkSource    (primary; RegAlertSource retired, kept for its data)
                                   ├──► identity                         OJK canonical IDs + text checks (pure rules)
                                   ├──► pdf_text                         text layer of official PDFs (pymupdf)
                                   └──► Store, monitoring                SQLite + raw blobs, alerts, reports
```

- **Sources** say *where* OJK documents come from. Each is one class behind the `Source` port and one `[sources.<name>]` table in config. A source declares what it supports: `check_status` (standing checks) and `read_entry` (catalogue entries). JDIH's page parsing lives in `sources/jdih/catalog.py` (pure, tested on real pages); nothing outside it knows JDIH's wording.
- **Identity** says *what* a document is. Canonical IDs look like `ojk:pojk:2026:1`, `ojk:seojk:2025:35` and `ojk:padk:2026:11`.

### The corpus (`data/corpus/`)

```
regulations.jsonl                               one line per document
documents/ojk/{family}/{year}/{number}/
  metadata.json                                 identity, evidence, sources, text inventory
  text.id.md                                    Indonesian text (authoritative)
  text.en.md                                    English, only if its rights allow export
documents/ojk/_unresolved/{source}/{id}/        no confident canonical ID yet
```

RegAlert's English translations are stored locally, but they are **kept out of the corpus** until RegAlert confirms we may use them. See [rights](docs/plan/02-architecture.md#rights-per-text-artifact).

## Sources

| Source | Status | Used for |
|---|---|---|
| [JDIH OJK](https://jdih.ojk.go.id/) | **Primary, built** (2026-10-16) | Discovery, catalogue entries (dates, standing, history, legal basis), official files, self-extracted text |
| [RegAlert](https://regalert.today) API | **Retired** 2026-10-16 (account banned) | Its 973 stored texts and lineage remain the text of the documents JDIH's files haven't replaced |
| [ojk.go.id](https://www.ojk.go.id/id/regulasi/) | Candidate | A second discovery channel for what JDIH's listing lacks (the 67 above); PDFs with ETags |
| [BPK Database Peraturan](https://peraturan.bpk.go.id/) | Not planned for OJK | ~512 POJK, ~25 SEOJK, no PADK; POJK appear a median 120 days late. Cross-check only |

The evaluation is in [docs/plan/01-sources.md](docs/plan/01-sources.md) and [docs/research/regalert-feasibility.md](docs/research/regalert-feasibility.md).

## Plan

| Document | Contents |
|---|---|
| [00 — Overview](docs/plan/00-overview.md) | Goal, scope, definition of done |
| [01 — Sources](docs/plan/01-sources.md) | Source evaluation, URL patterns, field mappings |
| [02 — Architecture](docs/plan/02-architecture.md) | Sources and identity, the two interfaces, stages, storage, rights, monitoring |
| [03 — Roadmap](docs/plan/03-roadmap.md) | Milestones with exit criteria |
| [04 — Risks & open questions](docs/plan/04-risks-and-open-questions.md) | Risks, mitigations, pending decisions |
| [05 — Connector interface](docs/plan/05-connector-contract.md) | How this repo implements the team's connector interface |
| [Team interface](contract/CONNECTOR_INTERFACE.md) | The interface every team connector implements (shared with the team) |

## Research

| Document | Contents |
|---|---|
| [RegAlert feasibility](docs/research/regalert-feasibility.md) | Live API test of RegAlert as an OJK source: coverage, field quality, problems found, licensing |
| [OJK publishing and storage](docs/research/ojk-publishing-and-storage/README.md) | How OJK publishes and structures its regulations (ojk.go.id, JDIH OJK, document anatomy), reconciliation against our corpus, and the recommended storage architecture for AI legal search |
