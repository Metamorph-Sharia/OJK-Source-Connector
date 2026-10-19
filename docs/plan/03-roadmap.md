# 03 — Roadmap

Each milestone ends with something that can be shown working. M1, M1.5 and M1.6 are done; M0's spikes can run alongside the rest.

## M0 — Source spikes (verify our assumptions)

Goal: confirm, before writing the BPK and JDIH OJK sources, the assumptions [01-sources.md](01-sources.md) couldn't verify.

- [ ] **RegAlert:** email `ask@regalert.today` about pacing/bulk access and licensing (Q9, Q10 in [04](04-risks-and-open-questions.md)).
- [x] **RegAlert:** confirm the REST response shapes (done on the first live run; see Q11).
- [ ] **BPK:** parse 50 OJK detail pages (a mix of POJK and SEOJK, old and new numbering) and confirm every field in the mapping table is present.
- [ ] **BPK:** check whether the per-year sitemaps have a `lastmod` for each URL; read the terms of use.
- [ ] **JDIH OJK:** find the XHR/JSON endpoint behind the list (or confirm we need Playwright); check whether the firewall blocks our runtime environment; count POJK and SEOJK/PADK as the completeness baseline.
- [ ] **Numbering:** confirm POJK/SEOJK/PADK numbers are unique per family and year across sectors. RegAlert data so far suggests they are.
- [ ] **PDFs:** sample 30 official PDFs and measure how many have a usable text layer.

**Exit criteria:** a short spike note in `docs/research/`. [04](04-risks-and-open-questions.md) and the data model are updated to match.

## M1 — OJK pipeline + RegAlert source  *(done)*

RegAlert is the first source. (Built as a multi-agency core, then narrowed to OJK in M1.6.)

- [x] Project skeleton: `pyproject.toml` (uv), Python package, CLI, TOML config, tests.
- [x] `Source` port (where from), separate from the OJK identity rules (what/identity).
- [x] Store: SQLite + content-addressed blob store; source records, texts with origin/rights, raw snapshots, discovery cursor, run log.
- [x] RegAlert source:
  - [x] OAuth sign-in: browser with PKCE (default), device code (for servers), refresh tokens, token file outside the repo.
  - [x] Discovery through `/v1/recent` with adaptive date windows (avoids unstable offset paging); de-duplicated by `uid`.
  - [x] Content via `/v1/documents/{uid}` with `include_original=true` and `text_next_offset` paging; raw payloads stored as snapshots.
  - [x] Quota awareness: account check, 429 `fair_use` → stop for today; retries with backoff on 5xx; ~1 request per second.
- [x] OJK identity rules: canonical IDs from the document header, identifier, URL slug, title and `instrument_no`, with confidence and evidence. Tested on real records.
- [x] Paced content queue: newest first, capped per run, skips duplicates, resumes the next day.
- [x] Corpus export with rights gating; `ojk-connector rights` to relabel after a licence decision.
- [x] JSON run report and exit codes.
- [x] First live run (2026-10-12): 977 documents discovered, 25 read, and all 25 IDs confirmed from the documents' own headers. Response shapes confirmed; the source was fixed to page the Indonesian text independently of the English.
- [x] Backfill complete (2026-10-12, runs 2–6): 973 documents read, plus 4 duplicates that were never read. 939 have the instrument's own Indonesian text; 28 have only a captured ojk.go.id page and 6 start with an annex or a guide (all listed in `ojk-connector review`; they need the official PDF, M3). 975 of 977 have a high or medium canonical ID. Exit criteria met.

**Exit criteria:** every RegAlert OJK document is stored locally with its Indonesian text. Over 95% have a `high` or `medium` canonical ID, and the rest are listed for review. Rerunning fetches nothing new.

## M1.5 — Error alerts, deduplication, change detection, monitoring *(done)*

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
- [x] **Notification channels:** file (always on), Slack, webhook, email; `min_severity` per channel; secrets from the environment.
- [x] **Monitoring:**
  - [x] JSON and markdown run reports (`latest.md`).
  - [x] `ojk-connector health` for schedulers (exit 0/1/2).
  - [x] `review`, `events`, `reparse` (re-apply rules without network) and `check-status` commands.
- [x] Schema v2 with an in-place migration of the existing database.
- Deferred (decided 2026-10-12): notification channels (Q4) and scheduled runs (Q3) move to the later phase that adopts an orchestration stack for multiple sources. Until then, runs are started by hand and alerts go to `data/alerts.jsonl` and the run reports.

## M1.6 — OJK-only connector behind the team interface *(done 2026-10-13)*

Decided 2026-10-13: each regulator gets its own repository; this one covers OJK only. All of them implement one [team interface](../../contract/CONNECTOR_INTERFACE.md).

- [x] Removed the multi-agency plug-ins: agency profiles, the `generic` profile, `[agencies.*]` config and dotted-path class loading. OJK identity is one module of pure functions.
- [x] Connectors became OJK **sources** behind one `Source` port: RegAlert now, official sources later.
- [x] Renamed the package to `ojk_connector` and the CLI to `ojk-connector`. The database and token moved to the new names; the schema is unchanged.
- [x] `OJKConnector` implements the team interface (`discover`, `fetch`), with tests for a fresh-process fetch and for how errors are translated.
- [x] A test shows that a fallback source plugs in beside RegAlert and merges by canonical ID.
- [x] Checked against the real data: reparse changed 0 texts and 0 IDs, and the re-exported corpus index is byte-identical.

## M2b — JDIH OJK as the primary source *(done 2026-10-16)*

RegAlert banned our account, so OJK's own legal database replaced it ahead of M2–M4. Decided 2026-10-16 over BPK: BPK holds ~512 POJK, ~25 SEOJK and no PADK, and publishes POJK a median 120 days late.

- [x] `JdihOjkSource`: discovery from the listing JSON (10 sectors × POJK, SEOJK/PADK; 20 requests), detail-page parser (`sources/jdih/catalog.py`, tested on real pages), file download, text-layer extraction (`pdf_text.py`, pymupdf, paged).
- [x] `read_entry`, a new optional `Source` capability: the catalogue entry (official dates, standing, history with article references, legal basis, file list) without the files. Pipeline stage `read_entries` (budgeted, never-read first); store schema v3; a changed entry is an event, a changed standing a warning.
- [x] Identity: Indonesian titles and JDIH's typos (`Nomor34/POJK.05/2015`, `28/POJK,05/2014`); `cited_id` for history targets; all 973 JDIH titles resolve, numbers agree with JDIH's own column.
- [x] Dedup: a record's names include the subject of its own Indonesian header, so JDIH's titles match RegAlert's English-titled records (885 of 905 shared documents, without downloading anything). A text that isn't the instrument (a page, an annex) can't contradict one that names itself. The subject rule was fixed on the way: 5 circulars' titles carried their first paragraph.
- [x] Contract v2: dates, status (`jdih_ojk`), files, paged texts with `of_file`, citations from history and legal basis (partial revocations with `pasal-N/ayat-M` addresses); documents keep their first source record, so the platform sees no churn.
- [x] Standing combines JDIH's status field with the entry's own history (the field says only "Berlaku" for 94 regulations its history lists as amended or partly revoked). "Tidak Berlaku Sejak …" is never an effective date.
- [x] `reparse` re-parses stored entry pages offline, so parser fixes never need a re-crawl.
- [x] Live (2026-10-16): 973 discovered; every entry read; 7 sample documents fetched with their official files; loaded into the platform (884 documents with official dates and status).
- [x] The registry RCA's OJK acquisition failures (Q5.1): 9 of 12 fixed with official PDFs (`fetch --document`); their review items resolved. POJK 21, 34 and 46 of 2024 wait for a second discovery source: JDIH doesn't list them.
- [x] The rest of the content queue (2026-10-16): 64 regulations RegAlert never had (now in the registry: 1,041 OJK documents) and 13 whose held text was a web page or an annex. 3 stay queued; their held text is the regulation.
- [x] Identity: JDIH's title and its own number column agreeing make a high-confidence id, so a file naming another regulation is a conflict for a person (JDIH serves POJK 14/2020 as POJK 14/2016's file; SEOJK 16/2016's header reads "116"). Dedup: texts sharing ≥ 97% of their words are one document (different circulars share ≤ 90%).
- [ ] OCR for pages without a text layer: POJK 16 and 17 of 2014 and SEOJK 2/2013 have a scanned first page (no header), SEOJK 48/2017 one scanned page.
- [ ] ojk.go.id as a second discovery source, for what JDIH's listing lacks (see [04](04-risks-and-open-questions.md)).

## M2 — BPK source *(superseded for OJK by M2b; BPK stays a possible cross-check)*

- [ ] Discovery through paginated search (`jenis=80`, `jenis=212`), plus sitemaps if M0 shows they're usable.
- [ ] Detail page parser (metadata, status, links between regulations, abstract) with saved HTML fixtures.
- [ ] Official PDF download (`/Download/{id}/...`) into the blob store, with sha256.

**Exit criteria:** a full backfill of all BPK OJK records, through a second `Source`, fed into the same store under the same canonical IDs.

## M3 — Official-source fallback: ojk.go.id files and self-extraction

The fallback for whatever RegAlert lacks, or for when it is unavailable. It is an `OJKWebsiteSource` beside RegAlert (see [02-architecture.md](02-architecture.md#design-principle-one-connector-per-regulator-replaceable-sources-inside)).

- [ ] First the 34 documents whose RegAlert text is a captured web page or starts with an annex (`ojk-connector review`).

- [ ] Download the official PDF for every document (from `official_url` on ojk.go.id, BPK or JDIH OJK).
- [ ] Text-layer extraction (`pymupdf`), with OCR fallback (`ocrmypdf`, `ind`), stored as `origin=self_extracted`.
- [ ] Check RegAlert's original text against our own extraction on a sample.
- [ ] `original.pdf` in the corpus.

**Exit criteria:** every corpus document has its official PDF. Self-extracted text exists wherever a source's text is missing, restricted or doubtful.

## M4 — JDIH OJK source and reconciliation

- [ ] JDIH OJK discovery across `sektor × jenisPeraturan`, detail parser, file download (Playwright only if needed).
- [ ] Reconciliation across RegAlert, BPK and JDIH OJK by canonical ID: gaps, conflicts and field priorities, with provenance.
- [ ] Relations normalized (`Mencabut`, `Mengubah`, RegAlert lineage) to canonical IDs; BPK status is authoritative.

**Exit criteria:** every JDIH OJK POJK and SEOJK/PADK has a canonical record. All conflicts appear in the report.

## M5 — Orchestration and operations

- [ ] The orchestrator (probably Airflow) runs `OJKConnector` alongside the team's other connectors, with token refresh, unattended. Until then, `ojk-connector run` by hand or cron. The alerting itself was built in M1.5.
- [ ] Runbook for each alert code (see [02-architecture.md](02-architecture.md#monitoring-and-alerts)).

**Exit criteria:** 14 days in a row of scheduled runs without anyone intervening, and a new publication picked up within 24 hours.

## Adding an OJK source (any milestone)

1. Write `src/ojk_connector/sources/<name>/` with a class implementing `Source` (`discover`, `fetch_content`), plus tests on recorded responses.
2. Register it in `sources/__init__.py` and add `[sources.<name>]` to config.

Nothing else changes: the pipeline, the identity rules and `OJKConnector` pick it up. Another regulator does not belong here; it gets its own repository implementing the team interface.

## Later (not part of this phase)

- Hukumonline, if commercial access is approved.
- Article-level (pasal) segmentation of the extracted text.
- Anything listed as out of scope in [00-overview.md](00-overview.md).
