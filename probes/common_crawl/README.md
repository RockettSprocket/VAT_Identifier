# Probe: visibility in Common Crawl

## What it measures

For the sites of one pipeline run, it asks the Common Crawl index whether the site, its home
page and the page where our own fetch found a VAT number are captured in the most recent
crawls. For the hand-confirmed companies it also reads those captured pages and looks for the
confirmed VAT number.

**Sites tested.** Rows of `runs/<run>/pipeline_results.csv` whose `site_confidence` is
`company_number_match` or `legal_name_match` (directories and `name_only` are skipped), plus the
websites of the companies whose `match_verdict` is `same` in `runs/<run>/to_verify_checked.csv`.
Sites are counted once per registrable domain. The confirmed companies' sites are done first.

**Index requests.** The list of crawls is read from
`https://index.commoncrawl.org/collinfo.json`; the most recent `--crawls` (default 3) are
queried through the CDX index API, status 200 only. Per site and crawl:

1. one presence request: `matchType=domain`, `limit=1` (is there any capture at all under the
   registrable domain);
2. only if there is one: one `matchType=exact` request for the home page (`scheme://host/` of
   the chosen website) and one for each VAT evidence URL of run 02. A page is not asked again
   when it is the capture the presence request already returned, or the same page as one
   already asked (the evidence page is often the home page).

**Per-site figures** (`output/cc_sites.csv`):

- `crawls_with_capture`, `crawl_ids_with_capture`: crawls with at least one capture of any type;
- `home_page_captured`, `home_page_crawls`: crawls with an HTML capture of the home page;
- `vat_evidence_page_captured`, `vat_evidence_crawls`: for sites where our own fetch found a
  VAT number, crawls with an HTML capture of the page that held it (empty for other sites);
- `crawls_answered`, `query_complete`: whether every request for the site got a usable answer.

URLs are compared ignoring scheme, `www.` and a trailing slash.

**Confirmed numbers** (`output/cc_vat_checks.csv`), for the `same` companies only. For each
crawl, the newest HTML capture of the evidence page and of the home page is fetched by byte
range from `https://data.commoncrawl.org/`, the visible text is extracted with the pipeline's
`visible_text`, and two checks are recorded:

- `vat_found_labelled`: the pipeline's `find_vat_numbers` returns the confirmed number;
- `vat_digits_found`: the nine digits occur anywhere in the text.

`same_page_as_our_fetch` says whether the capture is the page where our own fetch found the number.

`output/cc_summary.md` is one page of counts with the crawl IDs used, the number of sites
done and the number not reached.

## How to run

From the repository root. `CRAWLER_CONTACT` must be set in `.env`; the probe refuses to run without it.

```
python probes/common_crawl/cc_probe.py
python probes/common_crawl/cc_probe.py --crawls 3 --budget-minutes 30
```

Behaviour towards the Common Crawl servers:

- one request per second across both hosts, User-Agent from `src/fetch.py` with the contact;
- 30 second timeout per request;
- on 429, 5xx, a `SlowDown` answer or a network error: wait 5 s, then 10 s, up to 2 retries;
- every usable answer is cached in `cache/` (git-ignored), so a rerun repeats no finished request;
- `--budget-minutes` (default 30) is a total time budget. When it is reached no further
  request is sent; the site in progress is dropped, the outputs are written for the sites
  done, and the summary is marked `INCOMPLETE` with the number of sites not reached. A rerun
  continues from the cache;
- after 5 failed requests in a row the probe stops in the same way and says why;
- every request, cached or not, is appended to `run_log.jsonl`, with the response time in `seconds`.

Exit status is 1 when the run stopped early. Outputs in `output/` are replaced on each run.

## The run in `output/`

The committed outputs come from one run on run 02 at the defaults (3 crawls, 30-minute budget).
It is incomplete and was not rerun:

- 98 sites were selected; 20 were done and **78 were not reached**. The 20 are the 8 confirmed
  companies' sites, which are done first, and the next 12 in alphabetical order of domain.
- Why: the 30-minute budget ran out. Of 173 requests sent, `index.commoncrawl.org` answered 65
  with 504 and 23 with 502, and one timed out. Those 89 answers took 853 of the 1,800 seconds,
  and the back-off waits before their retries took more.
- For the same reason only 7 of the 20 sites had every request answered (`query_complete`).
  For the other 13, a "no" for a page can mean "the index gave no answer".
- `run_log.jsonl` also holds 83 lines from an earlier run with a different design (whole-domain
  listing, 6 crawls) that was stopped by hand after 5 sites; it wrote no outputs.

## What the numbers do not cover

- Only the queried crawls. A site absent from them may be present in older crawls.
- Presence means at least one status-200 capture of any type (it can be `robots.txt` or an
  image) under the registrable domain, subdomains included. The number of captured pages is not counted.
- Only two pages are looked up per site: the home page and the VAT evidence page. Contact,
  about, terms and privacy pages are not looked up unless one of them is the evidence page.
- The home page is `scheme://host/` of the chosen website. A site that only serves its home
  page at another address (for example `/en-gb/`) shows as not captured; redirects are not followed.
- The number check covers the `same` companies only, and only those two pages. A number on
  another captured page is not seen.
- Common Crawl stores the HTML as served to its crawler, without running scripts; the capture
  date differs from the date of our own fetch, so page content can differ.
- A crawl that gave no usable answer for a site is counted as "not answered", not as "no
  capture"; see `query_complete`.
- When the run stops early, all counts are over the sites done only.
- The index is the hosted CDX API; the columnar index was not used.
- Nothing here says how large Common Crawl is or what share of the web it holds.
