"""Stage 3: polite fetching. robots.txt, 1 request/second per domain, 10s timeout, disk cache."""
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

import runlog
from filter import registrable_domain

CACHE_DIR = runlog.DATA / "cache" / "html"
UA_TOKEN = "VATDiscoveryPoC"
MAX_BYTES = 3_000_000
MAX_REDIRECTS = 5
TIMEOUT = 10.0
PER_DOMAIN_INTERVAL = 1.0

LINK_KEYWORDS = re.compile(
    r"contact|about|terms|t-and-c|tandc|t&c|conditions|privacy|legal|company|imprint|impressum|pricing|faq", re.I
)
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|zip|docx?|xlsx?|mp4|mp3|ics|css|js)(\?|$)", re.I)


def user_agent() -> str:
    """User-Agent string identifying the crawler, with the optional CRAWLER_CONTACT."""
    contact = os.environ.get("CRAWLER_CONTACT", "").strip()
    extra = f"; contact: {contact}" if contact else ""
    return f"{UA_TOKEN}/0.1 (research proof-of-concept; low-volume automated fetcher{extra})"


@dataclass
class Page:
    """Result of fetching one URL, after redirects."""

    url: str            # URL requested
    final_url: str      # URL after redirects
    status: int | None
    body: bytes
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True for HTTP 200 with a non-empty body."""
        return self.status == 200 and bool(self.body)


class Fetcher:
    """HTTP client that obeys robots.txt, rate-limits per host and caches responses on disk."""

    def __init__(self):
        self.client = httpx.Client(
            timeout=TIMEOUT, follow_redirects=False,
            headers={"User-Agent": user_agent(), "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
        )
        self._last: dict[str, float] = {}
        self._robots: dict[str, object] = {}

    # ---- single request, with cache + log -------------------------------------------------
    def _get_one(self, url: str) -> dict:
        """One HTTP GET, no redirect following. Returns {status, location, body, error}."""
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        meta_path = CACHE_DIR / key[:2] / f"{key}.json"
        body_path = CACHE_DIR / key[:2] / f"{key}.body"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            body = body_path.read_bytes() if body_path.exists() else b""
            runlog.log("fetch", url, status=meta["status"], nbytes=len(body), cache=True)
            return {**meta, "body": body, "error": None}

        host = urlsplit(url).netloc.lower()
        wait = PER_DOMAIN_INTERVAL - (time.monotonic() - self._last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        try:
            body = b""
            with self.client.stream("GET", url) as r:
                ctype = r.headers.get("content-type", "")
                textual = ("html" in ctype or "text" in ctype or not ctype)
                if textual:
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) >= MAX_BYTES:
                            break
                meta = {"url": url, "status": r.status_code, "location": r.headers.get("location"),
                        "content_type": ctype,
                        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        except (httpx.HTTPError, OSError, ValueError) as e:
            self._last[host] = time.monotonic()
            err = f"{type(e).__name__}: {e}"[:300]
            runlog.log("fetch", url, error=err, cache=False)
            return {"url": url, "status": None, "location": None, "body": b"", "error": err}
        self._last[host] = time.monotonic()
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(body)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        runlog.log("fetch", url, status=meta["status"], nbytes=len(body), cache=False,
                   error=None if textual else f"skipped non-text content-type: {ctype}")
        return {**meta, "body": body, "error": None}

    # ---- robots.txt ------------------------------------------------------------------------
    def allowed(self, url: str) -> bool:
        """Whether robots.txt of the URL's origin allows this crawler to fetch it."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        rp = self._robots[origin]
        if rp is True or rp is False:
            return rp
        return rp.can_fetch(UA_TOKEN, url)

    def _load_robots(self, origin: str):
        """Parsed robots.txt for an origin, or True (allow all) / False (disallow all)."""
        url = origin + "/robots.txt"
        res = None
        for _ in range(MAX_REDIRECTS):
            res = self._get_one(url)
            if res["status"] in (301, 302, 303, 307, 308) and res.get("location"):
                url = urljoin(url, res["location"])
                continue
            break
        status = res["status"]
        if status == 200:
            rp = robotparser.RobotFileParser()
            rp.parse(res["body"].decode("utf-8", errors="replace").splitlines())
            return rp
        if status is not None and 400 <= status < 500:
            return True   # no robots.txt: everything allowed
        return False      # 5xx or unreachable: treat as disallowed (RFC 9309)

    # ---- public ----------------------------------------------------------------------------
    def fetch(self, url: str) -> Page:
        """GET with robots.txt checked on every redirect hop."""
        url = urldefrag(url)[0]
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if urlsplit(current).scheme not in ("http", "https"):
                return Page(url, current, None, b"", "unsupported_scheme")
            if not self.allowed(current):
                runlog.log("fetch", current, error="robots_disallowed")
                return Page(url, current, None, b"", "robots_disallowed")
            res = self._get_one(current)
            if res["status"] in (301, 302, 303, 307, 308) and res.get("location"):
                current = urldefrag(urljoin(current, res["location"]))[0]
                continue
            return Page(url, current, res["status"], res["body"], res["error"])
        return Page(url, current, None, b"", "too_many_redirects")


def select_links(soup: BeautifulSoup, base_url: str, limit: int = 5) -> list[str]:
    """Links on the same registrable domain whose text or URL suggests contact/about/terms/
    privacy/legal/company/pricing/faq info."""
    base_host = registrable_domain(base_url)
    base_norm = urldefrag(base_url)[0].rstrip("/")
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urldefrag(urljoin(base_url, href))[0]
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or registrable_domain(absolute) != base_host:
            continue
        if SKIP_EXT.search(parts.path) or absolute.rstrip("/") == base_norm:
            continue
        text = a.get_text(" ", strip=True)
        if not (LINK_KEYWORDS.search(text) or LINK_KEYWORDS.search(parts.path)):
            continue
        if absolute not in out:
            out.append(absolute)
        if len(out) >= limit:
            break
    return out
