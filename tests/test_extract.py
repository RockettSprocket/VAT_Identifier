import extract as ex


# ---- VAT checksum ----
def test_checksum_plus55_variant():
    assert ex.vat_checksum_variant("251256132") == "plus55"


def test_checksum_plain_variant():
    assert ex.vat_checksum_variant("667235907") == "plain"
    assert ex.vat_checksum_variant("384904621") == "plain"


def test_checksum_fails_both():
    assert ex.vat_checksum_variant("123456789") is None
    assert ex.vat_checksum_variant("000000000") is None
    assert ex.vat_checksum_variant("12345678") is None


# ---- VAT extraction ----
def test_vat_formats():
    for text in [
        "VAT No: 251256132",
        "VAT Registration Number GB 251 2561 32",
        "vat reg. no. GB251-256-132",
        "V.A.T. number: 251.256.132",
        "Company No 01234567 | VAT Number: GB251256132 | Tel 020 7946 0000",
    ]:
        found = ex.find_vat_numbers(text)
        assert [f["number"] for f in found] == ["251256132"], text
        assert found[0]["variant"] == "plus55"


def test_vat_requires_label_and_checksum():
    assert ex.find_vat_numbers("Call us on 251256132 today") == []
    assert ex.find_vat_numbers("VAT No: 123456789") == []
    assert ex.find_vat_numbers("Our private elevator code is 251256132") == []


def test_vat_must_start_within_60_chars():
    assert ex.find_vat_numbers("VAT " + "x" * 70 + " 251256132") == []
    assert len(ex.find_vat_numbers("VAT " + "x" * 40 + " 251256132")) == 1


def test_vat_not_taken_from_longer_digit_run():
    assert ex.find_vat_numbers("VAT helpline 25125613299") == []


# ---- company numbers ----
def test_normalise_company_number():
    assert ex.normalise_company_number("1671407") == "01671407"
    assert ex.normalise_company_number("sc 12345") == "SC012345"
    assert ex.normalise_company_number("ip11569r") == "IP11569R"
    assert ex.normalise_company_number("LP009169") == "LP009169"


def test_find_company_numbers():
    def nums(t):
        return [c["number"] for c in ex.find_company_numbers(t)]

    assert nums("Registered in England and Wales No. 1671407") == ["01671407"]
    assert nums("Company number: SC412647. VAT No 123 4567 89") == ["SC412647"]
    assert nums("Company Reg No 09946657") == ["09946657"]
    assert nums("Registered number IP11569R") == ["IP11569R"]
    assert nums("Give us a call on 01234567") == []


# ---- not registered ----
def test_not_registered():
    assert ex.find_not_registered("Nue Web is not VAT registered, so no VAT is added")
    assert ex.find_not_registered("We are not currently registered for VAT.")
    assert ex.find_not_registered("We aren't VAT registered")
    assert not ex.find_not_registered("We are VAT registered. VAT No 251256132")


# ---- names ----
def test_legal_name_ignores_case_punctuation_and_ltd_limited():
    assert ex.find_legal_name("© 2026 Climb Global Solutions Limited.", "CLIMB GLOBAL SOLUTIONS LTD")
    assert ex.find_legal_name("c & e moir and son ltd", "C & E MOIR & SON LTD.")
    assert ex.find_legal_name("Ali’s Takeaway Bradford Ltd", "ALI'S TAKEAWAY BRADFORD LTD")


def test_legal_name_keeps_suffix_so_related_entities_do_not_match():
    site = "Howells Legal Limited is authorised and regulated by the SRA"
    assert ex.find_legal_name(site, "HOWELLS LEGAL (HOLDINGS) LIMITED") is None
    holdings_site = "Howells Legal Holdings Limited"
    assert ex.find_legal_name(holdings_site, "HOWELLS LEGAL LIMITED") is None
    assert ex.find_legal_name("Movivan Ltd, removals", "MOVIVAN HEATHROW LIMITED") is None
    assert ex.find_legal_name("Welcome to Howells Legal", "HOWELLS LEGAL LIMITED") is None


def test_name_tokens_present():
    assert ex.name_tokens_present("NUE WEB LTD", "Nue Web builds websites", "nueweb.co.uk")
    assert ex.name_tokens_present("BARRIER2U LTD", "safety barriers", "barrier2u.co.uk")
    assert not ex.name_tokens_present("SYD TRADERS LTD", "Syed Traders store", "syedtraders.store")


def test_visible_text_drops_scripts():
    title, text, _ = ex.visible_text(b"<html><head><title>T</title><script>VAT 251256132</script></head>"
                                     b"<body><p>Hello</p><style>.a{}</style></body></html>")
    assert title == "T" and text == "Hello"
