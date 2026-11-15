# VAT Identifier Discovery

Proof of concept that tests whether UK VAT numbers can be discovered from company websites, for a
stratified sample of 420 active Companies House companies. The pipeline finds candidate numbers
and the evidence for them; whether a number belongs to the company is checked by hand.

## Repository layout

| Path | What it is |
|---|---|
| `README.md` | This file |
| `docs/PIPELINE.md` | Technical description of the pipeline, run commands, classification rules, known limitations |
| `src/` | Pipeline code: sample draw, search, filtering, fetching, extraction, classification, evaluation, verification summary |
| `tests/` | Unit tests |
| `config/blocklist.txt` | Domains never accepted as a company website |
| `sample/` | Tracked input files: the sample, the hand-checked pilot results, population bucket counts, and a README describing columns and values |
| `runs/` | One folder per pipeline run with its outputs, log and config; `runs/README.md` says which run is which |
| `requirements.txt` | Pinned Python dependencies |
| `.env.example` | Template for the local `.env` (search API key) |
| `data/` | Local only, git-ignored: Companies House bulk file, working copies of the inputs, search and page cache |

## How to reproduce

Setup, commands for Windows PowerShell and macOS/Linux, and what can be run without the local
`data/` folder are in [docs/PIPELINE.md](docs/PIPELINE.md). In short:

1. Install the pinned dependencies and run the unit tests.
2. Draw the sample from the Companies House snapshot of 2026-11-01 with `src/draw_sample.py`
   and check it against `sample/sample_blank.csv` ([sample/README.md](sample/README.md)).
3. Run the pipeline with `src/run_pipeline.py`; outputs go to a new folder under `runs/`
   ([runs/README.md](runs/README.md)).
4. Recompute the tables for the reported run with `src/evaluate.py` and
   `src/summarise_verification.py`.

## Part 1 Research

## Part 2 Proof of concept

## Part 3 With real resources

## Debate topics

## Beyond the UK
