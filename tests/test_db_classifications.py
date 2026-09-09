from fsa_pipeline import db
from fsa_pipeline.classifier import Classification


def test_already_classified_false_initially(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert not db.already_classified(conn, 1, "2026-08-21")


def test_record_and_check_classification(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    result = Classification(
        classification="NEW_VENUE", confidence="HIGH", reason="Matched X",
        evidence_company_number="12345678", evidence_predecessor_fhrsid=None,
    )

    db.record_classification(conn, 1, "857", "2026-08-21", result, "2026-08-21T00:00:00Z")

    assert db.already_classified(conn, 1, "2026-08-21")
    assert not db.already_classified(conn, 1, "2026-08-22")

    row = conn.execute(
        "SELECT classification, confidence, reason, evidence_company_number, evidence_predecessor_fhrsid "
        "FROM classifications WHERE fhrsid = 1"
    ).fetchone()
    assert row == ("NEW_VENUE", "HIGH", "Matched X", "12345678", None)


def test_record_classification_rerun_overwrites(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    first = Classification(classification="UNKNOWN", confidence="LOW", reason="no match")
    db.record_classification(conn, 1, "857", "2026-08-21", first, "2026-08-21T00:00:00Z")

    second = Classification(classification="NEW_VENUE", confidence="HIGH", reason="matched later", evidence_company_number="99")
    db.record_classification(conn, 1, "857", "2026-08-21", second, "2026-08-21T01:00:00Z")

    count = conn.execute("SELECT COUNT(*) FROM classifications WHERE fhrsid = 1").fetchone()[0]
    assert count == 1
    row = conn.execute("SELECT classification FROM classifications WHERE fhrsid = 1").fetchone()
    assert row == ("NEW_VENUE",)


def test_operator_change_classification_stores_predecessor_fhrsid(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    result = Classification(
        classification="OPERATOR_CHANGE", confidence="HIGH", reason="predecessor found",
        evidence_predecessor_fhrsid=42,
    )
    db.record_classification(conn, 1, "857", "2026-08-21", result, "2026-08-21T00:00:00Z")

    row = conn.execute("SELECT evidence_predecessor_fhrsid FROM classifications WHERE fhrsid = 1").fetchone()
    assert row == (42,)


def test_recently_incorporated_round_trips_true_false_and_none(tmp_path):
    """bool|None must map to INTEGER 1/0/NULL -- int(None) would raise,
    so this is worth its own explicit round-trip check rather than
    trusting the other field-presence tests to catch a regression here."""
    conn = db.connect(tmp_path / "t.db")

    for fhrsid, value in [(1, True), (2, False), (3, None)]:
        result = Classification(
            classification="NEW_VENUE", confidence="HIGH", reason="x",
            recently_incorporated=value,
        )
        db.record_classification(conn, fhrsid, "857", "2026-08-21", result, "2026-08-21T00:00:00Z")

    rows = dict(conn.execute("SELECT fhrsid, recently_incorporated FROM classifications").fetchall())
    assert rows == {1: 1, 2: 0, 3: None}


def test_predecessor_name_match_round_trips_true_false_and_none(tmp_path):
    conn = db.connect(tmp_path / "t.db")

    for fhrsid, value in [(1, True), (2, False), (3, None)]:
        result = Classification(
            classification="OPERATOR_CHANGE", confidence="MEDIUM", reason="x",
            predecessor_name_match=value,
        )
        db.record_classification(conn, fhrsid, "857", "2026-08-21", result, "2026-08-21T00:00:00Z")

    rows = dict(conn.execute("SELECT fhrsid, predecessor_name_match FROM classifications").fetchall())
    assert rows == {1: 1, 2: 0, 3: None}
