"""Verification summary: one verdict per company, counts per group, weighted rates."""
import pytest

import summarise_verification as sv


def _checked(number, vat, confidence, verdict, valid="yes"):
    return {"CompanyNumber": number, "vat_number": vat, "site_confidence": confidence,
            "hmrc_valid": valid, "match_verdict": verdict}


@pytest.fixture
def sample_rows():
    """Six sampled companies: four in bucket 'likely', two in bucket 'dormant'."""
    return [{"CompanyNumber": n, "vat_bucket": b} for n, b in [
        ("00000001", "likely"), ("00000002", "likely"), ("00000003", "likely"),
        ("00000004", "likely"), ("SC000005", "dormant"), ("00000006", "dormant")]]


@pytest.fixture
def checked_rows():
    """Four companies with VAT numbers; company 1 has three rows, company 3 has two."""
    return [
        _checked("1", "111111111", "other_company_number", "different"),
        _checked("00000001", "222222222", "company_number_match", "same"),
        _checked("00000001", "333333333", "company_number_match", "related_entity"),
        _checked("00000002", "444444444", "other_company_number", "related_entity"),
        _checked("00000003", "555555555", "legal_name_match", "different", valid="no"),
        _checked("00000003", "666666666", "name_only", "different"),
        _checked("SC000005", "777777777", "other_company_number", "different"),
    ]


POPULATION = {"likely": 100, "dormant": 300}


def test_best_row_decides_verdict_and_confidence(checked_rows, sample_rows):
    records = {r["CompanyNumber"]: r for r in sv.company_verdicts(checked_rows, sample_rows)}
    assert list(records) == ["00000001", "00000002", "00000003", "SC000005"]
    assert records["00000001"]["verdict"] == "same"
    assert records["00000001"]["site_confidence"] == "company_number_match"
    assert records["00000002"]["verdict"] == "related_entity"
    # A tie keeps the first row; valid is true if any row is valid.
    assert records["00000003"]["site_confidence"] == "legal_name_match"
    assert records["00000003"]["valid"] is True
    assert records["SC000005"]["vat_bucket"] == "dormant"


def test_empty_verdict_is_unverified_and_ranks_last(sample_rows):
    rows = [_checked("00000006", "111111111", "name_only", "", valid=""),
            _checked("00000006", "222222222", "legal_name_match", "different")]
    assert sv.company_verdicts(rows, sample_rows)[0]["verdict"] == "different"
    record = sv.company_verdicts(rows[:1], sample_rows)[0]
    assert record["verdict"] == sv.UNVERIFIED and record["valid"] is False


def test_unknown_verdict_or_company_is_an_error(sample_rows):
    with pytest.raises(ValueError, match="Unknown match_verdict"):
        sv.company_verdicts([_checked("00000001", "1", "name_only", "maybe")], sample_rows)
    with pytest.raises(ValueError, match="not in the sample"):
        sv.company_verdicts([_checked("09999999", "1", "name_only", "same")], sample_rows)


def test_summary_counts_and_rates(checked_rows, sample_rows):
    records = sv.company_verdicts(checked_rows, sample_rows)
    rows = {(r["group_by"], r["group"]): r for r in sv.summarise(records, sample_rows, POPULATION)}

    overall = rows[("all", "all")]
    assert [overall[c] for c in sv.COUNT_COLUMNS] == [4, 4, 1, 1, 2, 0]
    assert overall["sampled"] == 6 and overall["population"] == 400
    assert overall["found_rate"] == pytest.approx(4 / 6)

    assert rows[("site_confidence", "company_number_match")]["confirmed"] == 1
    assert rows[("site_confidence", "other_company_number")]["found"] == 2
    assert rows[("site_confidence", "legal_name_match")]["different"] == 1
    assert ("site_confidence", "name_only") not in rows

    likely, dormant = rows[("vat_bucket", "likely")], rows[("vat_bucket", "dormant")]
    assert (likely["sampled"], likely["found"], likely["confirmed"]) == (4, 3, 1)
    assert (dormant["sampled"], dormant["found"], dormant["confirmed"]) == (2, 1, 0)

    weighted = rows[("weighted", "population-weighted")]
    assert weighted["found_rate"] == pytest.approx(0.25 * 3 / 4 + 0.75 * 1 / 2)
    assert weighted["confirmed_rate"] == pytest.approx(0.25 * 1 / 4)


def test_group_order_and_text_output(checked_rows, sample_rows):
    rows = sv.summarise(sv.company_verdicts(checked_rows, sample_rows), sample_rows, POPULATION)
    assert [(r["group_by"], r["group"]) for r in rows] == [
        ("all", "all"),
        ("site_confidence", "company_number_match"), ("site_confidence", "legal_name_match"),
        ("site_confidence", "other_company_number"),
        ("vat_bucket", "likely"), ("vat_bucket", "dormant"),
        ("weighted", "population-weighted"),
    ]
    text = sv.csv_rows(rows)
    assert set(text[0]) == set(sv.SUMMARY_COLUMNS)
    assert text[0]["found_rate"] == "0.666667" and text[1]["found_rate"] == ""
    assert "| likely | 100 | 4 | 3 | 3 | 1 | 1 | 1 | 0 | 75.00% | 25.00% |" in sv.markdown_report("r", rows, [])


def test_missing_population_count_is_an_error(checked_rows, sample_rows):
    records = sv.company_verdicts(checked_rows, sample_rows)
    with pytest.raises(ValueError, match="dormant"):
        sv.summarise(records, sample_rows, {"likely": 100})
