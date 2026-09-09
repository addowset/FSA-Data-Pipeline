from types import SimpleNamespace

from fsa_pipeline import db


def make_est(fhrsid, name="Some Venue", post_code="NG17 3GA", first_seen="2026-08-25"):
    return {
        "fhrsid": fhrsid, "local_authority_business_id": "LA-1", "business_name": name,
        "business_type": "Restaurant", "business_type_id": 1, "address_line_1": "1 High Street",
        "address_line_2": None, "address_line_3": None, "address_line_4": None, "post_code": post_code,
        "rating_value": "4", "rating_value_numeric": 4, "rating_key": "fhrs_4_en-GB",
        "rating_date": "2026-01-01", "scheme_type": "FHRS", "new_rating_pending": False,
        "hygiene_score": 5, "structural_score": 5, "confidence_in_management_score": 5,
        "longitude": None, "latitude": None,
    }


def make_result(classification="OPERATOR_CHANGE", evidence_company_number="12345678"):
    return SimpleNamespace(
        classification=classification, confidence="HIGH", reason="test reason",
        evidence_company_number=evidence_company_number, evidence_predecessor_fhrsid=None,
        evidence_existing_operator_fhrsid=None, recently_incorporated=None,
        predecessor_name_match=None,
    )


def test_get_operator_changes_needing_officer_check_finds_unchecked(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_establishments(conn, "857", "2026-08-25", [make_est(1)])
    db.record_classification(conn, 1, "857", "2026-08-25", make_result(), "2026-09-08T00:00:00Z")

    targets = db.get_operator_changes_needing_officer_check(conn)
    assert targets == [(1, "12345678", "2026-08-25")]


def test_get_operator_changes_needing_officer_check_skips_already_checked(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_establishments(conn, "857", "2026-08-25", [make_est(1)])
    db.record_classification(conn, 1, "857", "2026-08-25", make_result(), "2026-09-08T00:00:00Z")
    db.record_officer_churn_check(
        conn, 1, "12345678",
        {"officer_count": 2, "appointed_near_event": True, "resigned_near_event": False},
        60, "2026-09-08T00:00:00Z",
    )

    targets = db.get_operator_changes_needing_officer_check(conn)
    assert targets == []


def test_get_operator_changes_needing_officer_check_skips_no_company_evidence(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_establishments(conn, "857", "2026-08-25", [make_est(1)])
    db.record_classification(conn, 1, "857", "2026-08-25", make_result(evidence_company_number=None), "2026-09-08T00:00:00Z")

    assert db.get_operator_changes_needing_officer_check(conn) == []


def test_get_operator_changes_needing_officer_check_skips_non_operator_change(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_establishments(conn, "857", "2026-08-25", [make_est(1)])
    db.record_classification(conn, 1, "857", "2026-08-25", make_result(classification="NEW_VENUE"), "2026-09-08T00:00:00Z")

    assert db.get_operator_changes_needing_officer_check(conn) == []


def test_record_officer_churn_check_persists_aggregate_only(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    signal = {"officer_count": 3, "appointed_near_event": True, "resigned_near_event": False}

    db.record_officer_churn_check(conn, 1, "12345678", signal, 60, "2026-09-08T00:00:00Z")

    row = conn.execute(
        "SELECT officer_count, appointed_near_event, resigned_near_event, window_days FROM officer_churn_checks "
        "WHERE fhrsid = 1 AND company_number = '12345678'"
    ).fetchone()
    assert row == (3, 1, 0, 60)

    columns = {r[1] for r in conn.execute("PRAGMA table_info(officer_churn_checks)")}
    assert columns == {"id", "company_number", "fhrsid", "checked_at", "officer_count",
                        "appointed_near_event", "resigned_near_event", "window_days"}


def test_record_officer_churn_check_upserts_on_rerun(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.record_officer_churn_check(
        conn, 1, "12345678", {"officer_count": 1, "appointed_near_event": False, "resigned_near_event": False},
        60, "2026-09-08T00:00:00Z",
    )
    db.record_officer_churn_check(
        conn, 1, "12345678", {"officer_count": 2, "appointed_near_event": True, "resigned_near_event": False},
        60, "2026-09-09T00:00:00Z",
    )

    count = conn.execute("SELECT COUNT(*) FROM officer_churn_checks").fetchone()[0]
    assert count == 1
    row = conn.execute("SELECT officer_count, appointed_near_event FROM officer_churn_checks").fetchone()
    assert row == (2, 1)
