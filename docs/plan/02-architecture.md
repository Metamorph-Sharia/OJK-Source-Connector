# 02 — Architecture

Status: **in progress.** The core, the RegAlert connector, deduplication, change detection and monitoring are built (M1, M1.5). Tech choices are defaults, chosen to keep the system small.

## Design principle: a multi-agency pipeline with replaceable parts

OJK is the first agency, not the only one. The pipeline is built so that adding an agency (BI, Bappebti, …) or a source (BPK, JDIH OJK, another API) **only adds a module and a few lines of config**. Nothing in the core changes. There are two kinds of plug-in:

| Plug-in | Answers | Examples | Interface |
|---|---|---|---|
| **Connector** | *Where* do we get documents, and how? | `regalert`, later `bpk`, `jdih_ojk` | `regingest.connectors.base.Connector` |
| **Agency profile** | *What* is this agency's document, and what is its identity? | `ojk`, later `bi`; `generic` as the fallback | `regingest.agencies.base.AgencyProfile` |

They are independent of each other: one connector can serve many agencies (RegAlert covers OJK, BI and Bappebti), and one agency can be fed by many connectors (OJK from RegAlert, BPK and JDIH OJK). Config ties them together:

```toml
[sources.regalert]                 # a connector instance and its settings
connector = "regalert"             # a built-in name, or "package.module:Class" for any other implementation

[agencies.ojk]                     # an agency, its profile, and which sources feed it
profile = "ojk"
[agencies.ojk.sources.regalert]    # source-specific parameters for this agency
regulator = "OJK"
```

**Replacing a connector** (e.g. if RegAlert goes away) means pointing `connector` at another implementation, or removing that source from the agency. Stored data, canonical IDs and the corpus are unaffected, because everything downstream works on source-independent records.

**Adding an agency:**
1. Add `[agencies.<code>]` with its sources.
2. Start with `profile = "generic"`: documents are ingested and stored, but filed under `_unresolved` in the corpus.
3. Add an agency profile with an ID parser when we need canonical IDs for that agency.

### Contracts

```text
Connector                                   # one per source
  discover(binding, since, until) -> Iterator[DiscoveredDocument]
  fetch_content(document)         -> DocumentContent      # texts + raw snapshots + extras
  login(...) / auth_status(...)                            # optional, for sources that need sign-in
  raises: QuotaExhausted (stop for today), SourceAuthError, SourceError, PermanentSourceError

AgencyProfile                               # one per agency
  resolve_id(document)            -> IdResolution          # from source metadata
  resolve_id_from_text(text)      -> IdResolution          # from the document's own header (most reliable)
```

`DiscoveredDocument` and `DocumentContent` are source-agnostic. Anything specific to a source goes in their `metadata` / `extra` dicts, and raw payloads are kept as snapshots.

## Pipeline stages

```
  agencies × sources (config)
          │
          ▼
   ┌────────────┐  DiscoveredDocument  ┌──────────────┐  DocumentContent  ┌─────────┐
   │  discover  │ ───────────────────► │ fetch content│ ─────────────────►│  store  │──► export ──► data/corpus/
   └────────────┘   + canonical ID     └──────────────┘  + ID from text   └─────────┘
     incremental     (agency profile)    paced queue,                         │
     by date window                      quota-aware                          ▼
                                                                         run report
```

| Stage | What it does | Notes |
|---|---|---|
| **discover** | Lists documents per (agency, source); upserts source records; derives a canonical ID from metadata | Always cheap. Incremental by date window with a look-back; `--full` re-lists everything |
| **fetch content** | Works through the queue of documents without text, **one at a time**, up to a per-run cap | Stops cleanly when the source pauses (fair use); the next run resumes. Skips documents whose canonical ID already has content (duplicates) |
| **store** | SQLite metadata + content-addressed raw blobs | Every raw API payload is kept as a snapshot, so we can re-parse without fetching again |
| **check standing** | Asks each source whether stored documents were amended or replaced | Free calls; prioritized by what new documents amend |
| **dedupe** | Decides duplicates vs ID collisions | See [Deduplication](#deduplication) |
| **export** | Rebuilds `data/corpus/` from the store, including only exportable rights | The knowledge-database hand-off |
| **monitor** | Evaluates alert rules, notifies channels, writes JSON + markdown reports | See [Monitoring and alerts](#monitoring-and-alerts) |

Later stages (official PDF download, self-extraction/OCR, reconciliation across sources) plug into the same store. See [03-roadmap.md](03-roadmap.md).

### Pacing the full-text reads

RegAlert's free tier allows unlimited metadata calls but pauses full-text reads at bulk scale. Its message says reads resume "tomorrow (UTC)". In practice (2026-10-12), a pause came after about 200 reads and lifted again within 15–20 minutes. The fetch stage therefore:

1. Checks the account once per run (`/v1/account`), and stops if reads are paused.
2. Reads at most `max_content_reads_per_run` documents (default 25), newest first.
3. Treats HTTP 429 `fair_use` as "stop for today", not as an error.
4. Leaves the remaining documents queued. A daily schedule clears the backlog over several days, after which ongoing volume (1–2 new OJK documents a week) is trivial.

## Canonical identity

The format is `{agency}:{family}:{year}:{number}`, all lowercase, e.g. `ojk:pojk:2026:1`, `ojk:seojk:2025:35`, `ojk:padk:2026:11`.

- Each agency profile defines its families. For OJK these are `pojk`, `seojk` and `padk`. PADK gets its own family, because PADK and POJK share numbers within a year (PADK 11/2026 ≠ POJK 11/2026).
- Old-style citations (`37/POJK.03/2017`) are parsed to number `37`, year `2017` and sector `03`. The sector is kept as evidence, not as part of the key.
- **Order of evidence for OJK:**
  1. The document's own header in its Indonesian text (`PERATURAN OTORITAS JASA KEUANGAN … NOMOR …`).
  2. The source identifier.
  3. The official URL slug (start-anchored; end-anchored only when the slug is not about an amendment).
  4. The English title (ignored when it starts with "Amendment to …").
  5. RegAlert's `instrument_no` (low confidence: it is ambiguous for PADK).
- Confidence is recorded as `high`, `medium`, `low`, `conflict` or `none`, together with the evidence used. Documents with `conflict` or `none` are listed in the corpus under `_unresolved` for review.

## Data model (schema v2; older databases are migrated in place)

```text
source_documents        one row per (source, source_id)
  agency, title, published, source_url, official_url
  metadata_json         raw source metadata
  canonical_id, id_confidence, id_evidence, id_origin (metadata | text)
  content_status        pending | fetched | failed | duplicate
  content_attempts, content_error, content_fetched_at, content_extra_json, refetch_reason
  duplicate_of, dedup_reason                      deduplication decision
  missing_since                                   no longer listed by the source
  status_fingerprint, status_edges_json, status_summary, status_checked_at, status_priority
  first_seen_at, last_seen_at, metadata_changed_at

texts                   one row per (source, source_id, language, origin)
  rights, blob_sha256, chars, fetched_at, assessment (instrument | page | unknown), complete

snapshots               raw payloads (API JSON, HTML, PDFs later), content-addressed
discovery_state         per (source, agency): last `until`, last full discovery, field fill rates
events                  everything that happened, per run (see Change detection)
alert_state             open/resolved alerts, for de-duplicated notifications
runs                    run log + report
```

**Text assessment:** an agency profile tells the regulation's own text ("instrument") apart from other text. E.g. RegAlert sometimes supplies a capture of the ojk.go.id summary page instead of the regulation. Such documents are flagged, and they get the official PDF in M3.

Later milestones add `files` (official PDFs and attachments), `relations` and a merged `regulations` view across sources.

## Storage

**Every document is stored locally.** The local store is the corpus the later knowledge database will be built from, so it must be complete, self-contained (no live calls to any source needed to read it), and clear about the rights attached to each text.

```
data/                                     # git-ignored; back it up
  raw/{sha256[:2]}/{sha256}               # immutable blobs: API payloads, texts, PDFs (later)
  db/regingest.sqlite                     # metadata, texts index, snapshots index, run log
  reports/run-{timestamp}.json            # one per run
  corpus/                                 # the knowledge-database hand-off (rebuilt by `regingest export`)
    regulations.jsonl                     # one line per document
    documents/{agency}/{family}/{year}/{number}/
      metadata.json                       # identity, provenance, sources, text inventory
      text.id.md                          # Indonesian text (authoritative)
      text.en.md                          # English text, ONLY if its rights allow it
      original.pdf                        # official PDF (from M3)
    documents/{agency}/_unresolved/{source}/{source_id}/   # no confident canonical ID yet
```

- **Raw store:** content-addressed and never modified. Can be moved to S3 or GCS later.
- **Metadata store:** SQLite. Move to Postgres only if we need concurrent writers.
- **Corpus:** a derived view. Later phases read only from `corpus/`.

### Rights per text artifact

Every stored text records **where it came from** (`origin`, plus the `source` name) and **what we may do with it** (`rights`):

| Artifact | `origin` | `rights` |
|---|---|---|
| Official PDF (later) | `official_file` | `public` |
| Text we extract from an official PDF (later) | `self_extracted` | `public` |
| Original-language text supplied by a source (e.g. RegAlert `include_original`) | `source_text` | per connector config. RegAlert default: `public` |
| Translation supplied by a source (e.g. RegAlert English) | `source_translation` | per connector config. RegAlert default: `restricted` until they confirm |
| Source summaries | — | kept only inside the raw snapshot, never exported |

`rights` is one of `public`, `licensed` or `restricted`. Only `public` and `licensed` reach `corpus/`. When a licence is granted, `regingest rights --source regalert --origin source_translation --set licensed` relabels the stored texts without fetching anything again.

## Tech choices

| Concern | Choice | Why |
|---|---|---|
| Language | Python ≥ 3.11 | Best tooling for PDFs and OCR. `tomllib` and `StrEnum` are in the standard library |
| Packaging | `uv` + `pyproject.toml` (hatchling) | Fast and reproducible |
| HTTP | `httpx` | Sync client, pluggable auth, `MockTransport` for tests |
| Config | One TOML file (`config/pipeline.toml`) | Standard-library parser, no extra dependency |
| Storage | SQLite + file system | |
| CLI | `argparse` (`regingest …`) | No extra dependency |
| JS-rendered pages | Playwright, only where required (JDIH OJK) | |
| PDF text / OCR | `pymupdf`; `ocrmypdf` + Tesseract `ind` | Later milestone |
| Scheduling | cron or a scheduled GitHub Actions workflow | |
| Tests | `pytest` with `httpx.MockTransport` and fixtures based on real records | |

## Polite crawling

- A User-Agent that identifies us (`regingest/0.x`, plus an optional contact address in config).
- About 1 request per second per source by default. Exponential backoff on 5xx responses; `Retry-After` honoured on 429.
- For APIs, use only the permitted interface (e.g. RegAlert's API, never its website).
- Obey robots.txt when crawling HTML sources (BPK, JDIH OJK).

## Deduplication

Two records are **the same document** if they share a trusted canonical ID (high or medium confidence) **and** at least one of these holds:
- their instrument texts are identical;
- their titles largely match (≥ 80% of the shorter title's distinctive words);
- both texts' own headers name the instrument.

What happens in each case:
- **Before a read:** a record matching an already-read document by title is marked `duplicate` and never read.
- **While either text is missing:** the pair is *undecided*. Both records are read, and no alert is raised.
- **Once both texts exist and still disagree:** it's an **ID collision**, meaning one ID is wrong. The records are kept apart and listed under `regingest review`. The corpus files the best-identified one under the canonical path and the other under `_unresolved`.
- **The same text under two different IDs** is reported as well.

Decisions are recomputed every run (`regingest dedupe`). An undone decision sends the record back to the queue.

Two cases from the first live run show why the ID alone isn't enough:
- POJK 28/2014 and 24/2014 are each listed twice in RegAlert. These are real duplicates.
- A SEOJK 8/2023 record's "text" was a summary page citing POJK 35/2017, which briefly gave it the wrong ID.

## Change detection

| Change | How it is detected | Event (`regingest events`) |
|---|---|---|
| New document | Not seen before in discovery | `new_document` |
| Metadata changed | Field-level diff on every listing | `metadata_changed` (with the fields) |
| Content may have changed | A connector's `content_fields` changed (RegAlert: title, identifier, instrument_no, source, file_url) → re-queued for a read | `refetch_queued` |
| Text changed | Re-read text hash differs (the old blob is kept) | `content_changed` |
| Removed from source | Not listed by a **full** discovery (every `full_discovery_every_days`) | `missing_from_source` / `reappeared` |
| Amended or replaced | Standing checks: incoming `amends`/`supersedes` edges, fingerprinted. Free, `status_checks_per_run` per run. Documents named in a newly read document's lineage are checked first | `status_changed` (e.g. "replaced by ojk:pojk:2022:10") |
| ID changed | The document's own header gives a different ID | `id_changed` |

Change *signals* are recorded, but not interpreted. Whether an amendment matters to a business is out of scope for this phase.

## Monitoring and alerts

Each run writes `data/reports/run-{timestamp}-{id}.{json,md}` and `latest.{json,md}`, and appends every notification to `data/alerts.jsonl`.

**Alert rules** (`regingest.monitoring.alerts`):

| Alert | Severity | When |
|---|---|---|
| `auth_failed` | critical | A source rejects our credentials (the message says how to sign in again) |
| `discovery_empty`, `discovery_drop` | critical | A full discovery lists 0 documents, or ≥ 10% fewer than stored |
| `source_charging` | critical | A read was charged (reads stop at once) |
| `discovery_failed`, `content_failed`, `status_check_failed` | error | A stage failed for a source |
| `content_failure_rate` | error | More than 20% of a run's reads failed |
| `fill_rate_drop` | warning | A field is filled ≥ 20 points less often than at the last full discovery (format drift) |
| `backlog_stalled` | warning | Documents are queued, but none were read and the source didn't pause |
| `documents_missing`, `content_changed`, `text_incomplete`, `text_not_instrument`, `id_unresolved`, `id_collision`, `same_text_different_ids`, `content_failures` | warning | Something happened this run that needs a look |
| `regulation_status_changed` | info | Stored regulations were amended or replaced |
| `stale_run` | critical | No successful run within `max_hours_between_runs` (checked by `regingest health`) |

**Alert state:**
- **Conditions** (e.g. `auth_failed`) notify when they start, when they escalate, every `renotify_hours` while they stay open, and once when they resolve. A condition is resolved only by a run that actually checked it.
- **Events** notify once.

**Channels** are plug-ins chosen in config, like connectors:
- `file` (always on), `slack`, `webhook` and `email`, or any `package.module:Class` implementing `Notifier`.
- Each channel has its own `min_severity`.
- Secrets come from environment variables.
- A failing channel never fails the run; its error goes into the report.

**Health:** `regingest health` exits `0` (ok), `1` (warning) or `2` (critical). It reports the last successful run, open alerts and the backlog. Run it from an external scheduler or uptime check (`--notify` also alerts on a stale pipeline).

**Exit code of `regingest run`:** `2` when an error or critical alert was raised, otherwise `0`. The run's status is `ok`, `paused`, `warning` or `error`.

## Repository layout

```
src/regingest/
  models.py             source-agnostic records (DiscoveredDocument, DocumentContent, TextArtifact, Origin, Rights)
  config.py             TOML config → typed config
  plugins.py            "package.module:Class" loader
  store.py              SQLite + blob store
  pipeline.py           discover / fetch content / check standing / dedupe / reparse / export / run
  dedup.py              same document vs id collision
  export.py             corpus writer
  monitoring/           alerts.py (rules), manager.py (state), notifiers.py (channels), health.py, summary.py
  cli.py                `regingest …`
  connectors/
    base.py             Connector contract + errors
    regalert/           client.py, auth.py, connector.py
  agencies/
    base.py             AgencyProfile contract, CanonicalId, GenericProfile
    ojk.py              OJK identity rules
config/pipeline.toml
tests/
docs/plan/, docs/research/
```
