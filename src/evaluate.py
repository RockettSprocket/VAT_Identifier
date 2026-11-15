"""Evaluate one run: pilot comparison, comparison with an earlier run, and run statistics.

Usage: python src/evaluate.py --run-name <run> [--compare-to run_01_exact_name_search]

Part 1 compares runs/<run>/pipeline_results.csv with the hand-checked data/pilot_results.csv.
Part 2 is a transition table of outcomes against the earlier run, with the companies that changed.
Part 3 reports domains chosen as websites, query variants and fetch failures.

The pilot file is the answer key and is never modified here. Pilot rows marked
"out of sample" (in any column), or whose CompanyNumber is not in sample_work.csv,
are ignored and listed.
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict

from classify import OUTCOMES
from extract import normalise_company_number
from filter import registrable_domain
from io_utils import load_pilot, read_csv_text, run_paths

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


def pilot_evaluation(result_rows: list[dict]) -> None:
    pilot, ignored = load_pilot()
    results = {normalise_company_number(r["CompanyNumber"]): r for r in result_rows}

    print(f"Pilot companies evaluated: {len(pilot)}"
          + (f"  (ignored as out of sample: {len(ignored)} - "
             + ", ".join(f"{r['CompanyName']} [{r['ignored_reason']}]" for r in ignored) + ")"
             if ignored else ""))
    not_run = [p for p in pilot if p["CompanyNumber"] not in results]
    if not_run:
        print(f"Not in this run ({len(not_run)}): " + ", ".join(p["CompanyName"] for p in not_run))

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
        print(f"      pipeline: {r['outcome']:27} {r['website'] or '-'}  [{r['site_confidence'] or '-'}]"
              + (f"  ({r['query_variant']})" if r.get("query_variant") else ""))
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


def run_comparison(result_rows: list[dict], old_rows: list[dict], old_name: str, new_name: str) -> None:
    old = {normalise_company_number(r["CompanyNumber"]): r for r in old_rows}
    cols = OUTCOMES
    table = {a: {b: 0 for b in cols} for a in cols}
    changed, missing = [], []
    for r in result_rows:
        o = old.get(normalise_company_number(r["CompanyNumber"]))
        if not o:
            missing.append(r)
            continue
        table[o["outcome"]][r["outcome"]] += 1
        if o["outcome"] != r["outcome"]:
            changed.append((o, r))
    compared = len(result_rows) - len(missing)
    print(f"Transition table (rows = {old_name}, columns = {new_name}); {compared} companies in both runs")
    w = 12
    print(" " * 14 + "".join(f"{SHORT[c]:>{w}}" for c in cols) + f"{'total':>{w}}")
    for a in cols:
        print(f"{SHORT[a]:14}" + "".join(f"{table[a][b]:>{w}}" for b in cols) + f"{sum(table[a].values()):>{w}}")
    print(f"{'total':14}" + "".join(f"{sum(table[a][b] for a in cols):>{w}}" for b in cols) + f"{compared:>{w}}")
    if missing:
        print(f"Not in {old_name} ({len(missing)}): " + ", ".join(r["CompanyName"] for r in missing))
    print(f"\nCompanies whose outcome changed: {len(changed)} of {compared}")
    order = lambda x: (OUTCOMES.index(x[0]["outcome"]), OUTCOMES.index(x[1]["outcome"]), x[1]["CompanyName"])
    for o, r in sorted(changed, key=order):
        print(f"  {r['CompanyName']} ({r['CompanyNumber']}): {SHORT[o['outcome']]} -> {SHORT[r['outcome']]}")
        print(f"      was: {o['website'] or '-'}  [{o['site_confidence'] or '-'}] {o['vat_number']}")
        print(f"      now: {r['website'] or '-'}  [{r['site_confidence'] or '-'}] {r['vat_number']}"
              + (f"  ({r['query_variant']})" if r.get("query_variant") else ""))


def run_statistics(result_rows: list[dict], log_path) -> None:
    # ---- domains chosen as websites ----
    by_domain = defaultdict(list)
    for r in result_rows:
        if r["website"]:
            by_domain[registrable_domain(r["website"])].append(r["CompanyName"])
    with_site = sum(len(v) for v in by_domain.values())
    print(f"Companies with a chosen website: {with_site} of {len(result_rows)}; "
          f"distinct registrable domains chosen: {len(by_domain)}")
    repeated = {d: v for d, v in by_domain.items() if len(v) > 1}
    print(f"Domains chosen for more than one company: {len(repeated)}")
    for d, v in sorted(repeated.items(), key=lambda kv: -len(kv[1])):
        print(f"  {d}: " + "; ".join(v))

    # ---- query variants ----
    variants = Counter(r.get("query_variant") or "" for r in result_rows if r["website"])
    print("\nCompanies resolved (a website was chosen), by query variant:")
    for v, label in (("q1", "quoted legal name"), ("q2", "unquoted name, suffix removed"),
                     ("q3", "quoted previous name")):
        print(f"  {v} ({label}): {variants.get(v, 0)}")
    print(f"  no website chosen: {len(result_rows) - with_site}")
    by_outcome = Counter((r.get("query_variant") or "-", r["outcome"]) for r in result_rows)
    print("  outcome by variant: " + "; ".join(f"{v}/{SHORT[o]}={n}" for (v, o), n in sorted(by_outcome.items())))

    # ---- flags ----
    shared = [r for r in result_rows if r.get("vat_shared_across_companies")]
    multi = [r for r in result_rows if r.get("multiple_vat_on_site")]
    print(f"\nFlags: vat_shared_across_companies on {len(shared)} companies; "
          f"multiple_vat_on_site on {len(multi)} companies")
    for r in shared:
        print(f"  shared:   {r['CompanyName']}: {r['vat_number']}  {r['website']}")
    for r in multi:
        print(f"  multiple: {r['CompanyName']}: {r['vat_number']}  {r['website']}")

    # ---- fetch failures ----
    if not log_path.exists():
        print(f"\n{log_path} not found; no fetch statistics.")
        return
    last = {}
    with log_path.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("kind") == "fetch":
                last[rec["url"]] = rec   # one entry per distinct URL (cache replays included)
    failures = Counter()
    for rec in last.values():
        status, error = rec.get("status"), rec.get("error")
        if status is None:
            failures[(error or "unknown error").split(":")[0]] += 1
        elif status >= 400:
            failures[f"HTTP {status}"] += 1
    total_fail = sum(failures.values())
    print(f"\nDistinct URLs requested (pages and robots.txt; responses replayed from cache included): "
          f"{len(last)}; failed: {total_fail}" + (f" = {total_fail / len(last):.0%}" if last else ""))
    for key, n in failures.most_common():
        print(f"  {key}: {n}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-name", required=True, help="run folder under runs/ to evaluate")
    ap.add_argument("--compare-to", default="run_01_exact_name_search",
                    help="earlier run for the transition table")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    paths = run_paths(args.run_name)
    if not paths["results"].exists():
        print(f"{paths['results']} not found. Run: python src/run_pipeline.py --run-name {args.run_name}")
        return 2
    result_rows = read_csv_text(paths["results"])

    print(f"===== 1. Pilot evaluation: {args.run_name} =====")
    pilot_evaluation(result_rows)

    print(f"\n===== 2. Run comparison: {args.compare_to} -> {args.run_name} =====")
    old_path = run_paths(args.compare_to)["results"]
    if old_path.exists():
        run_comparison(result_rows, read_csv_text(old_path), args.compare_to, args.run_name)
    else:
        print(f"{old_path} not found; comparison skipped.")

    print(f"\n===== 3. Run statistics: {args.run_name} =====")
    run_statistics(result_rows, paths["log"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
