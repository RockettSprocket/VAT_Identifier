"""Site confidence, the group-site flag, the outcome per company, and the blocklist."""
import classify as cl
from filter import is_blocked


def site(rank=1, ours=False, other=False, legal=False, tokens=True, vat=False, not_reg=False):
    """A candidate site with the given evidence and its confidence."""
    s = {
        "search_rank": rank,
        "our_number": [{"number": "01671407"}] if ours else [],
        "other_numbers": [{"number": "09999999"}] if other else [],
        "legal_name": [{"name": "X LTD"}] if legal else [],
        "name_tokens_ok": tokens,
        "vats": [{"number": "251256132", "variant": "plus55"}] if vat else [],
        "not_registered": [{"snippet": "not VAT registered"}] if not_reg else [],
    }
    s["confidence"] = cl.site_confidence(s)
    return s


def test_site_confidence_order():
    assert site(ours=True, other=True, legal=True)["confidence"] == "company_number_match"
    assert site(legal=True, other=True)["confidence"] == "legal_name_match"
    assert site(other=True)["confidence"] == "other_company_number"
    assert site()["confidence"] == "name_only"
    assert site(tokens=False)["confidence"] is None


def test_group_site_flag():
    assert cl.review_flag(site(legal=True, other=True)) == cl.GROUP_FLAG
    assert cl.review_flag(site(legal=True)) == ""
    assert cl.review_flag(site(ours=True, other=True)) == ""


def test_outcomes():
    assert cl.classify_company([])["outcome"] == "no_website_found"
    assert cl.classify_company([site()])["outcome"] == "candidate_site_unconfirmed"
    assert cl.classify_company([site(other=True)])["outcome"] == "candidate_site_unconfirmed"
    assert cl.classify_company([site(not_reg=True)])["outcome"] == "candidate_site_unconfirmed"
    assert cl.classify_company([site(ours=True)])["outcome"] == "website_no_vat"
    assert cl.classify_company([site(legal=True, not_reg=True)])["outcome"] == "site_states_not_registered"
    assert cl.classify_company([site(vat=True)])["outcome"] == "vat_found"
    assert cl.classify_company([site(legal=True, vat=True, not_reg=True)])["outcome"] == "vat_found"


def test_primary_site_and_weaker_site_with_vat():
    confirmed = site(rank=3, ours=True)
    weak_with_vat = site(rank=1, vat=True)
    res = cl.classify_company([weak_with_vat, confirmed])
    assert res["outcome"] == "vat_found" and res["site"] is weak_with_vat
    both = cl.classify_company([site(rank=1, vat=True), s2 := site(rank=2, ours=True, vat=True)])
    assert both["site"] is s2


def test_blocklist_matches_subdomains():
    bl = {"gov.uk", "facebook.com"}
    assert is_blocked("https://find-and-update.company-information.service.gov.uk/company/1", bl)
    assert is_blocked("https://www.facebook.com/x", bl)
    assert not is_blocked("https://notfacebook.com/", bl)
