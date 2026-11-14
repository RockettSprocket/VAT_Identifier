"""Small shared CSV helpers. Every column is read as text."""
import re
from pathlib import Path

import polars as pl

from extract import normalise_company_number
from runlog import DATA

SAMPLE_PATH = DATA / "sample_work.csv"      # read-only input: never written by this project
PILOT_PATH = DATA / "pilot_results.csv"     # read-only answer key
RESULTS_PATH = DATA / "pipeline_results.csv"
TO_VERIFY_PATH = DATA / "to_verify.csv"

OUT_OF_SAMPLE = re.compile(r"out[\s_\-]*of[\s_\-]*sample", re.I)


def read_csv_text(path: Path) -> list[dict]:
    """Read a CSV with every column as text; missing values become ''."""
    df = pl.read_csv(path, infer_schema=False)
    return [{k: (v or "").strip() for k, v in row.items()} for row in df.to_dicts()]


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    if path.resolve() in (SAMPLE_PATH.resolve(), PILOT_PATH.resolve()):
        raise RuntimeError(f"Refusing to write to input file {path}")
    df = pl.DataFrame(
        [{c: str(r.get(c, "") or "") for c in columns} for r in rows],
        schema={c: pl.String for c in columns},
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_csv(tmp)
    tmp.replace(path)


def load_pilot() -> tuple[list[dict], list[dict]]:
    """Return (rows to evaluate, rows ignored because they are marked out of sample).

    A row is out of sample if any of its fields says 'out of sample' / 'out_of_sample',
    or if its CompanyNumber does not appear in sample_work.csv at all.
    Each ignored row gets an 'ignored_reason'.
    """
    in_sample = {normalise_company_number(r["CompanyNumber"]) for r in read_csv_text(SAMPLE_PATH)}
    keep, ignored = [], []
    for row in read_csv_text(PILOT_PATH):
        row["CompanyNumber"] = normalise_company_number(row["CompanyNumber"])
        if any(OUT_OF_SAMPLE.search(v) for v in row.values()):
            ignored.append({**row, "ignored_reason": "marked out of sample"})
        elif row["CompanyNumber"] not in in_sample:
            ignored.append({**row, "ignored_reason": "not in sample_work.csv"})
        else:
            keep.append(row)
    return keep, ignored
