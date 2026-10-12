# RegAlert — Feasibility Study

**Date:** 2026-10-12
**Method:** live tests against the official RegAlert MCP API (`mcp.regalert.today`), signed in with a free account. Every row of OJK metadata was listed. Full text and lineage were checked on a sample.
**Question:** can RegAlert serve as a source for the OJK Source Connector (discover → download → extract → normalize → monitor)?

## Verdict

**Feasible as a primary source for discovery and text, but not as the source of truth.**

RegAlert would save us most of the hardest work: discovering documents, extracting text (including the Indonesian original), and the links between regulations. But its identifiers and dates can't be relied on, it contains duplicates, and its free tier has fair-use limits. So we keep BPK and JDIH OJK for reconciliation, and derive our own canonical IDs. Two things must happen before we depend on it: **request bulk access** and **clarify the licence** (`ask@regalert.today`).

## What we tested

| Check | Result |
|---|---|
| Access | ✅ OAuth sign-in works. `get_account`: `access: free`, `full_text_reads: available`, `bulk_access: false` |
| OJK regulator | ✅ code `OJK`, **977 documents** (listed with publication dates) |
| Time range | 2013-08 → 2026-09, plus one 2008 Bapepam-LK rule |
| Document types | POJK, SEOJK, PADK (all `type: rule`). Every `source` is an `ojk.go.id/id/regulasi/Pages/...` page |
| Spot checks on regulations central to our use case | ✅ POJK 77/POJK.01/2016 (P2P lending), ✅ POJK 35/POJK.05/2018 (financing companies), ✅ POJK 10/POJK.05/2019 (sharia financing), ✅ POJK 10/POJK.05/2022 (P2P lending, replaced 77/2016), ✅ POJK 32/2025 (BNPL), ✅ PADK 2/2026 (BNPL for financing companies) |
| Full text | ✅ English markdown with headings by chapter and article. ✅ **Original Indonesian text** via `include_original=true` (raw PDF text: line breaks kept, some hyphenation joins such as `UndangUndang`) |
| Lineage | ⚠️ Mostly correct, but incomplete. See below |
| Search | ⚠️ Poor relevance ranking: searching for "77/POJK.01/2016" didn't return it in the top results. Use date-based listing, not search, to find documents |

### Documents per year (from `recent_documents` totals)

| Year | Docs | | Year | Docs |
|---|---|---|---|---|
| 2026 (to Sep) | 27 | | 2019 | 66 |
| 2025 | 89 | | 2018 | 56 |
| 2024 | 69 | | 2017 | 122 |
| 2023 | 46 | | 2016 | 126 |
| 2022 | 57 | | 2015 | 89 |
| 2021 | 61 | | 2013–2014 | 77 |
| 2020 | 91 | | 2008 | 1 |

These volumes look like OJK's full POJK + SEOJK/PADK output, not a curated subset. **Exact completeness still needs checking against the JDIH OJK and BPK baselines** (roadmap M0).

## Field quality

| Field | Quality | Notes |
|---|---|---|
| `uid` | ✅ stable UUID | |
| `title` | ✅ English | Style varies ("POJK on …", "OJK Regulation No. …", or a bare subject) |
| `source` | ✅ link to the official OJK page | Lets us fetch the official PDF ourselves |
| `file_url` | ⚠️ RegAlert's own copy (`regalert.today/cms/{id}/save`) | Not the OJK PDF. We should store the official bytes from `source` |
| `instrument_no` | ❌ **not usable as an ID** | See problems 1 and 2 |
| `identifier` | ⚠️ often empty for 2023+ documents | Inconsistent format (`10.SEOJK.03/2014` vs `10/SEOJK.03/2020`) |
| `published` | ⚠️ not always the issue date | See problem 3 |
| `summary` | ✅ (out of scope for now) | Interpretive content. Not needed this phase |
| `lineage` | ⚠️ useful as hints | See problem 5 |

## Problems found

1. **`instrument_no` collisions between document types.** PADK and POJK with the same number and year get the same key:
   - PADK 11/2026 and POJK 11/2026 → both `ojk:regulation:11:2026`
   - PADK 1/2026 and POJK 1/2026 → both `ojk:regulation:1:2026`
   - PADK 6/2026 and POJK 6/2026 → both `ojk:regulation:6:2026`
2. **`instrument_no` missing or inconsistent.** It's `null` for POJK 10/2026, 10/2025, 47/2024, 36/2024 and others. The prefixes vary: `seojk.03-circular-letter`, `seojk.03-circular`, `seojk-circular-letter`, `seojk.05-letter`, `seojk.05-regulation`, `padk.05-decision`, `pojk-regulation`, `regulation`. → We must derive the canonical ID ourselves, from the `source` URL slug and the text.
3. **`published` is sometimes the date RegAlert ingested a document, not when OJK issued it.** SEOJK 5/2023, 7/2023, 8/2023, 12/2023 and 18/2023 are all dated `2026-06-23`. → Take dates from the document text or from BPK. An incremental sync can't assume older items never show up later.
4. **Duplicates.** POJK 28/POJK.05/2014 appears twice (`eb5c75dc…`, `56915646…`), and so does POJK 24/POJK.04/2014 (`2f014520…`, `428825a7…`). → Remove duplicates by canonical ID.
5. **Lineage is incomplete and sometimes mislabelled.**
   - ✅ POJK 77/2016 ← `supersedes` ← POJK 10/POJK.05/2022 (correct).
   - ✅ POJK 1/2026 → status "In force", based on Law 21/2011 and Law 4/2023.
   - ⚠️ POJK 1/2026 *replaces* 37/POJK.03/2017, but is labelled `amends`.
   - ❌ POJK 35/POJK.05/2018 has no `amends` link from POJK 7/POJK.05/2022, even though that regulation is titled as an amendment to it and RegAlert has it.
   → Use lineage as a hint, and reconcile it against BPK's "Status Peraturan".
6. **Unstable pagination.** Paging with `offset` returned the same rows at the end of one page and the start of the next (`f1aad90e…` and `ad8f4f2e…` at offsets 299/300), because rows with the same date have no fixed order. → Page by date range (`since`/`until`), not by offset alone, and remove duplicates by `uid`.
7. **Coverage is limited to OJK's own instruments.** No UU, PP, PBI or Bapepam-LK archive (JDIH OJK lists these alongside OJK's rules). Nothing before 2013 except one 2008 rule.

## Commercial and legal

- **Fair use:** free full-text reads "pause until the next day (UTC)" at bulk scale. **Observed during the backfill (2026-10-12):** a pause after about 200 reads, which lifted again within 15–20 minutes, so all 973 documents were read the same day without bulk access. A backfill is about 977 `get_document` calls. With `max_chars=400000`, most documents fit in one call; with `include_original=true`, each response doubles in size. The free tier will very likely pause partway through. → **Request bulk access.**
- **Licence:** "The documents are public regulatory instruments; the summaries and translations are RegAlert's." We may need permission to store and pass on their **English translations** to later phases. The Indonesian original text and the official PDF are public.
- **Third-party risk:** we know little about the operator (no named company was found). If RegAlert disappears, the pipeline must still work from BPK and JDIH OJK.
- **The API is the only permitted route.** Scraping the website is explicitly not allowed.

## How it would fit the pipeline

| Stage | RegAlert's role | Still needed from us / other sources |
|---|---|---|
| discover | **Primary.** `recent_documents regulator=OJK since=…` by date range | JDIH OJK / BPK for completeness checks and non-OJK instruments |
| fetch / download | Provides the `source` link | We download the official PDF from ojk.go.id ourselves and store its sha256 |
| extract | **Primary.** Indonesian original text and English translation | Our own PDF extraction as a fallback and for spot checks |
| normalize | — | Canonical ID from the `source` slug and text, never from `instrument_no` |
| reconcile | Lineage as hints | BPK status and links between regulations are authoritative |
| monitor | `get_account` (whether we're paused), daily `recent` totals | Our run report |

## Recommendation and next steps

1. **Email `ask@regalert.today`:** ask for bulk access, terms for storing English translations and full text, rate limits, and whether there's a changelog or feed of edits.
2. **M0 reconciliation spike:** match all 977 RegAlert rows to BPK (512 POJK) and to the JDIH OJK baseline, using canonical IDs. Measure overlap and gaps.
3. **If (1) and (2) go well:** add a `regalert` source connector to M1, ahead of the BPK connector, and shrink the scope of M4 (text extraction) to fallback and verification only.
4. **Keep BPK and JDIH OJK in the plan** for authority, status and completeness.

## Evidence (sample UIDs)

| Document | RegAlert uid |
|---|---|
| POJK 1/2026 (full text and lineage checked) | `925da278-6d46-495b-9dba-d3efc7bf7758` |
| POJK 77/POJK.01/2016 | `5e659907-8f2e-402f-a87a-d2b9984957e5` |
| POJK 10/POJK.05/2022 | `f350c503-b6aa-4bc3-9504-fd15d481750d` |
| POJK 35/POJK.05/2018 | `e66b4510-e2da-4800-bc87-e8f9543a9c90` |
| POJK 7/POJK.05/2022 (amends 35/2018; link missing) | `9ee80ab5-7b13-4cf5-9b6d-4af3a3d757a0` |
| POJK 10/POJK.05/2019 | `d69ff39d-747d-4620-9c3f-32a521732bb7` |
| POJK 32/2025 (BNPL) | `aa7aa266-49ae-4cf0-9f70-112b7eebc432` |
| PADK 2/2026 (BNPL, financing companies) | `75044138-fdce-4e6f-9f62-a25980b1efb3` |
| PADK 11/2026 / POJK 11/2026 (ID collision) | `63259131-2932-4912-a21b-75ba33dc9a1c` / `cf104e9d-242d-4b80-81fe-ea37d4ceb06e` |
| Duplicate POJK 28/POJK.05/2014 | `eb5c75dc-68bd-427c-994a-14b97c3da06a`, `56915646-d0df-4258-a4af-d459a0dce6a0` |
| Ingestion-dated SEOJK 7/2023 (`published` 2026-06-23) | `d7698690-d46f-489c-9f52-6b223f6f598a` |
