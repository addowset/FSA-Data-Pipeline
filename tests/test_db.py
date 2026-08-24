from fsa_pipeline import db


def make_establishment(fhrsid, business_name="Test Cafe", rating_value="4"):
    return {
        "fhrsid": fhrsid,
        "local_authority_business_id": "LA-1",
        "business_name": business_name,
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
        "longitude": -2.5,
        "latitude": 51.4,
    }


def test_ingest_new_establishment_is_first_seen(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    stats = db.ingest_establishments(conn, "857", "2026-08-20", [make_establishment(1)])

    assert stats.first_seen == 1
    assert stats.changed == 0
    assert stats.unchanged == 0

    row = conn.execute("SELECT business_name, first_seen_date, last_seen_date FROM establishments_current WHERE fhrsid = 1").fetchone()
    assert row == ("Test Cafe", "2026-08-20", "2026-08-20")

    obs = conn.execute("SELECT change_type FROM observations WHERE fhrsid = 1").fetchall()
    assert obs == [("first_seen",)]


def test_ingest_unchanged_establishment_bumps_last_seen_but_no_observation(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    est = make_establishment(1)
    db.ingest_establishments(conn, "857", "2026-08-20", [est])

    stats = db.ingest_establishments(conn, "857", "2026-08-21", [est])

    assert stats.first_seen == 0
    assert stats.changed == 0
    assert stats.unchanged == 1

    row = conn.execute("SELECT first_seen_date, last_seen_date, last_changed_date FROM establishments_current WHERE fhrsid = 1").fetchone()
    assert row == ("2026-08-20", "2026-08-21", "2026-08-20")

    obs_count = conn.execute("SELECT COUNT(*) FROM observations WHERE fhrsid = 1").fetchone()[0]
    assert obs_count == 1  # only the original first_seen row -- no duplicate for the unchanged day


def test_ingest_changed_field_creates_new_observation_and_updates_current(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.ingest_establishments(conn, "857", "2026-08-20", [make_establishment(1, rating_value="0")])

    stats = db.ingest_establishments(conn, "857", "2026-08-21", [make_establishment(1, rating_value="5")])

    assert stats.changed == 1
    assert stats.unchanged == 0

    row = conn.execute("SELECT rating_value, last_changed_date FROM establishments_current WHERE fhrsid = 1").fetchone()
    assert row == ("5", "2026-08-21")

    obs = conn.execute("SELECT change_type, rating_value FROM observations WHERE fhrsid = 1 ORDER BY id").fetchall()
    assert obs == [("first_seen", "0"), ("field_changed", "5")]


def test_ingest_geocode_only_difference_is_not_a_change(tmp_path):
    """Geocode is excluded from the fingerprint (see fhrs_parse.py) because
    it flaps null<->value in the real data far more often than it reflects
    a genuine change. A geocode-only difference must count as unchanged,
    but establishments_current should still pick up the new coordinates."""
    conn = db.connect(tmp_path / "test.db")
    est_day1 = make_establishment(1)
    est_day1["longitude"], est_day1["latitude"] = None, None
    db.ingest_establishments(conn, "857", "2026-08-20", [est_day1])

    est_day2 = make_establishment(1)
    est_day2["longitude"], est_day2["latitude"] = -2.5, 51.4
    stats = db.ingest_establishments(conn, "857", "2026-08-21", [est_day2])

    assert stats.changed == 0
    assert stats.unchanged == 1

    row = conn.execute(
        "SELECT longitude, latitude, last_seen_date FROM establishments_current WHERE fhrsid = 1"
    ).fetchone()
    assert row == (-2.5, 51.4, "2026-08-21")

    obs_count = conn.execute("SELECT COUNT(*) FROM observations WHERE fhrsid = 1").fetchone()[0]
    assert obs_count == 1  # only the original first_seen row


def test_ingest_does_not_touch_establishments_from_other_authorities(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.ingest_establishments(conn, "857", "2026-08-20", [make_establishment(1)])
    db.ingest_establishments(conn, "760", "2026-08-20", [make_establishment(2)])

    count_857 = conn.execute("SELECT COUNT(*) FROM establishments_current WHERE authority_code = '857'").fetchone()[0]
    count_760 = conn.execute("SELECT COUNT(*) FROM establishments_current WHERE authority_code = '760'").fetchone()[0]
    assert count_857 == 1
    assert count_760 == 1


def test_collection_run_recorded_and_already_parsed_check(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    assert not db.already_parsed(conn, "857", "2026-08-20")

    db.record_collection_run(
        conn,
        authority_code="857",
        collection_date="2026-08-20",
        extract_date="2026-08-19",
        item_count=1841,
        return_code="Success",
        raw_file_path="fhrs/2026-08-20/857_bath.xml.gz",
        status="ok",
        parsed_at="2026-08-20T12:00:00Z",
    )

    assert db.already_parsed(conn, "857", "2026-08-20")
    assert not db.already_parsed(conn, "857", "2026-08-21")

    row = conn.execute("SELECT extract_date, item_count FROM collection_runs WHERE authority_code = '857'").fetchone()
    assert row == ("2026-08-19", 1841)
