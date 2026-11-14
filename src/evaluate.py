"""Compare data/pipeline_results.csv with the hand-checked data/pilot_results.csv.

Usage: python src/evaluate.py

The pilot file is the answer key and is never modified here. Pilot rows marked
"out of sample" (in any column), or whose CompanyNumber is not in sample_work.csv,
are ignored and listed.
"""
import re
import sys

from classify import OUTCOMES
from extract import normalise_company_number
from io_utils import RESULTS_PATH, load_pilot, read_csv_text

SHORT = {
    "no_website_found": "no_site",
    "candidate_site_unconfirmed": "unconfirmed",
    "website_no_vat": "no_vat",
    "site_states_not_registered": "not_reg",
    "vat_found": "vat_found",
    "(not run)": "(not run)",
}


def vat_set(value: str) -> set[str]:
    return {re.sub(r"\D", "", v)[-9:] for v in re.split(r"[;|,]", value or "") if re.sub(r"\D", "", v)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pilot, ignored = load_pilot()
    if not RESULTS_PATH.exists():
        print(f"{RESULTS_PATH} not found. Run: python src/run_pipeline.py --pilot-only")
        return 2
    results = {normalise_company_number(r["CompanyNumber"]): r for r in read_csv_text(RESULTS_PATH)}

    print(f"Pilot companies evaluated: {len(pilot)}"
          + (f"  (ignored as out of sample: {len(ignored)} - "
             + ", ".join(f"{r['CompanyName']} [{r['ignored_reason']}]" for r in ignored) + ")"
             if ignored else ""))
    not_run = [p for p in pilot if p["CompanyNumber"] not in results]
    if not_run:
        print(f"Not in pipeline_results.csv ({len(not_run)}): " + ", ".join(p["CompanyName"] for p in not_run))

    # ---- confusion table ----
    cols = OUTCOMES + ["(not run)"]
    table = {m: {c: 0 for c in cols} for m in OUTCOMES}
    unknown, disagreements, missed, extra = [], [], [], []
    agree = compared = 0
    for p in pilot:
        r = results.get(p["CompanyNumber"])
        manual = p["outcome"]
        auto = r["outcome"] if r else "(not run)"
        if manual not in table:
            unknown.append(p)
            continue
        table[manual][auto] += 1
        if r:
            compared += 1
            agree += manual == auto
            if manual != auto:
                disagreements.append((p, r))
            hand, found = vat_set(p["vat_number"]), vat_set(r["vat_number"])
            missed += [(p, r, n) for n in sorted(hand - found)]
            extra += [(p, r, n) for n in sorted(found - hand)]

    print(f"\nOutcome agreement: {agree}/{compared}" + (f" = {agree / compared:.0%}" if compared else ""))
    print("\nConfusion table (rows = manual pilot outcome, columns = pipeline outcome)")
    w = 12
    print(" " * 14 + "".join(f"{SHORT[c]:>{w}}" for c in cols) + f"{'total':>{w}}")
    for m in OUTCOMES:
        print(f"{SHORT[m]:14}" + "".join(f"{table[m][c]:>{w}}" for c in cols) + f"{sum(table[m].values()):>{w}}")
    print(f"{'total':14}" + "".join(f"{sum(table[m][c] for m in OUTCOMES):>{w}}" for c in cols))
    if unknown:
        print("\nPilot rows with an unrecognised outcome (not tabulated): "
              + ", ".join(f"{p['CompanyName']} [{p['outcome']}]" for p in unknown))

    # ---- VAT numbers ----
    print(f"\nVAT numbers found by hand but MISSED by the pipeline: {len(missed)}")
    for p, r, n in missed:
        print(f"  {p['CompanyName']} ({p['CompanyNumber']}): {n}  hand site: {p['website'] or '-'}"
              f"  | pipeline: {r['outcome']} {r['website'] or '-'}")
    print(f"\nVAT numbers the pipeline found that the manual check did NOT: {len(extra)}")
    for p, r, n in extra:
        print(f"  {p['CompanyName']} ({p['CompanyNumber']}): {n}  [{r['site_confidence']}] {r['website']}"
              f"  | manual: {p['outcome']}")

    # ---- disagreements ----
    print(f"\nDisagreeing companies: {len(disagreements)}")
    weak = 0
    for p, r in disagreements:
        to_record = "TO RECORD" in p["site_match_evidence"].upper()
        weak += to_record
        print(f"  {p['CompanyName']} ({p['CompanyNumber']})")
        print(f"      manual:   {p['outcome']:27} {p['website'] or '-'}")
        print(f"      pipeline: {r['outcome']:27} {r['website'] or '-'}  [{r['site_confidence'] or '-'}]")
        if r["review_flag"]:
            print(f"      pipeline flag: {r['review_flag']}")
        if p["notes"]:
            print(f"      pilot note: {p['notes']}")
        if to_record:
            print("      NOTE: pilot evidence is 'TO RECORD' - the answer key itself is unverified here")
    if weak:
        print(f"\n{weak} of {len(disagreements)} disagreements are on pilot rows whose match evidence is still "
              "'TO RECORD'. Those may reflect a weak answer key, not a pipeline error. Do not re-code the "
              "key to fit the pipeline; re-inspect the site, and log any correction with its reason.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
