# 03 — Roadmap

Each milestone ends with something that can be shown working. M1 is under way; M0's spikes can run alongside it.

## M0 — Source spikes (verify our assumptions)

Goal: confirm, before writing the BPK and JDIH OJK connectors, the assumptions [01-sources.md](01-sources.md) couldn't verify.

- [ ] **RegAlert:** email `ask@regalert.today` about pacing/bulk access and licensing (Q9, Q10 in [04](04-risks-and-open-questions.md)).
- [x] **RegAlert:** confirm the REST response shapes (done on the first live run; see Q11).
- [ ] **BPK:** parse 50 OJK detail pages (a mix of POJK and SEOJK, old and new numbering) and confirm every field in the mapping table is present.
- [ ] **BPK:** check whether the per-year sitemaps have a `lastmod` for each URL; read the terms of use.
- [ ] **JDIH OJK:** find the XHR/JSON endpoint behind the list (or confirm we need Playwright); check whether the firewall blocks our runtime environment; count POJK and SEOJK/PADK as the completeness baseline.
- [ ] **Numbering:** confirm POJK/SEOJK/PADK numbers are unique per family and year across sectors. RegAlert data so far suggests they are.
- [ ] **PDFs:** sample 30 official PDFs and measure how many have a usable text layer.

**Exit criteria:** a short spike note in `docs/research/`. [04](04-risks-and-open-questions.md) and the data model are updated to match.

## M1 — Multi-agency core + RegAlert connector  *(in progress)*

The core is agency- and source-agnostic. RegAlert is the first connector and OJK the first agency profile.

- [x] Project skeleton: `pyproject.toml` (uv), `regingest` package, CLI, TOML config, tests.
- [x] Plug-in contracts: `Connector` (where from) and `AgencyProfile` (what/identity), loaded by built-in name or `package.module:Class`.
- [x] Store: SQLite + content-addressed blob store; source records, texts with origin/rights, raw snapshots, discovery cursor, run log.
- [x] RegAlert connector:
  - [x] OAuth sign-in: browser with PKCE (default), device code (for servers), refresh tokens, token file outside the repo.
  - [x] Discovery through `/v1/recent` with adaptive date windows (avoids unstable offset paging); de-duplicated by `uid`.
  - [x] Content via `/v1/documents/{uid}` with `include_original=true` and `text_next_offset` paging; raw payloads stored as snapshots.
  - [x] Quota awareness: account check, 429 `fair_use` → stop for today; retries with backoff on 5xx; ~1 request per second.
- [x] OJK agency profile: canonical IDs from the document header, identifier, URL slug, title and `instrument_no`, with confidence and evidence. Tested on real records.
- [x] Paced content queue: newest first, capped per run, skips duplicates, resumes the next day.
- [x] Corpus export with rights gating; `regingest rights` to relabel after a licence decision.
- [x] JSON run report and exit codes.
- [x] First live run (2026-10-12): 977 documents discovered, 25 read, and all 25 IDs confirmed from the documents' own headers. Response shapes confirmed; the connector was fixed to page the Indonesian text independently of the English.
- [x] Backfill complete (2026-10-12, runs 2–6): 973 documents read, plus 4 duplicates that were never read. 939 have the instrument's own Indonesian text; 28 have only a captured ojk.go.id page and 6 start with an annex or a guide (all listed in `regingest review`; they need the official PDF, M3). 975 of 977 have a high or medium canonical ID. Exit criteria met.

**Exit criteria:** every RegAlert OJK document is stored locally with its Indonesian text. Over 95% have a `high` or `medium` canonical ID, and the rest are listed for review. Rerunning fetches nothing new.

## M1.5 — Error alerts, deduplication, change detection, monitoring *(done; before adding agencies)*

- [x] **Deduplication:** same ID *plus* identical text, matching title, or agreeing headers; *undecided* until both texts exist; ID collisions kept apart and listed for review. Duplicates are never read.
- [x] **Change detection:**
  - [x] Field-level metadata diffs; content fields trigger a re-read.
  - [x] Text changes on re-read.
  - [x] Documents missing from a weekly full discovery, and documents that reappear.
  - [x] Amended or replaced regulations, via free standing checks.
  - [x] Everything recorded as events.
- [x] **Text quality:** the regulation's own text vs a captured web page; incomplete texts; quota and charge signals in every response.
- [x] **Alert rules:** auth, stage failures, empty or shrinking discovery, field fill-rate drift, stalled backlog, failure rate, charging, plus per-run events.
- [x] **Alert state:** no repeats while unchanged, escalation, reminders, resolution only by a run that checked the condition.
- [x] **Notification channels:** file (always on), Slack, webhook, email, or a custom class; `min_severity` per channel; secrets from the environment.
- [x] **Monitoring:**
  - [x] JSON and markdown run reports (`latest.md`).
  - [x] `regingest health` for schedulers (exit 0/1/2).
  - [x] `review`, `events`, `reparse` (re-apply rules without network) and `check-status` commands.
- [x] Schema v2 with an in-place migration of the existing database.
- Deferred (decided 2026-10-12): notification channels (Q4) and scheduled runs (Q3) move to the later phase that adopts an orchestration stack for multiple sources. Until then, runs are started by hand and alerts go to `data/alerts.jsonl` and the run reports.

## M2 — BPK connector

- [ ] Discovery through paginated search (`jenis=80`, `jenis=212`), plus sitemaps if M0 shows they're usable.
- [ ] Detail page parser (metadata, status, links between regulations, abstract) with saved HTML fixtures.
- [ ] Official PDF download (`/Download/{id}/...`) into the blob store, with sha256.

**Exit criteria:** a full backfill of all BPK OJK records, fed into the same store under the same canonical IDs.

## M3 — Official files and self-extraction

- [ ] Download the official PDF for every document (from `official_url` on ojk.go.id, BPK or JDIH OJK).
- [ ] Text-layer extraction (`pymupdf`), with OCR fallback (`ocrmypdf`, `ind`), stored as `origin=self_extracted`.
- [ ] Check RegAlert's original text against our own extraction on a sample.
- [ ] `original.pdf` in the corpus.

**Exit criteria:** every corpus document has its official PDF. Self-extracted text exists wherever a source's text is missing, restricted or doubtful.

## M4 — JDIH OJK connector and reconciliation

- [ ] JDIH OJK discovery across `sektor × jenisPeraturan`, detail parser, file download (Playwright only if needed).
- [ ] Reconciliation across RegAlert, BPK and JDIH OJK by canonical ID: gaps, conflicts and field priorities, with provenance.
- [ ] Relations normalized (`Mencabut`, `Mengubah`, RegAlert lineage) to canonical IDs; BPK status is authoritative.

**Exit criteria:** every JDIH OJK POJK and SEOJK/PADK has a canonical record. All conflicts appear in the report.

## M5 — Scheduling and operations

- [ ] Daily scheduled `regingest run` (cron or GitHub Actions), with token refresh, unattended. The alerting itself was built in M1.5.
- [ ] Runbook for each alert code (see [02-architecture.md](02-architecture.md#monitoring-and-alerts)).

**Exit criteria:** 14 days in a row of scheduled runs without anyone intervening, and a new publication picked up within 24 hours.

## Adding another agency (any milestone)

1. Add `[agencies.<code>]` with `profile = "generic"` and its source parameters (e.g. `[agencies.bi.sources.regalert] regulator = "BI"`). Documents are then ingested and stored straight away.
2. Write `src/regingest/agencies/<code>.py` (families + ID rules) with tests based on real records, and switch `profile` to it.
3. Add agency-specific connectors (e.g. the BI website) only if no existing connector covers that agency.

## Later (not part of this phase)

- Hukumonline, if commercial access is approved.
- Article-level (pasal) segmentation of the extracted text.
- Anything listed as out of scope in [00-overview.md](00-overview.md).
