# OJK Source Connector (`ojk-connector`)

Discovers, fetches, normalizes, stores and monitors the regulations of **OJK** (Otoritas Jasa Keuangan, Indonesia's Financial Services Authority).

This repository covers **OJK only**. Each other regulator has its own connector repository. Every connector implements the same [team interface](contract/CONNECTOR_INTERFACE.md), so the orchestrator in the next phase (probably Airflow) runs them all the same way.

It is the data foundation for a later AI-powered market-entry and compliance platform for businesses entering Indonesia's sharia multifinance and consumer lending markets. Every document is **stored locally** as a self-contained corpus that a later phase will turn into a knowledge database.

> **Status:** RegAlert backfill complete: 977 OJK documents, 973 read and 4 duplicates. Deduplication, change detection, monitoring and the team-interface adapter are built and tested. Next is the official-source fallback (M3). See the [roadmap](docs/plan/03-roadmap.md).

## Scope of this phase

The priority is **collecting data reliably and automatically.** We collect OJK regulations broadly and do **not** filter them by business relevance.

**Out of scope for now:** relevance classification (rule-based or AI), legal interpretation or compliance advice, extracting obligations, Q&A or RAG, user-facing apps, and dashboards beyond what's needed to show that ingestion is reliable.

## Quick start

```bash
uv sync                                        # Python ≥ 3.11; installs httpx (+ pytest for dev)
uv run pytest                                  # run the tests

uv run ojk-connector auth login regalert       # one-time browser sign-in (use --method device on a server)
uv run ojk-connector run                       # the whole pipeline, standalone (daily)
uv run ojk-connector health                    # for a scheduler: exit 0 ok / 1 warning / 2 critical
uv run ojk-connector review                    # records that need a person's attention
uv run ojk-connector events --since 2026-10-01 # what changed: new, changed, missing, amended, duplicates
uv run ojk-connector export --format v2 --out data/exports/v2/RUN   # a contract v2 bundle for the platform loader
```

What each command does:
- **`auth login`** stores a refreshable token in `~/.config/ojk-connector/credentials/`, outside the repository.
- **`run`** goes through every stage:
  1. Discovers documents: incrementally, plus a full re-list every 7 days to catch removals and format drift.
  2. Reads up to 25 documents, one at a time, newest first.
  3. Checks whether stored regulations were amended or replaced.
  4. Deduplicates.
  5. Rebuilds `data/corpus/`.
  6. Evaluates the alert rules and writes `data/reports/latest.md`.

  Exit code `2` means an error or critical alert.
- **Fair use:** RegAlert rations full-text reads. The pipeline stops cleanly when RegAlert pauses it and carries on in a later run.
- **Alerts** go to `data/alerts.jsonl`, plus any channels configured under `[monitoring]` in [config/pipeline.toml](config/pipeline.toml) (Slack, webhook, email).

Other commands:
- `discover`, `fetch`, `check-status`, `dedupe` and `export`: run one stage on its own.
- `reparse`: re-applies identity and text rules to stored data, with no network calls.
- `status`: counts by source, status, identity and rights.
- `rights`: relabels stored texts after a licence decision.

## For the platform (contract v2)

`export --format v2` writes the store as one [contract v2 bundle](../Metamorph-Knowledge-Platform/packages/metamorph-contract/CONTRACT_V2.md): a full snapshot that `metamorph load` reads into the platform's PostgreSQL. It uses the same document selection and text choice as the corpus export (`select_documents`, `choose_texts`), so the two never disagree.

- **IDs:** `ojk:{pojk|seojk|padk}:{year}:{number}`. A record without a confident ID, or one that lost an ID collision, is exported under `unresolved:ojk:…` and queued for review.
- **Title:** the "TENTANG …" subject from the regulation's own header, falling back to RegAlert's title.
- **Status:** the latest standing check, otherwise RegAlert's lineage status.
- **Citations:** RegAlert lineage (`based_on` → `legal_basis`, `amends`, `supersedes`), with the target's canonical ID when we hold it (`ojk:…`) or it is a national law (`id:uu:…`).
- **Texts:** only `public` or `licensed` ones. RegAlert's English translation stays out. There are no files until the official PDFs arrive (roadmap M3).

**As a container:** the platform's Airflow runs this connector from [Dockerfile](Dockerfile), built from the workspace root, the directory holding every repository: `docker build -f OJK-Source-Connector/Dockerfile -t metamorph/ojk-connector:dev .` (or `docker compose --profile connectors build` in Metamorph-Knowledge-Platform). `data/` is bind-mounted, not copied, so scheduled runs and your own CLI runs share one store. Don't run both at once. The RegAlert token directory is mounted too. If RegAlert refuses access (HTTP 403), the run stops asking at once and raises a critical `access_forbidden` alert.

## For the orchestrator

The orchestrator imports one class, which implements the [team interface](contract/CONNECTOR_INTERFACE.md):

```python
from ojk_connector import OJKConnector

connector = OJKConnector({"config": "config/pipeline.toml"})
discovery = connector.discover(cursor=None)              # every OJK document; returns a cursor for next time
fetched = connector.fetch(discovery.items[0], store)     # raw payloads + texts saved through store.put()
```

`max_concurrency = 1`, because RegAlert's reads are rationed. Errors arrive as the interface's `SourceUnavailable` (quota or outage), `AuthFailed` or `ItemFailed`.

## How it fits together

```
Airflow (later) ──► OJKConnector ──┐                 the team interface
ojk-connector CLI ─► Pipeline ─────┤                 discover → fetch → check standing → dedupe → export → alerts
                                   ├──► Source port ◄── RegAlertSource   (later: ojk.go.id fallback, BPK, JDIH OJK)
                                   ├──► identity                         OJK canonical IDs + text checks (pure rules)
                                   └──► Store, monitoring                SQLite + raw blobs, alerts, reports
```

- **Sources** say *where* OJK documents come from. Each is one class behind the `Source` port and one `[sources.<name>]` table in config. The official-source fallback will be added the same way, without changing the pipeline.
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
| [RegAlert](https://regalert.today) API | **Primary, built** (M1) | Discovery, Indonesian text, lineage. English translation pending a licence |
| [ojk.go.id](https://www.ojk.go.id/id/regulasi/) official files | **Fallback, planned** (M3) | Official PDFs and our own text extraction, where RegAlert lacks the regulation's text or is unavailable |
| [BPK Database Peraturan](https://peraturan.bpk.go.id/) | Planned (M2) | POJK metadata, status, links between regulations |
| [JDIH OJK](https://jdih.ojk.go.id/) | Planned (M4) | Authoritative completeness checks; SEOJK/PADK; sector classification |

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
