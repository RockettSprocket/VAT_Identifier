"""Common Crawl probe: CDX and WARC parsing, per-site queries, capture choice, polite client."""
import gzip
import json
import time

import httpx
import pytest

import cc_probe as cc

HOME = "https://www.example.co.uk/"
TERMS = "https://example.co.uk/terms"


def cap(url, ts="20260901120000", mime="text/html", offset="100", length="50"):
    return {"url": url, "timestamp": ts, "status": "200", "mime": mime,
            "filename": "crawl-data/a.warc.gz", "offset": offset, "length": length}


def cdx_body(*captures):
    return "\n".join(json.dumps(c) for c in captures)


CDX_BODY = cdx_body(cap(HOME), cap("https://example.co.uk/contact-us/", ts="20260902120000", offset="700"),
                    cap("https://example.co.uk/logo.png", mime="image/png")) + "\nnot json\n\n"


# ---- parsing ----
def test_parse_cdx_json_lines():
    caps = cc.parse_cdx(200, CDX_BODY)
    assert [c["url"] for c in caps] == [HOME, "https://example.co.uk/contact-us/", "https://example.co.uk/logo.png"]
    assert (caps[0]["offset"], caps[0]["length"]) == (100, 50)


def test_parse_cdx_no_captures_and_errors():
    assert cc.parse_cdx(404, '{"message": "No Captures found for: example.co.uk"}') == []
    assert cc.parse_cdx(404, "<html>Not Found</html>") is None
    assert cc.parse_cdx(504, "Gateway Time-out") is None
    assert cc.parse_cdx(None, "") is None
    assert cc.parse_cdx(200, "") == []


def test_parse_collinfo_takes_the_first_n():
    text = json.dumps([{"id": f"CC-MAIN-{i}", "name": "x", "cdx-api": f"https://idx/CC-MAIN-{i}-index"}
                       for i in (3, 2, 1)])
    assert cc.parse_collinfo(text, 2) == [
        {"id": "CC-MAIN-3", "cdx_api": "https://idx/CC-MAIN-3-index"},
        {"id": "CC-MAIN-2", "cdx_api": "https://idx/CC-MAIN-2-index"}]


def test_cdx_url_parameters():
    url = cc.cdx_url("https://idx/C3-index", "example.co.uk", "domain", 1)
    assert url.startswith("https://idx/C3-index?url=example.co.uk&matchType=domain&output=json")
    assert "filter=status%3A200" in url and url.endswith("limit=1")
    assert "matchType=exact" in cc.cdx_url("https://idx/C3-index", TERMS, "exact", 5)


def test_norm_url_and_home_url():
    assert cc.norm_url("http://WWW.Example.co.uk/Terms/") == cc.norm_url("https://example.co.uk/Terms")
    assert cc.norm_url("https://example.co.uk/a?x=1") != cc.norm_url("https://example.co.uk/a")
    assert cc.norm_url("https://example.co.uk/a#top") == "example.co.uk/a"
    assert cc.home_url("https://www.example.co.uk/shop/about?x=1") == HOME


def test_html_captures_of():
    caps = cc.parse_cdx(200, CDX_BODY)
    assert len(cc.html_captures_of(caps, "http://example.co.uk")) == 1
    assert cc.html_captures_of(caps, "https://example.co.uk/logo.png") == []
    assert cc.html_captures_of(None, HOME) == []


def warc_record(html: bytes) -> bytes:
    return gzip.compress(
        b"WARC/1.0\r\nWARC-Type: response\r\nWARC-Target-URI: https://example.co.uk/terms\r\n\r\n"
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + html + b"\r\n\r\n")


def test_parse_warc_record():
    rec = cc.parse_warc_record(warc_record(b"<html><body><p>VAT No: 251256132</p></body></html>"))
    assert rec["target_uri"] == "https://example.co.uk/terms" and rec["http_status"] == 200
    assert b"VAT No: 251256132" in rec["body"]
    assert cc.parse_warc_record(b"<Error><Code>SlowDown</Code></Error>") is None
    assert cc.parse_warc_record(gzip.compress(b"not a warc record")) is None


def test_digits_present():
    assert cc.digits_present("VAT 251 2561 32.", "251256132")
    assert not cc.digits_present("EORI GB251256132000", "251256132")


# ---- per-site figures ----
def test_summarise_site():
    per_crawl = {
        "C3": {"any": [cap(HOME)], "pages": {HOME: [cap(HOME)], TERMS: [cap(TERMS)]}},
        "C2": {"any": [cap("https://example.co.uk/robots.txt", mime="text/plain")],
               "pages": {HOME: [], TERMS: []}},
        "C1": {"any": [], "pages": {HOME: [], TERMS: []}},
        "C0": {"any": None, "pages": {HOME: None, TERMS: None}},
    }
    row = cc.summarise_site("example.co.uk", HOME, [TERMS], per_crawl)
    assert (row["crawls_queried"], row["crawls_answered"]) == (4, 3)
    assert row["crawls_with_capture"] == 2 and row["crawl_ids_with_capture"] == "C3 C2"
    assert row["home_page_captured"] == "yes" and row["home_page_crawls"] == "C3"
    assert row["vat_evidence_page_captured"] == "yes" and row["vat_evidence_crawls"] == "C3"
    assert row["query_complete"] == "no"
    del per_crawl["C0"]
    row = cc.summarise_site("example.co.uk", HOME, [], per_crawl)
    assert row["query_complete"] == "yes" and row["vat_evidence_page_captured"] == ""


def test_pick_captures_newest_per_crawl_and_page_evidence_first():
    per_crawl = {
        "C3": {"any": [], "pages": {HOME: [cap(HOME)],
                                    TERMS: [cap(TERMS, ts="20260901"), cap(TERMS, ts="20260905"),
                                            cap(TERMS, ts="20260909", offset=None)]}},
        "C2": {"any": [], "pages": {HOME: None, TERMS: [cap(TERMS, ts="20260801")]}},
    }
    for entry in per_crawl.values():   # offsets as parse_cdx would deliver them
        for caps in entry["pages"].values():
            for c in caps or []:
                c["offset"] = int(c["offset"]) if c["offset"] else None
                c["length"] = int(c["length"])
    picked = cc.pick_captures(per_crawl, [TERMS, HOME])
    assert [(c["crawl"], c["url"], c["timestamp"]) for c in picked] == [
        ("C3", TERMS, "20260905"), ("C2", TERMS, "20260801"), ("C3", HOME, "20260901120000")]
    assert len(cc.pick_captures(per_crawl, [HOME, HOME])) == 1


# ---- polite client and queries (no network: httpx.MockTransport) ----
def make_client(tmp_path, handler, **kw):
    sleeps = []
    client = cc.PoliteClient(cache_dir=tmp_path, transport=httpx.MockTransport(handler),
                             sleep=sleeps.append, **kw)
    return client, sleeps


def index_handler(answers, seen):
    """Mock index: `answers` maps (matchType, url) to a CDX body; anything else has no captures."""
    def handler(request):
        key = (request.url.params["matchType"], request.url.params["url"])
        seen.append((*key, request.url.params["limit"]))
        if key in answers:
            return httpx.Response(200, text=answers[key])
        return httpx.Response(404, text='{"message": "No Captures found for: x"}')
    return handler


CRAWL = [{"id": "C3", "cdx_api": "https://idx/C3-index"}]


def test_query_site_asks_presence_then_exact_pages(tmp_path):
    seen = []
    answers = {("domain", "example.co.uk"): cdx_body(cap("https://blog.example.co.uk/post")),
               ("exact", TERMS): cdx_body(cap(TERMS))}
    client, _ = make_client(tmp_path, index_handler(answers, seen))
    per_crawl = cc.query_site(client, CRAWL, "example.co.uk", HOME, [TERMS])
    assert seen == [("domain", "example.co.uk", "1"), ("exact", HOME, "5"), ("exact", TERMS, "5")]
    row = cc.summarise_site("example.co.uk", HOME, [TERMS], per_crawl)
    assert (row["crawls_with_capture"], row["home_page_captured"], row["vat_evidence_page_captured"]) == (1, "no", "yes")


def test_query_site_skips_page_queries_when_not_needed(tmp_path):
    seen = []
    client, _ = make_client(tmp_path, index_handler({}, seen))           # nothing captured
    per_crawl = cc.query_site(client, CRAWL, "example.co.uk", HOME, [TERMS])
    assert len(seen) == 1 and per_crawl["C3"] == {"any": [], "pages": {HOME: [], TERMS: []}}

    seen.clear()                                                         # presence answer is the home page,
    answers = {("domain", "other.co.uk"): cdx_body(cap("https://other.co.uk/"))}
    client, _ = make_client(tmp_path, index_handler(answers, seen))      # and the evidence page is the home page
    per_crawl = cc.query_site(client, CRAWL, "other.co.uk", "https://www.other.co.uk/", ["https://other.co.uk"])
    assert seen == [("domain", "other.co.uk", "1")]
    row = cc.summarise_site("other.co.uk", "https://www.other.co.uk/", ["https://other.co.uk"], per_crawl)
    assert row["home_page_captured"] == "yes" and row["vat_evidence_page_captured"] == "yes"


def test_query_site_unanswered_crawl(tmp_path):
    client, _ = make_client(tmp_path, lambda request: httpx.Response(504))
    per_crawl = cc.query_site(client, CRAWL, "example.co.uk", HOME, [TERMS])
    assert per_crawl["C3"]["any"] is None
    assert cc.summarise_site("example.co.uk", HOME, [TERMS], per_crawl)["query_complete"] == "no"


def test_check_confirmed_reads_the_record_and_finds_the_number(tmp_path):
    def handler(request):
        assert request.headers["range"] == "bytes=100-149"
        return httpx.Response(206, content=warc_record(b"<p>VAT No: 251256132</p>"))

    client, _ = make_client(tmp_path, handler)
    per_crawl = {"C3": {"any": [], "pages": {TERMS: cc.parse_cdx(200, cdx_body(cap(TERMS))), HOME: []}}}
    company = {"CompanyNumber": "1", "CompanyName": "A LTD", "vat_number": "251256132", "evidence_url": TERMS}
    (row,) = cc.check_confirmed(client, company, "example.co.uk", HOME, per_crawl)
    assert row["page"] == "evidence" and row["same_page_as_our_fetch"] == "yes" and row["record_read"] == "yes"
    assert row["vat_found_labelled"] == "yes" and row["vat_digits_found"] == "yes" and row["crawl"] == "C3"


def test_client_backs_off_on_5xx_then_caches(tmp_path):
    answers = [httpx.Response(504), httpx.Response(200, text="<Error><Code>SlowDown</Code></Error>"),
               httpx.Response(200, text="ok")]
    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        return answers.pop(0)

    client, sleeps = make_client(tmp_path, handler)
    assert client.get("https://idx/x") == (200, b"ok")
    assert client.network_requests == 3 and cc.BACKOFF_START in sleeps and 2 * cc.BACKOFF_START in sleeps
    assert "VATDiscoveryPoC" in seen[0]
    assert client.get("https://idx/x") == (200, b"ok")       # second call comes from the cache
    assert client.network_requests == 3 and client.cache_hits == 1


def test_client_sends_range_and_caches_per_range(tmp_path):
    def handler(request):
        return httpx.Response(206, content=request.headers["range"].encode())

    client, _ = make_client(tmp_path, handler)
    assert client.get("https://data/f", byte_range="0-9", ok=(206,)) == (206, b"bytes=0-9")
    assert client.get("https://data/f", byte_range="10-19", ok=(206,)) == (206, b"bytes=10-19")


def test_client_stops_after_repeated_failures(tmp_path):
    client, _ = make_client(tmp_path, lambda request: httpx.Response(503))
    for i in range(cc.MAX_CONSECUTIVE_FAILURES - 1):
        assert client.get(f"https://idx/{i}") == (None, b"")
    with pytest.raises(cc.StopProbe):
        client.get("https://idx/last")
    assert client.network_requests == cc.MAX_CONSECUTIVE_FAILURES * (cc.MAX_RETRIES + 1)
    assert not any(tmp_path.iterdir())                        # failures are never cached


def test_client_does_not_retry_a_plain_403(tmp_path):
    client, sleeps = make_client(tmp_path, lambda request: httpx.Response(403))
    assert client.get("https://idx/x") == (None, b"")
    assert client.network_requests == 1 and client.failed_requests == 1


def test_client_timeout_is_30_seconds(tmp_path):
    client, _ = make_client(tmp_path, lambda request: httpx.Response(200))
    assert cc.TIMEOUT == 30.0 and client.client.timeout.read == 30.0


def test_budget_stops_new_requests_but_not_cache_reads(tmp_path):
    client, _ = make_client(tmp_path, lambda request: httpx.Response(200, text="ok"))
    assert client.get("https://idx/cached") == (200, b"ok")
    client.deadline = time.monotonic() - 1
    assert client.get("https://idx/cached") == (200, b"ok")
    with pytest.raises(cc.BudgetReached):
        client.get("https://idx/new")
    assert client.network_requests == 1


def test_budget_is_checked_before_a_backoff_wait(tmp_path):
    client, sleeps = make_client(tmp_path, lambda request: httpx.Response(504),
                                 deadline=time.monotonic() + cc.BACKOFF_START / 2)
    with pytest.raises(cc.BudgetReached):
        client.get("https://idx/x")
    assert client.network_requests == 1 and cc.BACKOFF_START not in sleeps


def test_summary_states_sites_not_reached(tmp_path):
    client, _ = make_client(tmp_path, lambda request: httpx.Response(200))
    args = type("Args", (), {"crawls": 3, "budget_minutes": 30.0})()
    per_crawl = {"C3": {"any": [cap(HOME)], "pages": {HOME: [cap(HOME)]}}}
    row = {**cc.summarise_site("example.co.uk", HOME, [], per_crawl), "companies": "1",
           "site_confidence": "legal_name_match", "confirmed_same": "no"}
    text = cc.summary_markdown("run_x", args, ["C3"], 98, [row], [], [], client,
                               "time budget of 30 minutes reached; 97 of 98 sites not reached", 1800.0)
    assert "Sites selected: 98; done: 1; not reached: 97" in text
    assert "INCOMPLETE" in text and "| Sites tested (registrable domains) | 1 |" in text
