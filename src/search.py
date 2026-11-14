"""Stage 1: search. Providers sit behind SearchProvider so they can be swapped."""
import hashlib
import json
import os
import time
from dataclasses import dataclass

import httpx

import runlog

CACHE_DIR = runlog.DATA / "cache" / "search"


@dataclass
class SearchResult:
    rank: int
    url: str
    title: str
    description: str


class SearchError(RuntimeError):
    pass


class SearchProvider:
    """Interface. A provider does the HTTP call and parses its own raw response.

    Caching and cache-hit logging are handled by cached_search(), not the provider.
    """

    name = "base"

    def search_raw(self, query: str, count: int) -> dict:
        """Call the API, log the request to run_log, return the raw JSON. Raise SearchError on failure."""
        raise NotImplementedError

    def parse(self, raw: dict) -> list[SearchResult]:
        raise NotImplementedError


class BraveProvider(SearchProvider):
    name = "brave"
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str | None = None, qps: float | None = None):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
        if not self.api_key:
            raise SearchError("BRAVE_API_KEY is not set. Add it to .env.")
        self.min_interval = 1.0 / float(qps or os.environ.get("BRAVE_QPS") or 1.0) + 0.1
        self._last = 0.0
        self.client = httpx.Client(timeout=10)

    def search_raw(self, query: str, count: int) -> dict:
        params = {"q": query, "count": count, "country": "GB", "search_lang": "en"}
        headers = {"X-Subscription-Token": self.api_key, "Accept": "application/json"}
        log_url = f"{self.ENDPOINT}?q={query}"
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.client.get(self.ENDPOINT, params=params, headers=headers)
            except httpx.HTTPError as e:
                runlog.log("search", log_url, error=f"{type(e).__name__}: {e}", provider=self.name, query=query)
                time.sleep(2 * (attempt + 1))
                continue
            runlog.log(
                "search", log_url, status=r.status_code, nbytes=len(r.content),
                error=None if r.status_code == 200 else r.text[:200],
                provider=self.name, query=query, cache=False,
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            raise SearchError(f"Brave returned HTTP {r.status_code}: {r.text[:200]}")
        raise SearchError(f"Brave search failed after retries for query: {query}")

    def parse(self, raw: dict) -> list[SearchResult]:
        items = (raw.get("web") or {}).get("results") or []
        return [
            SearchResult(i + 1, it.get("url", ""), it.get("title", ""), it.get("description", ""))
            for i, it in enumerate(items)
            if it.get("url")
        ]


def cached_search(provider: SearchProvider, query: str, count: int = 5) -> list[SearchResult]:
    """Top `count` results for `query`; raw responses are cached on disk per provider."""
    key = hashlib.sha1(f"{query}|{count}".encode("utf-8")).hexdigest()
    path = CACHE_DIR / provider.name / f"{key}.json"
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))["response"]
        runlog.log("search", f"cache:{provider.name}", status=200, nbytes=path.stat().st_size,
                   provider=provider.name, query=query, cache=True)
    else:
        raw = provider.search_raw(query, count)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"query": query, "count": count, "response": raw}, ensure_ascii=False),
                        encoding="utf-8")
    return provider.parse(raw)[:count]
