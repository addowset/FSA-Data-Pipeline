from fsa_pipeline import db
from fsa_pipeline.matcher import Candidate


def make_candidate(number, score, high_density=False, count=1):
    return Candidate(
        company_number=number,
        company_name=f"Company {number}",
        name_similarity_score=score,
        postcode_district="NG17",
        address_company_count=count,
        is_high_density_address=high_density,
    )


def test_already_matched_false_initially(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert not db.already_matched(conn, 1, "2026-08-21")


def test_record_match_with_candidates(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    candidates = [make_candidate("1", 0.95), make_candidate("2", 0.5)]

    db.record_match(conn, 1, "857", "2026-08-21", candidates, "2026-08-21T00:00:00Z")

    assert db.already_matched(conn, 1, "2026-08-21")

    run_row = conn.execute(
        "SELECT candidates_found, best_match_company_number, best_match_score FROM company_match_runs WHERE fhrsid = 1"
    ).fetchone()
    assert run_row == (2, "1", 0.95)

    ranks = conn.execute(
        "SELECT rank, company_number FROM company_match_candidates WHERE fhrsid = 1 ORDER BY rank"
    ).fetchall()
    assert ranks == [(1, "1"), (2, "2")]


def test_record_match_with_no_candidates(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.record_match(conn, 1, "857", "2026-08-21", [], "2026-08-21T00:00:00Z")

    run_row = conn.execute(
        "SELECT candidates_found, best_match_company_number FROM company_match_runs WHERE fhrsid = 1"
    ).fetchone()
    assert run_row == (0, None)


def test_record_match_persists_address_matches_establishment(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    candidates = [
        Candidate(company_number="1", company_name="Best Grill Bristol Ltd", name_similarity_score=0.17,
                   postcode_district="BS7", address_company_count=2, is_high_density_address=False,
                   address_matches_establishment=True),
    ]
    db.record_match(conn, 1, "857", "2026-08-25", candidates, "2026-08-25T00:00:00Z")

    row = conn.execute(
        "SELECT address_matches_establishment FROM company_match_candidates WHERE fhrsid = 1"
    ).fetchone()
    assert row == (1,)


def test_record_match_rerun_replaces_candidates(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.record_match(conn, 1, "857", "2026-08-21", [make_candidate("1", 0.5)], "2026-08-21T00:00:00Z")

    db.record_match(conn, 1, "857", "2026-08-21", [make_candidate("2", 0.9)], "2026-08-21T01:00:00Z")

    count = conn.execute("SELECT COUNT(*) FROM company_match_candidates WHERE fhrsid = 1").fetchone()[0]
    assert count == 1
    row = conn.execute(
        "SELECT company_number FROM company_match_candidates WHERE fhrsid = 1"
    ).fetchone()
    assert row == ("2",)
