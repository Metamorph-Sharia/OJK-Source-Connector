# 03 — Source access findings

Part of the [OJK publishing and storage report](README.md). Live checks on 2026-10-14, about 1 request per second, User-Agent `ojk-connector/0.1 (research)`. Raw responses were kept outside the repository.

## JDIH OJK

**Access:** every request returned `200`, including plain `curl`. On 2026-10-12 the F5 firewall had rejected plain `curl`, so the block depends on the client and network, not on any rule of the site. Keep the risk open ([04-risks](../../plan/04-risks-and-open-questions.md)), but we don't need Playwright for now.

### List endpoint (answers open question Q2)

The list page is a DataTables table. Its data comes from JSON endpoints in the page's inline script:

| Endpoint | Parameters | Notes |
|---|---|---|
| `GET /Web/ViewPeraturan/ListDataPeraturan` | `sektor`, `jenisPeraturan`, `sLanguage=id` | **The one to use.** Returns every row of one sector × type, with no paging |
| `GET /Web/ViewPeraturan/ListDataPeraturanSeacrhAll` | `+ searchAll` | Free-text search (typo is in the URL itself). Returned 0 rows for our queries; probably needs session state |
| `GET /Web/ViewPeraturan/ListDataPeraturanSeacrhByFilter` | `sector`, `klasifikasi`, `sub_klasifikasi`, `jenis_peraturan`, years | Filter form |
| `GET /Web/ViewPeraturan/getListKlasifikasi`, `getListSubKlasifikasi` | `IdParentDimensi`, `SandiParentReferensi`, `IdDimensi` | Classification trees |

Response shape (legacy DataTables):

```json
{"sEcho": null, "iTotalRecords": [19], "iTotalDisplayRecords": [19],
 "aaData": [[
   "<a href='http://jdih.ojk.go.id/Web/ViewPeraturan/Detail/009e5e55-…/09/06'>Peraturan Otoritas Jasa Keuangan Republik Indonesia Nomor 8 Tahun 2026 tentang …</a>",
   "8",                                   // number
   "Lembaga Pembiayaan, Perusahaan Modal Ventura, …",   // sector
   null, null,                            // classification, sub-classification
   "Peraturan OJK",                       // type
   "06-07-2026",                          // date (DD-MM-YYYY; often blank)
   "Berlaku"                              // status
 ], …]}
```

**Coverage from one pass (10 sectors × 3 types = 30 requests):**

| `jenisPeraturan` | Rows (sum over sectors) | Unique documents | Notes |
|---|---|---|---|
| 06 Peraturan OJK | 565 | 565 | No document is listed under two sectors |
| 09 SEOJK / PADK | 408 | 408 | 391 titled SEOJK, 17 titled PADK |
| 08 Peraturan Bapepam dan LK | 0 | 0 | Empty under every sector; legacy rules may sit under another type code. Unverified |

A full catalogue sweep costs about 30 requests, so we can afford one every day. It doubles as a **status monitor**: a row going from `Berlaku` to `Tidak Berlaku` or `Dicabut Sebagian` is a change event we get for free.

### Detail page

`/Web/ViewPeraturan/Detail/{GUID}/{sektor}/{jenis}` is server-rendered HTML. Fields seen on POJK 8/2026:

| Field | Value |
|---|---|
| T.E.U Badan | Indonesia. Otoritas Jasa Keuangan |
| Nomor Peraturan / Jenis / Singkatan | 8 / Peraturan OJK / POJK |
| Sektor, Klasifikasi, Sub Klasifikasi | Lembaga Pembiayaan …; (empty); (empty) |
| Tempat / Tanggal Penetapan / Tanggal Pengundangan | Jakarta / 12-06-2026 / 06-07-2026 |
| Sumber LN / Sumber TLN | `2026 (16/OJK) : 18 HLM.` / `2026 (199/OJK)` |
| Status Peraturan | Berlaku Sejak Tanggal 06-07-2026 |
| Subjek | PVML - PELAPORAN DAN PERMINTAAN DATA TRANSAKSI PENDANAAN … |
| Bahasa / Lokasi / Bidang Hukum | Bahasa Indonesia / Direktorat Pengembangan Hukum / Hukum Ekonomi dan Bisnis |
| Landasan Hukum | UU 21/2011; UU 4/2023; UU 4/2026 (amending UU 4/2023) |
| Uji Materi | (empty) |
| Riwayat Peraturan | **Mencabut: POJK 40/2024 … Pasal Terkait: Pasal 187** |
| Dokumen | Peraturan `2026pojk008.pdf`, Abstrak `2026abspojk008.pdf`, FAQ `2026faqpojk008.pdf` |

File links: `/Web/ViewPeraturan/DownloadDokumen/{GUID}` (regulation, abstract, FAQ), `/Web/ViewPeraturan/DownloadFileRiwayat/{id}` (a related regulation's file) and `/peraturan/peraturan/downloadfilelandasan/{id}` (legal-basis files). The JDIH document GUID is stable and makes a good `source_id`.

### Data-quality issues

- **Title typos:** `Nomor34/POJK.05/2015`, `28/POJK,05/2014`, `12/POJK.05.2014`, `nomor 18/SEOJK.03/2023`. Never derive identity from JDIH titles alone. The document header stays the primary evidence.
- **Amendment titles cite the amended regulation:** "POJK Nomor 1 Tahun 2023 tentang Perubahan Atas POJK Nomor 5/POJK.05/2013 …". A naive parser picks up the second number. (Our first pass did; the identity rules already guard against this.)
- **List dates:** blank for 441 of 565 POJK; 8 SEOJK/PADK carry 2027 dates.
- **Relabelling:** some old SEOJKs appear as PADK (see [01](01-how-ojk-publishes.md#numbering-and-naming)).

## ojk.go.id

**Platform:** Microsoft SharePoint (`/_layouts/15/`, ASP.NET WebForms `__doPostBack`).

| Check | Result |
|---|---|
| Detail page `/id/regulasi/Pages/{slug}.aspx` | Server-rendered. Fields: Sektor, SubSektor, Jenis Regulasi, Nomor Regulasi, Tanggal Berlaku (`M/D/YYYY`), `Lampiran 1…n` attachments. No summary or FAQ |
| File URL | `/id/regulasi/Documents/Pages/{slug}/{file}.pdf`, e.g. `…/POJK-tentang-Perizinan-Usaha-dan-Kelembagaan-Lembaga-Penjaminan/pojk%205-2014.pdf` |
| File download | `200 application/pdf`, 1.26 MB, `Last-Modified: Tue, 05 Mar 2019`, **`ETag: "{4DDB4993-…},1"`**. The suffix after the GUID is SharePoint's file version, so a re-uploaded PDF shows up as a changed ETag. A conditional `HEAD` is a cheap change check |
| Listing `/id/regulasi/otoritas-jasa-keuangan/peraturan-ojk/Default.aspx` | Server-rendered (669 KB) and paged by an ASP.NET `DataPager` postback. Paging needs the form's ViewState round-trip, which is fragile |
| `robots.txt`, `sitemap.xml` | Both `404` |
| SharePoint REST `/_api/web/lists/…` | `401 Access denied` |
| English pages `/en/regulasi/Pages/…` | Exist for some regulations; unofficial |
| Draft regulations | `/id/regulasi/otoritas-jasa-keuangan/rancangan-regulasi/` ("Permintaan Tanggapan atas RPOJK …") |
| Hygiene | Test pages in the production menu ("coba eror", "Test Regulation") |

**Role:** a file source, not a discovery source. Use the `official_url` we already hold (from RegAlert, later from JDIH) to fetch the PDF and annexes, and watch the ETag. For discovery, JDIH's JSON is far better than ojk.go.id's postback paging.

## Reconciliation with our corpus

The JDIH catalogue (973 parsed IDs) compared with our RegAlert-based corpus (972 IDs). Matched by canonical ID `ojk:{family}:{year}:{number}`, where family and number were taken from the first `Nomor` in the JDIH title. This is a first pass, not the full M0/M4 reconciliation.

| Family | JDIH | Ours | Both | JDIH only | Ours only |
|---|---|---|---|---|---|
| POJK | 564 | 573 | 534 | 30 | 39 |
| SEOJK | 391 | 377 | 353 | 38 | 24 |
| PADK | 18 | 22 | 15 | 3 | 7 |
| **All** | **973** | **972** | **902** | **71** | **70** |

Patterns:
- **Ours only, mostly 2024.** We hold 27 POJK from 2024 (numbers between 10 and 48) and 17 SEOJK from 2024 that the JDIH sector listings don't return. All have a high-confidence ID from their own header. JDIH's search endpoint returned no rows for any query, so it couldn't settle this. Either JDIH files 2024 instruments under codes we didn't query, or its listing really lags. **Needs checking in the spike.**
- **JDIH only, mostly 2014–2019 POJK and SEOJK.** These are gaps in RegAlert's coverage that a JDIH source would fill: about 70 documents, 7% of the catalogue.
- **Label differences.** For example, JDIH `padk:2016:39` and `padk:2017:20` are our `seojk:2016:39` and `seojk:2017:20`. They are the same instruments, relabelled by JDIH.
- **The union is about 1,040 instruments**, before UU/PP/PBI and the instruments listed under other type codes.

**Conclusion:** no single channel is complete. JDIH is the best completeness baseline, but not a perfect one, so the registry must record which sources list each instrument and alert on any one-sided gap.
