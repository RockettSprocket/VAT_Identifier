# Pipeline: technical description

For each sampled Companies House company the pipeline searches for candidate websites, fetches
a few pages, extracts evidence and classifies the result.

It does **not** call HMRC and does **not** decide that a VAT number is correct. Every VAT number
it finds goes to `runs/<run>/to_verify.csv` for checking by hand.

## Setup

Python 3.12. Dependencies are pinned in `requirements.txt`.

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env      # then put your key in .env:  BRAVE_API_KEY=...
```

macOS / Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env             # then put your key in .env:  BRAVE_API_KEY=...
```

`.env` settings: `BRAVE_API_KEY` (required by `run_pipeline.py`), `BRAVE_QPS` (default 1
query/second), `CRAWLER_CONTACT` (optional contact shown in the User-Agent). `.env` is
git-ignored.

## What can be run without the local `data/` folder

`data/` (bulk file, cache) is not in the repository. The inputs in `sample/` and the outputs in
`runs/` are, so these work on a fresh clone and make no network request:

Windows PowerShell:

```powershell
.venv\Scripts\python.exe -m pytest -q tests
.venv\Scripts\python.exe src\summarise_verification.py --run-name run_02_directory_filter_fallback_search
.venv\Scripts\python.exe src\evaluate.py --run-name run_02_directory_filter_fallback_search
```

macOS / Linux:

```bash
.venv/bin/python -m pytest -q tests
.venv/bin/python src/summarise_verification.py --run-name run_02_directory_filter_fallback_search
.venv/bin/python src/evaluate.py --run-name run_02_directory_filter_fallback_search
```

`summarise_verification.py` prints its tables and does not overwrite the summary files already
in the run folder.

## Step 1: draw the sample

Needs the Companies House bulk file in `data/`; see [sample/README.md](../sample/README.md) for
the download location, the snapshot (2026-11-01) and the bucket rules.

Windows PowerShell:

```powershell
.venv\Scripts\python.exe src\draw_sample.py --check sample\sample_blank.csv
```

macOS / Linux:

```bash
.venv/bin/python src/draw_sample.py --check sample/sample_blank.csv
```

Writes `data/sample_blank.csv` and `data/population_bucket_counts.csv`, and reports whether the
draw is identical to the tracked sample (exit code 1 if not). Options: `--bulk PATH`,
`--cutoff YYYY-MM-DD`, `--out-dir DIR`.

The pipeline reads `data/sample_work.csv` if it exists and `sample/sample_blank.csv` otherwise;
likewise `data/pilot_results.csv`, then `sample/pilot_results.csv`. No code writes to these files.

## Step 2: run the pipeline

Windows PowerShell:

```powershell
.venv\Scripts\python.exe src\run_pipeline.py --run-name my_pilot_run --pilot-only
.venv\Scripts\python.exe src\run_pipeline.py --run-name my_full_run
```

macOS / Linux:

```bash
.venv/bin/python src/run_pipeline.py --run-name my_pilot_run --pilot-only
.venv/bin/python src/run_pipeline.py --run-name my_full_run
```

Flags:

- `--run-name` (required): outputs go to `runs/<run-name>/`. The run fails if that folder exists.
- `--pilot-only`: only the companies in the pilot results file.
- `--limit N`: at most N companies.
- `--max-new-searches N` (default 900): before anything is created, the run prints how many
  searches are cached and the largest number of new API calls it could need, and stops if that
  is more than N.

There is no resume: a stopped run leaves its folder marked INCOMPLETE in `RUN_NOTES.md`; rerun
under a new name. Search responses and fetched pages are cached in `data/cache`, shared between
runs, so nothing that succeeded is requested twice.

### Re-running from the cache

Two things to know when repeating a run from an existing `data/cache`:

- The search budget is an upper bound: it counts all three query variants for every company,
  including fallback queries that were never needed and so were never cached. For the 420
  companies it reports 904 possible queries, 776 cached and at most 128 new, while a repeat of
  the full run issues 0 new API calls. `--max-new-searches 0` therefore stops the run; use 128.
- Only responses that arrived are cached. A fetch that failed with a network error (timeout,
  DNS, TLS) is requested again.

## Step 3: evaluate and summarise

```
python src/evaluate.py --run-name <run> [--compare-to <other run>]
python src/summarise_verification.py --run-name <run>
```

`evaluate.py` prints three parts:

1. Pilot comparison: outcome agreement as a confusion table, VAT numbers found by hand but
   missed, VAT numbers found only by the pipeline, and the disagreeing companies. Pilot rows
   marked "out of sample" in any column, or whose company number is not in the sample, are
   ignored and listed. Disagreements on pilot rows whose evidence is `TO RECORD` are flagged.
2. Run comparison: a transition table of outcomes against another run (`--compare-to`, default
   `run_01_exact_name_search`) and the companies whose outcome differs.
3. Run statistics: distinct domains chosen as websites, companies resolved per query variant,
   VAT flags, and fetch failures by status code.

`summarise_verification.py` reads `runs/<run>/to_verify_checked.csv` (the hand-checked copy of
`to_verify.csv`), the sample and `sample/population_bucket_counts.csv`. It gives each company
one verdict, that of its best row (`same` > `related_entity` > `different`), and reports
companies found / valid / confirmed / related_entity / different overall, by `site_confidence`
and by `vat_bucket`, plus the found and confirmed rates weighted by the population bucket
counts. It writes `verification_summary.csv` and `verification_summary.md` into the run folder
and never overwrites existing files.

## How a run works

1. **Search (all companies first).** Up to three queries per company, stopping at the first
   that leaves a candidate after filtering: `q1` quoted legal name + post town; `q2` unquoted
   name with the company suffix removed + post town; `q3` quoted previous name + post town
   (only when a previous name exists). The top 5 results of each query are used.
2. **Filtering and directory detection.** Results on a blocklisted domain
   (`config/blocklist.txt`) are dropped. Per registrable domain (`a.example.co.uk` counts as
   `example.co.uk`), the pipeline counts the distinct sampled companies whose search results
   contained it. A domain seen for 2 or more companies is a directory and is discarded for
   every company; the list is written to `directories_detected.csv`. Because this changes
   which queries "leave a candidate", searching runs in rounds until no company needs another
   query. With `--pilot-only`, the cached `q1` results of the other sampled companies are
   counted too (no API call).
3. **Fetch.** For each remaining candidate site: the home page, the search result's landing
   page, and up to 5 linked pages whose link text or URL suggests contact, about, terms,
   privacy, legal, company, pricing or FAQ. The fetcher obeys robots.txt, waits 1 second
   between requests to a host, times out after 10 seconds and identifies itself in the
   User-Agent.
4. **Extract.** From the visible text: VAT numbers (9 digits within 60 characters after a
   "VAT" label, passing either HMRC checksum), company numbers near a registration label, the
   legal or previous name, and "not VAT registered" statements. Nothing is taken from a page
   that redirects to a blocklisted or directory domain.
5. **Classify.** One `site_confidence` per site and one `outcome` per company (rules below).

## Classification rules

`site_confidence`, best first:

1. `company_number_match` - the sampled company number appears near a registration label.
2. `legal_name_match` - the exact legal or previous name appears, ignoring case and punctuation.
   "Ltd" and "Limited" are the same word but the suffix is **kept**, so `HOWELLS LEGAL LIMITED`
   does not match `HOWELLS LEGAL (HOLDINGS) LIMITED`.
3. `name_only` - every distinctive word of the name is in the site text or domain.
4. `other_company_number` - the site shows a different company number and not the sampled one.

Sites with none of these are discarded (listed in `discarded_sites`).
`review_flag = legal_name_with_other_company_number` marks group sites: exact legal name, but the
company number shown belongs to someone else, so a VAT number there needs extra care.

`outcome`: the primary site is the best by confidence, then search rank. If any kept site has a
VAT number the outcome is `vat_found`, carrying that site's confidence (the best-confidence site
with a number). Otherwise `name_only` / `other_company_number` gives `candidate_site_unconfirmed`;
a confirmed site gives `site_states_not_registered` or `website_no_vat`. No kept site gives
`no_website_found`.

## Code

| Path | What it is |
|---|---|
| `src/draw_sample.py` | Draws the stratified sample from the Companies House bulk file |
| `src/run_pipeline.py` | CLI; runs the stages and writes the run folder |
| `src/search.py` | `SearchProvider` interface, `BraveProvider`, search cache, query variants |
| `src/filter.py`, `config/blocklist.txt` | Domain blocklist (aggregators, social, directories, gov.uk, news, maps), registrable domains |
| `src/fetch.py` | robots.txt, 1 request/second per host, 10 s timeout, page cache, link selection |
| `src/extract.py` | Visible text, VAT numbers + checksum, company numbers, "not VAT registered", name matching |
| `src/classify.py` | `site_confidence` and the single `outcome` per company |
| `src/evaluate.py` | Comparison with the pilot answer key and with another run; run statistics |
| `src/summarise_verification.py` | Counts and rates from the hand-checked VAT numbers of a run |
| `src/io_utils.py`, `src/runlog.py` | Input paths, CSV helpers, run log |
| `tests/` | Unit tests (`pytest`); no network access |

Run folder contents are described in [runs/README.md](../runs/README.md); the input files in
[sample/README.md](../sample/README.md).

## Known limitations

- **Bot blocking.** The fetcher uses an honest User-Agent, so some sites answer 403 (for example
  `howellslegal.co.uk`), and their evidence is not seen.
- **No JavaScript.** Text rendered client-side, in images or in PDFs is invisible.
- **Few pages.** Home page, the search result's landing page, and up to 5 linked pages. A VAT
  number elsewhere is missed.
- **`name_only` is strict.** All distinctive words must appear, so `SYD TRADERS` does not keep
  `syedtraders.store` and `CHIP 1 EXCHANGE UK` does not keep `chip1.com`; those become
  `no_website_found`. VAT numbers on discarded sites are not reported.
- **A "not VAT registered" statement on a `name_only` site** stays `candidate_site_unconfirmed`;
  the statement is still recorded in `not_registered_evidence`. The pattern can also fire on
  general advice text ("if you are not VAT registered...").
- **Pattern noise.** Any checksum-valid 9-digit run within 60 characters after "VAT" is reported;
  about 2% of random 9-digit runs pass. Company-number matching can pick up other 6-8 digit tokens
  near a registration label.
- **A VAT number on a site is not necessarily the sampled company's number.** Related entities
  (sister company, trading subsidiary, agent) are common. That is what manual verification is for.
- **Search dependence.** Only the top 5 results of up to three queries are considered. A directory
  that shows up for only one sampled company is not detected, and two sampled companies that
  genuinely share a website lose it to the directory rule (check `directories_detected.csv`).
- robots.txt that cannot be fetched (5xx or network error) is treated as "disallow".
