# sample/ - small input files under version control

`data/` is git-ignored because it holds the 2.8 GB Companies House bulk file, a parquet copy of
it and the search/page cache. The small files a reviewer needs are copied here. The code looks
in `data/` first and falls back to this folder (see `src/io_utils.py`).

| File | What it is | Local counterpart |
|---|---|---|
| `sample_blank.csv` | The 420 sampled companies, with empty columns for manual checking. Output of `src/draw_sample.py`. | `data/sample_blank.csv`; `data/sample_work.csv` is the working copy and is byte-identical to it |
| `pilot_results.csv` | Hand-checked results for the pilot companies (the answer key used by `src/evaluate.py`). | `data/pilot_results.csv` |
| `population_bucket_counts.csv` | Number of active companies in each bucket in the bulk file. Output of `src/draw_sample.py`; used for the weighted rates in `src/summarise_verification.py`. | - |

## Source data

- Source: Companies House "Free Company Data Product", the single-file download
  `BasicCompanyDataAsOneFile-2026-11-01.zip` from
  <https://download.companieshouse.gov.uk/en_output.html>.
- Snapshot used: **2026-11-01**. Unzip it to `data/BasicCompanyDataAsOneFile-2026-11-01.csv`.
- The unzipped CSV is 2,814,210,743 bytes, SHA-256
  `c3bcf0942a58bd1a6a9ed8829119befd564fbc79d3ace1d5382276e3e05b8ed1`.
- The download page offers the current monthly snapshot. A different snapshot gives a
  different sample.

## How the sample was drawn

```
python src/draw_sample.py --check sample/sample_blank.csv
```

1. Keep companies with `CompanyStatus` = `Active` (5,179,256 companies).
2. Assign `vat_bucket` from `Accounts.AccountCategory`:

   | vat_bucket | Account categories | Active companies (N) | Sampled |
   |---|---|---|---|
   | `likely` | FULL, GROUP, MEDIUM, AUDITED ABRIDGED | 113,515 | 60 |
   | `unclear` | MICRO ENTITY, TOTAL EXEMPTION FULL, UNAUDITED ABRIDGED, SMALL, TOTAL EXEMPTION SMALL, PARTIAL EXEMPTION | 3,129,556 | 200 |
   | `dormant` | DORMANT | 557,408 | 40 |
   | `new_no_accounts` | NO ACCOUNTS FILED, incorporated on or after the cutoff | 1,151,548 | 40 |
   | `overdue_no_accounts` | NO ACCOUNTS FILED, incorporated before the cutoff | 190,578 | 40 |
   | `subsidiary` | AUDIT EXEMPTION SUBSIDIARY, FILING EXEMPTION SUBSIDIARY | 35,399 | 40 |
   | (none, not sampled) | ACCOUNTS TYPE NOT AVAILABLE | 1,252 | 0 |

3. Cutoff: **2025-02-14**, which is 21 months before 2026-11-14, the day the sample was drawn.
   This is the value that reproduces the sample. With 2025-02-01 (21 months before the
   snapshot date) the two `no_accounts` buckets differ and only 340 of the 420 companies are
   drawn again.
4. Within each bucket, shuffle with seed 42 and keep the first *Sampled* rows; then shuffle the
   420 rows with seed 42.

`draw_sample.py` with the default settings reproduces `sample_blank.csv` byte for byte (polars
2.1.0, Windows). The bucket rules were reconstructed from the data: they give exactly the
bucket counts N recorded when the sample was drawn, and the same 420 companies in the same order.

## Columns of `sample_blank.csv`

| Column | Meaning |
|---|---|
| `CompanyName`, `CompanyNumber` | As in the bulk file. Company numbers are text (leading zeros, prefixes such as `SC`). |
| `RegAddress.AddressLine1`, `RegAddress.AddressLine2`, `RegAddress.PostTown`, `RegAddress.PostCode` | Registered office address. The post town is used in the search queries. |
| `CompanyStatus` | Always `Active`. |
| `CompanyCategory` | Company type, for example `Private Limited Company`. |
| `IncorporationDate` | `YYYY-MM-DD`. |
| `Accounts.AccountCategory` | Type of the last accounts filed. Determines `vat_bucket`. |
| `SICCode.SicText_1` ... `SICCode.SicText_4` | Up to four SIC codes with their text. |
| `PreviousName_1.CompanyName` | Most recent previous name, if any. Used for search query q3 and name matching. |
| `vat_bucket` | Sampling stratum, see the table above. |
| `outcome`, `search_query`, `website`, `site_match_evidence`, `company_number_on_site`, `vat_number`, `vat_location_on_site`, `hmrc_valid`, `hmrc_name`, `hmrc_address`, `match_verdict`, `notes` | Empty. Columns for manual checking; the pipeline does not read them. |

## Columns of `pilot_results.csv`

One row per company checked by hand (31 rows; row 31 is not in
the sample and is ignored by the code).

| Column | Meaning |
|---|---|
| `pilot_row` | Row number in the pilot. |
| `CompanyNumber`, `CompanyName` | The company checked. |
| `outcome` | Manual outcome; same vocabulary as the pipeline, see below. |
| `website` | Website judged to be the company's, or the best candidate. |
| `site_match_evidence` | What links the site to the company. `TO RECORD` = a site was accepted but the evidence was not written down; `none` = nothing links it. |
| `company_number_on_site` | Whether the company number was seen on the site. |
| `vat_number`, `vat_location_on_site` | VAT number seen on the site and where. |
| `hmrc_valid` | `yes` / `no`: whether HMRC confirmed the number as a valid VAT registration. |
| `hmrc_name` | Business name HMRC returned for the number. |
| `match_verdict` | See below. |
| `notes` | Free text. Rows whose text says "out of sample" are ignored by the code. |

### `outcome` values

| Value | Meaning |
|---|---|
| `no_website_found` | No site could be linked to the company. |
| `candidate_site_unconfirmed` | A site was found but the link to the company is weak (name words only, or a different company number). |
| `website_no_vat` | A site confirmed as the company's shows no VAT number. |
| `site_states_not_registered` | A confirmed site says the business is not VAT registered. |
| `vat_found` | A VAT number was seen on a kept site. |

### `match_verdict` values

Filled in by hand after the HMRC check; used in `pilot_results.csv` and in
`runs/<run>/to_verify_checked.csv`.

| Value | Meaning |
|---|---|
| `same` | The number is registered to the sampled company. |
| `related_entity` | The number is registered to a related entity (for example a sister company or trading subsidiary), not the sampled company. |
| `different` | The number is registered to a different entity. |
