import dataclasses
from pathlib import Path

from fsa_pipeline.classifier import (
    Predecessor,
    address_key,
    build_address_index,
    build_company_operator_index,
    build_postcode_index,
    classify,
    count_operator_venues,
    find_existing_operator,
    find_predecessor,
)
from fsa_pipeline.config import Config
from fsa_pipeline.matcher import EMPTY_IDF, Candidate, build_word_idf

# find_predecessor's fallback path scores two FHRS business names against
# each other via matcher.name_similarity (see fsa_pipeline/matcher.py's
# WordIdf/build_word_idf). EMPTY_IDF is a valid stand-in for every test
# below: identical normalized names always score 1.0 regardless of idf
# coverage, and every "should NOT match" fixture here uses names sharing
# zero words, which scores 0.0 with any idf table. See test_matcher.py's
# test_build_word_idf_weights_rare_words_above_common_ones for a test that
# actually exercises real partial-overlap weighting.
_NO_IDF = EMPTY_IDF


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


def make_establishment(fhrsid, name, address_line_1="1 High Street", postcode="NG17 3GA",
                        first_seen="2026-08-20", last_seen="2026-08-20"):
    return {
        "fhrsid": fhrsid, "business_name": name, "address_line_1": address_line_1, "postcode": postcode,
        "first_seen_date": first_seen, "last_seen_date": last_seen,
    }


def make_candidate(score, high_density=False, count=1, name="Some Company Ltd", number="12345678",
                    date_of_creation="2026-07-01", strategy="district", address_matches_establishment=False):
    return Candidate(
        company_number=number, company_name=name, name_similarity_score=score,
        postcode_district="NG17", address_company_count=count, is_high_density_address=high_density,
        match_strategy=strategy, date_of_creation=date_of_creation,
        address_matches_establishment=address_matches_establishment,
    )


# --- address_key ---

def test_address_key_normalizes():
    assert address_key(" 1 high street ", "ng17 3ga") == ("1 HIGH STREET", "NG17 3GA")


def test_address_key_none_when_missing():
    assert address_key(None, "NG17 3GA") is None
    assert address_key("1 High Street", None) is None
    assert address_key("", "NG17 3GA") is None


# --- find_predecessor: exact address path ---

def test_find_predecessor_none_when_no_history():
    index = build_address_index([make_establishment(1, "New Cafe")])
    pc_index = build_postcode_index([make_establishment(1, "New Cafe")])
    result = find_predecessor(1, "New Cafe", "1 High Street", "NG17 3GA", "2026-08-20", index, pc_index, _NO_IDF, 0.9)
    assert result is None


def test_find_predecessor_excludes_still_active_overlap():
    """The bug caught during design: a multi-tenant venue (college,
    community centre) where the 'predecessor' never actually left."""
    old = make_establishment(1, "Legends Bar", first_seen="2026-08-01", last_seen="2026-08-26")
    new = make_establishment(2, "New Outlet", first_seen="2026-08-20")
    index = build_address_index([old, new])
    pc_index = build_postcode_index([old, new])

    result = find_predecessor(2, new["business_name"], new["address_line_1"], new["postcode"], new["first_seen_date"], index, pc_index, _NO_IDF, 0.9)
    assert result is None  # old was still active (last_seen 08-26 >= new's first_seen 08-20)


def test_find_predecessor_finds_genuinely_departed_business():
    old = make_establishment(1, "The Castle Inn", first_seen="2020-01-01", last_seen="2026-08-15")
    new = make_establishment(2, "The New Kitchen", first_seen="2026-08-20")
    index = build_address_index([old, new])
    pc_index = build_postcode_index([old, new])

    result = find_predecessor(2, new["business_name"], new["address_line_1"], new["postcode"], new["first_seen_date"], index, pc_index, _NO_IDF, 0.9)
    assert result is not None
    assert result.fhrsid == 1
    assert result.business_name == "The Castle Inn"
    assert result.last_seen_date == "2026-08-15"


def test_find_predecessor_picks_most_recently_departed():
    old1 = make_establishment(1, "First Tenant", first_seen="2015-01-01", last_seen="2018-01-01")
    old2 = make_establishment(2, "Second Tenant", first_seen="2018-06-01", last_seen="2026-08-10")
    new = make_establishment(3, "Third Tenant", first_seen="2026-08-20")
    index = build_address_index([old1, old2, new])
    pc_index = build_postcode_index([old1, old2, new])

    result = find_predecessor(3, new["business_name"], new["address_line_1"], new["postcode"], new["first_seen_date"], index, pc_index, _NO_IDF, 0.9)
    assert result.fhrsid == 2  # most recent departure, not the oldest


def test_find_predecessor_ignores_different_address():
    old = make_establishment(1, "Elsewhere", address_line_1="99 Other Road", last_seen="2020-01-01")
    new = make_establishment(2, "New Place", first_seen="2026-08-20")
    index = build_address_index([old, new])
    pc_index = build_postcode_index([old, new])

    result = find_predecessor(2, new["business_name"], new["address_line_1"], new["postcode"], new["first_seen_date"], index, pc_index, _NO_IDF, 0.9)
    assert result is None


def test_find_predecessor_none_when_postcode_missing():
    index = build_address_index([make_establishment(1, "Old", last_seen="2020-01-01")])
    result = find_predecessor(2, "New", "1 High Street", None, "2026-08-20", index, {}, _NO_IDF, 0.9)
    assert result is None


# --- find_predecessor: fallback path (the Rassau bug) ---

def test_find_predecessor_fallback_when_address_line_1_missing():
    """The real bug: two 'Rassau Fish Bar' FHRSIDs at the identical
    postcode, genuinely sequential, missed because the newer record's
    address_line_1 was null -- the exact-match layer never even looks."""
    old = make_establishment(1, "Rassau Fish Bar", address_line_1=None, postcode="NP23 5PP",
                              first_seen="2026-08-20", last_seen="2026-08-21")
    new = make_establishment(2, "Rassau Fish Bar", address_line_1=None, postcode="NP23 5PP",
                              first_seen="2026-08-22")
    index = build_address_index([old, new])  # both excluded, address_line_1 is None
    pc_index = build_postcode_index([old, new])

    result = find_predecessor(2, new["business_name"], new["address_line_1"], new["postcode"], new["first_seen_date"], index, pc_index, _NO_IDF, 0.9)

    assert result is not None
    assert result.fhrsid == 1


def test_find_predecessor_fallback_requires_near_exact_name():
    """A shared postcode alone isn't enough evidence -- confirmed real
    data has postcodes hosting a dozen unrelated venues."""
    old = make_establishment(1, "Completely Different Shop", address_line_1=None, postcode="BT7 3GP", last_seen="2026-08-15")
    new = make_establishment(2, "Some Cafe", address_line_1=None, postcode="BT7 3GP", first_seen="2026-08-20")
    index = build_address_index([old, new])
    pc_index = build_postcode_index([old, new])

    result = find_predecessor(2, new["business_name"], new["address_line_1"], new["postcode"], new["first_seen_date"], index, pc_index, _NO_IDF, 0.9)
    assert result is None


def test_find_predecessor_fallback_only_used_when_exact_match_finds_nothing():
    """When address_line_1 is present but simply doesn't match anything,
    the fallback should still be tried (not short-circuited)."""
    old = make_establishment(1, "Rassau Fish Bar", address_line_1=None, postcode="NP23 5PP", last_seen="2026-08-21")
    new = make_establishment(2, "Rassau Fish Bar", address_line_1="4 School Road", postcode="NP23 5PP", first_seen="2026-08-22")
    index = build_address_index([old, new])
    pc_index = build_postcode_index([old, new])

    result = find_predecessor(2, new["business_name"], new["address_line_1"], new["postcode"], new["first_seen_date"], index, pc_index, _NO_IDF, 0.9)
    assert result is not None
    assert result.fhrsid == 1


# --- existing-operator detection (Soul Mama Stratford case) ---

def test_find_existing_operator_none_when_first_venue():
    index = build_company_operator_index([
        {"fhrsid": 1, "company_number": "123", "business_name": "Venue One", "first_seen_date": "2026-08-20"},
    ])
    result = find_existing_operator(1, "123", "2026-08-20", index)
    assert result is None


def test_find_existing_operator_finds_earlier_venue():
    index = build_company_operator_index([
        {"fhrsid": 1, "company_number": "123", "business_name": "Soul Mama Islington", "first_seen_date": "2026-08-21"},
        {"fhrsid": 2, "company_number": "123", "business_name": "Soul Mama Stratford", "first_seen_date": "2026-10-15"},
    ])
    result = find_existing_operator(2, "123", "2026-10-15", index)
    assert result is not None
    assert result.fhrsid == 1
    assert result.business_name == "Soul Mama Islington"


def test_find_existing_operator_ignores_later_venues():
    """A venue opening later than this one isn't "existing" relative to it."""
    index = build_company_operator_index([
        {"fhrsid": 1, "company_number": "123", "business_name": "First", "first_seen_date": "2026-08-21"},
        {"fhrsid": 2, "company_number": "123", "business_name": "Second", "first_seen_date": "2026-10-15"},
    ])
    result = find_existing_operator(1, "123", "2026-08-21", index)
    assert result is None


def test_find_existing_operator_different_company_ignored():
    index = build_company_operator_index([
        {"fhrsid": 1, "company_number": "999", "business_name": "Unrelated", "first_seen_date": "2020-01-01"},
    ])
    result = find_existing_operator(2, "123", "2026-08-21", index)
    assert result is None


# --- multi-venue-company discount (chain/national-retailer case) ---

def test_count_operator_venues_counts_all_matches_for_a_company():
    index = build_company_operator_index([
        {"fhrsid": 1, "company_number": "123", "business_name": "Store 1", "first_seen_date": "2026-01-01"},
        {"fhrsid": 2, "company_number": "123", "business_name": "Store 2", "first_seen_date": "2026-02-01"},
        {"fhrsid": 3, "company_number": "999", "business_name": "Unrelated", "first_seen_date": "2026-01-01"},
    ])
    assert count_operator_venues("123", index) == 2
    assert count_operator_venues("999", index) == 1
    assert count_operator_venues("no-such-company", index) == 0


# --- classify ---

def test_classify_operator_change_takes_priority_over_company_match():
    predecessor = Predecessor(fhrsid=1, business_name="Old Tenant", first_seen_date="2020-01-01", last_seen_date="2026-08-15")
    candidate = make_candidate(score=1.0)

    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=1, best_candidate=candidate,
        predecessor=predecessor, existing_operator=None, config=make_config(),
    )

    assert result.classification == "OPERATOR_CHANGE"
    assert result.confidence == "HIGH"
    assert "Old Tenant" in result.reason
    assert "FHRSID 1" in result.reason
    assert result.evidence_predecessor_fhrsid == 1
    assert "Corroborated" in result.reason  # strong candidate mentioned too
    assert result.evidence_company_number == candidate.company_number


def test_classify_operator_change_without_company_match():
    predecessor = Predecessor(fhrsid=1, business_name="Old Tenant", first_seen_date="2020-01-01", last_seen_date="2026-08-15")

    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=0, best_candidate=None,
        predecessor=predecessor, existing_operator=None, config=make_config(),
    )

    assert result.classification == "OPERATOR_CHANGE"
    assert result.confidence == "HIGH"
    assert "Corroborated" not in result.reason


def test_classify_new_venue_high_confidence():
    candidate = make_candidate(score=0.95, high_density=False, date_of_creation="2026-08-01")
    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(),
    )

    assert result.classification == "NEW_VENUE"
    assert result.confidence == "HIGH"
    assert result.evidence_company_number == candidate.company_number


def test_classify_new_venue_high_density_downgrades_confidence():
    candidate = make_candidate(score=0.9, high_density=True, count=8, date_of_creation="2026-08-01")
    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(),
    )

    assert result.classification == "NEW_VENUE"
    assert result.confidence == "LOW"
    assert "formation agent" in result.reason


def test_classify_new_venue_near_exact_match_not_downgraded_despite_high_density():
    candidate = make_candidate(score=1.0, high_density=True, count=8, date_of_creation="2026-08-01")
    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(),
    )

    assert result.classification == "NEW_VENUE"
    assert result.confidence == "HIGH"


def test_classify_new_venue_medium_confidence():
    candidate = make_candidate(score=0.65, high_density=False, date_of_creation="2026-08-01")
    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(),
    )

    assert result.classification == "NEW_VENUE"
    assert result.confidence == "MEDIUM"


def test_classify_new_venue_medium_score_high_density_downgrades_to_low():
    candidate = make_candidate(score=0.65, high_density=True, count=6, date_of_creation="2026-08-01")
    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(),
    )

    assert result.classification == "NEW_VENUE"
    assert result.confidence == "LOW"


def test_classify_unknown_weak_candidate_falls_below_threshold():
    candidate = make_candidate(score=0.3, high_density=False)
    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=3, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(),
    )

    assert result.classification == "UNKNOWN"
    assert result.confidence == "LOW"
    assert "0.3" in result.reason


def test_classify_unknown_no_candidates_in_district():
    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=0, best_candidate=None,
        predecessor=None, existing_operator=None, config=make_config(),
    )

    assert result.classification == "UNKNOWN"
    assert "No Companies House company found" in result.reason


def test_classify_unknown_no_postcode():
    result = classify(
        first_seen_date="2026-08-20", postcode=None, candidates_found=0, best_candidate=None,
        predecessor=None, existing_operator=None, config=make_config(),
    )

    assert result.classification == "UNKNOWN"
    assert "No postcode" in result.reason


def test_classify_threshold_boundary_is_inclusive():
    candidate_at_threshold = make_candidate(score=0.6, date_of_creation="2026-08-01")
    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=1, best_candidate=candidate_at_threshold,
        predecessor=None, existing_operator=None, config=make_config(),
    )
    assert result.classification == "NEW_VENUE"

    candidate_just_below = make_candidate(score=0.59, date_of_creation="2026-08-01")
    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=1, best_candidate=candidate_just_below,
        predecessor=None, existing_operator=None, config=make_config(),
    )
    assert result.classification == "UNKNOWN"


# --- incorporation-recency gate (Mamma Rosa case) ---

def test_classify_new_venue_rejected_when_company_too_old():
    """The Mamma Rosa case: real company, real strong name match, but
    incorporated 18 months before the FHRS record, with no evidence it
    operates anywhere else -- genuinely ambiguous (a new branch of an
    established operator vs a venue that traded for a while before its
    first FHRS record), so stays UNKNOWN. See
    test_classify_new_venue_qualifies_via_existing_operator_despite_old_company
    for the case that's now allowed through instead: the same old
    company, but WITH existing-operator corroboration (the Aramark/Greggs
    case, added 2026-09-10)."""
    candidate = make_candidate(score=1.0, date_of_creation="2025-02-19")
    result = classify(
        first_seen_date="2026-08-21", postcode="N19 3NU", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(),
    )

    assert result.classification == "UNKNOWN"
    assert result.confidence == "LOW"
    assert "no evidence this company already operates another" in result.reason
    assert result.evidence_company_number == candidate.company_number  # evidence kept even though rejected
    assert result.recently_incorporated is False  # confirmed old, not just unknown


def test_classify_new_venue_accepted_at_exactly_max_age():
    candidate = make_candidate(score=1.0, date_of_creation="2026-02-21")  # exactly 180 days before first_seen
    result = classify(
        first_seen_date="2026-08-20", postcode="N19 3NU", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(),
    )
    assert result.classification == "NEW_VENUE"
    assert result.recently_incorporated is True


def test_classify_new_venue_rejected_one_day_over_max_age():
    candidate = make_candidate(score=1.0, date_of_creation="2026-02-20")  # 181 days before first_seen
    result = classify(
        first_seen_date="2026-08-20", postcode="N19 3NU", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(),
    )
    assert result.classification == "UNKNOWN"


def test_classify_new_venue_rejected_when_no_creation_date():
    candidate = make_candidate(score=1.0, date_of_creation=None)
    result = classify(
        first_seen_date="2026-08-20", postcode="N19 3NU", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(),
    )
    assert result.classification == "UNKNOWN"
    assert result.recently_incorporated is None  # unknown, not confirmed False
    assert "incorporation date unknown" in result.reason


def test_classify_new_venue_qualifies_via_existing_operator_despite_old_company():
    """The Aramark/Greggs case, added 2026-09-10: a company incorporated
    decades ago still counts as NEW_VENUE evidence when it's confirmed to
    already operate another FHRS-registered venue -- real corroborating
    evidence this is an established operator's new branch, not just a
    coincidental old-company match. User's own insight: an INSERT event
    with no predecessor is itself signal a supplier may want, even when
    the operating company predates the venue by decades."""
    candidate = make_candidate(score=1.0, date_of_creation="1970-07-07")  # Aramark's real incorporation date
    existing_operator = Predecessor(fhrsid=1, business_name="Aramark @ Some Other School", first_seen_date="2026-01-01", last_seen_date="2026-01-01")

    result = classify(
        first_seen_date="2026-08-21", postcode="N19 3NU", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=existing_operator, config=make_config(),
    )

    assert result.classification == "NEW_VENUE"
    assert result.recently_incorporated is False
    assert "additional site" in result.reason
    assert result.evidence_existing_operator_fhrsid == 1


def test_classify_operator_change_stores_recently_incorporated_when_corroborating():
    predecessor = Predecessor(fhrsid=1, business_name="Old Tenant", first_seen_date="2020-01-01", last_seen_date="2026-08-15")
    candidate = make_candidate(score=1.0, date_of_creation="2026-08-01")  # recent

    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=1, best_candidate=candidate,
        predecessor=predecessor, existing_operator=None, config=make_config(),
    )

    assert result.classification == "OPERATOR_CHANGE"
    assert result.recently_incorporated is True


def test_classify_operator_change_recently_incorporated_none_without_corroboration():
    predecessor = Predecessor(fhrsid=1, business_name="Old Tenant", first_seen_date="2020-01-01", last_seen_date="2026-08-15")

    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=0, best_candidate=None,
        predecessor=predecessor, existing_operator=None, config=make_config(),
    )

    assert result.classification == "OPERATOR_CHANGE"
    assert result.recently_incorporated is None


# --- existing-operator note in NEW_VENUE reason ---

def test_classify_new_venue_notes_existing_operator():
    candidate = make_candidate(score=1.0, date_of_creation="2026-08-01")
    existing_operator = Predecessor(fhrsid=1, business_name="Soul Mama Islington", first_seen_date="2026-05-01", last_seen_date="2026-05-01")

    result = classify(
        first_seen_date="2026-10-15", postcode="E15 1XX", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=existing_operator, config=make_config(),
    )

    assert result.classification == "NEW_VENUE"
    assert "Soul Mama Islington" in result.reason
    assert "additional site" in result.reason
    assert result.evidence_existing_operator_fhrsid == 1


# --- multi-venue-company discount (chain/national-retailer case) ---

def test_classify_new_venue_downgrades_confidence_for_multi_venue_company():
    """The Aldi/Burger King case: a company already matched to many FHRS
    venues nationally isn't venue-specific corroboration, even at a
    perfect name score -- same treatment as a formation-agent address."""
    candidate = make_candidate(score=1.0, date_of_creation="2026-08-01")

    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(), operator_venue_count=5,
    )

    assert result.classification == "NEW_VENUE"
    assert result.confidence == "LOW"
    assert "5 FHRS-registered" in result.reason
    assert "chain/large-operator match" in result.reason


def test_classify_new_venue_below_multi_venue_threshold_not_downgraded():
    candidate = make_candidate(score=1.0, date_of_creation="2026-08-01")

    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(), operator_venue_count=4,
    )

    assert result.confidence == "HIGH"
    assert "chain/large-operator match" not in result.reason


def test_classify_multi_venue_note_takes_precedence_over_existing_operator_note():
    """Showing one example venue out of many nationally isn't useful once
    the company is already flagged as a chain -- the chain note replaces
    it rather than both appearing."""
    candidate = make_candidate(score=1.0, date_of_creation="2026-08-01")
    existing_operator = Predecessor(fhrsid=1, business_name="Some Other Branch", first_seen_date="2026-05-01", last_seen_date="2026-05-01")

    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=1, best_candidate=candidate,
        predecessor=None, existing_operator=existing_operator, config=make_config(), operator_venue_count=5,
    )

    assert "chain/large-operator match" in result.reason
    assert "additional site" not in result.reason


# --- exact-address corroboration (the "Best Grill Bristol" case) ---

def test_classify_new_venue_via_address_match_despite_low_name_score():
    """The real case: "Best Grill Bristol Ltd" scores low on name
    similarity against "Favourite Grill" but shares the establishment's
    exact registered address -- that alone should be enough to qualify
    for NEW_VENUE consideration, below the name-similarity threshold."""
    candidate = make_candidate(score=0.17, date_of_creation="2026-06-17", address_matches_establishment=True)

    result = classify(
        first_seen_date="2026-08-25", postcode="BS7 0SF", candidates_found=3, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(),
    )

    assert result.classification == "NEW_VENUE"
    assert "exact match for this establishment's address" in result.reason


def test_classify_address_match_still_subject_to_incorporation_gate():
    """An exact address match doesn't bypass the recency gate -- an old
    company at the right address is more likely just the long-standing
    occupant (or the previous one, caught separately via predecessor
    detection), not evidence of anything new."""
    candidate = make_candidate(score=0.17, date_of_creation="2020-01-01", address_matches_establishment=True)

    result = classify(
        first_seen_date="2026-08-25", postcode="BS7 0SF", candidates_found=3, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(),
    )

    assert result.classification == "UNKNOWN"
    assert "qualified via exact registered-address match" in result.reason


def test_classify_high_density_address_match_does_not_qualify():
    """A formation-agent address matching exactly still isn't trusted --
    the brief's original warning holds regardless of exact-address
    equality once the address is shared by many companies."""
    candidate = make_candidate(score=0.17, high_density=True, count=8, date_of_creation="2026-06-17",
                                address_matches_establishment=True)

    result = classify(
        first_seen_date="2026-08-25", postcode="BS7 0SF", candidates_found=3, best_candidate=candidate,
        predecessor=None, existing_operator=None, config=make_config(),
    )

    assert result.classification == "UNKNOWN"


def test_classify_operator_change_corroborated_via_address_match():
    """Same principle inside the OPERATOR_CHANGE branch: a low-scoring
    but address-matched candidate should still be cited as corroboration,
    not silently dropped for falling under the name-similarity bar."""
    predecessor = Predecessor(fhrsid=1, business_name="Hyderabadi Paradise", first_seen_date="2020-01-01", last_seen_date="2026-08-24")
    candidate = make_candidate(score=0.17, date_of_creation="2026-06-17", address_matches_establishment=True)

    result = classify(
        first_seen_date="2026-08-25", postcode="BS7 0SF", candidates_found=3, best_candidate=candidate,
        predecessor=predecessor, existing_operator=None, config=make_config(),
    )

    assert result.classification == "OPERATOR_CHANGE"
    assert "Corroborated" in result.reason
    assert "exact match for this establishment's address" in result.reason


def test_classify_operator_change_not_corroborated_by_weak_unmatched_candidate():
    """Without an address match, a low name score stays uncorroborated --
    unchanged behaviour, guards against the new check firing too broadly."""
    predecessor = Predecessor(fhrsid=1, business_name="Old Tenant", first_seen_date="2020-01-01", last_seen_date="2026-08-15")
    candidate = make_candidate(score=0.17, date_of_creation="2026-06-17", address_matches_establishment=False)

    result = classify(
        first_seen_date="2026-08-25", postcode="BS7 0SF", candidates_found=3, best_candidate=candidate,
        predecessor=predecessor, existing_operator=None, config=make_config(),
    )

    assert result.classification == "OPERATOR_CHANGE"
    assert "Corroborated" not in result.reason
    assert result.evidence_company_number is None  # real bug, fixed 2026-09-09: this used to be set anyway


# --- predecessor_name_match advisory downgrade (2026-09-10, "The Cabin" case) ---

def test_classify_operator_change_downgrades_when_same_name_uncorroborated():
    predecessor = Predecessor(fhrsid=1, business_name="The Cabin", first_seen_date="2026-08-20", last_seen_date="2026-08-20")

    result = classify(
        first_seen_date="2026-08-21", postcode="NR30 2DB", candidates_found=0, best_candidate=None,
        predecessor=predecessor, existing_operator=None, config=make_config(),
        business_name="The Cabin",
    )

    assert result.classification == "OPERATOR_CHANGE"
    assert result.confidence == "MEDIUM"
    assert result.predecessor_name_match is True
    assert "kept essentially the same trading name" in result.reason
    assert "Confidence downgraded" in result.reason


def test_classify_operator_change_not_downgraded_when_name_differs():
    predecessor = Predecessor(fhrsid=1, business_name="Old Tenant", first_seen_date="2020-01-01", last_seen_date="2026-08-15")

    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=0, best_candidate=None,
        predecessor=predecessor, existing_operator=None, config=make_config(),
        business_name="New Kebab House",
    )

    assert result.classification == "OPERATOR_CHANGE"
    assert result.confidence == "HIGH"
    assert result.predecessor_name_match is False
    assert "Confidence downgraded" not in result.reason


def test_classify_operator_change_not_downgraded_when_corroborated_despite_same_name():
    predecessor = Predecessor(fhrsid=1, business_name="The Cabin", first_seen_date="2026-08-20", last_seen_date="2026-08-20")
    candidate = make_candidate(score=1.0, date_of_creation="2026-08-01")

    result = classify(
        first_seen_date="2026-08-21", postcode="NR30 2DB", candidates_found=1, best_candidate=candidate,
        predecessor=predecessor, existing_operator=None, config=make_config(),
        business_name="The Cabin",
    )

    assert result.classification == "OPERATOR_CHANGE"
    assert result.confidence == "HIGH"  # real company match trumps the name-match ambiguity
    assert result.predecessor_name_match is True
    assert "Confidence downgraded" not in result.reason


def test_classify_operator_change_downgrades_via_near_exact_word_similarity():
    """"THE MULBERRY BUSH SCHOOL" vs "MULBERRY BUSH SCHOOL" aren't
    byte-identical after normalization (one has a leading "THE"), but
    "THE" is common enough in this corpus to score near-zero IDF weight,
    pushing name_similarity above the near-exact threshold -- a real
    case from production data (2026-09-10)."""
    idf = build_word_idf([
        {"company_name": "The Corner Shop"}, {"company_name": "The Fish Bar"},
        {"company_name": "The Kebab House"}, {"company_name": "Mulberry Bush School"},
    ])
    predecessor = Predecessor(fhrsid=1, business_name="Mulberry Bush School", first_seen_date="2020-01-01", last_seen_date="2026-08-15")

    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=0, best_candidate=None,
        predecessor=predecessor, existing_operator=None, config=make_config(),
        business_name="The Mulberry Bush School", idf=idf,
    )

    assert result.classification == "OPERATOR_CHANGE"
    assert result.confidence == "MEDIUM"
    assert result.predecessor_name_match is True


def test_classify_operator_change_downgrades_via_edit_distance_typo():
    """"CONELLY PIZZA" vs "Cornelly Pizza" -- a one-letter FSA/local-
    authority typo correction. Word-overlap name_similarity can't see
    this at all (the two spellings share zero tokens), but
    levenshtein_distance catches it. The real case that prompted
    widening predecessor_name_match beyond byte-exact matching (FHRSID
    1761387, flagged by the user 2026-09-10)."""
    predecessor = Predecessor(fhrsid=1, business_name="CONELLY PIZZA", first_seen_date="2026-08-20", last_seen_date="2026-08-24")

    result = classify(
        first_seen_date="2026-08-25", postcode="CF33 4LB", candidates_found=0, best_candidate=None,
        predecessor=predecessor, existing_operator=None, config=make_config(),
        business_name="Cornelly Pizza",
    )

    assert result.classification == "OPERATOR_CHANGE"
    assert result.confidence == "MEDIUM"
    assert result.predecessor_name_match is True


def test_classify_operator_change_short_names_not_matched_via_edit_distance():
    """Guard against coincidental low edit distance on short names --
    only normalized names of length >=4 get the character-level check;
    "ABC" vs "ABD" (distance 1) stays a genuine mismatch."""
    predecessor = Predecessor(fhrsid=1, business_name="ABC", first_seen_date="2020-01-01", last_seen_date="2026-08-15")

    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=0, best_candidate=None,
        predecessor=predecessor, existing_operator=None, config=make_config(),
        business_name="ABD",
    )

    assert result.predecessor_name_match is False
    assert result.confidence == "HIGH"


def test_classify_operator_change_predecessor_name_match_none_when_business_name_not_supplied():
    """Backward-compat guard: callers that don't pass business_name (or a
    predecessor with no recorded name) get an unknown result, not a false
    negative that would silently skip the downgrade."""
    predecessor = Predecessor(fhrsid=1, business_name="Old Tenant", first_seen_date="2020-01-01", last_seen_date="2026-08-15")

    result = classify(
        first_seen_date="2026-08-20", postcode="NG17 3GA", candidates_found=0, best_candidate=None,
        predecessor=predecessor, existing_operator=None, config=make_config(),
    )

    assert result.predecessor_name_match is None
    assert result.confidence == "HIGH"


# --- integration proof of the "Stage 5 design commitment" ---

def test_predecessor_lookup_works_against_establishment_with_zero_field_changed_events(tmp_path):
    """The "Stage 5 design commitment" this module exists to satisfy:
    the address-history lookup must find a predecessor sourced purely
    from establishments_current, even when that predecessor's only
    observations row is its original first_seen (i.e. it would be
    invisible to any lookup built against `observations` instead).
    Uses the real db module end-to-end, not synthetic dicts, so this
    genuinely proves the guarantee rather than assuming it."""
    from fsa_pipeline import db

    conn = db.connect(tmp_path / "t.db")

    def make_est(fhrsid, name, post_code="NG17 3GA"):
        return {
            "fhrsid": fhrsid, "local_authority_business_id": "LA-1", "business_name": name,
            "business_type": "Restaurant", "business_type_id": 1, "address_line_1": "1 High Street",
            "address_line_2": None, "address_line_3": None, "address_line_4": None, "post_code": post_code,
            "rating_value": "4", "rating_value_numeric": 4, "rating_key": "fhrs_4_en-GB",
            "rating_date": "2026-01-01", "scheme_type": "FHRS", "new_rating_pending": False,
            "hygiene_score": 5, "structural_score": 5, "confidence_in_management_score": 5,
            "longitude": None, "latitude": None,
        }

    # The predecessor: first_seen on day one, never touched again --
    # exactly one 'first_seen' observation, zero 'field_changed' ever.
    db.ingest_establishments(conn, "857", "2026-08-20", [make_est(1, "The Old Castle")])
    obs_count = conn.execute(
        "SELECT COUNT(*) FROM observations WHERE fhrsid = 1 AND change_type = 'field_changed'"
    ).fetchone()[0]
    assert obs_count == 0  # sanity check on the test's own premise

    # It stops appearing (closes) -- last_seen_date freezes at day one
    # since it's simply absent from every subsequent day's ingest.
    for date in ("2026-08-21", "2026-08-22"):
        db.ingest_establishments(conn, "857", date, [])

    # A new establishment appears at the same address, later.
    db.ingest_establishments(conn, "857", "2026-08-23", [make_est(2, "The New Castle")])

    columns = ("fhrsid", "business_name", "address_line_1", "postcode", "first_seen_date", "last_seen_date")
    establishments = [
        dict(zip(columns, row))
        for row in conn.execute(
            "SELECT fhrsid, business_name, address_line_1, COALESCE(post_code, postcode_from_live_api), "
            "first_seen_date, last_seen_date FROM establishments_current"
        ).fetchall()
    ]
    index = build_address_index(establishments)
    pc_index = build_postcode_index(establishments)

    result = find_predecessor(2, "The New Castle", "1 High Street", "NG17 3GA", "2026-08-23", index, pc_index, _NO_IDF, 0.9)

    assert result is not None
    assert result.fhrsid == 1
    assert result.business_name == "The Old Castle"
