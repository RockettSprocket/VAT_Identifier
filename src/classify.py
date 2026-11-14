"""Stage 5: site_confidence per site, exactly one outcome per company. Pure functions.

This never decides whether a VAT number is correct - it only reports what was seen where.
"""

OUTCOMES = [
    "no_website_found",
    "candidate_site_unconfirmed",
    "website_no_vat",
    "site_states_not_registered",
    "vat_found",
]
CONFIDENCE_RANK = {
    "company_number_match": 0,
    "legal_name_match": 1,
    "name_only": 2,
    "other_company_number": 3,
}
CONFIRMED = {"company_number_match", "legal_name_match"}
GROUP_FLAG = "legal_name_with_other_company_number"


def site_confidence(site: dict) -> str | None:
    """None means there is nothing linking the site to the company: discard it.

    site keys: our_number (list), other_numbers (list), legal_name (list), name_tokens_ok (bool)
    """
    if site["our_number"]:
        return "company_number_match"
    if site["legal_name"]:
        return "legal_name_match"   # other numbers, if any, are recorded and flagged
    if site["other_numbers"]:
        return "other_company_number"
    if site["name_tokens_ok"]:
        return "name_only"
    return None


def review_flag(site: dict) -> str:
    """Group-site warning: exact legal name, but the only company number shown is someone else's."""
    if site.get("confidence") == "legal_name_match" and site["other_numbers"]:
        return GROUP_FLAG
    return ""


def classify_company(sites: list[dict]) -> dict:
    """sites: kept candidate sites, each with 'confidence', 'search_rank', 'vats', 'not_registered'.

    Returns {'outcome', 'site'} where site is the one that determines the outcome (or None).
    Primary site = best confidence, ties broken by search rank. If the primary site has no VAT
    number but a weaker site does, the outcome is still vat_found, carrying that site's confidence.
    """
    if not sites:
        return {"outcome": "no_website_found", "site": None}
    ranked = sorted(sites, key=lambda s: (CONFIDENCE_RANK[s["confidence"]], s["search_rank"]))
    with_vat = [s for s in ranked if s["vats"]]
    if with_vat:
        return {"outcome": "vat_found", "site": with_vat[0]}
    primary = ranked[0]
    if primary["confidence"] not in CONFIRMED:
        return {"outcome": "candidate_site_unconfirmed", "site": primary}
    if primary["not_registered"]:
        return {"outcome": "site_states_not_registered", "site": primary}
    return {"outcome": "website_no_vat", "site": primary}
