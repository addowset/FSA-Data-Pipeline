from fsa_pipeline.officer_churn import compute_officer_churn_signal


def test_no_officers_returns_zero_and_false():
    signal = compute_officer_churn_signal([], "2026-08-25", window_days=60)
    assert signal == {"officer_count": 0, "appointed_near_event": False, "resigned_near_event": False}


def test_appointment_within_window_flagged():
    officers = [{"appointed_on": "2026-07-10"}]  # 46 days before
    signal = compute_officer_churn_signal(officers, "2026-08-25", window_days=60)
    assert signal["appointed_near_event"] is True
    assert signal["resigned_near_event"] is False
    assert signal["officer_count"] == 1


def test_appointment_outside_window_not_flagged():
    officers = [{"appointed_on": "2025-01-01"}]  # well over a year before
    signal = compute_officer_churn_signal(officers, "2026-08-25", window_days=60)
    assert signal["appointed_near_event"] is False


def test_resignation_within_window_flagged():
    officers = [{"appointed_on": "2020-01-01", "resigned_on": "2026-09-01"}]  # 7 days after event
    signal = compute_officer_churn_signal(officers, "2026-08-25", window_days=60)
    assert signal["resigned_near_event"] is True
    assert signal["appointed_near_event"] is False  # appointment is decades outside the window


def test_window_boundary_is_inclusive():
    officers = [{"appointed_on": "2026-06-26"}]  # exactly 60 days before 2026-08-25
    signal = compute_officer_churn_signal(officers, "2026-08-25", window_days=60)
    assert signal["appointed_near_event"] is True


def test_window_boundary_one_day_over_not_flagged():
    officers = [{"appointed_on": "2026-06-25"}]  # 61 days before
    signal = compute_officer_churn_signal(officers, "2026-08-25", window_days=60)
    assert signal["appointed_near_event"] is False


def test_missing_appointed_on_does_not_crash():
    officers = [{"officer_role": "director"}]  # no dates at all
    signal = compute_officer_churn_signal(officers, "2026-08-25", window_days=60)
    assert signal == {"officer_count": 1, "appointed_near_event": False, "resigned_near_event": False}


def test_multiple_officers_any_match_flags_true():
    officers = [
        {"appointed_on": "2019-01-01"},  # long-standing, no churn
        {"appointed_on": "2026-08-01"},  # recent, within window
    ]
    signal = compute_officer_churn_signal(officers, "2026-08-25", window_days=60)
    assert signal["officer_count"] == 2
    assert signal["appointed_near_event"] is True


def test_signal_never_carries_personal_fields():
    """The function must only read appointed_on/resigned_on -- even if
    the input has name/date_of_birth/nationality/address (as a real
    Companies House officer record would), none of it should be able to
    leak into the output, structurally."""
    officers = [{
        "name": "REDACTED PERSON",
        "date_of_birth": {"month": 1, "year": 1966},
        "nationality": "British",
        "address": {"address_line_1": "1 Example Street"},
        "appointed_on": "2026-08-01",
    }]
    signal = compute_officer_churn_signal(officers, "2026-08-25", window_days=60)
    assert set(signal.keys()) == {"officer_count", "appointed_near_event", "resigned_near_event"}
    assert "REDACTED PERSON" not in str(signal)
