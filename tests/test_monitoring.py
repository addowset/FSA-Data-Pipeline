import dataclasses
from pathlib import Path

from fsa_pipeline import db
from fsa_pipeline.config import Config
from fsa_pipeline.fhrs_bulk import Authority
from fsa_pipeline.monitoring import (
    check_canaries,
    check_companies_house_run,
    check_extract_date_staleness,
    check_missing_authorities,
    check_parse_failures,
    check_record_count_deviation,
    classification_ratio,
    extract_date_ages,
    extract_date_cadence,
    new_records_per_authority,
    parse_failure_rate,
)

AUTHORITY = "857"


def make_config(**overrides) -> Config:
    base = Config(
        authorities_url="https://example.invalid", raw_dir=Path("raw/fhrs"), log_dir=Path("logs"),
        request_delay_seconds=0.0, timeout_seconds=1.0, max_retries=1, backoff_factor=1.0,
        contact_email="test@example.invalid", db_path=Path("unused.db"),
        reupload_ratio_threshold=3.0, reupload_ratio_min_floor=10, reupload_absolute_threshold=150,
        reupload_min_history_days=5, median_window_days=28,
        ch_advanced_search_url="https://example.invalid/advanced-search/companies",
        ch_raw_dir=Path("raw/companies-house"), ch_sic_codes=["56101"], ch_incorporated_window_days=14,
        ch_page_size=500, ch_request_delay_seconds=0.0, ch_timeout_seconds=1.0, ch_max_retries=1,
        ch_backoff_factor=1.0, high_density_address_threshold=5, candidates_per_match=5,
        national_match_threshold=0.9,
        live_establishments_url="https://example.invalid/Establishments", live_raw_dir=Path("raw/fhrs-live"),
        live_page_size=5000, live_request_delay_seconds=0.0, live_timeout_seconds=1.0, live_max_retries=1,
        live_backoff_factor=1.0, postcode_recheck_after_days=30,
        new_venue_high_threshold=0.85, new_venue_medium_threshold=0.6,
        new_venue_max_incorporation_age_days=180, address_history_fallback_threshold=0.9,
        multi_venue_company_threshold=5,
        officer_churn_enabled=False, officer_churn_window_days=60, officer_churn_request_delay_seconds=0.0,
        operator_search_recheck_after_days=30,
        monitoring_staleness_ratio=3.0, monitoring_staleness_min_age_days=10,
        monitoring_staleness_fallback_absolute_days=30, monitoring_cadence_min_history=3,
        monitoring_record_count_deviation_ratio=0.5, monitoring_record_count_median_window_days=28,
        monitoring_record_count_min_history_days=5, monitoring_record_count_min_floor=10,
        monitoring_max_skipped_record_ratio=0.01,
        export_output_dir=Path("exports"),
    )
    return dataclasses.replace(base, **overrides)


def make_authority(code=AUTHORITY, name="Test Authority"):
    return Authority(
        local_authority_id=1, code=code, name=name, friendly_name=name,
        region_name="Test Region", file_url="https://example.invalid/x.xml", establishment_count=1,
    )


def record_run(conn, authority_code, date_str, extract_date, item_count=100, status="ok", skipped_records=0, error_message=None):
    db.record_collection_run(
        conn, authority_code=authority_code, collection_date=date_str,
        extract_date=extract_date, item_count=item_count, return_code="Success",
        raw_file_path="x", status=status, skipped_records=skipped_records,
        error_message=error_message, parsed_at=date_str + "T00:00:00Z",
    )


def record_diff_run(conn, authority_code, date_str, insert_count, quarantined=False):
    conn.execute(
        "INSERT INTO diff_runs (authority_code, collection_date, insert_count, update_count, delete_count, "
        "quarantined, computed_at) VALUES (?, ?, ?, 0, 0, ?, ?)",
        (authority_code, date_str, insert_count, int(quarantined), date_str + "T00:00:00Z"),
    )
    conn.commit()


def record_classification_row(conn, fhrsid, insert_date, classification, confidence):
    conn.execute(
        "INSERT INTO classifications (fhrsid, authority_code, insert_collection_date, classification, "
        "confidence, reason, classified_at) VALUES (?, ?, ?, ?, ?, 'x', ?)",
        (fhrsid, AUTHORITY, insert_date, classification, confidence, insert_date + "T00:00:00Z"),
    )
    conn.commit()


# --- metrics ---

def test_new_records_per_authority_sums_insert_counts(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    record_diff_run(conn, AUTHORITY, "2026-09-01", 3)
    record_diff_run(conn, AUTHORITY, "2026-09-02", 5)

    result = new_records_per_authority(conn, "2026-09-01", "2026-09-02")

    assert result == {AUTHORITY: 8}


def test_new_records_per_authority_excludes_quarantined_days(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    record_diff_run(conn, AUTHORITY, "2026-09-01", 3)
    record_diff_run(conn, AUTHORITY, "2026-09-02", 500, quarantined=True)

    result = new_records_per_authority(conn, "2026-09-01", "2026-09-02")

    assert result == {AUTHORITY: 3}


def test_classification_ratio_counts_by_class_and_confidence(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    record_classification_row(conn, 1, "2026-09-01", "NEW_VENUE", "HIGH")
    record_classification_row(conn, 2, "2026-09-01", "NEW_VENUE", "HIGH")
    record_classification_row(conn, 3, "2026-09-01", "UNKNOWN", "LOW")

    result = classification_ratio(conn, "2026-09-01", "2026-09-01")

    assert result == {"NEW_VENUE/HIGH": 2, "UNKNOWN/LOW": 1}


def test_extract_date_ages_computes_days_since_latest(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_authorities(conn, [make_authority()], "2026-09-01")
    record_run(conn, AUTHORITY, "2026-09-01", extract_date="2026-08-25")

    result = extract_date_ages(conn, "2026-09-01")

    assert result == [(AUTHORITY, "Test Authority", 7)]


def test_extract_date_ages_none_when_never_collected(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_authorities(conn, [make_authority()], "2026-09-01")

    result = extract_date_ages(conn, "2026-09-01")

    assert result == [(AUTHORITY, "Test Authority", None)]


def test_parse_failure_rate_counts_failed_and_attempted(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    record_run(conn, "1", "2026-09-01", extract_date="2026-09-01")
    record_run(conn, "2", "2026-09-01", extract_date=None, status="parse_error", error_message="boom")

    failed, attempted = parse_failure_rate(conn, "2026-09-01")

    assert (failed, attempted) == (1, 2)


# --- extract_date_cadence ---

def test_extract_date_cadence_median_of_gaps(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    for i, d in enumerate(["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"]):
        record_run(conn, AUTHORITY, d, extract_date=d)  # same-day extract, gaps: 1,3,1

    assert extract_date_cadence(conn, AUTHORITY) == 1


def test_extract_date_cadence_none_with_fewer_than_two_dates(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    record_run(conn, AUTHORITY, "2026-08-20", extract_date="2026-08-20")

    assert extract_date_cadence(conn, AUTHORITY) is None


# --- check_extract_date_staleness ---

def test_staleness_fires_when_age_exceeds_ratio_times_cadence(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    # Cadence of 1 day over several refreshes, then a long gap.
    for d in ["2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23"]:
        record_run(conn, AUTHORITY, d, extract_date=d)
    record_run(conn, AUTHORITY, "2026-09-05", extract_date="2026-08-23")  # 13 days since last extract

    fired, reason = check_extract_date_staleness(conn, AUTHORITY, "2026-09-05", make_config())

    assert fired is True
    assert "exceeds 3.0x" in reason


def test_staleness_does_not_fire_within_normal_cadence(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    for d in ["2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23"]:
        record_run(conn, AUTHORITY, d, extract_date=d)

    fired, reason = check_extract_date_staleness(conn, AUTHORITY, "2026-08-23", make_config())

    assert fired is False
    assert reason is None


def test_staleness_respects_min_age_floor(tmp_path):
    """A high ratio against a tiny cadence (e.g. 4x a 1-day cadence = 4
    days) shouldn't fire if the absolute gap is still trivial."""
    conn = db.connect(tmp_path / "t.db")
    for d in ["2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23"]:
        record_run(conn, AUTHORITY, d, extract_date=d)
    record_run(conn, AUTHORITY, "2026-08-27", extract_date="2026-08-23")  # 4 days -- under the 10d floor

    fired, reason = check_extract_date_staleness(conn, AUTHORITY, "2026-08-27", make_config())

    assert fired is False


def test_staleness_uses_absolute_fallback_with_insufficient_history(tmp_path):
    """Real case (2026-09-11): River Tees had shown only ONE distinct
    ExtractDate ever, 141 days stale -- the ratio check can't compute a
    cadence at all with under cadence_min_history distinct dates, so
    without a fallback this would be silently invisible forever."""
    conn = db.connect(tmp_path / "t.db")
    record_run(conn, AUTHORITY, "2026-04-22", extract_date="2026-04-22")
    record_run(conn, AUTHORITY, "2026-09-10", extract_date="2026-04-22")  # only ONE distinct extract_date

    fired, reason = check_extract_date_staleness(conn, AUTHORITY, "2026-09-10", make_config())

    assert fired is True
    assert "absolute fallback" in reason


def test_staleness_fallback_does_not_fire_under_the_threshold(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    record_run(conn, AUTHORITY, "2026-08-20", extract_date="2026-08-20")
    record_run(conn, AUTHORITY, "2026-09-01", extract_date="2026-08-20")  # 12 days, under the 30d fallback

    fired, reason = check_extract_date_staleness(conn, AUTHORITY, "2026-09-01", make_config())

    assert fired is False


def test_staleness_none_when_never_collected(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    fired, reason = check_extract_date_staleness(conn, AUTHORITY, "2026-09-01", make_config())
    assert fired is False
    assert reason is None


# --- check_record_count_deviation ---

def test_record_count_deviation_fires_on_large_drop():
    fired, reason = check_record_count_deviation(40, [100] * 10, make_config())
    assert fired is True
    assert "below" in reason


def test_record_count_deviation_fires_on_large_spike():
    fired, reason = check_record_count_deviation(200, [100] * 10, make_config())
    assert fired is True
    assert "above" in reason


def test_record_count_deviation_not_fired_within_band():
    fired, reason = check_record_count_deviation(120, [100] * 10, make_config())
    assert fired is False


def test_record_count_deviation_respects_min_history():
    fired, reason = check_record_count_deviation(1000, [100] * 2, make_config())
    assert fired is False  # only 2 prior days, below monitoring_record_count_min_history_days=5


def test_record_count_deviation_respects_min_floor():
    fired, reason = check_record_count_deviation(5, [1] * 10, make_config())
    assert fired is False  # item_count=5 is below monitoring_record_count_min_floor=10


def test_record_count_deviation_none_item_count_is_a_noop():
    fired, reason = check_record_count_deviation(None, [100] * 10, make_config())
    assert fired is False


# --- check_missing_authorities ---

def test_missing_authorities_detects_authority_with_no_row(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_authorities(conn, [make_authority("1", "Collected"), make_authority("2", "Missing")], "2026-09-01")
    record_run(conn, "1", "2026-09-01", extract_date="2026-09-01")

    missing = check_missing_authorities(conn, "2026-09-01")

    assert len(missing) == 1
    assert missing[0][0] == "2"
    assert "never fetched" in missing[0][2]


def test_missing_authorities_uses_manifest_reason_when_available(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_authorities(conn, [make_authority("2", "Missing")], "2026-09-01")
    manifest = {"failed": [{"code": "2", "name": "Missing", "error": "HTTP 500"}]}

    missing = check_missing_authorities(conn, "2026-09-01", manifest)

    assert missing == [("2", "Missing", "collection failed: HTTP 500")]


def test_missing_authorities_empty_when_all_collected(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_authorities(conn, [make_authority()], "2026-09-01")
    record_run(conn, AUTHORITY, "2026-09-01", extract_date="2026-09-01")

    assert check_missing_authorities(conn, "2026-09-01") == []


# --- check_parse_failures ---

def test_parse_failures_flags_parse_error_status(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    record_run(conn, AUTHORITY, "2026-09-01", extract_date=None, status="parse_error", error_message="bad xml")

    alerts = check_parse_failures(conn, "2026-09-01", make_config())

    assert len(alerts) == 1
    assert "bad xml" in alerts[0]


def test_parse_failures_flags_high_skipped_ratio(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    record_run(conn, AUTHORITY, "2026-09-01", extract_date="2026-09-01", item_count=100, skipped_records=5)

    alerts = check_parse_failures(conn, "2026-09-01", make_config())  # threshold is 1%

    assert len(alerts) == 1
    assert "5/100" in alerts[0]


def test_parse_failures_silent_on_clean_run(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    record_run(conn, AUTHORITY, "2026-09-01", extract_date="2026-09-01", item_count=100, skipped_records=0)

    assert check_parse_failures(conn, "2026-09-01", make_config()) == []


# --- check_companies_house_run ---

def test_companies_house_run_missing_row(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    reason = check_companies_house_run(conn, "2026-09-01")
    assert "no companies_house_runs row" in reason


def test_companies_house_run_ok_is_silent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO companies_house_runs (collection_date, hits, pages_parsed, status, parsed_at) "
        "VALUES (?, 10, 1, 'ok', ?)",
        ("2026-09-01", "2026-09-01T00:00:00Z"),
    )
    conn.commit()

    assert check_companies_house_run(conn, "2026-09-01") is None


def test_companies_house_run_flags_bad_status(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO companies_house_runs (collection_date, status, error_message, parsed_at) "
        "VALUES (?, 'error', 'timeout', ?)",
        ("2026-09-01", "2026-09-01T00:00:00Z"),
    )
    conn.commit()

    reason = check_companies_house_run(conn, "2026-09-01")
    assert "timeout" in reason


# --- check_canaries ---

def test_canaries_silent_when_all_match(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    from fsa_pipeline.canaries import CANARIES
    for canary in CANARIES:
        db.upsert_authorities(conn, [make_authority(canary.authority_code, "X")], "2026-09-01")
        db.ingest_establishments(conn, canary.authority_code, "2026-09-01", [{
            "fhrsid": canary.fhrsid, "local_authority_business_id": canary.expected_local_authority_business_id,
            "business_name": canary.expected_business_name, "business_type": "X", "business_type_id": 1,
            "address_line_1": None, "address_line_2": None, "address_line_3": None, "address_line_4": None,
            "post_code": None, "rating_value": "5", "rating_value_numeric": 5, "rating_key": "fhrs_5_en-GB",
            "rating_date": "2026-01-01", "scheme_type": "FHRS", "new_rating_pending": False,
            "hygiene_score": None, "structural_score": None, "confidence_in_management_score": None,
            "longitude": None, "latitude": None,
        }])

    assert check_canaries(conn) == []


def test_canaries_flags_missing_fhrsid(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    alerts = check_canaries(conn)  # empty DB -- every canary is missing
    assert len(alerts) > 0
    assert all("MISSING" in a for a in alerts)


def test_canaries_flags_business_name_mismatch(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    from fsa_pipeline.canaries import CANARIES
    canary = CANARIES[0]
    db.upsert_authorities(conn, [make_authority(canary.authority_code, "X")], "2026-09-01")
    db.ingest_establishments(conn, canary.authority_code, "2026-09-01", [{
        "fhrsid": canary.fhrsid, "local_authority_business_id": canary.expected_local_authority_business_id,
        "business_name": "SOMETHING ELSE ENTIRELY", "business_type": "X", "business_type_id": 1,
        "address_line_1": None, "address_line_2": None, "address_line_3": None, "address_line_4": None,
        "post_code": None, "rating_value": "5", "rating_value_numeric": 5, "rating_key": "fhrs_5_en-GB",
        "rating_date": "2026-01-01", "scheme_type": "FHRS", "new_rating_pending": False,
        "hygiene_score": None, "structural_score": None, "confidence_in_management_score": None,
        "longitude": None, "latitude": None,
    }])

    alerts = check_canaries(conn)

    matching = [a for a in alerts if str(canary.fhrsid) in a]
    assert len(matching) == 1
    assert "business_name" in matching[0]
