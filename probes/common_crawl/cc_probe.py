"""Probe: how much of what a pipeline run found is visible in Common Crawl.

Asks the Common Crawl CDX index, for each confirmed site of a run, whether the site, its home
page and the page where our fetch found a VAT number are captured in the most recent crawls.
For the companies whose VAT number was confirmed by hand it also reads those captured pages
and looks for the number.

Usage (from the repository root; needs CRAWLER_CONTACT in .env):
    python probes/common_crawl/cc_probe.py
    python probes/common_crawl/cc_probe.py --crawls 3 --budget-minutes 30
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "src"))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

import runlog  # noqa: E402
from classify import CONFIRMED  # noqa: E402
from extract import find_vat_numbers, visible_text  # noqa: E402
from fetch import user_agent  # noqa: E402
from filter import registrable_domain  # noqa: E402
from io_utils import read_csv_text, write_csv  # noqa: E402

DEFAULT_RUN = "run_02_directory_filter_fallback_search"
COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"
DATA_HOST = "https://data.commoncrawl.org/"
CACHE_DIR = HERE / "cache"
OUT_DIR = HERE / "output"
LOG_PATH = HERE / "run_log.jsonl"

REQUEST_INTERVAL = 1.0          # seconds between any two network requests (all hosts together)
TIMEOUT = 30.0                  # seconds per request
MAX_RETRIES = 2                 # retries after a 429 / 5xx / slow-down answer or a network error
BACKOFF_START = 5.0             # seconds; doubled on each retry
MAX_CONSECUTIVE_FAILURES = 5    # the probe stops after this many failed requests in a row
CDX_FIELDS = "url,timestamp,status,mime,filename,offset,length"
EXACT_LIMIT = 5                 # captures asked for one exact URL in one crawl

SITE_COLUMNS = [
    "registrable_domain", "companies", "site_confidence", "confirmed_same", "home_url",
    "crawls_queried", "crawls_answered", "crawls_with_capture", "crawl_ids_with_capture",
    "home_page_captured", "home_page_crawls", "vat_evidence_urls", "vat_evidence_page_captured",
    "vat_evidence_crawls", "query_complete",
]
CHECK_COLUMNS = [
    "CompanyNumber", "CompanyName", "vat_number", "registrable_domain", "our_evidence_url",
    "capture_url", "crawl", "capture_timestamp", "page", "same_page_as_our_fetch",
    "record_read", "vat_found_labelled", "vat_digits_found",
]


class StopProbe(RuntimeError):
    """Raised when too many requests in a row have failed; the probe writes what it has and stops."""


class BudgetReached(RuntimeError):
    """Raised when the total time budget is used up; the probe writes what it has and stops."""


# ---- pure helpers ----------------------------------------------------------------------------
def norm_url(url: str) -> str:
    """Comparison key for a URL: host without 'www.', path without trailing slash, query kept;
    scheme, port and fragment ignored."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = parts.path.rstrip("/")
    return host + path + (f"?{parts.query}" if parts.query else "")


def home_url(website: str) -> str:
    """The home page of a website URL: scheme://host/."""
    parts = urlsplit(website.strip())
    return f"{parts.scheme or 'https'}://{parts.hostname or ''}/"


def is_html(capture: dict) -> bool:
    """True when the capture's MIME type is HTML or XHTML."""
    return "html" in (capture.get("mime") or "").lower()


def parse_cdx(status: int | None, text: str) -> list[dict] | None:
    """Captures from a CDX API response (output=json: one JSON object per line).

    Returns [] for the 404 'No Captures found' answer and None when the response is not a
    usable index answer (any other status). Lines that are not JSON objects with a url are
    skipped; offset and length are converted to int.
    """
    if status == 404 and "no captures found" in text.lower():
        return []
    if status != 200:
        return None
    captures = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or not rec.get("url"):
            continue
        for key in ("offset", "length"):
            try:
                rec[key] = int(rec.get(key))
            except (TypeError, ValueError):
                rec[key] = None
        captures.append(rec)
    return captures


def parse_collinfo(text: str, n: int) -> list[dict]:
    """The n most recent crawls from collinfo.json (the list is newest first): id and cdx-api."""
    return [{"id": c["id"], "cdx_api": c["cdx-api"]} for c in json.loads(text)[:n]]


def cdx_url(cdx_api: str, url: str, match_type: str, limit: int) -> str:
    """CDX query for status-200 captures: match_type 'domain' (a registrable domain and its
    subdomains) or 'exact' (one URL; the index ignores scheme and 'www.')."""
    return cdx_api + "?" + urlencode({
        "url": url, "matchType": match_type, "output": "json", "fl": CDX_FIELDS,
        "filter": "status:200", "limit": limit,
    })


def parse_warc_record(data: bytes) -> dict | None:
    """Split one gzipped WARC response record into its parts.

    Returns {target_uri, http_status, body} or None if the bytes are not such a record.
    """
    try:
        raw = gzip.decompress(data)
    except (OSError, EOFError, zlib.error):
        return None
    warc_head, sep, rest = raw.partition(b"\r\n\r\n")
    if not sep or not warc_head.startswith(b"WARC/"):
        return None
    http_head, _, body = rest.partition(b"\r\n\r\n")
    target = re.search(rb"^WARC-Target-URI:\s*(\S+)", warc_head, re.I | re.M)
    status = re.match(rb"HTTP/\S+\s+(\d{3})", http_head)
    return {
        "target_uri": target.group(1).decode("utf-8", "replace") if target else "",
        "http_status": int(status.group(1)) if status else None,
        "body": body,
    }


def digits_present(text: str, vat: str) -> bool:
    """True if the 9 digits occur in the text (separators allowed, not inside a longer digit run)."""
    return bool(re.search(r"(?<!\d)" + r"[ .\-]?".join(vat) + r"(?![ .\-]?\d)", text))


def html_captures_of(captures: list[dict] | None, url: str) -> list[dict]:
    """The HTML captures in `captures` that are the page `url`."""
    return [c for c in captures or [] if is_html(c) and norm_url(c["url"]) == norm_url(url)]


def summarise_site(domain: str, home: str, evidence_urls: list[str], per_crawl: dict[str, dict]) -> dict:
    """Per-site index figures.

    per_crawl maps a crawl id to {"any": captures, "pages": {url: captures}}, where captures
    is a list (possibly empty) or None when the index gave no usable answer.
    """
    def answered(entry: dict) -> bool:
        return entry["any"] is not None and all(v is not None for v in entry["pages"].values())

    def crawls_with(urls: list[str]) -> list[str]:
        return [cid for cid, e in per_crawl.items()
                if any(html_captures_of(e["pages"].get(u), u) for u in urls)]

    with_capture = [cid for cid, e in per_crawl.items() if e["any"]]
    home_crawls = crawls_with([home])
    evidence_crawls = crawls_with(evidence_urls)
    n_answered = sum(answered(e) for e in per_crawl.values())
    return {
        "registrable_domain": domain,
        "home_url": home,
        "crawls_queried": len(per_crawl),
        "crawls_answered": n_answered,
        "crawls_with_capture": len(with_capture),
        "crawl_ids_with_capture": " ".join(with_capture),
        "home_page_captured": "yes" if home_crawls else "no",
        "home_page_crawls": " ".join(home_crawls),
        "vat_evidence_urls": " | ".join(evidence_urls),
        "vat_evidence_page_captured": ("yes" if evidence_crawls else "no") if evidence_urls else "",
        "vat_evidence_crawls": " ".join(evidence_crawls),
        "query_complete": "yes" if n_answered == len(per_crawl) else "no",
    }


def pick_captures(per_crawl: dict[str, dict], urls: list[str]) -> list[dict]:
    """For each crawl and each of `urls` (evidence page first, then home page): the newest
    HTML capture that has a WARC location. One entry per crawl and page."""
    picked, seen = [], set()
    for url in urls:
        for cid, entry in per_crawl.items():
            usable = [c for c in html_captures_of(entry["pages"].get(url), url)
                      if c.get("filename") and c.get("offset") is not None and c.get("length")]
            if not usable or (cid, norm_url(url)) in seen:
                continue
            seen.add((cid, norm_url(url)))
            picked.append({**max(usable, key=lambda c: c.get("timestamp", "")), "crawl": cid})
    return picked


# ---- polite, cached HTTP ---------------------------------------------------------------------
class PoliteClient:
    """GET with a 1 request/second limit, a 30 s timeout, disk cache, back-off on slow-down
    answers, a stop after MAX_CONSECUTIVE_FAILURES failed requests in a row and an optional
    deadline (time.monotonic() value) after which no request is sent. Every request is logged."""

    def __init__(self, cache_dir: Path = CACHE_DIR, transport=None, sleep=time.sleep,
                 deadline: float | None = None):
        self.cache_dir = cache_dir
        self.sleep = sleep
        self.deadline = deadline
        self.client = httpx.Client(timeout=TIMEOUT, follow_redirects=False, transport=transport,
                                   headers={"User-Agent": user_agent()})
        self.last_request = 0.0
        self.consecutive_failures = 0
        self.network_requests = 0
        self.cache_hits = 0
        self.failed_requests = 0
        self.error_answers = 0      # attempts answered 429 / 5xx / slow-down, or a network error

    def _paths(self, url: str, byte_range: str | None) -> tuple[Path, Path]:
        key = hashlib.sha1(f"{url}|{byte_range or ''}".encode("utf-8")).hexdigest()
        return self.cache_dir / key[:2] / f"{key}.json", self.cache_dir / key[:2] / f"{key}.body"

    def _check_budget(self, extra_wait: float = 0.0) -> None:
        if self.deadline is not None and time.monotonic() + extra_wait >= self.deadline:
            raise BudgetReached("time budget used up")

    def get(self, url: str, byte_range: str | None = None, ok=(200, 206, 404)) -> tuple[int | None, bytes]:
        """Return (status, body); (None, b'') after a failed request. Answers with a status in
        `ok` are cached on disk and served from there without a request. Raises StopProbe after
        too many failures in a row and BudgetReached when the deadline has passed."""
        meta_path, body_path = self._paths(url, byte_range)
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            body = body_path.read_bytes() if body_path.exists() else b""
            self.cache_hits += 1
            runlog.log("fetch", url, status=meta["status"], nbytes=len(body), cache=True, range=byte_range)
            return meta["status"], body

        headers = {"Range": f"bytes={byte_range}"} if byte_range else {}
        delay = BACKOFF_START
        for attempt in range(1, MAX_RETRIES + 2):
            self._check_budget()
            wait = self.last_request + REQUEST_INTERVAL - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            started = self.last_request = time.monotonic()
            self.network_requests += 1
            status, body, error = None, b"", None
            try:
                r = self.client.get(url, headers=headers)
                status, body = r.status_code, r.content
            except httpx.HTTPError as e:
                error = f"{type(e).__name__}: {e}"[:200]
            slow = status in (429, 500, 502, 503, 504) or b"SlowDown" in body[:2000]
            runlog.log("fetch", url, status=status, nbytes=len(body), error=error, cache=False,
                       range=byte_range, attempt=attempt, seconds=round(time.monotonic() - started, 2))
            if status in ok and not slow:
                meta_path.parent.mkdir(parents=True, exist_ok=True)
                body_path.write_bytes(body)
                meta_path.write_text(json.dumps({
                    "url": url, "range": byte_range, "status": status,
                    "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }), encoding="utf-8")
                self.consecutive_failures = 0
                return status, body
            self.error_answers += bool(slow or error)
            if not (slow or error) or attempt > MAX_RETRIES:
                break
            self._check_budget(extra_wait=delay)
            self.sleep(delay)
            delay *= 2

        self.failed_requests += 1
        self.consecutive_failures += 1
        if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            raise StopProbe(f"{self.consecutive_failures} requests in a row failed; last: {url} "
                            f"(status {status}, error {error})")
        return None, b""


# ---- inputs ----------------------------------------------------------------------------------
def load_sites(run_dir: Path) -> tuple[dict[str, dict], list[dict]]:
    """Sites to test, keyed by registrable domain, and the confirmed ('same') companies.

    Sites: rows of pipeline_results.csv whose site_confidence is company_number_match or
    legal_name_match, plus the websites of the companies with verdict 'same' in
    to_verify_checked.csv. Each site carries its home page URL, companies, confidences and
    VAT evidence URLs (the pages where our own fetch found a VAT number).
    """
    results = read_csv_text(run_dir / "pipeline_results.csv")
    same = [r for r in read_csv_text(run_dir / "to_verify_checked.csv")
            if r["match_verdict"].lower() == "same" and r["website"]]
    same_numbers = {r["CompanyNumber"] for r in same}
    sites: dict[str, dict] = {}

    def add(website: str, number: str, confidence: str, evidence: list[str], confirmed: bool) -> None:
        domain = registrable_domain(website)
        if not domain:
            return
        s = sites.setdefault(domain, {"home": home_url(website), "companies": [], "confidence": [],
                                      "evidence": [], "confirmed": False})
        keys = {norm_url(u) for u in s["evidence"]}
        for u in evidence:
            if u and registrable_domain(u) == domain and norm_url(u) not in keys:
                s["evidence"].append(u)
                keys.add(norm_url(u))
        for bucket, value in (("companies", number), ("confidence", confidence)):
            if value and value not in s[bucket]:
                s[bucket].append(value)
        s["confirmed"] = s["confirmed"] or confirmed

    for r in same:   # first, so that their evidence URL leads the site's list
        add(r["website"], r["CompanyNumber"], r["site_confidence"], [r["evidence_url"]], True)
    for r in results:
        if r["website"] and (r["site_confidence"] in CONFIRMED or r["CompanyNumber"] in same_numbers):
            evidence = re.findall(r"\[vat\]\s*(\S+)", r["evidence_urls"])
            add(r["website"], r["CompanyNumber"], r["site_confidence"], evidence, False)
    return sites, same


# ---- the probe -------------------------------------------------------------------------------
def query_site(client: PoliteClient, crawls: list[dict], domain: str, home: str,
               evidence_urls: list[str]) -> dict[str, dict]:
    """Ask the index about one site in each crawl.

    One presence request (any status-200 capture under the domain, limit=1). If there is one,
    one exact-URL request for the home page and one per evidence URL; a page that is the
    capture already returned by the presence request, or equal to a page already asked, is
    not asked again. No capture under the domain means no page can be captured: nothing more is asked.
    """
    per_crawl: dict[str, dict] = {}
    for crawl in crawls:
        status, body = client.get(cdx_url(crawl["cdx_api"], domain, "domain", 1))
        anything = parse_cdx(status, body.decode("utf-8", "replace"))
        pages: dict[str, list[dict] | None] = {}
        for url in [home, *evidence_urls]:
            if not anything:                       # no capture at all ([]) or no answer (None)
                pages[url] = anything
                continue
            known = next((caps for u, caps in pages.items() if norm_url(u) == norm_url(url)), None)
            if known is None and html_captures_of(anything, url):
                known = anything
            if known is None:
                status, body = client.get(cdx_url(crawl["cdx_api"], url, "exact", EXACT_LIMIT))
                known = parse_cdx(status, body.decode("utf-8", "replace"))
            pages[url] = known
        per_crawl[crawl["id"]] = {"any": anything, "pages": pages}
    return per_crawl


def check_confirmed(client: PoliteClient, company: dict, domain: str, home: str,
                    per_crawl: dict[str, dict]) -> list[dict]:
    """Read the captured evidence page and home page of a confirmed company's site in each
    crawl and look for its VAT number."""
    vat = re.sub(r"\D", "", company["vat_number"])
    evidence = company["evidence_url"]
    rows = []
    for c in pick_captures(per_crawl, [evidence, home]):
        byte_range = f"{c['offset']}-{c['offset'] + c['length'] - 1}"
        status, body = client.get(DATA_HOST + c["filename"], byte_range=byte_range, ok=(206,))
        record = parse_warc_record(body) if status == 206 else None
        text = visible_text(record["body"])[1] if record else ""
        same_page = norm_url(c["url"]) == norm_url(evidence)
        rows.append({
            "CompanyNumber": company["CompanyNumber"],
            "CompanyName": company["CompanyName"],
            "vat_number": vat,
            "registrable_domain": domain,
            "our_evidence_url": evidence,
            "capture_url": c["url"],
            "crawl": c["crawl"],
            "capture_timestamp": c.get("timestamp", ""),
            "page": "evidence" if same_page else "home",
            "same_page_as_our_fetch": "yes" if same_page else "no",
            "record_read": "yes" if record else "no",
            "vat_found_labelled": ("yes" if vat in {v["number"] for v in find_vat_numbers(text)} else "no") if record else "",
            "vat_digits_found": ("yes" if digits_present(text, vat) else "no") if record else "",
        })
    return rows


def summary_markdown(run: str, args, crawl_ids: list[str], n_sites: int, site_rows: list[dict],
                     check_rows: list[dict], same: list[dict], client: PoliteClient,
                     stopped: str | None, elapsed: float) -> str:
    """One page of counts. No interpretation."""
    with_evidence = [s for s in site_rows if s["vat_evidence_urls"]]
    confirmed_sites = [s for s in site_rows if s["confirmed_same"] == "yes"]
    done_companies = {n for s in confirmed_sites for n in s["companies"].split()}
    same_done = [r for r in same if r["CompanyNumber"] in done_companies]

    def companies_where(column: str, same_page: bool = False) -> int:
        return len({r["CompanyNumber"] for r in check_rows if r[column] == "yes"
                    and (not same_page or r["same_page_as_our_fetch"] == "yes")})

    def of(rows: list[dict], column: str) -> int:
        return sum(r[column] == "yes" for r in rows)

    lines = [
        "# Common Crawl probe: counts",
        "",
        f"- Status: {'INCOMPLETE - stopped early: ' + stopped if stopped else 'complete'}",
        f"- Sites selected: {n_sites}; done: {len(site_rows)}; not reached: {n_sites - len(site_rows)}",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Run: `{run}`",
        f"- Crawls queried ({len(crawl_ids)}, most recent first): {', '.join(crawl_ids)}",
        f"- Parameters: --crawls {args.crawls}, --budget-minutes {args.budget_minutes}",
        f"- Time used: {elapsed / 60:.1f} minutes",
        f"- Requests: {client.network_requests} sent, {client.cache_hits} answered from the disk cache, "
        f"{client.failed_requests} failed after retries",
        f"- Of the requests sent, {client.error_answers} got a 429, 5xx or slow-down answer or a "
        f"network error (each is waited out and retried, which uses up the time budget)",
        "",
        "All counts below are over the sites done.",
        "",
        "| Count | Value |",
        "|---|---|",
        f"| Sites tested (registrable domains) | {len(site_rows)} |",
        f"| Sites with every queried crawl answered | {of(site_rows, 'query_complete')} |",
        f"| Sites in the index in any queried crawl | {sum(int(s['crawls_with_capture']) > 0 for s in site_rows)} |",
        f"| Sites in the index in every queried crawl | "
        f"{sum(int(s['crawls_with_capture']) == len(crawl_ids) for s in site_rows)} |",
        f"| Sites with the home page captured | {of(site_rows, 'home_page_captured')} |",
        f"| Sites where our fetch found a VAT number (any verdict) | {len(with_evidence)} |",
        f"| ... of those, sites with the page that holds the number captured | "
        f"{of(with_evidence, 'vat_evidence_page_captured')} |",
        f"| Confirmed (`same`) companies: selected / done | {len(same)} / {len(same_done)} |",
        f"| Confirmed companies' sites in the index in any queried crawl | "
        f"{sum(int(s['crawls_with_capture']) > 0 for s in confirmed_sites)} of {len(confirmed_sites)} sites |",
        f"| Confirmed companies' sites with the page that holds the number captured | "
        f"{of(confirmed_sites, 'vat_evidence_page_captured')} of {len(confirmed_sites)} sites |",
        f"| Captured records requested for confirmed companies | {len(check_rows)} |",
        f"| ... records read | {of(check_rows, 'record_read')} |",
        f"| Confirmed numbers found in Common Crawl text, after a VAT label (companies) | "
        f"{companies_where('vat_found_labelled')} |",
        f"| ... on the same page as our own fetch (companies) | {companies_where('vat_found_labelled', True)} |",
        f"| Confirmed numbers found in Common Crawl text, digits anywhere (companies) | "
        f"{companies_where('vat_digits_found')} |",
        "",
        "Per-site figures: `cc_sites.csv`. Per-capture checks: `cc_vat_checks.csv`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    """Run the probe; write output/cc_sites.csv, output/cc_vat_checks.csv and output/cc_summary.md."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", default=DEFAULT_RUN, help="run folder name under runs/")
    ap.add_argument("--crawls", type=int, default=3, help="number of most recent crawls to query")
    ap.add_argument("--budget-minutes", type=float, default=30.0,
                    help="total time budget; when reached the probe stops and reports the sites done")
    args = ap.parse_args()

    load_dotenv(runlog.ROOT / ".env")
    if not os.environ.get("CRAWLER_CONTACT", "").strip():
        print("CRAWLER_CONTACT is not set in .env; refusing to send requests without a contact.")
        return 2
    runlog.set_log_path(LOG_PATH)
    runlog.set_context(probe="common_crawl", run=args.run)
    sites, same = load_sites(runlog.RUNS / args.run)
    same_by_domain: dict[str, list[dict]] = {}
    for r in same:
        same_by_domain.setdefault(registrable_domain(r["website"]), []).append(r)
    # Confirmed companies' sites first, so that a run cut short by the budget still covers them.
    ordered = sorted(sites.items(), key=lambda kv: (not kv[1]["confirmed"], kv[0]))

    started = time.monotonic()
    client = PoliteClient(deadline=started + args.budget_minutes * 60)
    site_rows, check_rows, crawl_ids = [], [], []
    stopped = None
    try:
        status, body = client.get(COLLINFO_URL, ok=(200,))
        if status != 200:
            raise StopProbe("could not read the list of crawls (collinfo.json)")
        crawls = parse_collinfo(body.decode("utf-8"), args.crawls)
        crawl_ids = [c["id"] for c in crawls]
        print(f"crawls: {', '.join(crawl_ids)}")
        print(f"sites: {len(sites)}  confirmed companies: {len(same)}", flush=True)
        for i, (domain, site) in enumerate(ordered, 1):
            per_crawl = query_site(client, crawls, domain, site["home"], site["evidence"])
            row = summarise_site(domain, site["home"], site["evidence"], per_crawl)
            row.update({"companies": " ".join(site["companies"]),
                        "site_confidence": " ".join(site["confidence"]),
                        "confirmed_same": "yes" if site["confirmed"] else "no"})
            checks = []
            for company in same_by_domain.get(domain, []):
                checks.extend(check_confirmed(client, company, domain, site["home"], per_crawl))
            site_rows.append(row)          # a site counts as done only when all its requests are done
            check_rows.extend(checks)
            print(f"[{i}/{len(sites)}] {domain}: crawls with capture {row['crawls_with_capture']}/"
                  f"{len(crawls)}, home {row['home_page_captured']}, "
                  f"evidence page {row['vat_evidence_page_captured'] or '-'}", flush=True)
    except BudgetReached:
        stopped = (f"time budget of {args.budget_minutes:g} minutes reached; "
                   f"{len(sites) - len(site_rows)} of {len(sites)} sites not reached")
    except StopProbe as e:
        stopped = f"{e}; {len(sites) - len(site_rows)} of {len(sites)} sites not reached"
    if stopped:
        print(f"STOPPED EARLY: {stopped}")

    OUT_DIR.mkdir(exist_ok=True)
    write_csv(OUT_DIR / "cc_sites.csv", site_rows, SITE_COLUMNS)
    write_csv(OUT_DIR / "cc_vat_checks.csv", check_rows, CHECK_COLUMNS)
    summary = summary_markdown(args.run, args, crawl_ids, len(sites), site_rows, check_rows, same,
                               client, stopped, time.monotonic() - started)
    (OUT_DIR / "cc_summary.md").write_text(summary, encoding="utf-8", newline="\n")
    print(summary)
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
