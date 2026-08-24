import datetime as dt
import json
from types import SimpleNamespace

from fsa_pipeline.companies_house import (
    RecordParseError,
    compute_company_fingerprint,
    hits_from_page_bytes,
    incorporated_date_window,
    items_from_page_bytes,
    parse_company_item,
)

# Shaped per the documented Advanced Search response schema (verified
# against Companies House's own developer docs -- see companies_house.py
# module docstring). No live API key was available to confirm this
# against a real response; this fixture is documentation-derived.
SAMPLE_ITEM_FULL = {
    "company_name": "THE COB KINGS LTD",
    "company_number": "12345678",
    "company_status": "active",
    "company_subtype": None,
    "company_type": "ltd",
    "date_of_creation": "2026-08-10",
    "date_of_cessation": None,
    "kind": "search-results#advanced-search",
    "links": {"company_profile": "/company/12345678"},
    "registered_office_address": {
        "address_line_1": "1 High Street",
        "address_line_2": "Sutton-in-Ashfield",
        "locality": "Nottingham",
        "region": "Nottinghamshire",
        "postal_code": "NG17 3GA",
        "country": "England",
    },
    "sic_codes": ["56101", "56302"],
}

# registered_office_address entirely absent -- documented as a real
# possibility, must not crash the parser.
SAMPLE_ITEM_SPARSE = {
    "company_name": "SPARSE VENTURES LTD",
    "company_number": "87654321",
    "company_status": "active",
    "company_type": "ltd",
    "date_of_creation": "2026-08-11",
    "sic_codes": ["56290"],
}


def test_parse_company_item_full():
    result = parse_company_item(SAMPLE_ITEM_FULL)
    assert result["company_number"] == "12345678"
    assert result["company_name"] == "THE COB KINGS LTD"
    assert result["postal_code"] == "NG17 3GA"
    assert json.loads(result["sic_codes"]) == ["56101", "56302"]


def test_parse_company_item_missing_address_does_not_crash():
    result = parse_company_item(SAMPLE_ITEM_SPARSE)
    assert result["company_number"] == "87654321"
    assert result["address_line_1"] is None
    assert result["postal_code"] is None


def test_parse_company_item_missing_company_number_raises():
    try:
        parse_company_item({"company_name": "No Number Ltd"})
        assert False, "expected RecordParseError"
    except RecordParseError:
        pass


def test_fingerprint_stable_for_identical_input():
    a = parse_company_item(SAMPLE_ITEM_FULL)
    b = dict(a)
    assert compute_company_fingerprint(a) == compute_company_fingerprint(b)


def test_fingerprint_changes_on_status_change():
    a = parse_company_item(SAMPLE_ITEM_FULL)
    b = dict(a, company_status="dissolved")
    assert compute_company_fingerprint(a) != compute_company_fingerprint(b)


def test_fingerprint_ignores_company_number():
    a = parse_company_item(SAMPLE_ITEM_FULL)
    b = dict(a, company_number="00000000")
    assert compute_company_fingerprint(a) == compute_company_fingerprint(b)


def test_hits_and_items_from_page_bytes():
    page = json.dumps({"hits": 42, "items": [SAMPLE_ITEM_FULL, SAMPLE_ITEM_SPARSE]}).encode("utf-8")
    assert hits_from_page_bytes(page) == 42
    assert len(items_from_page_bytes(page)) == 2


def test_hits_from_page_bytes_defaults_to_zero_when_absent():
    page = json.dumps({"items": []}).encode("utf-8")
    assert hits_from_page_bytes(page) == 0


def test_incorporated_date_window_is_trailing():
    config = SimpleNamespace(ch_incorporated_window_days=14)
    incorporated_from, incorporated_to = incorporated_date_window(config, dt.date(2026, 8, 24))
    assert incorporated_from == "2026-08-10"
    assert incorporated_to == "2026-08-24"
