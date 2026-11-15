"""EORI probe: label-to-number pattern, the prefix + VAT + 000 check, same-site lookup."""
import hashlib

import eori_scan as es

VAT = "251256132"  # checksum-valid (plus55), also used in test_extract.py


def numbers(text):
    return [e["number"] for e in es.find_eori_numbers(text)]


def test_words_between_label_and_number():
    assert numbers("EORI No. GB840931924000") == ["GB840931924000"]
    assert numbers("Our EORI number is: GB 840 931 924 000.") == ["GB840931924000"]
    assert numbers("eori: gb840931924000") == ["GB840931924000"]


def test_xi_prefix_and_15_digits():
    assert numbers("EORI XI840931924000") == ["XI840931924000"]
    assert numbers("EORI number GB840931924000123") == ["GB840931924000123"]


def test_non_matches():
    assert numbers("VAT No. GB840931924000") == []             # no EORI label
    assert numbers("EORI No. GB84093192400") == []              # 11 digits
    assert numbers("EORI No. GB8409319240001") == []            # 13 digits
    assert numbers("EORI No. DE840931924000") == []             # other prefix
    assert numbers("EORI tel 01234 567890 GB840931924000") == []  # another number in between
    assert numbers("EORI " + "x" * 60 + " GB840931924000") == []  # label too far away
    assert numbers("THEORIST GB840931924000") == []


def test_prefix_vat_000_check():
    (e,) = es.find_eori_numbers(f"EORI No. GB{VAT}000")
    assert e["is_prefix_vat_000"] and e["vat_number"] == VAT and e["vat_checksum_variant"] == "plus55"
    (e,) = es.find_eori_numbers("EORI No. GB840931924001")
    assert not e["is_prefix_vat_000"] and e["vat_number"] == ""
    (e,) = es.find_eori_numbers("EORI No. GB123456789000")
    assert e["is_prefix_vat_000"] and e["vat_checksum_variant"] == ""


def test_digits_elsewhere_ignores_the_eori_itself():
    text = f"EORI No. GB{VAT}000"
    (e,) = es.find_eori_numbers(text)
    assert not es.digits_elsewhere(text, VAT, (e["start"], e["end"]))
    assert not es.digits_elsewhere(f"EORI GB{VAT} 000", VAT)
    assert es.digits_elsewhere(f"VAT 251 2561 32 and EORI GB{VAT}000", VAT)


def test_snippet_is_at_most_100_characters():
    text = "a" * 300 + " EORI No. GB840931924000 " + "b" * 300
    (e,) = es.find_eori_numbers(text)
    snip = es.short_snippet(text, e["start"], e["end"])
    assert len(snip) <= 100 and "GB840931924000" in snip


def test_pages_by_company_filters_log(tmp_path):
    log = tmp_path / "run_log.jsonl"
    log.write_text("\n".join([
        '{"kind": "search", "url": "cache:brave", "status": 200, "company_number": "1"}',
        '{"kind": "fetch", "url": "https://a.example/robots.txt", "status": 200, "company_number": "1"}',
        '{"kind": "fetch", "url": "https://a.example/", "status": 200, "company_number": "1"}',
        '{"kind": "fetch", "url": "https://a.example/", "status": 200, "company_number": "1"}',
        '{"kind": "fetch", "url": "https://a.example/x", "status": 404, "company_number": "1"}',
    ]), encoding="utf-8")
    assert es.pages_by_company(log) == {"1": ["https://a.example/"]}


def _cache(tmp_path, url, html):
    key = hashlib.sha1(url.encode()).hexdigest()
    (tmp_path / key[:2]).mkdir(exist_ok=True)
    (tmp_path / key[:2] / f"{key}.body").write_text(html, encoding="utf-8")


def test_mention_in_script_only_counts_as_raw_not_visible(tmp_path):
    url = "https://a.example/"
    _cache(tmp_path, url, '<p>hello</p><script>var d = ["eori-search.co.uk"]</script>')
    company = {"CompanyNumber": "1", "CompanyName": "A LTD", "website": url}
    res = es.scan_company(company, [url], tmp_path)
    assert not res["mentions"] and res["mentions_raw"] and res["hits"] == []


def test_scan_company_same_site_lookup(tmp_path):
    pages = {
        "https://www.a.example/": f"<p>EORI No. GB{VAT}000</p><script>EORI GB111111111000</script>",
        "https://a.example/terms": f"<p>VAT Reg No: {VAT}</p>",
        "https://other.example/": "<p>nothing</p>",
    }
    for url, html in pages.items():
        _cache(tmp_path, url, html)
    company = {"CompanyNumber": "1", "CompanyName": "A LTD", "website": "https://a.example/"}
    res = es.scan_company(company, [*pages, "https://a.example/gone"], tmp_path)
    assert (res["pages_read"], res["pages_missing"], res["mentions"]) == (3, 1, True)
    assert res["mentions_raw"]
    (hit,) = res["hits"]
    assert hit["eori_number"] == f"GB{VAT}000" and hit["page_on_chosen_site"] == "yes"
    assert hit["vat_labelled_elsewhere_on_site"] == "yes"
    assert hit["vat_seen_urls"] == "https://a.example/terms"
