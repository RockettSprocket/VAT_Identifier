# run_02_directory_filter_fallback_search

- Date: 2026-11-14 17:46 UTC
- Status: COMPLETE. Raw pipeline output; nothing here is HMRC-verified.

## Parameters
- run_name: run_02_directory_filter_fallback_search
- run_id: 395e154c
- scope: all sampled companies
- companies: 420
- search: brave, top 5 results, query variants q1 (quoted legal name + post town), q2 (unquoted name without company suffix + post town), q3 (quoted previous name + post town)
- directory rule: registrable domain seen in search results for >= 2 sampled companies
- directory counting also used cached q1 results of: 0 sampled companies outside this run
- blocklist entries: 226
- pages per site: home + landing page + up to 5 linked pages
- searches cached before run: 478
- new search API calls: 298
- searches served from cache: 450
- directories detected: 109 domains seen for >= 2 companies, of which 25 were not already on the blocklist
- companies with a candidate after filtering, by query variant: q1=100, q2=262, q3=2, none=56

## Raw outcome counts (unverified)
| Outcome | Companies |
|---|---|
| no_website_found | 145 |
| candidate_site_unconfirmed | 151 |
| website_no_vat | 85 |
| site_states_not_registered | 2 |
| vat_found | 37 |
| total | 420 |

VAT numbers in to_verify.csv: 42 rows, 40 distinct numbers.

## Review notes
(to be written after review)
