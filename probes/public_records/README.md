# Probe: VAT numbers in public-record exports

## What it measures

For each export file placed by hand in `input/`:

- `records`: number of records (CSV row, JSONL line, element of a JSON array);
- `records_with_vat_number`: records containing a checksum-valid 9-digit number that starts
  within 60 characters after a label containing "VAT". This is the pipeline's own rule
  (`find_vat_numbers` in `src/extract.py`). The record is read as `field name: value` pairs, so
  a column named e.g. `VAT number` counts as the label for its value;
- `vat_yield_per_1000`: `1000 * records_with_vat_number / records`;
- `sampled_matched_by_company_number`: how many of the 420 sampled companies have a record
  with a field equal to their company number (after the pipeline's number normalisation);
- `sampled_matched_by_legal_name`: how many of the remaining sampled companies have a record
  with a field equal to their legal name under the pipeline's name rule (`name_tokens` in
  `src/extract.py`: case and punctuation ignored, `&` = `and`, `Ltd` = `Limited`, suffix kept).

A company matched by number is never counted again under name.

## Input: placed by hand, nothing is downloaded

The probe makes no network requests.

1. Put one or more exports in `probes/public_records/input/` as `.csv`, `.json` or `.jsonl`.
2. Describe each file in `probes/public_records/input/sources.csv` (copy `sources.example.csv`):

   ```
   file,source_name,url,download_date
   contracts_2026.csv,Example register,https://example.org/export,2026-11-16
   ```

   `file` is the file name inside `input/`. A file that is not listed, or has no
   `source_name`, is skipped and reported.

`input/` is git-ignored. JSON files are loaded whole into memory; use JSONL or CSV for large exports.
A JSON file may be an array of records, or an object holding exactly one array of records.

## How to run

From the repository root:

```
python probes/public_records/records_scan.py
```

Outputs, replaced on each run:

- `output/source_counts.csv`: one row per source with the counts above;
- `output/matched_companies.csv`: one row per source and matched sampled company, with the
  match type, the number of matching records, any VAT-labelled number found in those records
  and a snippet of at most 100 characters around the first such number. No other record text is kept;
- `run_log.jsonl`: appended; one `file_read` line per input file.

## What the numbers do not cover

- A VAT-labelled number in a record is not shown to belong to the company matched in that
  record, and is not checked against HMRC.
- Numbers without a "VAT" label within 60 characters before them are not counted, whatever
  the column is called in another language or abbreviation.
- Matching compares whole field values. A company number or name inside a longer free-text
  field is not matched; previous company names are not tried.
- Name matches can be a different legal entity with the same name; they are reported
  separately from number matches for that reason.
- The counts describe the export files as supplied (their date, coverage and filters), not the
  source as a whole.
