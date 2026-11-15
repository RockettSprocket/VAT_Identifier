"""Shared paths and CSV helpers. Every column is read as text.

Input files are looked up in data/ first (the local, git-ignored working folder) and then in
sample/ (the tracked copies), so a fresh clone runs without any setup of data/.
"""
import re
from pathlib import Path

import polars as pl

from extract import normalise_company_number
from runlog import DATA, ROOT, RUNS

SAMPLE_DIR = ROOT / "sample"
SAMPLE_CANDIDATES = (DATA / "sample_work.csv", SAMPLE_DIR / "sample_blank.csv")
PILOT_CANDIDATES = (DATA / "pilot_results.csv", SAMPLE_DIR / "pilot_results.csv")
BUCKET_COUNTS_PATH = SAMPLE_DIR / "population_bucket_counts.csv"


def first_existing(candidates: tuple[Path, ...]) -> Path:
    """The first path that exists, or the first candidate when none does."""
    return next((p for p in candidates if p.exists()), candidates[0])


SAMPLE_PATH = first_existing(SAMPLE_CANDIDATES)   # read-only input: the sampled companies
PILOT_PATH = first_existing(PILOT_CANDIDATES)     # read-only input: hand-checked answer key
READ_ONLY_NAMES = {"to_verify_checked.csv"}       # hand-checked files inside run folders
OUT_OF_SAMPLE = re.compile(r"out[\s_\-]*of[\s_\-]*sample", re.I)
RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]*")


def run_dir(run_name: str) -> Path:
    """runs/<run-name>/ - every run writes its outputs to its own folder."""
    if not RUN_NAME.fullmatch(run_name or ""):
        raise ValueError(f"Invalid run name {run_name!r}: use letters, digits, '.', '_' and '-' only")
    return RUNS / run_name


def run_paths(run_name: str) -> dict[str, Path]:
    """Paths of the files a pipeline run writes into its run folder."""
    d = run_dir(run_name)
    return {
        "dir": d,
        "results": d / "pipeline_results.csv",
        "to_verify": d / "to_verify.csv",
        "log": d / "run_log.jsonl",
        "directories": d / "directories_detected.csv",
        "notes": d / "RUN_NOTES.md",
        "config": d / "config",
    }


def read_csv_text(path: Path) -> list[dict]:
    """Read a CSV with every column as text; missing values become ''."""
    df = pl.read_csv(path, infer_schema=False)
    return [{k: (v or "").strip() for k, v in row.items()} for row in df.to_dicts()]


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    """Write rows as an all-text CSV with exactly `columns`, replacing the file atomically.

    Refuses to write to the sample, the pilot answer key or a hand-checked file.
    """
    inputs = {p.resolve() for p in (*SAMPLE_CANDIDATES, *PILOT_CANDIDATES)}
    if path.resolve() in inputs or path.name in READ_ONLY_NAMES:
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
    or if its CompanyNumber does not appear in the sample at all.
    Each ignored row gets an 'ignored_reason'.
    """
    in_sample = {normalise_company_number(r["CompanyNumber"]) for r in read_csv_text(SAMPLE_PATH)}
    keep, ignored = [], []
    for row in read_csv_text(PILOT_PATH):
        row["CompanyNumber"] = normalise_company_number(row["CompanyNumber"])
        if any(OUT_OF_SAMPLE.search(v) for v in row.values()):
            ignored.append({**row, "ignored_reason": "marked out of sample"})
        elif row["CompanyNumber"] not in in_sample:
            ignored.append({**row, "ignored_reason": f"not in {SAMPLE_PATH.name}"})
        else:
            keep.append(row)
    return keep, ignored
