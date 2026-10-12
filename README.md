# OJK Source Connector (`regingest`)

An automated, modular pipeline that **discovers, fetches, normalizes, stores and monitors** regulatory documents. OJK (Otoritas Jasa Keuangan, Indonesia's Financial Services Authority) is the first agency; other agencies plug in through config.

It is the data acquisition foundation for a later AI-powered market-entry and compliance platform for international businesses entering Indonesia's sharia multifinance and consumer lending markets. Every document is **stored locally** as a self-contained corpus that a later phase will turn into a knowledge database.

> **Status:** the multi-agency core, the RegAlert connector, deduplication, change detection and monitoring are built and tested. The OJK backfill is under way (977 documents discovered). See the [roadmap](docs/plan/03-roadmap.md).

## Scope of this phase

The priority is **collecting data reliably and automatically.** We collect regulations broadly and do **not** filter them by business relevance.

**Out of scope for now:** relevance classification (rule-based or AI), legal interpretation or compliance advice, extracting obligations, Q&A or RAG, user-facing apps, and dashboards beyond what's needed to show that ingestion is reliable.

## Quick start

```bash
uv sync                                   # Python ≥ 3.11; installs httpx (+ pytest for dev)
uv run pytest                             # run the tests

uv run regingest auth login regalert      # one-time browser sign-in (use --method device on a server)
uv run regingest run                      # the scheduled job (daily)
uv run regingest health                   # for an external scheduler: exit 0 ok / 1 warning / 2 critical
uv run regingest review                   # records that need a person's attention
uv run regingest events --since 2026-10-01   # what changed: new, changed, missing, amended, duplicates
```

What each command does:
- **`auth login`** stores a refreshable token in `~/.config/regingest/credentials/`, outside the repository.
- **`run`** goes through every stage:
  1. Discovers documents: incrementally, plus a full re-list every 7 days to catch removals and format drift.
  2. Reads up to 25 documents, one at a time, newest first.
  3. Checks whether stored regulations were amended or replaced.
  4. Deduplicates.
  5. Rebuilds `data/corpus/`.
  6. Evaluates the alert rules and writes `data/reports/latest.md`.

  Exit code `2` means an error or critical alert.
- **Fair use:** RegAlert rations full-text reads. The pipeline stops cleanly when RegAlert pauses it and carries on the next day, so a daily schedule clears the backlog and then keeps up with the 1–2 new OJK documents a week.
- **Alerts** go to `data/alerts.jsonl`, plus any channels configured under `[monitoring]` in [config/pipeline.toml](config/pipeline.toml) (Slack, webhook, email). An alert that stays open isn't repeated except as a daily reminder, and a notification is sent when it resolves.

Other commands:
- `discover`, `fetch`, `check-status`, `dedupe` and `export`: run one stage on its own.
- `reparse`: re-applies identity and text rules to stored data, with no network calls.
- `status`: counts by agency, source, status, identity and rights.
- `rights`: relabels stored texts after a licence decision.

## How it fits together

```
config/pipeline.toml
  [sources.*]   → Connector       where documents come from   (regalert; later bpk, jdih_ojk)
  [agencies.*]  → AgencyProfile   what they are + canonical ids (ojk; "generic" for new agencies)

discover ─► fetch content (paced) ─► check standing ─► dedupe ─► export ─► data/corpus/
   │ change detection    │ text quality      │ amended/replaced            └─► alerts ─► channels
   └──────────────── events + store (SQLite + raw blobs) ───────────────────► reports, health
```

- **Add an agency:** add `[agencies.<code>]` with `profile = "generic"` and the sources that cover it. No code is needed until you want canonical IDs.
- **Replace or add a source:** set `connector = "package.module:Class"` to any `Connector` subclass.
- Canonical IDs look like `ojk:pojk:2026:1`, `ojk:seojk:2025:35` and `ojk:padk:2026:11`.

### The corpus (`data/corpus/`)

```
regulations.jsonl                                  one line per document
documents/{agency}/{family}/{year}/{number}/
  metadata.json                                    identity, evidence, sources, text inventory
  text.id.md                                       Indonesian text (authoritative)
  text.en.md                                       English, only if its rights allow export
documents/{agency}/_unresolved/{source}/{id}/      no confident canonical ID yet
```

RegAlert's English translations are stored locally, but they are **kept out of the corpus** until RegAlert confirms we may use them. See [rights](docs/plan/02-architecture.md#rights-per-text-artifact).

## Sources

| Source | Status | Used for |
|---|---|---|
| [RegAlert](https://regalert.today) API | **Built** (M1) | Discovery, Indonesian text, lineage. English translation pending a licence |
| [BPK Database Peraturan](https://peraturan.bpk.go.id/) | Planned (M2) | POJK metadata, status, links between regulations, official PDFs |
| [JDIH OJK](https://jdih.ojk.go.id/) | Planned (M4) | Authoritative completeness checks; SEOJK/PADK; sector classification |

The evaluation is in [docs/plan/01-sources.md](docs/plan/01-sources.md) and [docs/research/regalert-feasibility.md](docs/research/regalert-feasibility.md).

## Plan

| Document | Contents |
|---|---|
| [00 — Overview](docs/plan/00-overview.md) | Goal, scope, definition of done |
| [01 — Sources](docs/plan/01-sources.md) | Source evaluation, URL patterns, field mappings |
| [02 — Architecture](docs/plan/02-architecture.md) | Plug-in design, stages, identity, storage, rights, monitoring |
| [03 — Roadmap](docs/plan/03-roadmap.md) | Milestones M0–M5 with exit criteria |
| [04 — Risks & open questions](docs/plan/04-risks-and-open-questions.md) | Risks, mitigations, pending decisions |

## Research

| Document | Contents |
|---|---|
| [RegAlert feasibility](docs/research/regalert-feasibility.md) | Live API test of RegAlert as an OJK source: coverage, field quality, problems found, licensing |
