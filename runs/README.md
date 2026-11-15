# runs/ - one folder per pipeline run

Run folders are evidence: a run never writes into an existing folder, and files here are not
edited afterwards. Counts in `RUN_NOTES.md` are raw pipeline output, not HMRC-verified.

| Folder | What it is |
|---|---|
| `run_01_exact_name_search/` | Superseded. First full run (420 companies): quoted exact-name search with a previous-name retry, no directory detection. Kept as a record; its `RUN_NOTES.md` lists the problems found. |
| `pilot_try_01/` | Pilot-only trial: the 30 pilot companies, with query variants q1-q3 and directory detection. |
| `run_02_directory_filter_fallback_search/` | The reported run: all 420 sampled companies, query variants q1-q3 and directory detection. The only run whose VAT numbers were checked by hand. |

## Files written by the pipeline (`src/run_pipeline.py`)

| File | Contents |
|---|---|
| `pipeline_results.csv` | One row per company: `outcome`, `site_confidence`, `website`, `query_variant` (the query that produced the chosen site), VAT number(s), checksum variant, `review_flag`, `vat_shared_across_companies`, `multiple_vat_on_site`, company numbers on the site, evidence URLs and snippets, "not registered" evidence, other candidate sites, discarded sites, search queries, notes. Run 01 has no `query_variant` or VAT flag columns. |
| `to_verify.csv` | One row per company and VAT number found on any kept site, with its `site_confidence`, `review_flag`, the two VAT flags, evidence URL and snippet, and empty `hmrc_valid`, `hmrc_name`, `hmrc_address`, `match_verdict` columns for manual checking. Flagged numbers are still listed. |
| `run_log.jsonl` | Evidence trail: one JSON line per search query and per HTTP fetch (timestamp, URL, status, bytes, error, cache hit, company number, run id). |
| `directories_detected.csv` | Registrable domains seen in the search results of 2 or more sampled companies, with the count, whether the domain was already on the blocklist, and example companies. Not in run 01. |
| `config/blocklist.txt` | Copy of `config/blocklist.txt` as used by the run. |
| `RUN_NOTES.md` | Date, status (COMPLETE / INCOMPLETE / SUPERSEDED), parameters and raw outcome counts. |

## Additional files in `run_02_directory_filter_fallback_search/`

| File | Contents |
|---|---|
| `to_verify_checked.csv` | Hand-checked copy of `to_verify.csv`: the same 42 rows with `check_row` added, `hmrc_valid`, `hmrc_name`, `hmrc_address` and `match_verdict` filled in, and a `verify_notes` column. Never written by code. `match_verdict` values are described in [sample/README.md](../sample/README.md). |
| `vat_found.csv` | The 37 rows of `pipeline_results.csv` that have a VAT number, reduced to `CompanyNumber`, `CompanyName`, `vat_number`. Written by the notebook below. |
| `run_2_vat_check.ipynb` | Notebook that counts the outcomes in `pipeline_results.csv` and writes `vat_found.csv`. It uses absolute paths of the machine it was run on. |
| `verification_summary.csv`, `verification_summary.md` | Output of `src/summarise_verification.py`: companies found / valid / confirmed / related_entity / different, overall, by `site_confidence` and by `vat_bucket`, and the population-weighted found and confirmed rates. The sample file it names, `data/sample_work.csv`, is byte-identical to `sample/sample_blank.csv`. |
