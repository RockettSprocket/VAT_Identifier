"""Registrable domains, query variants, the search phase with directory detection, VAT flags."""
from filter import is_blocked, registrable_domain
from run_pipeline import apply_flags, search_phase
from search import SearchResult, build_queries, strip_company_suffix


def test_registrable_domain():
    assert registrable_domain("https://company.easydigitalfiling.com/x") == "easydigitalfiling.com"
    assert registrable_domain("easydigitalfiling.com") == "easydigitalfiling.com"
    assert registrable_domain("https://www.shop.example.co.uk/") == "example.co.uk"
    assert registrable_domain("http://example.org.uk") == "example.org.uk"
    assert registrable_domain("") == ""


def test_is_blocked_uses_registrable_domain_for_directories():
    assert is_blocked("https://abc.dir.co.uk/page", set(), {"dir.co.uk"})
    assert not is_blocked("https://abc.other.co.uk/page", set(), {"dir.co.uk"})
    assert is_blocked("https://find.service.gov.uk/", {"gov.uk"})


def test_strip_company_suffix():
    assert strip_company_suffix("HOWELLS LEGAL (HOLDINGS) LIMITED") == "HOWELLS LEGAL (HOLDINGS)"
    assert strip_company_suffix("C & E MOIR & SON LTD.") == "C & E MOIR & SON"
    assert strip_company_suffix("PARTNERS GROUP ACCESS 454 L.P.") == "PARTNERS GROUP ACCESS 454"
    assert strip_company_suffix("SOME FIRM LLP") == "SOME FIRM"
    assert strip_company_suffix("BIG CO PLC") == "BIG CO"
    assert strip_company_suffix("X RETAIL LIMITED PARTNERSHIP") == "X RETAIL"
    assert strip_company_suffix("HAVANT LIGHT OPERA") == "HAVANT LIGHT OPERA"
    assert strip_company_suffix("HELP") == "HELP"
    assert strip_company_suffix("LIMITED") == "LIMITED"   # never strip to nothing


def _company(number, name, previous=""):
    return {"CompanyNumber": number, "CompanyName": name, "RegAddress.PostTown": "LEEDS",
            "PreviousName_1.CompanyName": previous}


def test_build_queries():
    assert build_queries(_company("1", "ACME WIDGETS LTD")) == [
        ("q1", '"ACME WIDGETS LTD" LEEDS'), ("q2", "ACME WIDGETS LEEDS")]
    third = build_queries(_company("1", "ACME WIDGETS LTD", "OLD NAME LIMITED"))[2]
    assert third == ("q3", '"OLD NAME LIMITED" LEEDS')


def _res(*urls):
    return [SearchResult(i + 1, u, "", "") for i, u in enumerate(urls)]


def test_search_phase_fallback_and_directories():
    companies = [_company("A", "ALPHA LTD"), _company("B", "BETA LTD", "OLD BETA LTD"), _company("C", "GAMMA LTD")]
    answers = {
        '"ALPHA LTD" LEEDS': _res("https://a.listing.com/alpha", "https://alpha.co.uk/"),
        '"BETA LTD" LEEDS': _res("https://b.listing.com/beta", "https://facebook.com/beta"),
        "BETA LEEDS": _res("https://www.listing.com/beta"),
        '"OLD BETA LTD" LEEDS': _res("https://beta.co.uk/about", "https://www.beta.co.uk/"),
        '"GAMMA LTD" LEEDS': _res(),
        "GAMMA LEEDS": _res("https://shared.org/x"),
    }
    calls = []

    def search_fn(company, variant, query):
        calls.append(query)
        return answers[query]

    background = {"Z": _res("https://shared.org/other")}
    plans, seen, directories = search_phase(companies, search_fn, {"facebook.com"}, background)

    assert directories == {"listing.com", "shared.org"}
    assert seen["listing.com"] == {"A", "B"} and seen["shared.org"] == {"C", "Z"}
    # A stops at q1: the directory is dropped, its own site is kept.
    assert plans["A"]["variant"] == "q1"
    assert [r.url for r in plans["A"]["candidates"]] == ["https://alpha.co.uk/"]
    assert "ALPHA LEEDS" not in calls
    # B falls through to q3; two URLs on one registrable domain give one candidate.
    assert plans["B"]["variant"] == "q3" and len(plans["B"]["candidates"]) == 1
    assert plans["B"]["dropped_directories"] == ["listing.com (directory: seen for 2 companies)"]
    # C: its only result is a directory because a company outside the run has it too.
    assert plans["C"]["variant"] == "" and plans["C"]["candidates"] == []
    assert len(plans["C"]["queries"]) == 2


def test_apply_flags():
    rows = [{"CompanyNumber": "A", "vat_number": "111111111; 222222222"},
            {"CompanyNumber": "B", "vat_number": "333333333"},
            {"CompanyNumber": "C", "vat_number": ""}]
    verify = [{"CompanyNumber": "A", "vat_number": "111111111"}, {"CompanyNumber": "A", "vat_number": "222222222"},
              {"CompanyNumber": "B", "vat_number": "333333333"}, {"CompanyNumber": "C", "vat_number": "222222222"}]
    apply_flags(rows, verify)
    assert [r["vat_shared_across_companies"] for r in rows] == ["yes", "", ""]
    assert [v["vat_shared_across_companies"] for v in verify] == ["", "yes", "", "yes"]
