"""Probe: count VAT-labelled numbers and sampled companies in public-record exports.

Nothing is downloaded. Place exports (CSV, JSON or JSONL) in probes/public_records/input/ and
describe them in probes/public_records/input/sources.csv, then run from the repository root:
    python probes/public_records/records_scan.py
"""
import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "src"))

import runlog  # noqa: E402
from extract import VAT_LABEL, find_vat_numbers, name_tokens, normalise_company_number  # noqa: E402
from io_utils import SAMPLE_PATH, read_csv_text, write_csv  # noqa: E402

INPUT_DIR = HERE / "input"
OUT_DIR = HERE / "output"
LOG_PATH = HERE / "run_log.jsonl"
SOURCES_NAME = "sources.csv"
SOURCES_COLUMNS = ["file", "source_name", "url", "download_date"]
EXTENSIONS = {".csv", ".json", ".jsonl"}
SNIPPET_LIMIT = 100

COUNT_COLUMNS = [
    "file", "source_name", "url", "download_date", "records", "records_with_vat_number",
    "vat_yield_per_1000", "sampled_matched_by_company_number", "sampled_matched_by_legal_name",
]
MATCH_COLUMNS = [
    "file", "source_name", "CompanyNumber", "CompanyName", "match_type", "matching_records",
    "vat_number", "snippet",
]


# ---- reading records -------------------------------------------------------------------------
def leaf_values(value) -> Iterator[str]:
    """Every scalar inside a record (nested dicts and lists included), as non-empty text."""
    if isinstance(value, dict):
        for v in value.values():
            yield from leaf_values(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from leaf_values(v)
    elif value is not None and not isinstance(value, bool):
        s = str(value).strip()
        if s:
            yield s


def record_text(record: dict) -> str:
    """'key: value' for every top-level field, joined with ' | '.

    Field names are included so that a column called e.g. 'VAT number' counts as the label
    for the value in it. Nested values are flattened into their top-level field.
    """
    parts = []
    for key, value in record.items():
        joined = " ".join(leaf_values(value))
        if joined:
            parts.append(f"{key}: {joined}")
    return " | ".join(parts)


def _json_records(data) -> list:
    """The record list of a JSON document: a top-level array, or the only list of objects
    found among the values of a top-level object; otherwise the document is one record."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if len(lists) == 1:
            return lists[0]
    return [data]


def iter_records(path: Path) -> Iterator[dict]:
    """Records of a CSV (one per row), JSONL (one per line) or JSON file, as dicts."""
    ext = path.suffix.lower()
    if ext == ".csv":
        csv.field_size_limit(2**31 - 1)
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as f:
            yield from csv.DictReader(f)
    elif ext == ".jsonl":
        with path.open(encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    yield rec if isinstance(rec, dict) else {"value": rec}
    elif ext == ".json":
        with path.open(encoding="utf-8-sig", errors="replace") as f:
            for rec in _json_records(json.load(f)):
                yield rec if isinstance(rec, dict) else {"value": rec}
    else:
        raise ValueError(f"Unsupported file type: {path.name}")


# ---- scanning --------------------------------------------------------------------------------
def name_key(name: str) -> tuple[str, ...]:
    """Comparison key for a legal name: the pipeline's name_tokens (case and punctuation
    ignored, '&' == 'and', 'ltd' == 'limited', suffix kept)."""
    return tuple(name_tokens(name))


def vat_hits(text: str) -> list[dict]:
    """Checksum-valid VAT numbers after a 'VAT' label (pipeline rule), each with a snippet of
    at most 100 characters that contains the label end and the number."""
    hits = []
    for hit in find_vat_numbers(text):
        snip = hit["snippet"]
        label = VAT_LABEL.search(snip)
        start = max(0, (label.start() if label else 0) - 10)
        hits.append({"number": hit["number"], "variant": hit["variant"],
                     "snippet": snip[start: start + SNIPPET_LIMIT].strip()})
    return hits


def build_sample_index(sample_rows: list[dict]) -> dict:
    """Lookup tables for the sampled companies: by normalised company number and by name key."""
    by_number = {normalise_company_number(r["CompanyNumber"]): r for r in sample_rows}
    by_name: dict[tuple, dict] = {}
    for r in sample_rows:
        key = name_key(r["CompanyName"])
        if key:
            by_name.setdefault(key, r)
    return {"by_number": by_number, "by_name": by_name}


def match_record(record: dict, index: dict) -> tuple[set[str], set[str]]:
    """Sampled company numbers this record matches: (by company number, by legal name).

    A field matches by number when its whole value, normalised like a company number, equals
    a sampled number; by name when its whole value has the same name key as a sampled name.
    """
    by_number: set[str] = set()
    by_name: set[str] = set()
    for value in leaf_values(record):
        if len(value) <= 12:
            number = normalise_company_number(value)
            if number in index["by_number"]:
                by_number.add(number)
        hit = index["by_name"].get(name_key(value)) if len(value) <= 200 else None
        if hit:
            by_name.add(normalise_company_number(hit["CompanyNumber"]))
    return by_number, by_name


def scan_records(records, index: dict) -> dict:
    """Count records and VAT hits and collect matched sampled companies.

    Returns {records, records_with_vat_number, matches}; matches maps a sampled company
    number to {match_type, matching_records, vat_number, snippet}. A company matched by
    number in any record is reported as 'company_number' even if its name also matches.
    """
    n = with_vat = 0
    numbered: dict[str, dict] = {}
    named: dict[str, dict] = {}
    for record in records:
        n += 1
        hits = vat_hits(record_text(record))
        with_vat += bool(hits)
        by_number, by_name = match_record(record, index)
        for bucket, numbers in ((numbered, by_number), (named, by_name - by_number)):
            for number in numbers:
                m = bucket.setdefault(number, {"matching_records": 0, "vat_numbers": [], "snippet": ""})
                m["matching_records"] += 1
                for h in hits:
                    if h["number"] not in m["vat_numbers"]:
                        m["vat_numbers"].append(h["number"])
                    m["snippet"] = m["snippet"] or h["snippet"]
    matches = {}
    for match_type, bucket in (("legal_name", named), ("company_number", numbered)):
        for number, m in bucket.items():
            matches[number] = {"match_type": match_type, "matching_records": m["matching_records"],
                               "vat_number": "; ".join(m["vat_numbers"]), "snippet": m["snippet"]}
    return {"records": n, "records_with_vat_number": with_vat, "matches": matches}


def yield_per_1000(hits: int, records: int) -> str:
    """Hits per 1,000 records with two decimals; '' when there are no records."""
    return f"{1000 * hits / records:.2f}" if records else ""


# ---- command line ----------------------------------------------------------------------------
def main() -> int:
    """Scan every listed input file; write output/source_counts.csv and output/matched_companies.csv."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, default=INPUT_DIR, help="folder with exports and sources.csv")
    args = ap.parse_args()

    runlog.set_log_path(LOG_PATH)
    runlog.set_context(probe="public_records")
    files = sorted(p for p in args.input.glob("*") if p.suffix.lower() in EXTENSIONS
                   and p.name != SOURCES_NAME) if args.input.exists() else []
    sources_path = args.input / SOURCES_NAME
    sources = {r["file"]: r for r in read_csv_text(sources_path)} if sources_path.exists() else {}
    if not files:
        print(f"No input files in {args.input} (CSV, JSON or JSONL). Nothing written. See README.md.")
        return 0

    sample_rows = read_csv_text(SAMPLE_PATH)
    index = build_sample_index(sample_rows)
    count_rows, match_rows, skipped = [], [], []
    for path in files:
        source = sources.get(path.name)
        if not source or not source.get("source_name"):
            skipped.append(path.name)
            runlog.log("file_read", path.name, error="not_listed_in_sources_csv")
            continue
        res = scan_records(iter_records(path), index)
        runlog.log("file_read", path.name, status=200, nbytes=path.stat().st_size, records=res["records"])
        types = [m["match_type"] for m in res["matches"].values()]
        count_rows.append({
            **{c: source.get(c, "") for c in SOURCES_COLUMNS},
            "records": res["records"],
            "records_with_vat_number": res["records_with_vat_number"],
            "vat_yield_per_1000": yield_per_1000(res["records_with_vat_number"], res["records"]),
            "sampled_matched_by_company_number": types.count("company_number"),
            "sampled_matched_by_legal_name": types.count("legal_name"),
        })
        for number, m in sorted(res["matches"].items()):
            company = index["by_number"][number]
            match_rows.append({"file": path.name, "source_name": source["source_name"],
                               "CompanyNumber": company["CompanyNumber"],
                               "CompanyName": company["CompanyName"], **m})

    OUT_DIR.mkdir(exist_ok=True)
    write_csv(OUT_DIR / "source_counts.csv", count_rows, COUNT_COLUMNS)
    write_csv(OUT_DIR / "matched_companies.csv", match_rows, MATCH_COLUMNS)
    print(f"sampled companies: {len(sample_rows)}")
    for row in count_rows:
        print(" | ".join(f"{c}={row[c]}" for c in COUNT_COLUMNS if c not in ("url", "download_date")))
    for name in skipped:
        print(f"SKIPPED {name}: not listed in {SOURCES_NAME} (file, source_name, url, download_date)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
