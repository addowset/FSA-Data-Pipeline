from fsa_pipeline import db
from fsa_pipeline.classifier import Classification
from fsa_pipeline.csv_export import (
    EXPORT_FIELDNAMES,
    address_completeness,
    build_row,
    fetch_records,
    fhrs_url,
    join_address,
    matches_filters,
)
from fsa_pipeline.fhrs_bulk import Authority

AUTHORITY = "857"


# --- join_address ---

def test_join_address_joins_present_lines():
    assert join_address("1 High Street", "Sutton", None, "Bristol") == "1 High Street, Sutton, Bristol"


def test_join_address_all_none_is_empty_string():
    assert join_address(None, None, None, None) == ""


# --- address_completeness ---
# Grounded in the real cross-tab checked 2026-09-11 before building this:
# address_line_1 alone is a bad signal (many bulk rows have real content
# in lines 2-4 instead), and not one live-API-backfilled postcode is a
# full postcode -- all bare districts, with zero address lines.

def test_address_completeness_full_when_street_and_full_postcode():
    assert address_completeness("1 High Street", None, None, None, "NG17 3GA") == "full"


def test_address_completeness_postcode_only_when_no_street_but_full_postcode():
    assert address_completeness(None, None, None, None, "NG17 3GA") == "postcode_only"


def test_address_completeness_street_in_line_two_still_counts():
    """The real bulk pattern: address_line_1 NULL but content shifted to
    line 2 -- still a usable street address, not "no address"."""
    assert address_completeness(None, "1 High Street", None, None, "NG17 3GA") == "full"


def test_address_completeness_district_only_postcode_dominates():
    """Real pattern: live-API-backfilled rows are always district-only
    AND always addressless -- but even a street address present
    alongside a bare district still counts as district_only, since a
    satnav only gets you to the wide area either way."""
    assert address_completeness("1 High Street", None, None, None, "NG17") == "district_only"
    assert address_completeness(None, None, None, None, "NG17") == "district_only"


def test_address_completeness_no_address_when_nothing_present():
    assert address_completeness(None, None, None, None, None) == "no_address"


# --- fhrs_url ---

def test_fhrs_url_format():
    assert fhrs_url(1980200) == "https://ratings.food.gov.uk/business/1980200"


# --- build_row ---

def test_build_row_maps_matched_record():
    record = {
        "fhrsid": 1, "authority_name": "Bristol", "business_name": "The Cafe",
        "address_line_1": "1 High Street", "address_line_2": None, "address_line_3": None, "address_line_4": None,
        "post_code": "BS1 1AA", "postcode_from_live_api": None, "business_type": "Restaurant/Cafe/Canteen",
        "first_seen_date": "2026-08-25", "classification": "NEW_VENUE", "confidence": "HIGH", "reason": "x",
        "evidence_company_number": "12345678", "incorporation_date": "2026-08-01",
    }
    row = build_row(record)
    assert row["match_status"] == "Yes"
    assert row["company_number"] == "12345678"
    assert row["incorporation_date"] == "2026-08-01"
    assert row["postcode"] == "BS1 1AA"
    assert row["address_completeness"] == "full"
    assert row["fhrs_url"] == "https://ratings.food.gov.uk/business/1"
    assert set(row.keys()) == set(EXPORT_FIELDNAMES)


def test_build_row_unmatched_record_blank_company_fields():
    record = {
        "fhrsid": 2, "authority_name": "Bristol", "business_name": "The Cafe",
        "address_line_1": None, "address_line_2": None, "address_line_3": None, "address_line_4": None,
        "post_code": None, "postcode_from_live_api": "NG17", "business_type": "Restaurant/Cafe/Canteen",
        "first_seen_date": "2026-08-25", "classification": "UNKNOWN", "confidence": "LOW", "reason": "x",
        "evidence_company_number": None, "incorporation_date": None,
    }
    row = build_row(record)
    assert row["match_status"] == "No"
    assert row["company_number"] == ""
    assert row["incorporation_date"] == ""
    assert row["postcode"] == "NG17"
    assert row["address_completeness"] == "district_only"


# --- matches_filters ---

def make_record(**overrides):
    base = {
        "fhrsid": 1, "authority_code": AUTHORITY, "authority_name": "Bristol, City of",
        "post_code": "NG17 3GA", "postcode_from_live_api": None, "business_type": "Takeaway/sandwich shop",
        "classification": "NEW_VENUE",
    }
    base.update(overrides)
    return base


def test_matches_filters_postcode_area():
    assert matches_filters(make_record(), postcode_area="NG") is True
    assert matches_filters(make_record(), postcode_area="SW") is False


def test_matches_filters_authority_by_code():
    assert matches_filters(make_record(), authority="857") is True
    assert matches_filters(make_record(), authority="999") is False


def test_matches_filters_authority_by_name_substring():
    assert matches_filters(make_record(), authority="bristol") is True
    assert matches_filters(make_record(), authority="manchester") is False


def test_matches_filters_business_type_substring():
    assert matches_filters(make_record(), business_type_substring="takeaway") is True
    assert matches_filters(make_record(), business_type_substring="hotel") is False


def test_matches_filters_classification():
    assert matches_filters(make_record(), classification="NEW_VENUE") is True
    assert matches_filters(make_record(), classification="UNKNOWN") is False


def test_matches_filters_combine_with_and():
    record = make_record()
    assert matches_filters(record, postcode_area="NG", classification="NEW_VENUE") is True
    assert matches_filters(record, postcode_area="NG", classification="UNKNOWN") is False


def test_matches_filters_no_filters_matches_everything():
    assert matches_filters(make_record()) is True


# --- fetch_records (integration) ---

def make_authority(code=AUTHORITY, name="Bristol, City of"):
    return Authority(
        local_authority_id=1, code=code, name=name, friendly_name=name,
        region_name="South West", file_url="https://example.invalid/x.xml", establishment_count=1,
    )


def make_establishment(fhrsid, business_name="Test Cafe", post_code="BS1 1AA", first_seen="2026-08-25"):
    return {
        "fhrsid": fhrsid, "local_authority_business_id": "LA-1", "business_name": business_name,
        "business_type": "Restaurant/Cafe/Canteen", "business_type_id": 1,
        "address_line_1": "1 High Street", "address_line_2": None, "address_line_3": None, "address_line_4": None,
        "post_code": post_code, "rating_value": "5", "rating_value_numeric": 5, "rating_key": "fhrs_5_en-GB",
        "rating_date": "2026-01-01", "scheme_type": "FHRS", "new_rating_pending": False,
        "hygiene_score": 5, "structural_score": 5, "confidence_in_management_score": 5,
        "longitude": None, "latitude": None,
    }


def make_company(number="12345678", name="Test Cafe Ltd", created="2026-08-01"):
    return {
        "company_number": number, "company_name": name, "company_status": "active", "company_subtype": None,
        "company_type": "ltd", "date_of_creation": created, "date_of_cessation": None,
        "address_line_1": "1 High Street", "address_line_2": None, "locality": "Bristol", "region": "South West",
        "postal_code": "BS1 1AA", "country": "England", "sic_codes": '["56101"]', "source": "bulk",
    }


def setup_insert_event(conn, fhrsid, quarantined=False, collection_date="2026-08-25", classification="NEW_VENUE",
                        company_number=None):
    db.upsert_authorities(conn, [make_authority()], collection_date)
    db.ingest_establishments(conn, AUTHORITY, collection_date, [make_establishment(fhrsid, first_seen=collection_date)])
    conn.execute(
        "INSERT INTO diff_events (authority_code, collection_date, fhrsid, event_type, quarantined) "
        "VALUES (?, ?, ?, 'INSERT', ?)",
        (AUTHORITY, collection_date, fhrsid, int(quarantined)),
    )
    result = Classification(
        classification=classification, confidence="HIGH", reason="x", evidence_company_number=company_number,
    )
    db.record_classification(conn, fhrsid, AUTHORITY, collection_date, result, collection_date + "T00:00:00Z")
    conn.commit()


def test_fetch_records_excludes_quarantined(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    setup_insert_event(conn, 1, quarantined=False)
    setup_insert_event(conn, 2, quarantined=True)

    records = fetch_records(conn)

    assert [r["fhrsid"] for r in records] == [1]


def test_fetch_records_filters_by_date_range(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    setup_insert_event(conn, 1, collection_date="2026-08-20")
    setup_insert_event(conn, 2, collection_date="2026-09-01")

    records = fetch_records(conn, since="2026-08-25")

    assert [r["fhrsid"] for r in records] == [2]


def test_fetch_records_joins_incorporation_date_for_matched_company(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_companies(conn, "2026-08-25", [make_company()])
    setup_insert_event(conn, 1, company_number="12345678")

    records = fetch_records(conn)

    assert records[0]["incorporation_date"] == "2026-08-01"


def test_fetch_records_unmatched_has_no_incorporation_date(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    setup_insert_event(conn, 1, company_number=None)

    records = fetch_records(conn)

    assert records[0]["incorporation_date"] is None
