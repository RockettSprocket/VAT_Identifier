# Probes

Three small, separate measurements that sit beside the pipeline. Each lives in its own folder
with its own README, imports helpers from `src/`, writes only inside its own folder
(`output/` and `run_log.jsonl`) and reports counts only.

| Folder | Network | What it counts |
|---|---|---|
| `eori_scan/` | none | EORI mentions and EORI-like numbers in the cached pages of run 02 |
| `common_crawl/` | Common Crawl index and data servers | how many run 02 sites, and confirmed VAT numbers, are present in recent Common Crawl crawls |
| `public_records/` | none | VAT-labelled numbers and sampled companies in public-record exports placed by hand |

Run each from the repository root, for example `python probes/eori_scan/eori_scan.py`.
Tests: `python -m pytest`.
