# Common Crawl probe: counts

- Status: INCOMPLETE - stopped early: time budget of 30 minutes reached; 78 of 98 sites not reached
- Sites selected: 98; done: 20; not reached: 78
- Generated: 2026-11-15T23:42:47+00:00
- Run: `run_02_directory_filter_fallback_search`
- Crawls queried (3, most recent first): CC-MAIN-2026-43, CC-MAIN-2026-39, CC-MAIN-2026-34
- Parameters: --crawls 3, --budget-minutes 30.0
- Time used: 30.0 minutes
- Requests: 173 sent, 1 answered from the disk cache, 17 failed after retries

All counts below are over the sites done.

| Count | Value |
|---|---|
| Sites tested (registrable domains) | 20 |
| Sites with every queried crawl answered | 7 |
| Sites in the index in any queried crawl | 17 |
| Sites in the index in every queried crawl | 8 |
| Sites with the home page captured | 12 |
| Sites where our fetch found a VAT number (any verdict) | 9 |
| ... of those, sites with the page that holds the number captured | 4 |
| Confirmed (`same`) companies: selected / done | 8 / 8 |
| Confirmed companies' sites in the index in any queried crawl | 8 of 8 sites |
| Confirmed companies' sites with the page that holds the number captured | 4 of 8 sites |
| Captured records requested for confirmed companies | 10 |
| ... records read | 10 |
| Confirmed numbers found in Common Crawl text, after a VAT label (companies) | 3 |
| ... on the same page as our own fetch (companies) | 3 |
| Confirmed numbers found in Common Crawl text, digits anywhere (companies) | 3 |

Per-site figures: `cc_sites.csv`. Per-capture checks: `cc_vat_checks.csv`.
