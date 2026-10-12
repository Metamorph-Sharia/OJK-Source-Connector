# 01 — Source Evaluation

Research done 2026-10-12. The question was: does any existing site already provide OJK regulations in extracted, normalized form, so that we don't have to build all of that from OJK's own pages?

**Answer: not completely.** No public source gives full text in structured form. One source, BPK, has good structured *metadata* for POJK but covers SEOJK/PADK poorly. The plan is to use several sources and reconcile them.

## Summary

| Source | Structured data | OJK coverage | Role in this project |
|---|---|---|---|
| **BPK Database Peraturan** — `peraturan.bpk.go.id` | Yes. Consistent metadata fields, status and links between regulations, abstracts | ~512 POJK, only ~25 SEOJK (as of 2026-10-12) | **Primary source for POJK metadata and links between regulations** |
| **JDIH OJK** — `jdih.ojk.go.id` | No. HTML only, with lists rendered by JavaScript. No API, JSON, RSS or export | Most complete: 14 document types × 10 sectors, includes PADK | **Authoritative source.** Primary for SEOJK/PADK, sector classification and completeness checks |
| **ojk.go.id regulation pages** — `ojk.go.id/id/regulasi/...`, `ojk.go.id/id/kanal/.../regulasi/...` | No. Pages are organized by sector and rendered by JavaScript | Broad | Optional third source and fallback |
| **Hukumonline Pusat Data** | Yes (paid). Includes analysis and English versions | Broad | Commercial option. No public API docs found; needs a sales inquiry |
| **JDIHN** — `jdihn.go.id` | Has a sync mechanism between member portals, but it isn't public | Unclear | Not usable |
| **Perpusnas JDIH API** — `api-jdih.perpusnas.go.id` | JSON API with a bearer token | Library law only | Not relevant. Its docs also show signs of tampering |
| **Apify "OJK Regulatory Monitor"** | Metadata, abstract and PDF links (no full text) | Scrapes ojk.go.id | Not recommended: 2 users, paid, unproven |
| **RegAlert** — `regalert.today`, API at `mcp.regalert.today` | **Yes.** Official MCP and REST API: metadata, English and original-language text, lineage (citation links) | 977 OJK docs (POJK, SEOJK, PADK; 2013–2026) | **Feasible as the primary source for discovery and text, not as the source of truth.** See [feasibility study](../research/regalert-feasibility.md) |
| **Hugging Face / GitHub datasets** | — | None found specific to OJK | None |

## BPK Database Peraturan

**Discovery**
- Search: `https://peraturan.bpk.go.id/Search?keywords=&tentang=&nomor=&jenis={id}` (paginated).
  - `jenis=80` → Peraturan Otoritas Jasa Keuangan (512 results)
  - `jenis=212` → Surat Edaran Otoritas Jasa Keuangan (25 results)
  - `entitas=584` → Otoritas Jasa Keuangan
- Sitemaps: `https://peraturan.bpk.go.id/sitemap.xml` is an index of per-year files (`sitemap-peraturan-{year}.xml`), each with a `lastmod` date. These cover *all* Indonesian regulations, not just OJK, so they would need filtering.
- `robots.txt` blocks only `/Admin/`, `/Identity/`, `/Account/`, `/Manage/`, `/health` and `/Error`.

**Detail page** — `/Details/{id}/{slug}`. Fields seen on POJK 1/2026:

| BPK label | Meaning | Example |
|---|---|---|
| Tipe Dokumen | document class | Peraturan Perundang-undangan |
| Judul | full title | Peraturan Otoritas Jasa Keuangan Nomor 1 Tahun 2026 tentang … |
| T.E.U. | issuing body | Indonesia, Otoritas Jasa Keuangan |
| Nomor | number | 1 |
| Bentuk / Bentuk Singkat | type / short type | Peraturan Otoritas Jasa Keuangan / Peraturan OJK |
| Tahun | year | 2026 |
| Tempat Penetapan | place of enactment | Jakarta |
| Tanggal Penetapan / Pengundangan / Berlaku | enacted / promulgated / effective dates | 06 Feb 2026 / 23 Feb 2026 / 23 Feb 2026 |
| Sumber | gazette reference | BN.2026 (3/35 hlm) |
| Subjek | subjects | PERBANKAN, LEMBAGA KEUANGAN |
| Status | in force or not | Berlaku |
| Bahasa, Lokasi, Bidang | language, location, field of law | |
| Alias | alternate citations | Peraturan OJK 1/2026 … |
| Status Peraturan | links to other regulations | Mencabut: POJK 37/POJK.03/2017 |
| Abstrak | editor-written summary | considerations, legal basis, scope, effective date |
| Files | PDFs | `/Download/{fileId}/{name}.pdf`, `/Read/{fileId}/{name}.pdf` |

**Known issues**
- SEOJK/PADK coverage is very thin.
- Some data errors. For example, the URL slug for POJK 26/2019 (`/Details/128521/permenag-no-20-tahun-2019`) names an unrelated regulation. Don't treat slugs as identifiers.
- BPK itself says to check its documents against the issuing agency.
- No full text. PDFs must be downloaded and text extracted ourselves.

## JDIH OJK

**URL patterns**
- List: `/Web/ViewPeraturan/Index?sektor={01..10}&jenisPeraturan={01..14}`
- Detail: `/web/ViewPeraturan/Detail/{GUID}/00/00`

**Sector codes (`sektor`)**

| Code | Sector |
|---|---|
| 01 | Perbankan |
| 02 | Pasar Modal, Keuangan Derivatif, dan Bursa Karbon |
| 03 | IKNB |
| 04 | Perilaku Pelaku Usaha Jasa Keuangan, Edukasi dan Perlindungan Konsumen |
| 05 | Manajemen Strategis |
| 06 | Lain-Lain |
| 07 | Kebijakan Strategis |
| 08 | Perasuransian, Penjaminan, dan Dana Pensiun |
| 09 | Lembaga Pembiayaan, Perusahaan Modal Ventura, Lembaga Keuangan Mikro, dan LJK Lainnya |
| 10 | Inovasi Teknologi Sektor Keuangan, Aset Keuangan Digital dan Aset Kripto |

**Document type codes (`jenisPeraturan`)**

| Code | Type |
|---|---|
| 01 | Undang-Undang |
| 02 | PERPPU |
| 03 | Peraturan Presiden |
| 04 | Peraturan Pemerintah |
| 05 | Peraturan Menteri |
| 06 | Peraturan OJK |
| 07 | Peraturan BI |
| 08 | Peraturan Bapepam dan LK |
| 09 | Surat Edaran OJK / Peraturan Anggota Dewan Komisioner (PADK) |
| 10 | Surat Edaran BI |
| 11 | SK Dir |
| 12 | Keputusan Ketua Bapepam |
| 13 | SE Ketua Bapepam |
| 14 | Peraturan Lain |

**Known issues**
- The result list is rendered by JavaScript, so a plain HTTP fetch returns an empty list. We will probably need a headless browser, or to find the XHR endpoint the page calls.
- The F5-style firewall rejected plain `curl` ("Request Rejected"). It may block datacenter IPs or default HTTP clients.
- OJK has renamed SEOJK to **PADK** (Peraturan Anggota Dewan Komisioner). Existing SEOJKs remain valid and are treated as PADKs until they are replaced. Both labels must map to one document type family.

## RegAlert

Evaluated 2026-10-12. **Verdict: feasible as the primary source for discovery and text, not as the source of truth.** Full results are in [../research/regalert-feasibility.md](../research/regalert-feasibility.md). The "still needs checking" list at the end of this section was answered there.

**What it is.** A third-party tracker of financial regulators' official publications: 70,482 documents from 371 regulators in 192 jurisdictions, according to its own docs. Each document has an English summary and English text, published the same day the regulator publishes it. Contact: `ask@regalert.today`.

**Access**
- The website (`regalert.today`) shows a Vercel bot challenge (HTTP 429) to anything that isn't a browser. **Scraping the website is explicitly not permitted.**
- The official machine interface is at `mcp.regalert.today`, documented in `https://mcp.regalert.today/llms.txt` and `https://mcp.regalert.today/openapi.json`:
  - MCP (Streamable HTTP): `https://mcp.regalert.today/mcp`
  - REST (JSON): `https://mcp.regalert.today/v1`
  - Auth: an OAuth 2.1 bearer token from a RegAlert account (Clerk, `clerk.regalert.today`, PKCE S256). Every route except the docs needs it.
- **Terms:** free with an account under fair use. At bulk scale, full-text reads pause until the next day (UTC), while search and lineage keep working. Bulk access: write to `ask@regalert.today`. "The documents are public regulatory instruments; the summaries and translations are RegAlert's."

**REST endpoints**

| Endpoint | Use for us |
|---|---|
| `GET /v1/recent?regulator=&jurisdiction=ID&since=&until=&limit≤100&offset=` | **Discovery and incremental sync.** Returns documents newest first with an exact total |
| `GET /v1/search?q=&regulator=&jurisdiction=&limit≤50` | Looking up a specific regulation |
| `GET /v1/documents/{uid}?offset=&max_chars≤400000&include_original=true` | **Full text.** English text, plus the original-language text when `include_original=true`. Annexes appear as `enclosures` |
| `GET /v1/documents/{uid}/lineage?direction=outgoing\|incoming&relation=amends\|supersedes\|issued_under\|refers` | **Links between regulations** |
| `GET /v1/regulators?jurisdiction=ID` | Regulator codes (OJK's code not yet confirmed) |
| `GET /v1/topics`, `GET /v1/account` | Topic tags; whether we're currently under a fair-use pause |

**Fields** returned by `recent`: `uid, title, published, regulator, summary, summary_full, identifier, regulator_name, jurisdiction, jurisdiction_name, region, topics, url, instrument_no, type, language, source, file_url, thumbnail_url`.

**Why it's attractive:** it covers most of the pipeline we'd otherwise build — discovery, text extraction, links between regulations and incremental sync by date — in one structured API.

**What still needs checking (needs a signed-in account):**
1. OJK's regulator code, and the total document count for OJK.
2. **Completeness** against JDIH OJK and BPK, especially for regulations from before 2026. Is there a historical backfill, or only items since tracking started?
3. Which document types are covered: POJK, SEOJK/PADK, press releases, others. Is `instrument_no` filled in, and does it parse into our canonical ID?
4. Quality of the original-language (Indonesian) text compared with the PDF. Our authoritative text must be the Indonesian original, not an English translation.
5. Whether `source` / `file_url` point at the official OJK PDF, so we can still keep the original bytes.
6. Fair-use limits at backfill scale, and whether we need to ask for bulk access.
7. Licensing of the derived content (summaries and translations belong to RegAlert) if we pass it on to later phases.

**Fit with the plan:** even if coverage is good, RegAlert is a third party. Use it as a source alongside BPK and JDIH OJK, reconciled against them, not as our only source of truth.

## References

- BPK Database Peraturan — https://peraturan.bpk.go.id/
- BPK example record — https://peraturan.bpk.go.id/Details/347285/peraturan-ojk-no-1-tahun-2026
- JDIH OJK — https://jdih.ojk.go.id/
- OJK regulation pages — https://ojk.go.id/id/regulasi/otoritas-jasa-keuangan/peraturan-ojk/Default.aspx
- OJK press release, SEOJK renamed to PADK — https://ojk.go.id/id/berita-dan-kegiatan/siaran-pers/Documents/Pages/OJK-Sempurnakan-Tata-Cara-Pembentukan-Peraturan-Nomenklatur-SEOJK-Berubah-menjadi-PADK/SP%20167%20OJK%20Sempurnakan%20Tata%20Cara%20Pembentukan%20Peraturan%2c%20Nomenklatur%20SEOJK%20Berubah%20Menjadi%20PADK.pdf
- Hukumonline Pusat Data — https://www.hukumonline.com/pusatdata/
- JDIHN — https://jdihn.go.id/
- Perpusnas JDIH API — https://api-jdih.perpusnas.go.id/
- Apify OJK Regulatory Monitor — https://apify.com/kaladata/ojk-regulatory-monitor
- AIEPA field guide to Indonesian policy data — https://aiepa.ai/playbook-indonesian-policy-data
- RegAlert — https://regalert.today/about, https://regalert.today/agents
- RegAlert agent API docs — https://mcp.regalert.today/llms.txt, https://mcp.regalert.today/openapi.json
