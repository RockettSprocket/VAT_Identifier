# Probe: EORI scan of cached pages

## What it measures

For the companies of one pipeline run that have a non-empty chosen `website`, it counts:

- companies with at least one fetched page (log entry `kind=fetch`, status 200, not `robots.txt`);
- companies where the word `EORI` appears in the visible text of any of those pages
  (`companies_mentioning_eori`), and separately companies where it appears anywhere in the raw
  HTML, scripts and attributes included (`companies_with_eori_in_raw_html`);
- companies where an EORI-like number appears after an EORI label.

An EORI-like number is `GB` or `XI` followed by 12 or 15 digits (spaces, dots and hyphens
allowed), starting within 40 non-digit characters after the word `EORI`, so other words may sit
between label and number (`EORI No. GB840931924000`).

For each number it records:

- `is_prefix_vat_000`: the number is the prefix + 9 digits + `000`;
- `vat_checksum_variant`: result of the pipeline's VAT checksum on those 9 digits;
- `vat_labelled_elsewhere_on_site`: the pipeline's VAT extraction (`find_vat_numbers`) finds
  those 9 digits on a fetched page of the same registrable domain;
- `vat_digits_elsewhere_on_site`: the 9 digits occur anywhere in the text of a fetched page of
  the same registrable domain, with or without a VAT label, not counting the EORI number itself.

## How to run

From the repository root, with no network needed:

```
python probes/eori_scan/eori_scan.py
python probes/eori_scan/eori_scan.py --run run_02_directory_filter_fallback_search
```

Inputs (read only): `runs/<run>/pipeline_results.csv`, `runs/<run>/run_log.jsonl` and the page
cache `data/cache/html/` (key = sha1 of the URL, body in `<key[:2]>/<key>.body`).

Outputs, replaced on each run:

- `output/eori_counts.csv`: one row per count;
- `output/eori_hits.csv`: one row per company and EORI number, with a snippet of at most 100 characters;
- `run_log.jsonl`: appended; one `cache_read` line per page looked up (the probe makes no HTTP requests).

The scan takes a few minutes because every cached page is parsed again.

## What the numbers do not cover

- Only pages the pipeline fetched for that run: the landing page of each candidate site and up
  to five linked pages. An EORI number elsewhere on a site is not seen.
- Pages of all candidate sites fetched for a company are scanned, not only the chosen website;
  `page_on_chosen_site` says which is which.
- Only visible HTML text. Numbers in images, PDFs, scripts or page metadata are not seen.
- Only `GB` and `XI` prefixes, and only numbers that follow the word `EORI`.
- Companies without a chosen website are left out.
- No EORI number is checked against HMRC or any other register; "EORI-like" is a format test.
- "Same site" means same registrable domain, among the fetched pages only.
