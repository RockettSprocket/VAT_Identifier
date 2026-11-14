# Run 01 - exact-name search, no directory filtering

- Date: 2026-11-14 (run log timestamps 13:45-14:44 UTC)
- Scope: all 420 sampled companies (data/sample_work.csv)
- Search: Brave Search API, query = quoted legal name + post town, previous-name retry
- Status: SUPERSEDED. Kept as evidence of what failed. Nothing here is HMRC-verified.

## Files
- pipeline_results.csv - one row per company (outcome, site, evidence)
- to_verify.csv        - VAT numbers found, awaiting manual HMRC check (not done for this run)
- run_log.jsonl        - every search query and page fetch, including failures
- config/              - blocklist and settings as they were for this run

## Raw counts (unverified)
| Outcome | Companies |
|---|---|
| no_website_found | 221 |
| website_no_vat | 127 |
| candidate_site_unconfirmed | 53 |
| vat_found | 18 (16 distinct numbers) |
| site_states_not_registered | 1 |

## Known problems found on review
1. Directory pollution: the 199 companies with a site share only 116 distinct domains.
   Company-listing sites were accepted as "the company's website" because they show the
   legal name and company number (e.g. one domain recorded for 17 unrelated companies).
   At least 65 of the 127 website_no_vat rows are on domains used for more than one company.
2. Directory VAT numbers: the same VAT number was attached to several unrelated companies
   (one number to 3 companies on a charity directory, one to 3 on another site, one to 2).
3. Search recall: the quoted exact-legal-name query missed sites found by hand in the pilot
   (Movivan, Howells, Ali's Takeaway, Chip 1). Agreement with the manual pilot: 17 of 30
   rows, almost all of them "no website".
4. Fetch failures: 167 HTTP 403 responses and 87 connection errors out of roughly 1,700
   fetches (about 15%).
5. One false "site states not registered" taken from directory text; the real statement on
   nueweb.co.uk/pricing was missed.

## Changes planned for run 02
- Discard any domain that appears for more than one sampled company; extend the blocklist.
- Add an unquoted fallback query when the exact-name search returns nothing.
