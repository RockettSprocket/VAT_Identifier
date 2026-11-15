# VAT Identifier Discovery (proof of concept)

Tests whether UK VAT numbers can be discovered from company websites, for a stratified sample of
Companies House companies. For each company the pipeline searches for candidate websites, fetches
a few pages, extracts evidence and classifies the result.

It does **not** call HMRC and does **not** decide that a VAT number is correct. Everything it finds
goes to `data/to_verify.csv` for checking by hand.

## How to run

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt

# put your key in .env:  BRAVE_API_KEY=...
.venv\Scripts\python.exe -m pytest -q tests           # unit tests
.venv\Scripts\python.exe src\run_pipeline.py --run-name pilot_try_01 --pilot-only
.venv\Scripts\python.exe src\evaluate.py --run-name pilot_try_01   # pilot, run comparison, statistics
.venv\Scripts\python.exe src\run_pipeline.py --run-name run_02_directory_filter_fallback_search
```

Flags: `--run-name` (required; outputs go to `runs/<run-name>/`, and the run fails if that folder
already exists), `--pilot-only` (only companies in `data/pilot_results.csv`), `--limit N`,
`--max-new-searches N` (default 900: the run prints how many searches are cached and how many new
API calls it needs at most, and stops before doing anything if that is more than N).
There is no `--resume`: a stopped run leaves its folder marked INCOMPLETE in `RUN_NOTES.md`; rerun
under a new name. The shared cache in `data/cache` means no search or page is requested twice.

## How a run works

1. **Search (all companies first).** Up to three queries per company, stopping at the first that
   leaves a candidate after filtering: `q1` quoted legal name + post town; `q2` unquoted name with
   the company suffix removed + post town; `q3` quoted previous name + post town.
2. **Directory detection.** Per registrable domain (`a.example.co.uk` = `example.co.uk`), count the
   distinct sampled companies whose search results contained it. A domain seen for 2 or more
   companies is a directory and is discarded for every company; the list is written to
   `directories_detected.csv`. Because this changes which queries "leave a candidate", searching
   runs in rounds until no company needs another query. With `--pilot-only`, the cached q1 results
   of the other sampled companies are counted too (no API call).
3. **Fetch, extract, classify** as before. Evidence (including "not VAT registered" statements) is
   never taken from a blocklisted or directory domain. Linked pages now include pricing and FAQ.

`.env` settings: `BRAVE_API_KEY` (required), `BRAVE_QPS` (default 1 query/second),
`CRAWLER_CONTACT` (optional contact shown in the User-Agent).

## Files

| Path | What it is |
|---|---|
| `src/run_pipeline.py` | CLI; runs the five stages and writes the outputs |
| `src/search.py` | `SearchProvider` interface, `BraveProvider`, search cache |
| `src/filter.py`, `config/blocklist.txt` | domain blocklist (aggregators, social, directories, gov.uk, news, maps) |
| `src/fetch.py` | robots.txt, 1 request/second per domain, 10s timeout, HTML cache, link selection |
| `src/extract.py` | visible text, VAT numbers + checksum, company numbers, "not VAT registered", name matching |
| `src/classify.py` | `site_confidence` and the single `outcome` per company |
| `src/evaluate.py` | comparison with the pilot answer key |
| `data/sample_work.csv` | input sample. **Never written.** |
| `data/pilot_results.csv` | hand-checked answer key. Never written by code. |
| `runs/<run>/pipeline_results.csv` | one row per company: outcome, site_confidence, website, `query_variant` (the query that produced the chosen site), VAT number(s), checksum variant, review flag, `vat_shared_across_companies`, `multiple_vat_on_site`, evidence URLs and snippets, other candidate sites, discarded sites, search queries |
| `runs/<run>/to_verify.csv` | one row per company and VAT number found on any kept site, with its `site_confidence`, `review_flag`, the two VAT flags, evidence URL and snippet, and empty `hmrc_valid`, `hmrc_name`, `hmrc_address`, `match_verdict` to fill in by hand. Flagged numbers are still listed. |
| `runs/<run>/run_log.jsonl` | evidence trail: one line per search query and per HTTP fetch (timestamp, URL, status, bytes, error, cache hit, company number, run id) |
| `runs/<run>/directories_detected.csv` | registrable domains seen for 2 or more companies, with the count |
| `runs/<run>/config/`, `runs/<run>/RUN_NOTES.md` | copy of the config used; date, parameters and raw outcome counts |
| `data/cache/search/`, `data/cache/html/` | raw search responses and fetched pages |

## Classification rules

`site_confidence`, best first:

1. `company_number_match` - the sampled company number appears near a registration label.
2. `legal_name_match` - the exact legal or previous name appears, ignoring case and punctuation.
   "Ltd" and "Limited" are the same word but the suffix is **kept**, so `HOWELLS LEGAL LIMITED`
   does not match `HOWELLS LEGAL (HOLDINGS) LIMITED`.
3. `name_only` - every distinctive word of the name is in the site text or domain.
4. `other_company_number` - the site shows a different company number and not ours.

Sites with none of these are discarded (listed in `discarded_sites`).
`review_flag = legal_name_with_other_company_number` marks group sites: exact legal name, but the
company number shown belongs to someone else, so a VAT number there needs extra care.

`outcome`: the primary site is the best by confidence, then search rank. If any kept site has a
VAT number the outcome is `vat_found`, carrying that site's confidence (the best-confidence site
with a number). Otherwise `name_only` / `other_company_number` gives `candidate_site_unconfirmed`;
a confirmed site gives `site_states_not_registered` or `website_no_vat`. No kept site gives
`no_website_found`.

## Evaluation

`src/evaluate.py --run-name <run>` prints a transition table against an earlier run
(`--compare-to`, default run 1) with the companies whose outcome changed, the distinct domains
chosen as websites, companies resolved per query variant, and fetch failures by status code.
For the pilot it prints outcome agreement as a confusion table, VAT numbers found by hand but
missed, VAT numbers found only by the pipeline, and the disagreeing companies by name. Pilot rows
marked "out of sample" in any column, or whose company number is not in `sample_work.csv`, are
ignored and listed. Disagreements on pilot rows whose evidence is still `TO RECORD` are flagged,
because they may be a weakness of the key rather than of the pipeline. Do not re-code the key to
fit the pipeline; correct it only after re-inspecting a row, and log the change with its reason.

## Known limitations

- **Bot blocking.** The fetcher uses an honest User-Agent, so some sites answer 403 (for example
  `howellslegal.co.uk`), and their evidence is not seen.
- **No JavaScript.** Text rendered client-side, in images or in PDFs is invisible.
- **Few pages.** Home page, the search result's landing page, and up to 5 linked
  contact/about/terms/privacy/legal/company pages. A VAT number elsewhere is missed.
- **`name_only` is strict.** All distinctive words must appear, so `SYD TRADERS` does not keep
  `syedtraders.store` and `CHIP 1 EXCHANGE UK` does not keep `chip1.com`; those become
  `no_website_found`. VAT numbers on discarded sites are not reported.
- **A "not VAT registered" statement on a `name_only` site** stays `candidate_site_unconfirmed`;
  the statement is still recorded in `not_registered_evidence`. The pattern can also fire on
  general advice text ("if you are not VAT registered...").
- **Pattern noise.** Any checksum-valid 9-digit run within 60 characters after "VAT" is reported;
  about 2% of random 9-digit runs pass. Company-number matching can pick up other 6-8 digit tokens
  near a registration label.
- **A VAT number on a site is not the sampled company's number.** Related entities (sister
  company, trading subsidiary, agent) are common. That is what manual verification is for.
- **Search dependence.** Only the top 5 results of up to three queries are considered. A directory
  that shows up for only one sampled company is not detected, and two sampled companies that
  genuinely share a website lose it to the directory rule (check `directories_detected.csv`).
- robots.txt that cannot be fetched (5xx or network error) is treated as "disallow".
