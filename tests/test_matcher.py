from fsa_pipeline.matcher import (
    build_address_density,
    build_companies_by_district,
    find_candidates,
    name_similarity,
    normalize_company_name,
    normalize_postcode_district,
)


def test_normalize_postcode_district_handles_various_formats():
    assert normalize_postcode_district("NG17 3GA") == "NG17"
    assert normalize_postcode_district("ng173ga") == "NG17"
    assert normalize_postcode_district("EC1V 2NX") == "EC1V"
    assert normalize_postcode_district("BS1 1AA") == "BS1"
    assert normalize_postcode_district("  W1W 5PF  ") == "W1W"


def test_normalize_postcode_district_rejects_junk():
    assert normalize_postcode_district(None) is None
    assert normalize_postcode_district("") is None
    assert normalize_postcode_district("NOT A POSTCODE") is None
    assert normalize_postcode_district("12345") is None


def test_normalize_company_name_strips_legal_suffix():
    assert normalize_company_name("THE COB KINGS LTD") == "THE COB KINGS"
    assert normalize_company_name("Acme Catering Limited") == "ACME CATERING"
    assert normalize_company_name("Smith & Sons LLP") == "SMITH AND SONS"


def test_normalize_company_name_strips_punctuation_and_collapses_whitespace():
    assert normalize_company_name("O'Brien's Café  Ltd.") == "O BRIEN S CAF"
    # apostrophes/periods become spaces then collapse -- documents the
    # behaviour rather than asserting an idealised transliteration.


def test_normalize_company_name_handles_none_and_empty():
    assert normalize_company_name(None) == ""
    assert normalize_company_name("") == ""


def test_name_similarity_identical_after_normalization():
    a = normalize_company_name("The Cob Kings")
    b = normalize_company_name("THE COB KINGS LTD")
    assert name_similarity(a, b) == 1.0


def test_name_similarity_unrelated_names_score_low():
    a = normalize_company_name("The Cob Kings")
    b = normalize_company_name("Greenacre FZCO Ltd")
    assert name_similarity(a, b) < 0.4


def test_name_similarity_empty_strings_score_zero():
    assert name_similarity("", "SOMETHING") == 0.0
    assert name_similarity("SOMETHING", "") == 0.0


def make_company(number, name, address_line_1, postal_code):
    return {"company_number": number, "company_name": name, "address_line_1": address_line_1, "postal_code": postal_code}


def test_build_companies_by_district_groups_correctly():
    companies = [
        make_company("1", "A", "1 St", "NG17 3GA"),
        make_company("2", "B", "2 St", "NG17 3GB"),
        make_company("3", "C", "3 St", "BS1 1AA"),
    ]
    by_district = build_companies_by_district(companies)
    assert {c["company_number"] for c in by_district["NG17"]} == {"1", "2"}
    assert {c["company_number"] for c in by_district["BS1"]} == {"3"}


def test_build_companies_by_district_skips_unparseable_postcodes():
    companies = [make_company("1", "A", "1 St", "GARBAGE")]
    by_district = build_companies_by_district(companies)
    assert by_district == {}


def test_build_address_density_counts_shared_addresses():
    companies = [
        make_company("1", "A", "71-75 Shelton Street", "WC2H 9JQ"),
        make_company("2", "B", "71-75 Shelton Street", "WC2H 9JQ"),
        make_company("3", "C", "1 Unique Road", "BS1 1AA"),
    ]
    density = build_address_density(companies)
    assert density["71-75 SHELTON STREET|WC2H 9JQ"] == 2
    assert density["1 UNIQUE ROAD|BS1 1AA"] == 1


def test_find_candidates_ranks_by_similarity_and_flags_high_density():
    companies = [
        make_company("1", "The Cob Kings Ltd", "1 High St", "NG17 3GA"),
        make_company("2", "Totally Different Ventures Ltd", "1 High St", "NG17 3GA"),
        make_company("3", "Some Other Shop", "9 Far Away Rd", "BS1 1AA"),  # different district, excluded
    ]
    by_district = build_companies_by_district(companies)
    density = build_address_density(companies)

    candidates = find_candidates(
        "The Cob Kings", "NG17 3GA", by_district, density,
        high_density_threshold=5, top_n=5,
    )

    assert len(candidates) == 2  # only the two in NG17
    assert candidates[0].company_number == "1"
    assert candidates[0].name_similarity_score == 1.0
    assert candidates[0].is_high_density_address is False  # only 2 companies at that address, below threshold=5


def test_find_candidates_flags_high_density_address():
    companies = [make_company(str(i), f"Company {i} Ltd", "71-75 Shelton Street", "WC2H 9JQ") for i in range(6)]
    by_district = build_companies_by_district(companies)
    density = build_address_density(companies)

    candidates = find_candidates(
        "Company 0", "WC2H 9JQ", by_district, density,
        high_density_threshold=5, top_n=5,
    )

    assert candidates[0].is_high_density_address is True
    assert candidates[0].address_company_count == 6


def test_find_candidates_returns_empty_for_unmatched_district():
    candidates = find_candidates("Anything", "ZZ99 9ZZ", {}, {}, high_density_threshold=5, top_n=5)
    assert candidates == []


def test_find_candidates_returns_empty_for_missing_postcode():
    candidates = find_candidates("Anything", None, {"NG17": []}, {}, high_density_threshold=5, top_n=5)
    assert candidates == []


def test_find_candidates_respects_top_n():
    companies = [make_company(str(i), f"Restaurant {i}", "Some Rd", "NG17 3GA") for i in range(10)]
    by_district = build_companies_by_district(companies)
    density = build_address_density(companies)

    candidates = find_candidates("Restaurant", "NG17 3GA", by_district, density, high_density_threshold=5, top_n=3)
    assert len(candidates) == 3
