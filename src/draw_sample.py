"""Draw the stratified sample of active companies from the Companies House bulk file.

Usage:
    python src/draw_sample.py [--bulk data/BasicCompanyDataAsOneFile-2026-11-01.csv]
                              [--cutoff 2025-02-14] [--out-dir data] [--check sample/sample_blank.csv]

Steps:
1. Load the bulk file ("Free Company Data Product", one CSV) and keep companies whose
   CompanyStatus is "Active".
2. Assign each company a vat_bucket from Accounts.AccountCategory (see BUCKET_CATEGORIES).
   Companies with NO ACCOUNTS FILED are split by IncorporationDate: on or after the cutoff
   they are new_no_accounts, before it overdue_no_accounts. Companies whose category is in no
   bucket (ACCOUNTS TYPE NOT AVAILABLE) get no bucket and cannot be sampled.
3. Within each bucket, shuffle with seed 42 and keep the first BUCKET_SIZES[bucket] rows; then
   shuffle the whole sample with seed 42.
4. Write <out-dir>/sample_blank.csv (company columns, vat_bucket and empty columns for manual
   checking) and <out-dir>/population_bucket_counts.csv (active companies per bucket).

The draw depends on the row order of the bulk file and on the polars version, so use the
snapshot named above and the pinned requirements. The hand-edited working copy
data/sample_work.csv is never written here.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import polars as pl

from runlog import DATA

BULK_PATH = DATA / "BasicCompanyDataAsOneFile-2026-11-01.csv"
SEED = 42
# 21 months before 2026-11-14, the day the sample was drawn.
CUTOFF = date(2025, 2, 14)

BUCKET_SIZES = {"likely": 60, "unclear": 200, "dormant": 40,
                "new_no_accounts": 40, "overdue_no_accounts": 40, "subsidiary": 40}
NO_ACCOUNTS = "NO ACCOUNTS FILED"
BUCKET_CATEGORIES = {
    "dormant": ["DORMANT"],
    "likely": ["FULL", "GROUP", "MEDIUM", "AUDITED ABRIDGED"],
    "subsidiary": ["AUDIT EXEMPTION SUBSIDIARY", "FILING EXEMPTION SUBSIDIARY"],
    "unclear": ["MICRO ENTITY", "TOTAL EXEMPTION FULL", "UNAUDITED ABRIDGED", "SMALL",
                "TOTAL EXEMPTION SMALL", "PARTIAL EXEMPTION"],
}

COMPANY_COLUMNS = [
    "CompanyName", "CompanyNumber", "RegAddress.AddressLine1", "RegAddress.AddressLine2",
    "RegAddress.PostTown", "RegAddress.PostCode", "CompanyStatus", "CompanyCategory",
    "IncorporationDate", "Accounts.AccountCategory", "SICCode.SicText_1", "SICCode.SicText_2",
    "SICCode.SicText_3", "SICCode.SicText_4", "PreviousName_1.CompanyName",
]
MANUAL_COLUMNS = [
    "outcome", "search_query", "website", "site_match_evidence", "company_number_on_site",
    "vat_number", "vat_location_on_site", "hmrc_valid", "hmrc_name", "hmrc_address",
    "match_verdict", "notes",
]


def load_active(bulk_path: Path) -> pl.DataFrame:
    """Active companies from the bulk file, in file order, with COMPANY_COLUMNS only.

    Every column is read as text (company numbers keep leading zeros); IncorporationDate is
    parsed as a date. Some header names in the bulk file start with a space; they are stripped.
    """
    lf = pl.scan_csv(bulk_path, infer_schema=False)
    lf = lf.rename({name: name.strip() for name in lf.collect_schema().names()})
    return (
        lf.filter(pl.col("CompanyStatus") == "Active")
          .select(COMPANY_COLUMNS)
          .with_columns(pl.col("IncorporationDate").str.to_date("%d/%m/%Y"))
          .collect()
    )


def assign_buckets(active: pl.DataFrame, cutoff: date) -> pl.DataFrame:
    """Add the vat_bucket column (null when the account category belongs to no bucket)."""
    category = pl.col("Accounts.AccountCategory")
    bucket = pl.when(category == NO_ACCOUNTS).then(
        pl.when(pl.col("IncorporationDate") >= cutoff)
          .then(pl.lit("new_no_accounts"))
          .otherwise(pl.lit("overdue_no_accounts"))
    )
    for name, categories in BUCKET_CATEGORIES.items():
        bucket = bucket.when(category.is_in(categories)).then(pl.lit(name))
    return active.with_columns(vat_bucket=bucket.otherwise(pl.lit(None, dtype=pl.String)))


def bucket_counts(population: pl.DataFrame) -> pl.DataFrame:
    """Active companies per sampled bucket (column N), in BUCKET_SIZES order."""
    counts = dict(population.filter(pl.col("vat_bucket").is_in(list(BUCKET_SIZES)))
                            .group_by("vat_bucket").len().iter_rows())
    return pl.DataFrame({"vat_bucket": list(BUCKET_SIZES),
                         "N": [counts.get(b, 0) for b in BUCKET_SIZES]})


def draw_sample(population: pl.DataFrame, sizes: dict[str, int] = BUCKET_SIZES, seed: int = SEED) -> pl.DataFrame:
    """Stratified sample: sizes[bucket] companies per bucket, then the rows shuffled."""
    return (
        population.filter(pl.col("vat_bucket").is_in(list(sizes)))
        .filter(pl.int_range(pl.len()).shuffle(seed=seed).over("vat_bucket")
                < pl.col("vat_bucket").replace_strict(sizes))
        .with_columns(*[pl.lit(None, dtype=pl.String).alias(c) for c in MANUAL_COLUMNS])
        .sample(fraction=1.0, shuffle=True, seed=seed)
    )


def check_against(sample: pl.DataFrame, reference_path: Path) -> bool:
    """True if `sample` has the same company numbers and buckets, in the same order, as the file."""
    reference = pl.read_csv(reference_path, infer_schema=False)
    same = (sample["CompanyNumber"].to_list() == reference["CompanyNumber"].to_list()
            and sample["vat_bucket"].to_list() == reference["vat_bucket"].to_list())
    shared = len(set(sample["CompanyNumber"]) & set(reference["CompanyNumber"]))
    print(f"Check against {reference_path}: {'IDENTICAL' if same else 'DIFFERENT'} "
          f"({sample.height} drawn, {reference.height} in file, {shared} company numbers in both)")
    return same


def main() -> int:
    """Draw the sample, write the two output files and optionally check the draw."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bulk", type=Path, default=BULK_PATH, help="Companies House bulk CSV")
    ap.add_argument("--cutoff", type=date.fromisoformat, default=CUTOFF,
                    help=f"NO ACCOUNTS FILED companies incorporated on or after this date are "
                         f"new_no_accounts (default {CUTOFF})")
    ap.add_argument("--out-dir", type=Path, default=DATA, help="folder for the two output files")
    ap.add_argument("--check", type=Path, default=None,
                    help="existing sample CSV to compare the draw with (exit code 1 if different)")
    args = ap.parse_args()

    if not args.bulk.exists():
        print(f"ERROR: {args.bulk} not found. See sample/README.md for where to download it.")
        return 2
    population = assign_buckets(load_active(args.bulk), args.cutoff)
    counts = bucket_counts(population)
    sample = draw_sample(population)
    print(f"Active companies: {population.height}; in a sampled bucket: {counts['N'].sum()}; "
          f"cutoff {args.cutoff}; seed {SEED}")
    for bucket, n in counts.iter_rows():
        print(f"  {bucket:20} N={n:>8}  sampled={BUCKET_SIZES[bucket]}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    sample.write_csv(args.out_dir / "sample_blank.csv")
    counts.write_csv(args.out_dir / "population_bucket_counts.csv")
    print(f"wrote {args.out_dir / 'sample_blank.csv'} ({sample.height} rows) and "
          f"{args.out_dir / 'population_bucket_counts.csv'}")

    if args.check is not None:
        return 0 if check_against(sample, args.check) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
