"""Summarise the hand-checked VAT numbers of one run.

Usage: python src/summarise_verification.py --run-name run_02_directory_filter_fallback_search

Reads runs/<run>/to_verify_checked.csv (one row per company and VAT number, with hmrc_valid and
match_verdict filled in by hand), the sample (for vat_bucket and the number of companies sampled
per bucket) and sample/population_bucket_counts.csv (active companies per bucket).

Each company gets one verdict: that of its best row, where same > related_entity > different
(> unverified, for rows with an empty match_verdict). Ties keep the first row in the file. The
company's site_confidence is taken from that same row.

Counts, per group:
    found           companies with at least one row in to_verify_checked.csv
    valid           companies with at least one row where hmrc_valid is "yes"
    confirmed       companies whose verdict is "same"
    related_entity  companies whose verdict is "related_entity"
    different       companies whose verdict is "different"
    unverified      companies whose rows all have an empty match_verdict

Groups: all companies, by site_confidence, by vat_bucket. For "all" and for each bucket,
found_rate = found / sampled and confirmed_rate = confirmed / sampled. The weighted rates are
sum over buckets of (N_bucket / N_total) * rate_bucket, with N from the population counts.

Prints the tables and writes verification_summary.csv and verification_summary.md into the run
folder. Existing files are never overwritten.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

from classify import CONFIDENCE_RANK
from extract import normalise_company_number
from io_utils import BUCKET_COUNTS_PATH, SAMPLE_PATH, read_csv_text, run_dir, write_csv

CHECKED_NAME = "to_verify_checked.csv"
SUMMARY_CSV_NAME = "verification_summary.csv"
SUMMARY_MD_NAME = "verification_summary.md"

UNVERIFIED = "unverified"
VERDICT_RANK = {"same": 0, "related_entity": 1, "different": 2, UNVERIFIED: 3}
COUNT_COLUMNS = ["found", "valid", "confirmed", "related_entity", "different", UNVERIFIED]
SUMMARY_COLUMNS = ["group_by", "group", "population", "sampled", *COUNT_COLUMNS,
                   "found_rate", "confirmed_rate"]


def company_verdicts(checked_rows: list[dict], sample_rows: list[dict]) -> list[dict]:
    """One record per company in the checked file, in order of first appearance.

    Record keys: CompanyNumber, vat_bucket, site_confidence, valid (bool), verdict.
    Raises ValueError for an unknown match_verdict or a company that is not in the sample.
    """
    buckets = {normalise_company_number(r["CompanyNumber"]): r["vat_bucket"] for r in sample_rows}
    records: dict[str, dict] = {}
    for row in checked_rows:
        number = normalise_company_number(row["CompanyNumber"])
        verdict = row["match_verdict"].strip().lower() or UNVERIFIED
        if verdict not in VERDICT_RANK:
            raise ValueError(f"Unknown match_verdict {row['match_verdict']!r} for company {number}")
        if number not in buckets:
            raise ValueError(f"Company {number} is in the checked file but not in the sample")
        valid = row["hmrc_valid"].strip().lower() == "yes"
        record = records.get(number)
        if record is None:
            records[number] = {"CompanyNumber": number, "vat_bucket": buckets[number],
                               "site_confidence": row["site_confidence"], "valid": valid,
                               "verdict": verdict}
            continue
        record["valid"] = record["valid"] or valid
        if VERDICT_RANK[verdict] < VERDICT_RANK[record["verdict"]]:
            record["verdict"] = verdict
            record["site_confidence"] = row["site_confidence"]
    return list(records.values())


def count_group(records: list[dict]) -> dict[str, int]:
    """The COUNT_COLUMNS for a list of company records."""
    verdicts = Counter(r["verdict"] for r in records)
    return {
        "found": len(records),
        "valid": sum(r["valid"] for r in records),
        "confirmed": verdicts["same"],
        "related_entity": verdicts["related_entity"],
        "different": verdicts["different"],
        UNVERIFIED: verdicts[UNVERIFIED],
    }


def _with_rates(row: dict) -> dict:
    """Add found_rate and confirmed_rate (counts divided by the number sampled)."""
    row["found_rate"] = row["found"] / row["sampled"]
    row["confirmed_rate"] = row["confirmed"] / row["sampled"]
    return row


def summarise(records: list[dict], sample_rows: list[dict], population: dict[str, int]) -> list[dict]:
    """Summary rows (SUMMARY_COLUMNS): all, by site_confidence, by vat_bucket, weighted.

    `population` maps vat_bucket -> number of active companies; its key order is the bucket
    order of the output. Raises ValueError if a sampled bucket has no population count.
    """
    sampled = Counter(r["vat_bucket"] for r in sample_rows)
    missing = sorted(set(sampled) - set(population))
    if missing:
        raise ValueError(f"No population count for bucket(s): {', '.join(missing)}")

    rows = [_with_rates({"group_by": "all", "group": "all", "population": sum(population.values()),
                         "sampled": len(sample_rows), **count_group(records)})]

    confidences = sorted({r["site_confidence"] for r in records},
                         key=lambda c: (CONFIDENCE_RANK.get(c, len(CONFIDENCE_RANK)), c))
    for confidence in confidences:
        group = [r for r in records if r["site_confidence"] == confidence]
        rows.append({"group_by": "site_confidence", "group": confidence, **count_group(group)})

    bucket_rows = []
    for bucket in population:
        if not sampled[bucket]:
            continue
        group = [r for r in records if r["vat_bucket"] == bucket]
        bucket_rows.append(_with_rates({"group_by": "vat_bucket", "group": bucket,
                                        "population": population[bucket], "sampled": sampled[bucket],
                                        **count_group(group)}))
    rows.extend(bucket_rows)

    total = sum(r["population"] for r in bucket_rows)
    rows.append({
        "group_by": "weighted", "group": "population-weighted", "population": total,
        "sampled": sum(r["sampled"] for r in bucket_rows),
        "found_rate": sum(r["population"] / total * r["found_rate"] for r in bucket_rows),
        "confirmed_rate": sum(r["population"] / total * r["confirmed_rate"] for r in bucket_rows),
    })
    return rows


def _cell(row: dict, column: str, percent: bool) -> str:
    """One value as text; a missing value is empty, a rate is a percentage or a fraction."""
    value = row.get(column, "")
    if isinstance(value, float):
        return f"{value:.2%}" if percent else f"{value:.6f}"
    return str(value)


def csv_rows(rows: list[dict]) -> list[dict]:
    """Summary rows as text, rates as fractions with six decimals."""
    return [{c: _cell(r, c, percent=False) for c in SUMMARY_COLUMNS} for r in rows]


def markdown_table(rows: list[dict], columns: list[str]) -> str:
    """A Markdown table of `columns`, rates as percentages."""
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(_cell(r, c, percent=True) for c in columns) + " |" for r in rows]
    return "\n".join(lines)


def markdown_report(run_name: str, rows: list[dict], sources: list[str]) -> str:
    """The Markdown summary: the same tables as the CSV, with the definitions used."""
    def section(group_by: str, columns: list[str]) -> str:
        """Table of the rows of one group_by."""
        return markdown_table([r for r in rows if r["group_by"] == group_by], columns)

    counts = ["group", *COUNT_COLUMNS]
    with_rates = ["group", "population", "sampled", *COUNT_COLUMNS, "found_rate", "confirmed_rate"]
    return "\n".join([
        f"# Verification summary: {run_name}",
        "",
        "Generated by `src/summarise_verification.py` from:",
        *[f"- `{s}`" for s in sources],
        "",
        "One verdict per company: its best row, same > related_entity > different. `confirmed` is",
        "the number of companies whose verdict is `same`. `valid` is the number of companies with",
        "at least one number that HMRC confirmed as valid. Rates are counts divided by `sampled`.",
        "The weighted rates weight each bucket's rate by its share of `population`.",
        "",
        "## All companies",
        "",
        section("all", with_rates),
        "",
        "## By site_confidence",
        "",
        section("site_confidence", counts),
        "",
        "## By vat_bucket",
        "",
        section("vat_bucket", with_rates),
        "",
        "## Population-weighted rates",
        "",
        section("weighted", ["group", "population", "sampled", "found_rate", "confirmed_rate"]),
        "",
    ])


def load_population(path: Path) -> dict[str, int]:
    """vat_bucket -> number of active companies, in file order."""
    return {r["vat_bucket"]: int(r["N"]) for r in read_csv_text(path)}


def main() -> int:
    """Print the summary of one run and write it into the run folder if not already there."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-name", required=True, help="run folder under runs/ holding to_verify_checked.csv")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    folder = run_dir(args.run_name)
    checked_path = folder / CHECKED_NAME
    if not checked_path.exists():
        print(f"ERROR: {checked_path} not found.")
        return 2
    sample_rows = read_csv_text(SAMPLE_PATH)
    records = company_verdicts(read_csv_text(checked_path), sample_rows)
    rows = summarise(records, sample_rows, load_population(BUCKET_COUNTS_PATH))

    root = folder.parent.parent
    sources = [p.relative_to(root).as_posix() for p in (checked_path, SAMPLE_PATH, BUCKET_COUNTS_PATH)]
    report = markdown_report(args.run_name, rows, sources)
    print(report)

    csv_path, md_path = folder / SUMMARY_CSV_NAME, folder / SUMMARY_MD_NAME
    existing = [p.name for p in (csv_path, md_path) if p.exists()]
    if existing:
        print(f"Not written: {', '.join(existing)} already exist(s) in {folder}. "
              "Existing files in a run folder are never overwritten.")
        return 0
    write_csv(csv_path, csv_rows(rows), SUMMARY_COLUMNS)
    md_path.write_text(report, encoding="utf-8", newline="\n")
    print(f"wrote {csv_path} and {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
