"""Probe: scan the cached pages of a pipeline run for EORI numbers. No network requests.

Usage (from the repository root):
    python probes/eori_scan/eori_scan.py
    python probes/eori_scan/eori_scan.py --run run_02_directory_filter_fallback_search
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "src"))

import runlog  # noqa: E402
from extract import find_vat_numbers, vat_checksum_variant, visible_text  # noqa: E402
from filter import registrable_domain  # noqa: E402
from io_utils import read_csv_text, write_csv  # noqa: E402

DEFAULT_RUN = "run_02_directory_filter_fallback_search"
CACHE_DIR = runlog.DATA / "cache" / "html"
OUT_DIR = HERE / "output"
LOG_PATH = HERE / "run_log.jsonl"
SNIPPET_PAD = 35   # chars either side of the number -> snippets of at most 100 chars
LABEL_GAP = 40     # max non-digit characters between the EORI label and the number

EORI_WORD = re.compile(r"(?<![A-Za-z])EORI(?![A-Za-z])", re.I)
# Label, then up to LABEL_GAP characters that are not digits (so words such as "No." or
# "number is" are allowed), then GB or XI and 12 or 15 digits with optional separators.
EORI_NUMBER = re.compile(
    rf"(?<![A-Za-z])EORI(?![A-Za-z])[^0-9]{{0,{LABEL_GAP}}}?"
    r"(?<![A-Za-z0-9])(GB|XI)[ .\-]?(\d(?:[ .\-]?\d){11}(?:(?:[ .\-]?\d){3})?)(?![ .\-]?\d)",
    re.I,
)

HIT_COLUMNS = [
    "CompanyNumber", "CompanyName", "chosen_website", "page_url", "page_on_chosen_site",
    "eori_number", "prefix", "digits", "is_prefix_vat_000", "vat_number", "vat_checksum_variant",
    "vat_labelled_elsewhere_on_site", "vat_digits_elsewhere_on_site", "vat_seen_urls", "snippet",
]
COUNT_COLUMNS = ["measure", "value"]


def find_eori_numbers(text: str) -> list[dict]:
    """EORI-like numbers that follow an EORI label, one entry per distinct number.

    Each entry: number (e.g. GB840931924000), prefix, digits, start/end of the number in
    `text`, is_prefix_vat_000 (prefix + 9 digits + 000), vat_number (those 9 digits, or '')
    and vat_checksum_variant (from the pipeline's VAT checksum, '' if it fails).
    """
    found: dict[str, dict] = {}
    for m in EORI_NUMBER.finditer(text):
        prefix = m.group(1).upper()
        digits = re.sub(r"\D", "", m.group(2))
        number = prefix + digits
        if number in found:
            continue
        shaped = len(digits) == 12 and digits.endswith("000")
        vat = digits[:9] if shaped else ""
        found[number] = {
            "number": number,
            "prefix": prefix,
            "digits": digits,
            "start": m.start(1),
            "end": m.end(2),
            "is_prefix_vat_000": shaped,
            "vat_number": vat,
            "vat_checksum_variant": (vat_checksum_variant(vat) or "") if vat else "",
        }
    return list(found.values())


def short_snippet(text: str, start: int, end: int, limit: int = 100) -> str:
    """Text around text[start:end], never longer than `limit` characters."""
    return text[max(0, start - SNIPPET_PAD): end + SNIPPET_PAD].strip()[:limit]


def digits_elsewhere(text: str, vat: str, skip: tuple[int, int] | None = None) -> bool:
    """True if the 9 digits of `vat` occur in `text` (separators allowed, not inside a longer
    digit run), ignoring an occurrence that overlaps the span `skip`."""
    rx = re.compile(r"(?<!\d)" + r"[ .\-]?".join(vat) + r"(?![ .\-]?\d)")
    return any(not skip or m.end() <= skip[0] or m.start() >= skip[1] for m in rx.finditer(text))


def pages_by_company(log_path: Path) -> dict[str, list[str]]:
    """Per company number: URLs of fetch entries with status 200, robots.txt skipped, in log order."""
    pages: dict[str, list[str]] = {}
    with log_path.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("kind") != "fetch" or rec.get("status") != 200:
                continue
            url = rec.get("url") or ""
            if url.split("?", 1)[0].rstrip("/").endswith("/robots.txt"):
                continue
            urls = pages.setdefault(rec.get("company_number") or "", [])
            if url not in urls:
                urls.append(url)
    return pages


def cached_page(url: str, cache_dir: Path = CACHE_DIR) -> tuple[str, bool] | None:
    """(visible text, EORI word anywhere in the raw HTML) for the cached body of `url`,
    or None when the cache has no body for it."""
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    path = cache_dir / key[:2] / f"{key}.body"
    if not path.exists():
        runlog.log("cache_read", url, error="not_in_cache")
        return None
    body = path.read_bytes()
    runlog.log("cache_read", url, status=200, nbytes=len(body), cache=True)
    raw_mention = bool(EORI_WORD.search(body.decode("utf-8", errors="replace")))
    return visible_text(body)[1], raw_mention


def scan_company(company: dict, urls: list[str], cache_dir: Path = CACHE_DIR) -> dict:
    """Scan one company's pages.

    Returns pages_read, pages_missing, mentions (EORI word in visible text), mentions_raw
    (EORI word anywhere in the raw HTML, scripts and attributes included) and hit rows.
    """
    texts: dict[str, str] = {}
    missing = 0
    mentions_raw = False
    for url in urls:
        page = cached_page(url, cache_dir)
        if page is None:
            missing += 1
        else:
            texts[url] = page[0]
            mentions_raw = mentions_raw or page[1]
    chosen_domain = registrable_domain(company["website"])
    vat_by_url = {url: {v["number"] for v in find_vat_numbers(text)} for url, text in texts.items()}
    hits: dict[str, dict] = {}
    for url, text in texts.items():
        site = registrable_domain(url)
        same_site = [u for u in texts if registrable_domain(u) == site]
        for e in find_eori_numbers(text):
            if e["number"] in hits:
                continue
            vat = e["vat_number"]
            labelled = [u for u in same_site if vat and vat in vat_by_url[u]]
            loose = [u for u in same_site if vat and digits_elsewhere(
                texts[u], vat, (e["start"], e["end"]) if u == url else None)]
            hits[e["number"]] = {
                "CompanyNumber": company["CompanyNumber"],
                "CompanyName": company["CompanyName"],
                "chosen_website": company["website"],
                "page_url": url,
                "page_on_chosen_site": "yes" if site == chosen_domain else "no",
                "eori_number": e["number"],
                "prefix": e["prefix"],
                "digits": e["digits"],
                "is_prefix_vat_000": "yes" if e["is_prefix_vat_000"] else "no",
                "vat_number": vat,
                "vat_checksum_variant": e["vat_checksum_variant"],
                "vat_labelled_elsewhere_on_site": ("yes" if labelled else "no") if vat else "",
                "vat_digits_elsewhere_on_site": ("yes" if loose else "no") if vat else "",
                "vat_seen_urls": " | ".join(dict.fromkeys(labelled + loose)),
                "snippet": short_snippet(text, e["start"], e["end"]),
            }
    return {
        "pages_read": len(texts),
        "pages_missing": missing,
        "mentions": any(EORI_WORD.search(t) for t in texts.values()),
        "mentions_raw": mentions_raw,
        "hits": list(hits.values()),
    }


def main() -> int:
    """Scan the run's cached pages and write output/eori_hits.csv and output/eori_counts.csv."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", default=DEFAULT_RUN, help="run folder name under runs/")
    args = ap.parse_args()

    run = runlog.RUNS / args.run
    runlog.set_log_path(LOG_PATH)
    runlog.set_context(probe="eori_scan", run=args.run)
    companies = [r for r in read_csv_text(run / "pipeline_results.csv") if r["website"]]
    pages = pages_by_company(run / "run_log.jsonl")

    counts = dict.fromkeys([
        "companies_with_chosen_website", "companies_with_fetched_pages", "pages_read",
        "pages_missing_from_cache", "companies_mentioning_eori", "companies_with_eori_in_raw_html",
        "companies_showing_eori_number",
        "eori_numbers", "eori_numbers_prefix_vat_000", "eori_numbers_vat_labelled_elsewhere_on_site",
        "eori_numbers_vat_digits_elsewhere_on_site",
    ], 0)
    counts["companies_with_chosen_website"] = len(companies)
    hits: list[dict] = []
    for company in companies:
        urls = pages.get(company["CompanyNumber"], [])
        if not urls:
            continue
        res = scan_company(company, urls)
        counts["companies_with_fetched_pages"] += 1
        counts["pages_read"] += res["pages_read"]
        counts["pages_missing_from_cache"] += res["pages_missing"]
        counts["companies_mentioning_eori"] += res["mentions"]
        counts["companies_with_eori_in_raw_html"] += res["mentions_raw"]
        counts["companies_showing_eori_number"] += bool(res["hits"])
        hits.extend(res["hits"])
    counts["eori_numbers"] = len(hits)
    counts["eori_numbers_prefix_vat_000"] = sum(h["is_prefix_vat_000"] == "yes" for h in hits)
    counts["eori_numbers_vat_labelled_elsewhere_on_site"] = sum(
        h["vat_labelled_elsewhere_on_site"] == "yes" for h in hits)
    counts["eori_numbers_vat_digits_elsewhere_on_site"] = sum(
        h["vat_digits_elsewhere_on_site"] == "yes" for h in hits)

    OUT_DIR.mkdir(exist_ok=True)
    write_csv(OUT_DIR / "eori_hits.csv", hits, HIT_COLUMNS)
    write_csv(OUT_DIR / "eori_counts.csv",
              [{"measure": k, "value": v} for k, v in counts.items()], COUNT_COLUMNS)
    print(f"run: {args.run}")
    for k, v in counts.items():
        print(f"{k}: {v}")
    for h in hits:
        print(f"  {h['eori_number']}  {h['CompanyNumber']}  {h['page_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
