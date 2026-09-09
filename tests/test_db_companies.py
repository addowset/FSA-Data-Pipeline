from fsa_pipeline import db


def make_company(number, status="active", name="Test Kitchen Ltd", source="bulk"):
    return {
        "company_number": number,
        "company_name": name,
        "company_status": status,
        "company_subtype": None,
        "company_type": "ltd",
        "date_of_creation": "2026-08-10",
        "date_of_cessation": None,
        "address_line_1": "1 High Street",
        "address_line_2": None,
        "locality": "Bristol",
        "region": "South West",
        "postal_code": "BS1 1AA",
        "country": "England",
        "sic_codes": '["56101"]',
        "source": source,
    }


def test_ingest_new_company_is_first_seen(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    stats = db.ingest_companies(conn, "2026-08-20", [make_company("12345678")])

    assert stats.first_seen == 1
    assert stats.changed == 0
    assert stats.unchanged == 0

    row = conn.execute(
        "SELECT company_name, first_seen_date, last_seen_date FROM companies_current WHERE company_number = '12345678'"
    ).fetchone()
    assert row == ("Test Kitchen Ltd", "2026-08-20", "2026-08-20")

    obs = conn.execute("SELECT change_type FROM company_observations WHERE company_number = '12345678'").fetchall()
    assert obs == [("first_seen",)]


def test_ingest_unchanged_company_bumps_last_seen_only(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    company = make_company("12345678")
    db.ingest_companies(conn, "2026-08-20", [company])

    stats = db.ingest_companies(conn, "2026-08-21", [company])

    assert stats.unchanged == 1
    row = conn.execute(
        "SELECT first_seen_date, last_seen_date, last_changed_date FROM companies_current WHERE company_number = '12345678'"
    ).fetchone()
    assert row == ("2026-08-20", "2026-08-21", "2026-08-20")

    count = conn.execute("SELECT COUNT(*) FROM company_observations WHERE company_number = '12345678'").fetchone()[0]
    assert count == 1


def test_ingest_status_change_creates_new_observation(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_companies(conn, "2026-08-20", [make_company("12345678", status="active")])

    stats = db.ingest_companies(conn, "2026-08-21", [make_company("12345678", status="dissolved")])

    assert stats.changed == 1
    row = conn.execute("SELECT company_status FROM companies_current WHERE company_number = '12345678'").fetchone()
    assert row == ("dissolved",)

    obs = conn.execute(
        "SELECT change_type, company_status FROM company_observations WHERE company_number = '12345678' ORDER BY id"
    ).fetchall()
    assert obs == [("first_seen", "active"), ("field_changed", "dissolved")]


def test_companies_house_run_tracking(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert not db.already_parsed_companies_house(conn, "2026-08-20")

    db.record_companies_house_run(
        conn, collection_date="2026-08-20", hits=5, pages_parsed=1,
        status="ok", parsed_at="2026-08-20T12:00:00Z",
    )

    assert db.already_parsed_companies_house(conn, "2026-08-20")
    assert not db.already_parsed_companies_house(conn, "2026-08-21")
