"""VAT identifier discovery pipeline: search -> directory detection -> fetch -> extract -> classify.

Usage:
    python src/run_pipeline.py --run-name pilot_try_1 --pilot-only
    python src/run_pipeline.py --run-name run_02_directory_filter_fallback_search

Two phases. Phase 1 runs every search (up to three query variants per company) and counts, per
registrable domain, how many distinct sampled companies it appeared for; a domain seen for 2 or
more companies is a directory and is discarded for every company. Phase 2 fetches and classifies.

Outputs go to runs/<run-name>/ and an existing run folder is never written to. The search and
page caches in data/cache are shared between runs.

Does NOT call HMRC and does NOT decide that a VAT number is correct.
"""
import argparse
import shutil
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import urlsplit

from dotenv import load_dotenv

import classify
import extract
import runlog
from fetch import Fetcher, select_links
from filter import host_of, is_blocked, load_blocklist, registrable_domain
from io_utils import SAMPLE_PATH, load_pilot, read_csv_text, run_paths, write_csv
from search import (BraveProvider, SearchError, build_queries, cache_size, cached_only,
                    cached_search, is_cached)

TOP_N = 5
MAX_LINKED_PAGES = 5
DIRECTORY_MIN_COMPANIES = 2
MAX_NEW_SEARCHES = 900

RESULT_COLUMNS = [
    "CompanyNumber", "CompanyName", "vat_bucket", "outcome", "site_confidence", "website",
    "query_variant", "vat_number", "checksum_variant", "review_flag",
    "vat_shared_across_companies", "multiple_vat_on_site", "company_numbers_on_site",
    "evidence_urls", "evidence_snippets", "not_registered_evidence", "other_candidate_sites",
    "discarded_sites", "search_queries", "notes",
]
MANUAL_COLUMNS = ["hmrc_valid", "hmrc_name", "hmrc_address", "match_verdict"]
VERIFY_COLUMNS = [
    "CompanyNumber", "CompanyName", "vat_number", "checksum_variant", "site_confidence",
    "review_flag", "vat_shared_across_companies", "multiple_vat_on_site", "website", "evidence_url",
    "snippet", "company_numbers_on_site", "also_seen_on", *MANUAL_COLUMNS,
]
DIRECTORY_COLUMNS = ["registrable_domain", "companies_count", "already_on_blocklist", "example_companies"]


# ---- phase 1: all searches, then directory detection ---------------------------------------
def search_budget(companies: list[dict], provider_name: str) -> dict:
    """How many of the queries this run could issue are already cached."""
    queries = {q for c in companies for _, q in build_queries(c)}
    cached = sum(is_cached(provider_name, q, TOP_N) for q in queries)
    return {"cache_total": cache_size(provider_name), "possible": len(queries), "cached": cached,
            "max_new": len(queries) - cached}


def count_domains(results_by_company: dict[str, list]) -> dict[str, set[str]]:
    """registrable domain -> set of companies whose search results contained it."""
    seen: dict[str, set[str]] = defaultdict(set)
    for number, results in results_by_company.items():
        for res in results:
            domain = registrable_domain(res.url)
            if domain:
                seen[domain].add(number)
    return seen


def detect_directories(seen: dict[str, set[str]]) -> set[str]:
    return {d for d, companies in seen.items() if len(companies) >= DIRECTORY_MIN_COMPANIES}


def usable_results(results: list, blocklist: set[str], directories: set[str]) -> list:
    """Results that survive the blocklist and the directory rule, one per registrable domain."""
    out, taken = [], set()
    for res in results:
        domain = registrable_domain(res.url)
        if is_blocked(res.url, blocklist, directories) or domain in taken:
            continue
        taken.add(domain)
        out.append(res)
    return out


def search_phase(companies: list[dict], search_fn, blocklist: set[str], background: dict[str, list]):
    """Run the query variants in rounds until no company needs another query.

    Whether a query "leaves a candidate" depends on the directory set, which depends on every
    search run so far, so: round 1 runs q1 for all companies; directories are counted; companies
    left with no candidate get their next query; directories are recounted (the set only grows);
    and so on. `search_fn(company, variant, query)` returns the result list.
    `background` holds search results of sampled companies outside this run (cache only), used
    for counting directories but not otherwise.

    Returns (plans, seen, directories). plans[CompanyNumber] = {variant, candidates, queries,
    dropped_directories}; variant is '' when no query left a candidate.
    """
    todo = {c["CompanyNumber"]: build_queries(c) for c in companies}
    ran: dict[str, list[tuple[str, str, list]]] = {c["CompanyNumber"]: [] for c in companies}
    pending = list(companies)
    seen, directories = {}, set()
    while True:
        for company in pending:
            number = company["CompanyNumber"]
            variant, query = todo[number][len(ran[number])]
            ran[number].append((variant, query, search_fn(company, variant, query)))
        merged = {n: [r for _, _, results in runs for r in results] for n, runs in ran.items()}
        for number, results in background.items():
            merged.setdefault(number, results)
        seen = count_domains(merged)
        directories = detect_directories(seen)
        pending = [
            c for c in companies
            if len(ran[c["CompanyNumber"]]) < len(todo[c["CompanyNumber"]])
            and not usable_results(ran[c["CompanyNumber"]][-1][2], blocklist, directories)
        ]
        if not pending:
            break

    plans = {}
    for number, runs in ran.items():
        variant, _, results = runs[-1]
        candidates = usable_results(results, blocklist, directories)
        dropped = []
        for _, _, res_list in runs:
            for res in res_list:
                domain = registrable_domain(res.url)
                if domain in directories and not is_blocked(res.url, blocklist) and domain not in dropped:
                    dropped.append(domain)
        plans[number] = {
            "variant": variant if candidates else "",
            "candidates": candidates,
            "queries": [f"{v}: {q}" for v, q, _ in runs],
            "dropped_directories": [f"{d} (directory: seen for {len(seen[d])} companies)" for d in dropped],
        }
    return plans, seen, directories


# ---- phase 2: fetch + extract ---------------------------------------------------------------
def gather_site(company: dict, result, fetcher: Fetcher, blocklist: set[str], directories=frozenset()) -> dict:
    """Fetch home page + landing page + up to 5 relevant links; extract evidence from each."""
    our_number = extract.normalise_company_number(company["CompanyNumber"])
    names = [n for n in (company["CompanyName"], company["PreviousName_1.CompanyName"]) if n]
    parts = urlsplit(result.url)
    home = f"{parts.scheme}://{parts.netloc}/"
    site = {
        "domain": registrable_domain(result.url), "host": host_of(result.url), "website": home,
        "search_rank": result.rank,
        "pages_ok": 0, "fetch_note": "", "our_number": [], "other_numbers": [], "legal_name": [],
        "name_tokens_ok": False, "vats": [], "not_registered": [],
    }

    todo, done, all_text = [home], set(), []
    if result.url.rstrip("/") != home.rstrip("/"):
        todo.append(result.url)
    links_added = False
    errors = []
    while todo:
        url = todo.pop(0)
        page = fetcher.fetch(url)
        if not page.ok:
            errors.append(f"{url}: {page.error or page.status}")
            continue
        if is_blocked(page.final_url, blocklist, directories):
            # A site that is a directory (or blocklisted) contributes no evidence at all,
            # including "not VAT registered" statements.
            errors.append(f"{url}: redirected to blocklisted or directory domain {host_of(page.final_url)}")
            continue
        if url == home:
            site["domain"] = registrable_domain(page.final_url)
            site["host"] = host_of(page.final_url)
            site["website"] = page.final_url
        elif registrable_domain(page.final_url) != site["domain"]:
            continue
        if page.final_url in done:
            continue
        done.add(page.final_url)
        site["pages_ok"] += 1

        title, text, soup = extract.visible_text(page.body)
        full = f"{title} . {text}"
        all_text.append(full)
        for v in extract.find_vat_numbers(full):
            if v["number"] not in {x["number"] for x in site["vats"]}:
                site["vats"].append({**v, "url": page.final_url})
        for c in extract.find_company_numbers(full):
            bucket = site["our_number"] if c["number"] == our_number else site["other_numbers"]
            if c["number"] not in {x["number"] for x in bucket}:
                bucket.append({**c, "url": page.final_url})
        for name in names:
            hit = extract.find_legal_name(full, name)
            if hit and name not in {x["name"] for x in site["legal_name"]}:
                site["legal_name"].append({**hit, "url": page.final_url})
        if not site["not_registered"]:
            site["not_registered"] = [{**n, "url": page.final_url} for n in extract.find_not_registered(full)]

        if not links_added and (url == home or not todo):
            links_added = True
            for link in select_links(soup, page.final_url, MAX_LINKED_PAGES):
                if link not in todo and link not in done:
                    todo.append(link)

    if site["pages_ok"] == 0:
        # Nothing readable: judge relevance from the search result's own title and description.
        site["fetch_note"] = "fetch_failed (" + "; ".join(errors)[:300] + ")"
        all_text = [f"{result.title} {result.description}"]
    joined = " ".join(all_text)
    # The name_only test keeps using the full host, exactly as in run 1.
    site["name_tokens_ok"] = any(extract.name_tokens_present(n, joined, site["host"]) for n in names)
    site["confidence"] = classify.site_confidence(site)
    site["review_flag"] = classify.review_flag(site)
    return site


# ---- classify + output rows ----------------------------------------------------------------
def _numbers_on_site(site: dict) -> str:
    ours = [f"{c['number']} (sampled)" for c in site["our_number"]]
    return "; ".join(ours + [f"{c['number']} (other)" for c in site["other_numbers"]])


def build_rows(company: dict, sites: list[dict], discarded: list[str], queries: list[str], variant: str = ""):
    """Return (pipeline_results row, to_verify rows) for one company."""
    result = classify.classify_company(sites)
    site = result["site"]
    row = {
        "CompanyNumber": company["CompanyNumber"], "CompanyName": company["CompanyName"],
        "vat_bucket": company["vat_bucket"], "outcome": result["outcome"],
        "search_queries": " | ".join(queries), "discarded_sites": "; ".join(discarded),
    }
    if site:
        evidence = (
            [("vat", v) for v in site["vats"]]
            + [("company_number", c) for c in site["our_number"]]
            + [("legal_name", n) for n in site["legal_name"]]
            + [("other_company_number", c) for c in site["other_numbers"]]
        )
        row.update({
            "site_confidence": site["confidence"], "website": site["website"],
            "query_variant": variant,
            "vat_number": "; ".join(v["number"] for v in site["vats"]),
            "checksum_variant": "; ".join(v["variant"] for v in site["vats"]),
            "review_flag": site["review_flag"],
            "multiple_vat_on_site": "yes" if len(site["vats"]) > 1 else "",
            "company_numbers_on_site": _numbers_on_site(site),
            "evidence_urls": " | ".join(f"[{k}] {e['url']}" for k, e in evidence),
            "evidence_snippets": " || ".join(f"[{k}] {e['snippet']}" for k, e in evidence),
            "not_registered_evidence": " || ".join(
                f"{n['url']} :: {n['snippet']}" for s in sites for n in s["not_registered"]),
            "other_candidate_sites": "; ".join(
                f"{s['website']} ({s['confidence']})" for s in sites if s is not site),
            "notes": "; ".join(f"{s['domain']}: {s['fetch_note']}" for s in sites if s["fetch_note"]),
        })

    # Every checksum-valid VAT number from every kept site goes to manual verification,
    # carrying the confidence of the best site it was seen on.
    verify: dict[str, dict] = {}
    ranked = sorted(sites, key=lambda s: (classify.CONFIDENCE_RANK[s["confidence"]], s["search_rank"]))
    for s in ranked:
        for v in s["vats"]:
            if v["number"] in verify:
                verify[v["number"]]["also_seen_on"] += ("; " if verify[v["number"]]["also_seen_on"] else "") \
                    + f"{s['website']} ({s['confidence']})"
                continue
            verify[v["number"]] = {
                "CompanyNumber": company["CompanyNumber"], "CompanyName": company["CompanyName"],
                "vat_number": v["number"], "checksum_variant": v["variant"],
                "site_confidence": s["confidence"], "review_flag": s["review_flag"],
                "multiple_vat_on_site": "yes" if len(s["vats"]) > 1 else "",
                "website": s["website"], "evidence_url": v["url"], "snippet": v["snippet"],
                "company_numbers_on_site": _numbers_on_site(s), "also_seen_on": "",
            }
    return row, list(verify.values())


def apply_flags(rows: list[dict], verify_rows: list[dict]) -> None:
    """vat_shared_across_companies: the same VAT number was found for more than one sampled company."""
    owners: dict[str, set[str]] = defaultdict(set)
    for v in verify_rows:
        owners[v["vat_number"]].add(v["CompanyNumber"])
    shared = {n for n, companies in owners.items() if len(companies) > 1}
    for v in verify_rows:
        v["vat_shared_across_companies"] = "yes" if v["vat_number"] in shared else ""
    for r in rows:
        numbers = [n.strip() for n in (r.get("vat_number") or "").split(";") if n.strip()]
        r["vat_shared_across_companies"] = "yes" if any(n in shared for n in numbers) else ""


def process_company(company: dict, plan: dict, fetcher: Fetcher, blocklist: set[str], directories: set[str]):
    runlog.set_context(company_number=company["CompanyNumber"], run_id=RUN_ID)
    sites, discarded = [], list(plan["dropped_directories"])
    for res in plan["candidates"]:
        site = gather_site(company, res, fetcher, blocklist, directories)
        if site["confidence"] is None:
            discarded.append(f"{site['domain']} (no name or number link{', ' + site['fetch_note'] if site['fetch_note'] else ''})")
        elif site["domain"] not in {s["domain"] for s in sites}:
            sites.append(site)
    return build_rows(company, sites, discarded, plan["queries"], plan["variant"])


# ---- run folder ----------------------------------------------------------------------------
def write_run_notes(path, run_name: str, status: str, params: dict, rows: list[dict], verify_rows: list[dict]) -> None:
    outcomes = Counter(r["outcome"] for r in rows)
    lines = [
        f"# {run_name}",
        "",
        f"- Date: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        f"- Status: {status}. Raw pipeline output; nothing here is HMRC-verified.",
        "",
        "## Parameters",
        *[f"- {k}: {v}" for k, v in params.items()],
        "",
        "## Raw outcome counts (unverified)",
        "| Outcome | Companies |",
        "|---|---|",
        *[f"| {o} | {outcomes.get(o, 0)} |" for o in classify.OUTCOMES],
        f"| total | {len(rows)} |",
        "",
        f"VAT numbers in to_verify.csv: {len(verify_rows)} rows, "
        f"{len({v['vat_number'] for v in verify_rows})} distinct numbers.",
        "",
        "## Review notes",
        "(to be written after review)",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


# ---- CLI -----------------------------------------------------------------------------------
RUN_ID = uuid.uuid4().hex[:8]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-name", required=True, help="outputs go to runs/<run-name>/; the folder must not exist")
    ap.add_argument("--pilot-only", action="store_true", help="only the companies in data/pilot_results.csv")
    ap.add_argument("--limit", type=int, default=None, help="process at most N companies")
    ap.add_argument("--max-new-searches", type=int, default=MAX_NEW_SEARCHES,
                    help=f"stop before searching if the run could need more new API calls (default {MAX_NEW_SEARCHES})")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(runlog.ROOT / ".env")

    try:
        paths = run_paths(args.run_name)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2
    if paths["dir"].exists():
        print(f"ERROR: {paths['dir']} already exists. A run never writes into an existing run folder; "
              "choose a new --run-name.")
        return 2

    sample = read_csv_text(SAMPLE_PATH)
    companies = sample
    if args.pilot_only:
        pilot, _ = load_pilot()
        wanted = {r["CompanyNumber"] for r in pilot}
        companies = [c for c in sample if extract.normalise_company_number(c["CompanyNumber"]) in wanted]
    if args.limit is not None:
        companies = companies[: args.limit]

    try:
        provider = BraveProvider()
    except SearchError as e:
        print(f"ERROR: {e}")
        return 2
    blocklist = load_blocklist()

    # ---- search budget: printed before anything is created or any API call is made ----
    budget = search_budget(companies, provider.name)
    print(f"Search budget: {budget['cache_total']} searches already cached in data/cache/search/{provider.name}.")
    print(f"  This run ({len(companies)} companies) can issue at most {budget['possible']} distinct queries: "
          f"{budget['cached']} already cached, at most {budget['max_new']} new API calls.")
    if budget["max_new"] > args.max_new_searches:
        print(f"STOPPED: {budget['max_new']} new API calls is more than the limit of {args.max_new_searches}. "
              "Nothing was run and no run folder was created. Ask before raising --max-new-searches.")
        return 3

    paths["dir"].mkdir(parents=True)
    shutil.copytree(runlog.ROOT / "config", paths["config"])
    runlog.set_log_path(paths["log"])

    # Sampled companies outside this run still count towards directory detection, using only
    # their cached q1 results (no API call).
    in_run = {c["CompanyNumber"] for c in companies}
    background = {}
    for c in sample:
        if c["CompanyNumber"] not in in_run:
            cached = cached_only(provider, build_queries(c)[0][1], TOP_N)
            if cached is not None:
                background[c["CompanyNumber"]] = cached

    params = {
        "run_name": args.run_name, "run_id": RUN_ID,
        "scope": ("pilot companies only" if args.pilot_only else "all sampled companies")
                 + (f", limit {args.limit}" if args.limit is not None else ""),
        "companies": len(companies),
        "search": f"{provider.name}, top {TOP_N} results, query variants q1 (quoted legal name + post town), "
                  "q2 (unquoted name without company suffix + post town), q3 (quoted previous name + post town)",
        "directory rule": f"registrable domain seen in search results for >= {DIRECTORY_MIN_COMPANIES} sampled companies",
        "directory counting also used cached q1 results of": f"{len(background)} sampled companies outside this run",
        "blocklist entries": len(blocklist),
        "pages per site": f"home + landing page + up to {MAX_LINKED_PAGES} linked pages",
        "searches cached before run": budget["cache_total"],
    }
    rows, verify_rows = [], []
    run_status = "INCOMPLETE (stopped before finishing)"
    api_calls = Counter()

    def search_fn(company, variant, query):
        runlog.set_context(company_number=company["CompanyNumber"], run_id=RUN_ID, query_variant=variant)
        api_calls["cached" if is_cached(provider.name, query, TOP_N) else "new"] += 1
        return cached_search(provider, query, TOP_N)

    try:
        print(f"run {RUN_ID} -> {paths['dir']}")
        print(f"Phase 1: searching for {len(companies)} companies ...")
        try:
            plans, seen, directories = search_phase(companies, search_fn, blocklist, background)
        except SearchError as e:
            print("ERROR: search failed, stopping. Searches done so far are cached; rerun under a new "
                  f"run name. {e}")
            params["search calls attempted (new)"] = api_calls["new"]
            return 1
        params["new search API calls"] = api_calls["new"]
        params["searches served from cache"] = api_calls["cached"]

        names = {c["CompanyNumber"]: c["CompanyName"] for c in sample}
        new_dirs = [d for d in directories if not is_blocked(f"http://{d}/", blocklist)]
        write_csv(paths["directories"], [
            {"registrable_domain": d, "companies_count": len(seen[d]),
             "already_on_blocklist": "yes" if is_blocked(f"http://{d}/", blocklist) else "no",
             "example_companies": "; ".join(names.get(n, n) for n in sorted(seen[d])[:5])}
            for d in sorted(directories, key=lambda d: (-len(seen[d]), d))
        ], DIRECTORY_COLUMNS)
        params["directories detected"] = (f"{len(directories)} domains seen for >= {DIRECTORY_MIN_COMPANIES} "
                                          f"companies, of which {len(new_dirs)} were not already on the blocklist")
        variants = Counter(p["variant"] or "none" for p in plans.values())
        params["companies with a candidate after filtering, by query variant"] = ", ".join(
            f"{k}={variants[k]}" for k in ("q1", "q2", "q3", "none"))
        print(f"  {api_calls['new']} new API calls, {api_calls['cached']} searches from cache.")
        print(f"  directories detected: {len(directories)} ({len(new_dirs)} not already on the blocklist) "
              f"-> {paths['directories']}")
        print("  companies with a candidate, by query variant: "
              + params["companies with a candidate after filtering, by query variant"])

        print("Phase 2: fetching ...")
        fetcher = Fetcher()
        for i, company in enumerate(companies, 1):
            row, verify = process_company(company, plans[company["CompanyNumber"]], fetcher, blocklist, directories)
            rows.append(row)
            verify_rows.extend(verify)
            apply_flags(rows, verify_rows)
            write_csv(paths["results"], rows, RESULT_COLUMNS)
            write_csv(paths["to_verify"], verify_rows, VERIFY_COLUMNS)
            print(f"[{i}/{len(companies)}] {company['CompanyNumber']} {company['CompanyName'][:45]:45} "
                  f"{row['outcome']:27} {row.get('site_confidence', ''):22} {row.get('vat_number', '')}")
        run_status = "COMPLETE"
        print(f"wrote {paths['results']} ({len(rows)} rows) and {paths['to_verify']} ({len(verify_rows)} rows)")
    finally:
        write_run_notes(paths["notes"], args.run_name, run_status, params, rows, verify_rows)
    return 0 if run_status == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
