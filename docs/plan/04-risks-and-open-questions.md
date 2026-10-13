# 04 — Risks and Open Questions

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| RegAlert becomes unavailable or blocks us. On 2026-10-13 its edge returned HTTP 403 to every request from our network, signed in or not | Seen once | High | Everything already read is stored locally and can be re-parsed offline. The official-source fallback (M3) plugs in as a second `Source` with no pipeline change. Raise it with RegAlert (Q9, Q10) |
| The team's connectors drift from the shared interface | Medium | Medium | Each repo copies `source_connector.py` unmodified; a test fails if this repo's copy differs from `contract/CONNECTOR_INTERFACE.md` |
| JDIH OJK's firewall blocks our crawler (it already rejected plain `curl`) | High | High | Identifying UA, low request rate, real browser via Playwright if needed. Test from the actual runtime environment in M0. If still blocked, ask OJK for access |
| JDIH OJK lists need JavaScript | Confirmed | Medium | Find the XHR endpoint first; fall back to Playwright |
| Source layout changes break parsers without anyone noticing | Medium | High | Parser fixtures and tests, alerts on zero records or falling field-fill rates, versioned parsers, re-parsing from stored snapshots |
| BPK metadata errors (e.g. wrong slugs, wrong subject tags) | Confirmed | Medium | Don't use slugs as identifiers. Check BPK fields against JDIH during reconciliation, and record provenance per field |
| BPK has almost no SEOJK/PADK | Confirmed | High | JDIH OJK is the primary source for that family |
| Scanned PDFs, or PDFs with a bad text layer | Medium | Medium | Quality heuristics plus OCR fallback, with quality reported for each file |
| SEOJK → PADK renaming causes duplicates or misclassification | Medium | Medium | One family covering both, with the original label kept |
| Terms of use forbid automated access | Low–Medium | High | Read the ToS in M0. Keep the crawl light and identifiable. Escalate if anything is unclear |
| Government sites go down or change domain | Medium | Medium | Retries, alerts, and stored raw snapshots so we can keep working offline |

## Open questions

| # | Question | Who answers it | When |
|---|---|---|---|
| Q1 | Are POJK and SEOJK numbers unique per year across sectors? This decides the canonical key format | Engineering (M0 spike) | M0 |
| Q2 | Does JDIH OJK have an XHR or JSON endpoint behind its list pages? | Engineering (M0 spike) | M0 |
| Q3 | Where will the scheduled job run (local, CI, cloud VM)? This affects firewall behavior and storage | Team | Deferred to the orchestration phase (2026-10-12), probably Airflow calling `OJKConnector`; runs are manual for now |
| Q4 | Which notification channel for alerts? | Team | Deferred to the orchestration phase (2026-10-12); alerts go to data/alerts.jsonl and run reports |
| Q5 | Which document types besides POJK and SEOJK/PADK belong in this phase (UU, PP, Bapepam-LK legacy rules, BI rules listed by OJK)? Default: collect everything JDIH OJK lists | Product | M2 |
| Q6 | Should we contact Hukumonline about API access, and with what budget? | Product / business | Any time |
| Q7 | Do we also need English versions where they exist? | Product | M2 |
| Q8 | How long do we keep raw HTML snapshots? | Team | M1 |
| Q9 | May we store RegAlert's English translations (and its extracted Indonesian text) locally, and use them in our knowledge database? Until they confirm, translations stay out of `corpus/` | RegAlert (`ask@regalert.today`) | Before the RegAlert source ships |
| Q10 | Is a paced backfill (about 977 full-text reads spread over days, then a few reads a week) acceptable under fair use, or do we need bulk access? | RegAlert | Before the RegAlert source ships |
| Q11 | ~~Which field holds RegAlert's original-language text, and how is it paged?~~ **Answered 2026-10-12:** `original_text`, paged independently with `original_text_next_offset` and `original_text_total_chars`. Lineage edges carry `relation`, `uid`, `title`, `date`. RegAlert sometimes supplies a captured ojk.go.id page instead of the instrument (flagged as `text_not_instrument`; needs the official PDF, M3) | Engineering | Done |
