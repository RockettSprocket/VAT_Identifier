"""Public-records probe: record readers, VAT hits per record, sample matching."""
import json

import records_scan as rs

VAT = "251256132"  # checksum-valid
SAMPLE = [
    {"CompanyNumber": "01234567", "CompanyName": "HOWELLS LEGAL LIMITED"},
    {"CompanyNumber": "SC123456", "CompanyName": "SMITH & SONS LTD"},
    {"CompanyNumber": "07654321", "CompanyName": "ACME TRADING LTD"},
]
INDEX = rs.build_sample_index(SAMPLE)


def test_readers_csv_jsonl_json(tmp_path):
    (tmp_path / "a.csv").write_text("name,VAT number\nA LTD,251256132\nB LTD,\n", encoding="utf-8")
    (tmp_path / "b.jsonl").write_text('{"name": "A LTD"}\n\n{"name": "B LTD"}\n', encoding="utf-8")
    (tmp_path / "c.json").write_text(json.dumps([{"n": 1}, {"n": 2}, {"n": 3}]), encoding="utf-8")
    (tmp_path / "d.json").write_text(json.dumps({"total": 2, "items": [{"n": 1}, {"n": 2}]}), encoding="utf-8")
    counts = [len(list(rs.iter_records(tmp_path / f))) for f in ("a.csv", "b.jsonl", "c.json", "d.json")]
    assert counts == [2, 2, 3, 2]


def test_record_text_flattens_nested_values_and_keeps_field_names():
    text = rs.record_text({"supplier": {"name": "A LTD", "ids": ["x", 5]}, "empty": None, "VAT number": VAT})
    assert text == f"supplier: A LTD x 5 | VAT number: {VAT}"


def test_vat_hit_from_label_in_text_or_in_field_name():
    assert [h["number"] for h in rs.vat_hits(f"notes: Supplier VAT Reg No GB {VAT}")] == [VAT]
    assert [h["number"] for h in rs.vat_hits(rs.record_text({"VAT number": VAT}))] == [VAT]


def test_vat_hit_needs_label_checksum_and_60_char_window():
    assert rs.vat_hits(f"reference: {VAT}") == []                    # no label
    assert rs.vat_hits("VAT number: 123456789") == []                # checksum fails
    assert rs.vat_hits("VAT " + "x" * 70 + f" {VAT}") == []          # more than 60 chars after label
    assert len(rs.vat_hits("VAT " + "x" * 40 + f" {VAT}")) == 1


def test_snippet_is_at_most_100_characters_and_holds_the_number():
    text = "a" * 400 + f" VAT registration number: {VAT} " + "b" * 400
    (hit,) = rs.vat_hits(text)
    assert len(hit["snippet"]) <= 100 and VAT in hit["snippet"]


def test_name_key_ltd_equals_limited_and_suffix_is_kept():
    assert rs.name_key("Howells Legal Ltd.") == rs.name_key("HOWELLS LEGAL LIMITED")
    assert rs.name_key("Smith and Sons Limited") == rs.name_key("SMITH & SONS LTD")
    assert rs.name_key("HOWELLS LEGAL") != rs.name_key("HOWELLS LEGAL LIMITED")
    assert rs.name_key("HOWELLS LEGAL (HOLDINGS) LIMITED") != rs.name_key("HOWELLS LEGAL LIMITED")


def test_match_by_number_first_then_by_name():
    records = [
        {"company_no": "1234567", "supplier": "Howells Legal Ltd", "note": f"VAT No {VAT}"},  # number (padded)
        {"supplier": "Smith and Sons Limited"},                                              # name only
        {"supplier": "Smith & Sons Ltd", "extra": {"crn": "sc123456"}},                      # number, nested
        {"supplier": "Acme Trading"},                                                        # suffix missing
        {"supplier": "Acme Trading Limited Group"},                                          # not the whole value
        {"supplier": "Howells Legal Limited"},                                               # name of a number match
    ]
    res = rs.scan_records(records, INDEX)
    assert res["records"] == 6 and res["records_with_vat_number"] == 1
    assert {k: v["match_type"] for k, v in res["matches"].items()} == {
        "01234567": "company_number", "SC123456": "company_number"}
    assert res["matches"]["01234567"]["vat_number"] == VAT
    assert res["matches"]["01234567"]["matching_records"] == 1


def test_name_only_match_is_reported_as_legal_name():
    res = rs.scan_records([{"supplier": "ACME TRADING LIMITED"}, {"supplier": "Acme Trading Ltd"}], INDEX)
    assert res["matches"] == {"07654321": {
        "match_type": "legal_name", "matching_records": 2, "vat_number": "", "snippet": ""}}


def test_yield_per_1000():
    assert rs.yield_per_1000(3, 2000) == "1.50"
    assert rs.yield_per_1000(0, 0) == ""
