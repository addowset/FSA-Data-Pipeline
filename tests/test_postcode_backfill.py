import json

from fsa_pipeline import db
from fsa_pipeline.fhrs_live import meta_from_page_bytes, postcodes_from_page_bytes

AUTHORITY = "857"


def make_establishment(fhrsid, post_code="BS1 1AA", rating_value="4"):
    return {
        "fhrsid": fhrsid,
        "local_authority_business_id": "LA-1",
        "business_name": "Test Cafe",
        "business_type": "Restaurant/Cafe/Canteen",
        "business_type_id": 1,
        "address_line_1": "1 High Street",
        "address_line_2": None,
        "address_line_3": None,
        "address_line_4": None,
        "post_code": post_code,
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


# --- fhrs_live parsing ---

SAMPLE_LIVE_PAGE = json.dumps({
    "establishments": [
        {"FHRSID": 1, "PostCode": "NG17 3GA", "BusinessName": "A"},
        {"FHRSID": 2, "PostCode": "", "BusinessName": "B"},
        {"FHRSID": 3, "BusinessName": "C"},  # no PostCode key at all
    ],
    "meta": {"totalCount": 3, "totalPages": 1, "pageNumber": 1},
}).encode("utf-8")


def test_postcodes_from_page_bytes():
    result = postcodes_from_page_bytes(SAMPLE_LIVE_PAGE)
    assert result == {1: "NG17 3GA", 2: None, 3: None}


def test_meta_from_page_bytes():
    meta = meta_from_page_bytes(SAMPLE_LIVE_PAGE)
    assert meta["totalPages"] == 1
    assert meta["totalCount"] == 3


# --- db.py: schema migration ---

def test_ensure_columns_adds_missing_columns_idempotently(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY)")
    conn.commit()

    db._ensure_columns(conn, "demo", {"new_col": "TEXT"})
    cols = {row[1] for row in conn.execute("PRAGMA table_info(demo)")}
    assert "new_col" in cols

    db._ensure_columns(conn, "demo", {"new_col": "TEXT"})  # must not raise on rerun
    cols_after = {row[1] for row in conn.execute("PRAGMA table_info(demo)")}
    assert cols_after == cols


def test_connect_migrates_existing_database_missing_new_columns(tmp_path):
    """Simulates the real 2026-08-26 situation: a database created before
    the postcode-backfill columns existed."""
    import sqlite3
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE establishments_current (
            fhrsid INTEGER PRIMARY KEY,
            authority_code TEXT,
            post_code TEXT,
            fingerprint TEXT,
            first_seen_date TEXT,
            last_seen_date TEXT,
            last_changed_date TEXT
        );
        """
    )
    conn.commit()
    conn.close()

    conn = db.connect(db_path)  # should migrate, not error
    cols = {row[1] for row in conn.execute("PRAGMA table_info(establishments_current)")}
    assert {"postcode_source", "postcode_from_live_api", "postcode_backfill_checked_at"} <= cols


# --- db.py: postcode_source set correctly by normal bulk ingest ---

def test_ingest_sets_postcode_source_bulk_when_present(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_establishments(conn, AUTHORITY, "2026-08-20", [make_establishment(1, post_code="BS1 1AA")])

    row = conn.execute("SELECT post_code, postcode_source FROM establishments_current WHERE fhrsid=1").fetchone()
    assert row == ("BS1 1AA", "bulk")


def test_ingest_leaves_postcode_source_null_when_bulk_has_no_postcode(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_establishments(conn, AUTHORITY, "2026-08-20", [make_establishment(1, post_code=None)])

    row = conn.execute("SELECT post_code, postcode_source FROM establishments_current WHERE fhrsid=1").fetchone()
    assert row == (None, None)


def test_backfilled_postcode_survives_unchanged_bulk_ingest(tmp_path):
    """The core correctness requirement: a live-API backfill must not be
    wiped out by a later bulk ingest that still reports no postcode."""
    conn = db.connect(tmp_path / "t.db")
    est = make_establishment(1, post_code=None)
    db.ingest_establishments(conn, AUTHORITY, "2026-08-20", [est])

    db.apply_postcode_backfill(conn, 1, AUTHORITY, "NG17 3GA", "2026-08-21T00:00:00Z")
    row = conn.execute(
        "SELECT postcode_from_live_api, postcode_source FROM establishments_current WHERE fhrsid=1"
    ).fetchone()
    assert row == ("NG17 3GA", "live_api")

    # Next day's bulk ingest: same establishment, still no bulk postcode,
    # nothing else changed either -- fingerprint is identical, so this
    # takes the "unchanged" path.
    stats = db.ingest_establishments(conn, AUTHORITY, "2026-08-22", [est])
    assert stats.unchanged == 1

    row = conn.execute(
        "SELECT post_code, postcode_from_live_api, postcode_source FROM establishments_current WHERE fhrsid=1"
    ).fetchone()
    assert row == (None, "NG17 3GA", "live_api")  # backfill survived


def test_bulk_postcode_arriving_later_takes_precedence_and_is_a_real_field_changed_event(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_establishments(conn, AUTHORITY, "2026-08-20", [make_establishment(1, post_code=None)])
    db.apply_postcode_backfill(conn, 1, AUTHORITY, "NG17 3GA", "2026-08-21T00:00:00Z")

    # FSA's own bulk data now genuinely has a postcode -- a real change.
    stats = db.ingest_establishments(conn, AUTHORITY, "2026-08-22", [make_establishment(1, post_code="BS1 1AA")])
    assert stats.changed == 1

    row = conn.execute(
        "SELECT post_code, postcode_source FROM establishments_current WHERE fhrsid=1"
    ).fetchone()
    assert row == ("BS1 1AA", "bulk")  # bulk wins over the stale backfill

    obs = conn.execute(
        "SELECT change_type, post_code FROM observations WHERE fhrsid=1 ORDER BY id"
    ).fetchall()
    assert obs == [("first_seen", None), ("field_changed", "BS1 1AA")]


def test_apply_postcode_backfill_never_overwrites_a_real_bulk_postcode(tmp_path):
    """Defensive guard: even if called by mistake for a record that
    already has a bulk postcode, it must not clobber it."""
    conn = db.connect(tmp_path / "t.db")
    db.ingest_establishments(conn, AUTHORITY, "2026-08-20", [make_establishment(1, post_code="BS1 1AA")])

    db.apply_postcode_backfill(conn, 1, AUTHORITY, "WRONG POSTCODE", "2026-08-21T00:00:00Z")

    row = conn.execute(
        "SELECT post_code, postcode_from_live_api, postcode_source FROM establishments_current WHERE fhrsid=1"
    ).fetchone()
    assert row == ("BS1 1AA", None, "bulk")


def test_apply_postcode_backfill_unresolved_still_records_checked_at_and_event(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_establishments(conn, AUTHORITY, "2026-08-20", [make_establishment(1, post_code=None)])

    db.apply_postcode_backfill(conn, 1, AUTHORITY, None, "2026-08-21T00:00:00Z")

    row = conn.execute(
        "SELECT postcode_from_live_api, postcode_source, postcode_backfill_checked_at FROM establishments_current WHERE fhrsid=1"
    ).fetchone()
    assert row == (None, None, "2026-08-21T00:00:00Z")

    event = conn.execute(
        "SELECT resolved, postcode_value FROM postcode_backfill_events WHERE fhrsid=1"
    ).fetchone()
    assert event == (0, None)


def test_apply_postcode_backfill_resolved_records_event(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_establishments(conn, AUTHORITY, "2026-08-20", [make_establishment(1, post_code=None)])

    db.apply_postcode_backfill(conn, 1, AUTHORITY, "NG17 3GA", "2026-08-21T00:00:00Z")

    event = conn.execute(
        "SELECT resolved, postcode_value FROM postcode_backfill_events WHERE fhrsid=1"
    ).fetchone()
    assert event == (1, "NG17 3GA")


# --- db.py: candidate selection ---

def test_get_postcode_backfill_candidates_only_returns_missing_postcodes(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_establishments(conn, AUTHORITY, "2026-08-20", [
        make_establishment(1, post_code=None),
        make_establishment(2, post_code="BS1 1AA"),
    ])

    candidates = db.get_postcode_backfill_candidates(conn, recheck_after_days=30)
    assert candidates == [(1, AUTHORITY)]


def test_get_postcode_backfill_candidates_excludes_recently_checked(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_establishments(conn, AUTHORITY, "2026-08-20", [make_establishment(1, post_code=None)])
    db.apply_postcode_backfill(conn, 1, AUTHORITY, None, dt_now_iso())

    candidates = db.get_postcode_backfill_candidates(conn, recheck_after_days=30)
    assert candidates == []


def test_get_postcode_backfill_candidates_includes_stale_checks(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest_establishments(conn, AUTHORITY, "2026-08-20", [make_establishment(1, post_code=None)])
    old_timestamp = "2020-01-01T00:00:00Z"
    conn.execute("UPDATE establishments_current SET postcode_backfill_checked_at = ? WHERE fhrsid = 1", (old_timestamp,))
    conn.commit()

    candidates = db.get_postcode_backfill_candidates(conn, recheck_after_days=30)
    assert candidates == [(1, AUTHORITY)]


def dt_now_iso():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat()


# --- db.py: run tracking ---

def test_backfill_run_tracking(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert not db.already_backfilled(conn, AUTHORITY, "2026-08-21")

    db.record_postcode_backfill_run(
        conn, authority_code=AUTHORITY, run_date="2026-08-21",
        candidates_checked=5, resolved_count=2, status="ok", run_at=dt_now_iso(),
    )

    assert db.already_backfilled(conn, AUTHORITY, "2026-08-21")
    assert not db.already_backfilled(conn, AUTHORITY, "2026-08-22")
