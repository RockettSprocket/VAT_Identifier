# pilot_try_01

- Date: 2026-11-14 16:47 UTC
- Status: COMPLETE. Raw pipeline output; nothing here is HMRC-verified.

## Parameters
- run_name: pilot_try_01
- run_id: 4ffcad56
- scope: pilot companies only
- companies: 30
- search: brave, top 5 results, query variants q1 (quoted legal name + post town), q2 (unquoted name without company suffix + post town), q3 (quoted previous name + post town)
- directory rule: registrable domain seen in search results for >= 2 sampled companies
- directory counting also used cached q1 results of: 390 sampled companies outside this run
- blocklist entries: 226
- pages per site: home + landing page + up to 5 linked pages
- searches cached before run: 453
- new search API calls: 25
- searches served from cache: 30
- directories detected: 71 domains seen for >= 2 companies, of which 6 were not already on the blocklist
- companies with a candidate after filtering, by query variant: q1=5, q2=23, q3=0, none=2

## Raw outcome counts (unverified)
| Outcome | Companies |
|---|---|
| no_website_found | 9 |
| candidate_site_unconfirmed | 11 |
| website_no_vat | 4 |
| site_states_not_registered | 2 |
| vat_found | 4 |
| total | 30 |

VAT numbers in to_verify.csv: 4 rows, 4 distinct numbers.

## Review notes
(to be written after review)
