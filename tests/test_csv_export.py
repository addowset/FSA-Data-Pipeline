from fsa_pipeline import db
from fsa_pipeline.classifier import Classification
from fsa_pipeline.csv_export import (
    EXPORT_FIELDNAMES,
    address_completeness,
    build_row,
    companies_house_url,
    customer_reason,
    days_since_first_seen,
    fetch_records,
    fhrs_url,
    format_sic_codes,
    google_maps_url,
    join_address,
    matches_filters,
    rating_status,
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
    assert address_completeness(None, "1 High Street", None, None, "NG17 3GA") == "full"


def test_address_completeness_district_only_postcode_dominates():
    assert address_completeness("1 High Street", None, None, None, "NG17") == "district_only"
    assert address_completeness(None, None, None, None, "NG17") == "district_only"


def test_address_completeness_no_address_when_nothing_present():
    assert address_completeness(None, None, None, None, None) == "no_address"


# --- rating_status ---
# Real distinct rating_value strings checked 2026-09-11: both spaced and
# unspaced variants exist ("AwaitingInspection" / "Awaiting Inspection").

def test_rating_status_numeric_and_qualitative_rated_values():
    for v in ["5", "0", "Pass", "Pass and Eat Safe", "Improvement Required"]:
        assert rating_status(v) == "Rated"


def test_rating_status_awaiting_inspection_both_spellings():
    assert rating_status("AwaitingInspection") == "Awaiting Inspection"
    assert rating_status("Awaiting Inspection") == "Awaiting Inspection"


def test_rating_status_awaiting_publication_both_spellings():
    assert rating_status("AwaitingPublication") == "Awaiting Publication"
    assert rating_status("Awaiting Publication") == "Awaiting Publication"


def test_rating_status_exempt():
    assert rating_status("Exempt") == "Exempt"


def test_rating_status_unknown_when_missing():
    assert rating_status(None) == "Unknown"
    assert rating_status("") == "Unknown"


# --- days_since_first_seen ---

def test_days_since_first_seen_computes_difference():
    import datetime as dt
    assert days_since_first_seen("2026-08-25", as_of=dt.date(2026, 9, 11)) == 17


def test_days_since_first_seen_none_when_missing():
    assert days_since_first_seen(None) is None


# --- URL builders ---

def test_fhrs_url_format():
    assert fhrs_url(1980200) == "https://ratings.food.gov.uk/business/1980200"


def test_companies_house_url_format():
    assert companies_house_url("12345678") == "https://find-and-update.company-information.service.gov.uk/company/12345678"


def test_companies_house_url_empty_when_no_number():
    assert companies_house_url(None) == ""
    assert companies_house_url("") == ""


def test_google_maps_url_encodes_address():
    url = google_maps_url("1 High Street", "NG17 3GA")
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "High%20Street" in url or "High+Street" in url


def test_google_maps_url_empty_when_nothing_to_search():
    assert google_maps_url("", None) == ""


# --- format_sic_codes ---

def test_format_sic_codes_joins_list():
    assert format_sic_codes('["56101", "56302"]') == "56101, 56302"


def test_format_sic_codes_empty_when_none():
    assert format_sic_codes(None) == ""


# --- customer_reason ---
# Real case that prompted this, 2026-09-11: "Closest match was THE BOND
# BAKERY LTD at similarity 0.0777" reads as a confession the matcher is
# unreliable, not a status -- customer_reason never surfaces a score.

def make_reason_record(**overrides):
    base = {
        "classification": "NEW_VENUE", "business_name": "The Cafe",
        "company_name": None, "previous_business_name": None, "evidence_company_number": None,
    }
    base.update(overrides)
    return base


def test_customer_reason_new_venue_with_match():
    text = customer_reason(make_reason_record(classification="NEW_VENUE", company_name="THE CAFE LTD"))
    assert "THE CAFE LTD" in text
    assert "%" not in text and "similarity" not in text


def test_customer_reason_new_venue_no_match():
    assert customer_reason(make_reason_record(classification="NEW_VENUE")) == "New registration."


def test_customer_reason_operator_change_with_different_previous_name():
    text = customer_reason(make_reason_record(
        classification="OPERATOR_CHANGE", business_name="New Name", previous_business_name="Old Name",
    ))
    assert "Old Name" in text


def test_customer_reason_operator_change_same_trading_name():
    text = customer_reason(make_reason_record(
        classification="OPERATOR_CHANGE", business_name="Same Name", previous_business_name="Same Name",
    ))
    assert "not company-confirmed" in text


def test_customer_reason_unknown_with_weak_candidate_never_shows_score():
    """The real case: an UNKNOWN row with only a rejected weak candidate
    must never surface that candidate's name/score to the customer."""
    text = customer_reason(make_reason_record(classification="UNKNOWN", evidence_company_number=None))
    assert text == "No confirmed company match."


def test_customer_reason_unknown_with_uncorroborated_match():
    text = customer_reason(make_reason_record(
        classification="UNKNOWN", evidence_company_number="12345678", company_name="SOME CO LTD",
    ))
    assert "SOME CO LTD" in text
    assert "not confirmed as a new registration" in text


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


# --- build_row ---

def make_full_record(**overrides):
    base = {
        "fhrsid": 1, "authority_name": "Bristol", "authority_extract_date": "2026-09-10",
        "business_name": "The Cafe",
        "address_line_1": "1 High Street", "address_line_2": None, "address_line_3": None, "address_line_4": None,
        "post_code": "BS1 1AA", "postcode_from_live_api": None, "postcode_source": "bulk",
        "longitude": -2.58, "latitude": 51.45,
        "business_type": "Restaurant/Cafe/Canteen", "rating_value": "AwaitingInspection",
        "first_seen_date": "2026-08-25", "classification": "NEW_VENUE", "confidence": "HIGH", "reason": "diagnostic text",
        "evidence_company_number": "12345678", "company_name": "THE CAFE LTD", "company_status": "active",
        "incorporation_date": "2026-08-01", "sic_codes": '["56101"]',
        "previous_business_name": None, "previous_fhrsid": None, "previous_last_seen": None,
    }
    base.update(overrides)
    return base


def test_build_row_has_every_export_field():
    row = build_row(make_full_record())
    assert set(row.keys()) == set(EXPORT_FIELDNAMES)


def test_build_row_matched_record():
    row = build_row(make_full_record())
    assert row["match_status"] == "Yes"
    assert row["company_name"] == "THE CAFE LTD"
    assert row["company_number"] == "12345678"
    assert row["incorporation_date"] == "2026-08-01"
    assert row["sic_codes"] == "56101"
    assert row["companies_house_url"] != ""
    assert row["reason"] != row["reason_detail"]
    assert row["reason_detail"] == "diagnostic text"


def test_build_row_unmatched_record_blank_company_fields():
    row = build_row(make_full_record(
        post_code=None, postcode_from_live_api="NG17", postcode_source="live_api",
        evidence_company_number=None, company_name=None, company_status=None, incorporation_date=None,
        sic_codes=None,
    ))
    assert row["match_status"] == "No"
    assert row["company_number"] == ""
    assert row["company_name"] == ""
    assert row["incorporation_date"] == ""
    assert row["sic_codes"] == ""
    assert row["companies_house_url"] == ""
    assert row["postcode"] == "NG17"
    assert row["address_completeness"] == "district_only"


def test_build_row_operator_change_includes_previous_fields():
    row = build_row(make_full_record(
        classification="OPERATOR_CHANGE",
        previous_business_name="Old Tenant", previous_fhrsid=999, previous_last_seen="2026-08-15",
    ))
    assert row["previous_business_name"] == "Old Tenant"
    assert row["previous_fhrsid"] == 999
    assert row["previous_last_seen"] == "2026-08-15"


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


def setup_insert_event(conn, fhrsid, business_name="Test Cafe", quarantined=False, collection_date="2026-08-25",
                        classification="NEW_VENUE", company_number=None, predecessor_fhrsid=None):
    db.upsert_authorities(conn, [make_authority()], collection_date)
    db.record_collection_run(
        conn, authority_code=AUTHORITY, collection_date=collection_date, extract_date=collection_date,
        item_count=1, return_code="Success", raw_file_path="x", status="ok", parsed_at=collection_date + "T00:00:00Z",
    )
    db.ingest_establishments(conn, AUTHORITY, collection_date, [
        make_establishment(fhrsid, business_name=business_name, first_seen=collection_date),
    ])
    conn.execute(
        "INSERT INTO diff_events (authority_code, collection_date, fhrsid, event_type, quarantined) "
        "VALUES (?, ?, ?, 'INSERT', ?)",
        (AUTHORITY, collection_date, fhrsid, int(quarantined)),
    )
    result = Classification(
        classification=classification, confidence="HIGH", reason="x", evidence_company_number=company_number,
        evidence_predecessor_fhrsid=predecessor_fhrsid,
    )
    db.record_classification(conn, fhrsid, AUTHORITY, collection_date, result, collection_date + "T00:00:00Z")
    conn.commit()


def test_fetch_records_excludes_quarantined(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    setup_insert_event(conn, 1, quarantined=False)
    setup_insert_event(conn, 2, quarantined=True)

    records = fetch_records(conn)

    assert [r["fhrsid"] for r in records] == [1]


def test_fetch_records_excludes_closed_businesses(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    setup_insert_event(conn, 1, business_name="The Cafe")
    setup_insert_event(conn, 2, business_name="Old Diner (CLOSED)")

    records = fetch_records(conn)

    assert [r["fhrsid"] for r in records] == [1]


def test_fetch_records_filters_by_date_range(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    setup_insert_event(conn, 1, collection_date="2026-08-20")
    setup_insert_event(conn, 2, collection_date="2026-09-01")

    records = fetch_records(conn, since="2026-08-25")

    assert [r["fhrsid"] for r in records] == [2]


def test_fetch_records_joins_company_name_and_incorporation_date(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_companies(conn, "2026-08-25", [make_company()])
    setup_insert_event(conn, 1, company_number="12345678")

    records = fetch_records(conn)

    assert records[0]["company_name"] == "Test Cafe Ltd"
    assert records[0]["incorporation_date"] == "2026-08-01"


def test_fetch_records_unmatched_has_no_company_fields(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    setup_insert_event(conn, 1, company_number=None)

    records = fetch_records(conn)

    assert records[0]["company_name"] is None
    assert records[0]["incorporation_date"] is None


def test_fetch_records_joins_predecessor_for_operator_change(tmp_path):
    """establishments_current never deletes a departed predecessor's row
    (confirmed 2026-09-11), so this join always resolves for a real
    OPERATOR_CHANGE."""
    conn = db.connect(tmp_path / "t.db")
    setup_insert_event(conn, 1, business_name="Old Tenant", collection_date="2026-08-20")
    setup_insert_event(conn, 2, business_name="New Tenant", collection_date="2026-08-25",
                        classification="OPERATOR_CHANGE", predecessor_fhrsid=1)

    records = fetch_records(conn)
    row = next(r for r in records if r["fhrsid"] == 2)

    assert row["previous_business_name"] == "Old Tenant"
    assert row["previous_fhrsid"] == 1


def test_fetch_records_authority_extract_date_is_most_recent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    setup_insert_event(conn, 1, collection_date="2026-08-25")

    records = fetch_records(conn)

    assert records[0]["authority_extract_date"] == "2026-08-25"
