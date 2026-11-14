"""VAT identifier discovery pipeline: search -> filter -> fetch -> extract -> classify.

Usage:
    python src/run_pipeline.py --pilot-only
    python src/run_pipeline.py --limit 50 --resume

Does NOT call HMRC and does NOT decide that a VAT number is correct.
"""
import argparse
import sys
import uuid
from urllib.parse import urlsplit

from dotenv import load_dotenv

import classify
import extract
import runlog
from fetch import Fetcher, select_links
from filter import host_of, is_blocked, load_blocklist
from io_utils import (RESULTS_PATH, SAMPLE_PATH, TO_VERIFY_PATH, load_pilot, read_csv_text, write_csv)
from search import BraveProvider, SearchError, SearchProvider, cached_search

TOP_N = 5
MAX_LINKED_PAGES = 5

RESULT_COLUMNS = [
    "CompanyNumber", "CompanyName", "vat_bucket", "outcome", "site_confidence", "website",
    "vat_number", "checksum_variant", "review_flag", "company_numbers_on_site",
    "evidence_urls", "evidence_snippets", "not_registered_evidence", "other_candidate_sites",
    "discarded_sites", "search_queries", "notes",
]
MANUAL_COLUMNS = ["hmrc_valid", "hmrc_name", "hmrc_address", "match_verdict"]
VERIFY_COLUMNS = [
    "CompanyNumber", "CompanyName", "vat_number", "checksum_variant", "site_confidence",
    "review_flag", "website", "evidence_url", "snippet", "company_numbers_on_site",
    "also_seen_on", *MANUAL_COLUMNS,
]


# ---- stage 1 + 2 ---------------------------------------------------------------------------
def find_candidates(company: dict, provider: SearchProvider, blocklist: set[str]):
    """Search with the legal name, then the previous name if nothing usable. Returns (results, queries)."""
    town = company["RegAddress.PostTown"]
    names = [company["CompanyName"]]
    if company["PreviousName_1.CompanyName"]:
        names.append(company["PreviousName_1.CompanyName"])
    queries, usable = [], []
    for name in names:
        query = f'"{name}" {town}'.strip()
        queries.append(query)
        seen = set()
        for res in cached_search(provider, query, TOP_N):
            domain = host_of(res.url)
            if is_blocked(res.url, blocklist) or domain in seen:
                continue
            seen.add(domain)
            usable.append(res)
        if usable:
            break
    return usable, queries


# ---- stage 3 + 4 ---------------------------------------------------------------------------
def gather_site(company: dict, result, fetcher: Fetcher, blocklist: set[str]) -> dict:
    """Fetch home page + landing page + up to 5 relevant links; extract evidence from each."""
    our_number = extract.normalise_company_number(company["CompanyNumber"])
    names = [n for n in (company["CompanyName"], company["PreviousName_1.CompanyName"]) if n]
    parts = urlsplit(result.url)
    home = f"{parts.scheme}://{parts.netloc}/"
    site = {
        "domain": host_of(result.url), "website": home, "search_rank": result.rank,
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
        if is_blocked(page.final_url, blocklist):
            errors.append(f"{url}: redirected to blocklisted {host_of(page.final_url)}")
            continue
        if url == home:
            site["domain"] = host_of(page.final_url)
            site["website"] = page.final_url
        elif host_of(page.final_url) != site["domain"]:
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
    site["name_tokens_ok"] = any(extract.name_tokens_present(n, joined, site["domain"]) for n in names)
    site["confidence"] = classify.site_confidence(site)
    site["review_flag"] = classify.review_flag(site)
    return site


# ---- stage 5 + output rows -----------------------------------------------------------------
def _numbers_on_site(site: dict) -> str:
    ours = [f"{c['number']} (sampled)" for c in site["our_number"]]
    return "; ".join(ours + [f"{c['number']} (other)" for c in site["other_numbers"]])


def build_rows(company: dict, sites: list[dict], discarded: list[str], queries: list[str]):
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
            "vat_number": "; ".join(v["number"] for v in site["vats"]),
            "checksum_variant": "; ".join(v["variant"] for v in site["vats"]),
            "review_flag": site["review_flag"],
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
                "website": s["website"], "evidence_url": v["url"], "snippet": v["snippet"],
                "company_numbers_on_site": _numbers_on_site(s), "also_seen_on": "",
            }
    return row, list(verify.values())


def process_company(company: dict, provider: SearchProvider, fetcher: Fetcher, blocklist: set[str]):
    runlog.set_context(company_number=company["CompanyNumber"], run_id=RUN_ID)
    candidates, queries = find_candidates(company, provider, blocklist)
    sites, discarded = [], []
    for res in candidates:
        site = gather_site(company, res, fetcher, blocklist)
        if site["confidence"] is None:
            discarded.append(f"{site['domain']} (no name or number link{', ' + site['fetch_note'] if site['fetch_note'] else ''})")
        elif site["domain"] not in {s["domain"] for s in sites}:
            sites.append(site)
    return build_rows(company, sites, discarded, queries)


# ---- CLI -----------------------------------------------------------------------------------
RUN_ID = uuid.uuid4().hex[:8]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pilot-only", action="store_true", help="only the companies in data/pilot_results.csv")
    ap.add_argument("--limit", type=int, default=None, help="process at most N companies")
    ap.add_argument("--resume", action="store_true",
                    help="keep existing pipeline_results.csv rows and skip those companies")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(runlog.ROOT / ".env")

    companies = read_csv_text(SAMPLE_PATH)
    if args.pilot_only:
        pilot, _ = load_pilot()
        wanted = {r["CompanyNumber"] for r in pilot}
        companies = [c for c in companies if extract.normalise_company_number(c["CompanyNumber"]) in wanted]

    rows, verify_rows = [], []
    if args.resume and RESULTS_PATH.exists():
        rows = read_csv_text(RESULTS_PATH)
        done = {r["CompanyNumber"] for r in rows}
        if TO_VERIFY_PATH.exists():
            verify_rows = [v for v in read_csv_text(TO_VERIFY_PATH) if v["CompanyNumber"] in done]
        companies = [c for c in companies if c["CompanyNumber"] not in done]
    # Never lose hand-entered HMRC columns when to_verify.csv is rewritten.
    manual = {}
    if TO_VERIFY_PATH.exists():
        for v in read_csv_text(TO_VERIFY_PATH):
            if any(v.get(c) for c in MANUAL_COLUMNS):
                manual[(v["CompanyNumber"], v["vat_number"])] = {c: v.get(c, "") for c in MANUAL_COLUMNS}
    if args.limit is not None:
        companies = companies[: args.limit]

    try:
        provider = BraveProvider()
    except SearchError as e:
        print(f"ERROR: {e}")
        return 2
    fetcher = Fetcher()
    blocklist = load_blocklist()

    print(f"run {RUN_ID}: {len(companies)} companies to process ({len(rows)} already done)")
    status = 0
    for i, company in enumerate(companies, 1):
        try:
            row, verify = process_company(company, provider, fetcher, blocklist)
        except SearchError as e:
            print(f"ERROR: search failed, stopping. Rerun with --resume to continue. {e}")
            status = 1
            break
        for v in verify:
            v.update(manual.get((v["CompanyNumber"], v["vat_number"]), {}))
        rows.append(row)
        verify_rows.extend(verify)
        write_csv(RESULTS_PATH, rows, RESULT_COLUMNS)
        write_csv(TO_VERIFY_PATH, verify_rows, VERIFY_COLUMNS)
        print(f"[{i}/{len(companies)}] {company['CompanyNumber']} {company['CompanyName'][:45]:45} "
              f"{row['outcome']:27} {row.get('site_confidence', ''):22} {row.get('vat_number', '')}")
    write_csv(RESULTS_PATH, rows, RESULT_COLUMNS)
    write_csv(TO_VERIFY_PATH, verify_rows, VERIFY_COLUMNS)
    print(f"wrote {RESULTS_PATH} ({len(rows)} rows) and {TO_VERIFY_PATH} ({len(verify_rows)} rows)")
    return status


if __name__ == "__main__":
    sys.exit(main())
