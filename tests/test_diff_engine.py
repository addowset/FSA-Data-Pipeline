import dataclasses
from pathlib import Path

from fsa_pipeline import db
from fsa_pipeline.config import Config
from fsa_pipeline.diff_engine import compute_diff_for_authority, record_diff

AUTHORITY = "857"


def make_config(**overrides) -> Config:
    base = Config(
        authorities_url="https://example.invalid",
        raw_dir=Path("raw/fhrs"),
        log_dir=Path("logs"),
        request_delay_seconds=0.0,
        timeout_seconds=1.0,
        max_retries=1,
        backoff_factor=1.0,
        contact_email="test@example.invalid",
        db_path=Path("unused.db"),
        reupload_ratio_threshold=3.0,
        reupload_ratio_min_floor=10,
        reupload_absolute_threshold=150,
        reupload_min_history_days=5,
        median_window_days=28,
        ch_advanced_search_url="https://example.invalid/advanced-search/companies",
        ch_raw_dir=Path("raw/companies-house"),
        ch_sic_codes=["56101"],
        ch_incorporated_window_days=14,
        ch_page_size=500,
        ch_request_delay_seconds=0.0,
        ch_timeout_seconds=1.0,
        ch_max_retries=1,
        ch_backoff_factor=1.0,
        high_density_address_threshold=5,
        candidates_per_match=5,
        national_match_threshold=0.9,
        live_establishments_url="https://example.invalid/Establishments",
        live_raw_dir=Path("raw/fhrs-live"),
        live_page_size=5000,
        live_request_delay_seconds=0.0,
        live_timeout_seconds=1.0,
        live_max_retries=1,
        live_backoff_factor=1.0,
        postcode_recheck_after_days=30,
        new_venue_high_threshold=0.85,
        new_venue_medium_threshold=0.6,
        new_venue_max_incorporation_age_days=180,
        address_history_fallback_threshold=0.9,
        multi_venue_company_threshold=5,
        officer_churn_enabled=False, officer_churn_window_days=60, officer_churn_request_delay_seconds=0.0,
        operator_search_recheck_after_days=30,
        monitoring_staleness_ratio=3.0, monitoring_staleness_min_age_days=10,
        monitoring_staleness_fallback_absolute_days=30, monitoring_cadence_min_history=3,
        monitoring_record_count_deviation_ratio=0.5, monitoring_record_count_median_window_days=28,
        monitoring_record_count_min_history_days=5, monitoring_record_count_min_floor=10,
        monitoring_max_skipped_record_ratio=0.01,
    )
    return dataclasses.replace(base, **overrides)


def make_establishment(fhrsid, rating_value="4"):
    return {
        "fhrsid": fhrsid,
        "local_authority_business_id": "LA-1",
        "business_name": f"Establishment {fhrsid}",
        "business_type": "Restaurant/Cafe/Canteen",
        "business_type_id": 1,
        "address_line_1": "1 High Street",
        "address_line_2": None,
        "address_line_3": None,
        "address_line_4": None,
        "post_code": "BS1 1AA",
        "rating_value": rating_value,
        "rating_value_numeric": int(rating_value) if rating_value.isdigit() else None,
        "rating_key": f"fhrs_{rating_value}_en-GB",
        "rating_date": "2026-01-01",
        "scheme_type": "FHRS",
        "new_rating_pending": False,
        "hygiene_score": 5,
        "structural_score": 5,
        "confidence_in_management_score": 5,
        "longitude": None,
        "latitude": None,
    }


def collect_and_parse(conn, authority_code, date_str, establishments, status="ok"):
    db.record_collection_run(
        conn, authority_code=authority_code, collection_date=date_str,
        extract_date=date_str, item_count=len(establishments), return_code="Success",
        raw_file_path="x", status=status, parsed_at=date_str + "T00:00:00Z",
    )
    if status == "ok":
        db.ingest_establishments(conn, authority_code, date_str, establishments)


def test_bootstrap_day_has_no_diff(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    collect_and_parse(conn, AUTHORITY, "2026-08-20", [make_establishment(1)])

    result = compute_diff_for_authority(conn, AUTHORITY, "2026-08-20", make_config())

    assert result.previous_collection_date is None
    assert result.insert_count == 0
    assert result.update_count == 0
    assert result.delete_count == 0


def test_insert_detected_on_second_day(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    collect_and_parse(conn, AUTHORITY, "2026-08-20", [make_establishment(1)])
    collect_and_parse(conn, AUTHORITY, "2026-08-21", [make_establishment(1), make_establishment(2)])

    result = compute_diff_for_authority(conn, AUTHORITY, "2026-08-21", make_config())

    assert result.previous_collection_date == "2026-08-20"
    assert result.insert_fhrsids == [2]
    assert result.update_count == 0
    assert result.delete_count == 0


def test_update_detected(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    collect_and_parse(conn, AUTHORITY, "2026-08-20", [make_establishment(1, rating_value="0")])
    collect_and_parse(conn, AUTHORITY, "2026-08-21", [make_establishment(1, rating_value="5")])

    result = compute_diff_for_authority(conn, AUTHORITY, "2026-08-21", make_config())

    assert result.update_fhrsids == [1]
    assert result.insert_count == 0
    assert result.delete_count == 0


def test_delete_detected_when_establishment_stops_appearing(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    collect_and_parse(conn, AUTHORITY, "2026-08-20", [make_establishment(1)])
    collect_and_parse(conn, AUTHORITY, "2026-08-21", [])  # establishment 1 no longer present

    result = compute_diff_for_authority(conn, AUTHORITY, "2026-08-21", make_config())

    assert result.delete_fhrsids == [1]
    assert result.insert_count == 0
    assert result.update_count == 0


def test_delete_not_repeated_on_subsequent_days(tmp_path):
    """Once an establishment is flagged DELETE, it shouldn't show up as
    DELETE again on every later day just because it's still absent."""
    conn = db.connect(tmp_path / "t.db")
    collect_and_parse(conn, AUTHORITY, "2026-08-20", [make_establishment(1)])
    collect_and_parse(conn, AUTHORITY, "2026-08-21", [])
    collect_and_parse(conn, AUTHORITY, "2026-08-22", [])

    result_day2 = compute_diff_for_authority(conn, AUTHORITY, "2026-08-21", make_config())
    result_day3 = compute_diff_for_authority(conn, AUTHORITY, "2026-08-22", make_config())

    assert result_day2.delete_fhrsids == [1]
    assert result_day3.delete_fhrsids == []


def test_previous_date_skips_failed_collection_days(tmp_path):
    """If a day's collection failed outright (no successful run recorded),
    the diff should compare against the last *successful* day, not treat
    the gap as mass churn."""
    conn = db.connect(tmp_path / "t.db")
    collect_and_parse(conn, AUTHORITY, "2026-08-20", [make_establishment(1)])
    collect_and_parse(conn, AUTHORITY, "2026-08-21", [], status="parse_error")
    collect_and_parse(conn, AUTHORITY, "2026-08-22", [make_establishment(1)])  # unchanged from day1

    result = compute_diff_for_authority(conn, AUTHORITY, "2026-08-22", make_config())

    assert result.previous_collection_date == "2026-08-20"
    assert result.insert_count == 0
    assert result.update_count == 0
    assert result.delete_count == 0


def test_reupload_guard_trips_on_absolute_threshold(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    collect_and_parse(conn, AUTHORITY, "2026-08-20", [])
    collect_and_parse(conn, AUTHORITY, "2026-08-21", [make_establishment(i) for i in range(1, 11)])

    result = compute_diff_for_authority(conn, AUTHORITY, "2026-08-21", make_config(reupload_absolute_threshold=5))

    assert result.quarantined
    assert "absolute threshold" in result.quarantine_reason


def test_reupload_guard_does_not_trip_under_thresholds(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    collect_and_parse(conn, AUTHORITY, "2026-08-20", [])
    collect_and_parse(conn, AUTHORITY, "2026-08-21", [make_establishment(i) for i in range(1, 4)])

    result = compute_diff_for_authority(conn, AUTHORITY, "2026-08-21", make_config())

    assert not result.quarantined
    assert result.quarantine_reason is None


def test_reupload_guard_trips_on_ratio_with_enough_history(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    config = make_config(reupload_min_history_days=3, reupload_ratio_min_floor=5, reupload_absolute_threshold=1000)

    # Five prior days with a stable low insert count of 2.
    dates = ["2026-08-15", "2026-08-16", "2026-08-17", "2026-08-18", "2026-08-19"]
    next_id = 1
    collect_and_parse(conn, AUTHORITY, dates[0], [])
    for d in dates[1:]:
        ests = [make_establishment(next_id + i) for i in range(2)]
        next_id += 2
        collect_and_parse(conn, AUTHORITY, d, ests)
        result = compute_diff_for_authority(conn, AUTHORITY, d, config)
        record_diff(conn, AUTHORITY, d, result, d + "T00:00:00Z")

    # Now a day with a spike well above 3x the trailing median of 2.
    spike_ests = [make_establishment(next_id + i) for i in range(30)]
    collect_and_parse(conn, AUTHORITY, "2026-08-20", spike_ests)
    result = compute_diff_for_authority(conn, AUTHORITY, "2026-08-20", config)

    assert result.quarantined
    assert "trailing median" in result.quarantine_reason


def test_reupload_guard_ignores_ratio_below_floor(tmp_path):
    """A jump from 0 to 4 new registrations is trivially normal, even
    though it may technically exceed 3x a near-zero trailing median."""
    conn = db.connect(tmp_path / "t.db")
    config = make_config(reupload_min_history_days=1, reupload_ratio_min_floor=10, reupload_absolute_threshold=1000)

    collect_and_parse(conn, AUTHORITY, "2026-08-19", [])
    collect_and_parse(conn, AUTHORITY, "2026-08-20", [])
    result = compute_diff_for_authority(conn, AUTHORITY, "2026-08-20", config)
    record_diff(conn, AUTHORITY, "2026-08-20", result, "2026-08-20T00:00:00Z")

    collect_and_parse(conn, AUTHORITY, "2026-08-21", [make_establishment(i) for i in range(1, 5)])
    result = compute_diff_for_authority(conn, AUTHORITY, "2026-08-21", config)

    assert not result.quarantined


def test_record_diff_writes_events_and_is_overwritable(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    collect_and_parse(conn, AUTHORITY, "2026-08-20", [make_establishment(1)])
    collect_and_parse(conn, AUTHORITY, "2026-08-21", [make_establishment(1), make_establishment(2)])

    result = compute_diff_for_authority(conn, AUTHORITY, "2026-08-21", make_config())
    record_diff(conn, AUTHORITY, "2026-08-21", result, "2026-08-21T00:00:00Z")

    events = conn.execute(
        "SELECT event_type, fhrsid FROM diff_events WHERE authority_code = ? AND collection_date = ?",
        (AUTHORITY, "2026-08-21"),
    ).fetchall()
    assert events == [("INSERT", 2)]

    run_row = conn.execute(
        "SELECT insert_count, update_count, delete_count FROM diff_runs WHERE authority_code = ? AND collection_date = ?",
        (AUTHORITY, "2026-08-21"),
    ).fetchone()
    assert run_row == (1, 0, 0)

    # Re-recording (as --force would) should replace, not duplicate.
    record_diff(conn, AUTHORITY, "2026-08-21", result, "2026-08-21T00:01:00Z")
    count = conn.execute(
        "SELECT COUNT(*) FROM diff_events WHERE authority_code = ? AND collection_date = ?",
        (AUTHORITY, "2026-08-21"),
    ).fetchone()[0]
    assert count == 1
