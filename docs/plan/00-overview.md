# 00 — Overview

## Goal

Build a reliable, maintainable, automated ingestion pipeline that **discovers, downloads, extracts, normalizes, and monitors** regulatory documents published by OJK (Otoritas Jasa Keuangan).

This repository covers **OJK only**. Each other regulator gets its own connector repository, built by another team member. All of them implement the same [connector interface](../../contract/CONNECTOR_INTERFACE.md), so the orchestrator in the next phase (probably Airflow) can run them alike.

This pipeline is the data acquisition foundation for a later AI-powered market-entry and compliance platform. That platform will serve international businesses entering Indonesia's sharia multifinance and consumer lending markets.

**The priority for this phase is collecting data reliably and automatically. Deciding which regulations matter to a particular business comes later.**

## Scope

### In scope
- **Broad collection.** Ingest every OJK document type we can reach: POJK, SEOJK/PADK, and the related laws OJK lists alongside them. Don't filter by sector or business relevance.
- **Discovery** of new and changed documents from several sources.
- **Download** of the original files (PDF and attachments), keeping exactly what was published.
- **Extraction** of plain text from those files, including OCR for scanned PDFs.
- **Normalization** into one canonical document model: identity, dates, status, links between regulations, and where each piece came from.
- **Reconciliation** across sources, to find gaps and conflicts.
- **Local storage** of every document (official PDF, extracted text, metadata) as a self-contained corpus. That corpus is the input for the **knowledge database in a later phase**, and records the usage rights of every text.
- **Monitoring** of ingestion health: run reports, failure alerts, and detection of source layout changes.

### Out of scope for this phase
- Classifying regulations as relevant or irrelevant to consumer lending, multifinance, or sharia finance.
- Classifying regulatory relevance with AI.
- Legal interpretation, compliance advice, or extracting obligations.
- Regulatory question-answering or RAG.
- A user-facing compliance application.
- Complex dashboards, or any infrastructure not needed to show that ingestion is reliable.

Sector tags that come *from the source* (e.g. JDIH OJK's `sektor`) are kept as metadata. That is normalization, not relevance classification.

## Approach in one paragraph

**RegAlert** is the primary source today. Its API supplies OJK's documents with their Indonesian text and the links between them, and it holds the full backfill: 977 documents (see [01-sources.md](01-sources.md) and the [feasibility study](../research/regalert-feasibility.md)). The fallback is **OJK's own official files on ojk.go.id**, which we download and extract ourselves (M3). They cover what RegAlert lacks, such as the 34 documents whose RegAlert text is a web page or an annex rather than the regulation. BPK and JDIH OJK follow for completeness checks. Every source plugs in behind one internal port. Records from different sources are merged under one canonical ID, and every run ends with a health report.

## Plan documents

| File | Contents |
|---|---|
| [01-sources.md](01-sources.md) | Source evaluation, URL patterns, field mappings, known issues |
| [02-architecture.md](02-architecture.md) | Pipeline stages, data model, storage, tech choices |
| [03-roadmap.md](03-roadmap.md) | Milestones, tasks, acceptance criteria |
| [04-risks-and-open-questions.md](04-risks-and-open-questions.md) | Risks, mitigations, decisions still needed |
| [05-connector-contract.md](05-connector-contract.md) | How this repo implements the team's connector interface |

## Definition of done for this phase

1. Every POJK and SEOJK/PADK listed on JDIH OJK has a canonical record. Any gaps are explained in the reconciliation report.
2. For each record, the original PDFs are stored with checksums, and extracted text exists with a quality score.
3. A scheduled run picks up a newly published regulation within 24 hours, without anyone intervening.
4. Each run produces a machine-readable health report. When a source breaks (e.g. it starts returning zero records, or the parse rate drops), the run fails loudly rather than quietly ingesting nothing.
5. Rerunning the pipeline is idempotent: unchanged documents are not downloaded again or duplicated.
6. `data/corpus/` holds, for every regulation, its official PDF, its Indonesian text and its metadata. It can be read with no network access, and only contains content we are allowed to use.
7. `OJKConnector` implements the team's connector interface, so the orchestrator can run it like every other connector.
