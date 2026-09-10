from fsa_pipeline.matcher import (
    EMPTY_IDF,
    build_address_density,
    build_companies_by_district,
    build_companies_by_first_word,
    build_word_idf,
    extract_operator_prefix,
    find_candidates,
    find_national_candidates,
    levenshtein_distance,
    merge_candidates,
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


# --- normalize_postcode_district: bare outward code fallback ---
# Added 2026-09-10 after the user flagged FHRSID 1980200 ("Evernutra"):
# the live-API postcode backfill's source field is sometimes genuinely
# just the district ("OX7 ", confirmed against the raw archived
# response, not a parsing bug) -- ~95,000 establishments nationally rely
# solely on this field and were silently invisible to district-scoped
# matching as a result.

def test_normalize_postcode_district_accepts_bare_outward_code():
    assert normalize_postcode_district("OX7") == "OX7"
    assert normalize_postcode_district("OX7 ") == "OX7"  # real value, trailing space
    assert normalize_postcode_district("s43") == "S43"
    assert normalize_postcode_district("RH20") == "RH20"
    assert normalize_postcode_district("SW1A") == "SW1A"


def test_normalize_postcode_district_bare_outward_code_still_rejects_junk():
    assert normalize_postcode_district("12") is None
    assert normalize_postcode_district("TOOLONG1") is None
    assert normalize_postcode_district("A") is None


def test_normalize_postcode_district_malformed_full_length_input_still_rejected():
    """A 5-7 char string that fails the inward-code shape check must not
    fall through to the bare-outward-code path -- outward codes are at
    most 4 characters, so this is already impossible, but worth locking
    in explicitly since the fallback was added right next to this check."""
    assert normalize_postcode_district("ABCDE") is None


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


def test_build_word_idf_weights_rare_words_above_common_ones():
    """The real bug this replaced raw-character scoring for: "Rassau Fish
    Bar" vs "KHAN SONS FISH BAR LTD" (shares only the generic "FISH BAR")
    should score below "RASSAU TRADING LTD" (shares the rare proper noun
    "RASSAU") once shared words are weighted by rarity, not just counted."""
    companies = [
        make_company("1", "Khan Sons Fish Bar Ltd", "1 Rd", "NP23 5AA"),
        make_company("2", "Rassau Trading Ltd", "2 Rd", "NP23 5AA"),
        make_company("3", "Some Fish Bar Ltd", "3 Rd", "NP23 5AA"),
        make_company("4", "Another Fish Bar Ltd", "4 Rd", "NP23 5AA"),
    ]
    idf = build_word_idf(companies)
    target = normalize_company_name("Rassau Fish Bar")

    khan_score = name_similarity(target, normalize_company_name("Khan Sons Fish Bar Ltd"), idf)
    rassau_score = name_similarity(target, normalize_company_name("Rassau Trading Ltd"), idf)

    assert rassau_score > khan_score


def test_name_similarity_identical_after_normalization():
    a = normalize_company_name("The Cob Kings")
    b = normalize_company_name("THE COB KINGS LTD")
    assert name_similarity(a, b, EMPTY_IDF) == 1.0


def test_name_similarity_unrelated_names_score_zero():
    a = normalize_company_name("The Cob Kings")
    b = normalize_company_name("Greenacre FZCO Ltd")
    assert name_similarity(a, b, EMPTY_IDF) == 0.0  # no shared words at all


def test_name_similarity_empty_strings_score_zero():
    assert name_similarity("", "SOMETHING", EMPTY_IDF) == 0.0
    assert name_similarity("SOMETHING", "", EMPTY_IDF) == 0.0


# --- levenshtein_distance ---
# Added 2026-09-10 for classifier.py's _predecessor_name_match, after
# "CORNELLY PIZZA" vs "CONELLY PIZZA" (one letter apart) scored 0.15 on
# name_similarity -- word-overlap scoring can't see a typo within a
# single word at all, since the two spellings share zero tokens.

def test_levenshtein_distance_identical_strings_is_zero():
    assert levenshtein_distance("CORNELLY PIZZA", "CORNELLY PIZZA") == 0


def test_levenshtein_distance_single_letter_typo():
    assert levenshtein_distance("CORNELLY PIZZA", "CONELLY PIZZA") == 1


def test_levenshtein_distance_against_empty_string_is_length():
    assert levenshtein_distance("", "ABC") == 3
    assert levenshtein_distance("ABC", "") == 3


def test_levenshtein_distance_unrelated_strings_is_large():
    assert levenshtein_distance("THE COB KINGS", "GREENACRE FZCO") > 2


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
    idf = build_word_idf(companies)

    candidates = find_candidates(
        "The Cob Kings", "NG17 3GA", by_district, density,
        high_density_threshold=5, idf=idf,
        establishment_address_line_1=None, establishment_first_seen_date="2026-01-01", top_n=5,
    )

    assert len(candidates) == 2  # only the two in NG17
    assert candidates[0].company_number == "1"
    assert candidates[0].name_similarity_score == 1.0
    assert candidates[0].is_high_density_address is False  # only 2 companies at that address, below threshold=5


def test_find_candidates_flags_high_density_address():
    companies = [make_company(str(i), f"Company {i} Ltd", "71-75 Shelton Street", "WC2H 9JQ") for i in range(6)]
    by_district = build_companies_by_district(companies)
    density = build_address_density(companies)
    idf = build_word_idf(companies)

    candidates = find_candidates(
        "Company 0", "WC2H 9JQ", by_district, density,
        high_density_threshold=5, idf=idf,
        establishment_address_line_1=None, establishment_first_seen_date="2026-01-01", top_n=5,
    )

    assert candidates[0].is_high_density_address is True
    assert candidates[0].address_company_count == 6


def test_find_candidates_returns_empty_for_unmatched_district():
    candidates = find_candidates(
        "Anything", "ZZ99 9ZZ", {}, {}, high_density_threshold=5, idf=EMPTY_IDF,
        establishment_address_line_1=None, establishment_first_seen_date="2026-01-01", top_n=5,
    )
    assert candidates == []


def test_find_candidates_returns_empty_for_missing_postcode():
    candidates = find_candidates(
        "Anything", None, {"NG17": []}, {}, high_density_threshold=5, idf=EMPTY_IDF,
        establishment_address_line_1=None, establishment_first_seen_date="2026-01-01", top_n=5,
    )
    assert candidates == []


def test_find_candidates_respects_top_n():
    companies = [make_company(str(i), f"Restaurant {i}", "Some Rd", "NG17 3GA") for i in range(10)]
    by_district = build_companies_by_district(companies)
    density = build_address_density(companies)
    idf = build_word_idf(companies)

    candidates = find_candidates(
        "Restaurant", "NG17 3GA", by_district, density, high_density_threshold=5, idf=idf,
        establishment_address_line_1=None, establishment_first_seen_date="2026-01-01", top_n=3,
    )
    assert len(candidates) == 3


def test_find_candidates_prioritizes_exact_address_match_over_higher_name_score():
    """The real case: FHRSID 1981162 "Favourite Grill" -- the top-ranked-
    by-name candidate ("Favourite Grill Ltd") turned out to be a
    coincidental namesake in a different town entirely (caught via the
    national channel, not this test), while a low-name-score candidate
    ("Best Grill Bristol Ltd") shares the establishment's exact
    registered address. Within find_candidates alone: a low-scoring
    exact-address match must still outrank a higher-scoring one at a
    different address."""
    companies = [
        make_company("1", "Cheap Grill Limited", "16 Gloucester Road North", "BS7 0SF"),
        make_company("2", "Something Grill Related Ltd", "9 Elsewhere Road", "BS7 1AA"),
    ]
    by_district = build_companies_by_district(companies)
    density = build_address_density(companies)
    idf = build_word_idf(companies)

    candidates = find_candidates(
        "Favourite Grill", "BS7 0SF", by_district, density, high_density_threshold=5, idf=idf,
        establishment_address_line_1="16 Gloucester Road North", establishment_first_seen_date="2026-08-25", top_n=5,
    )

    assert candidates[0].company_number == "1"  # address match wins despite a weaker name score
    assert candidates[0].address_matches_establishment is True
    assert candidates[1].address_matches_establishment is False


def test_find_candidates_prefers_closer_incorporation_among_address_matches():
    """The user's own reasoning: when two companies share the exact
    establishment address, the one incorporated closer to the
    establishment's first-seen date is the more plausible trigger for
    this FHRS record (the other is more likely the previous occupant)."""
    companies = [
        make_company("old", "Best Grill Bristol Ltd", "16 Gloucester Road North", "BS7 0SF"),
        make_company("new", "Cheap Grill Limited", "16 Gloucester Road North", "BS7 0SF"),
    ]
    companies[0]["date_of_creation"] = "2025-04-12"  # over a year before first_seen
    companies[1]["date_of_creation"] = "2026-06-17"  # ~2 months before first_seen
    by_district = build_companies_by_district(companies)
    density = build_address_density(companies)
    idf = build_word_idf(companies)

    candidates = find_candidates(
        "Favourite Grill", "BS7 0SF", by_district, density, high_density_threshold=5, idf=idf,
        establishment_address_line_1="16 Gloucester Road North", establishment_first_seen_date="2026-08-25", top_n=5,
    )

    assert candidates[0].company_number == "new"


def test_find_candidates_does_not_boost_high_density_address_match():
    """A formation-agent address matching exactly is still not trusted --
    the brief's original warning still holds there."""
    companies = [make_company(str(i), f"Company {i} Ltd", "16 Gloucester Road North", "BS7 0SF") for i in range(6)]
    by_district = build_companies_by_district(companies)
    density = build_address_density(companies)
    idf = build_word_idf(companies)

    candidates = find_candidates(
        "Totally Unrelated Name", "BS7 0SF", by_district, density, high_density_threshold=5, idf=idf,
        establishment_address_line_1="16 Gloucester Road North", establishment_first_seen_date="2026-08-25", top_n=5,
    )

    # All share the address, all high-density -- ranking falls back to
    # name similarity (all ~0.0 here), not the address match.
    assert all(c.is_high_density_address for c in candidates)


# --- operator-prefix extraction ("<operator> @ <site>" contract catering) ---

def test_extract_operator_prefix_splits_on_at():
    assert extract_operator_prefix("Aramark @ Drayton Manor High School") == "Aramark"
    assert extract_operator_prefix("Impact Food Group @ John Cabot Academy") == "Impact Food Group"


def test_extract_operator_prefix_none_without_at():
    assert extract_operator_prefix("The Cotswold Cafe") is None


def test_extract_operator_prefix_none_for_none_or_empty():
    assert extract_operator_prefix(None) is None
    assert extract_operator_prefix("") is None


def test_extract_operator_prefix_none_when_nothing_before_at():
    assert extract_operator_prefix("@ Some School") is None
    assert extract_operator_prefix("  @ Some School") is None


def test_extract_operator_prefix_uses_first_at_only():
    assert extract_operator_prefix("A @ B @ C") == "A"


def test_extract_operator_prefix_strips_whitespace():
    assert extract_operator_prefix("Aramark   @ Some School") == "Aramark"


# --- national name-match channel (Soul Mama / Mamma Rosa case) ---

def test_build_companies_by_first_word_blocks_correctly():
    companies = [
        make_company("1", "Soul Mama Islington Limited", "71-75 Shelton Street", "WC2H 9JQ"),
        make_company("2", "Mamma Rosa London Limited", "5 Flat 5c Chesterfield Mews", "N4 1LH"),
    ]
    index = build_companies_by_first_word(companies)
    assert {c["company_number"] for c in index["SOUL"]} == {"1"}
    assert {c["company_number"] for c in index["MAMMA"]} == {"2"}


def test_find_national_candidates_finds_formation_agent_registered_company():
    """The real bug: a company registered via a formation agent in a
    completely different postcode district to where it trades --
    invisible to district search no matter the threshold."""
    companies = [make_company("17102443", "SOUL MAMA ISLINGTON LIMITED", "71-75 Shelton Street", "WC2H 9JQ")]
    by_first_word = build_companies_by_first_word(companies)
    density = build_address_density(companies)
    idf = build_word_idf(companies)

    # The FHRS establishment trades from N1, nowhere near WC2H -- district
    # search would never find this; national search doesn't care.
    candidates = find_national_candidates("Soul Mama Islington", by_first_word, density, 5, threshold=0.9, idf=idf, top_n=5)

    assert len(candidates) == 1
    assert candidates[0].company_number == "17102443"
    assert candidates[0].match_strategy == "national"
    assert candidates[0].name_similarity_score == 1.0


def test_find_national_candidates_respects_threshold():
    companies = [make_company("1", "Soul Mama Somewhere Else Entirely Ltd", "1 Road", "AB1 1AA")]
    by_first_word = build_companies_by_first_word(companies)
    density = build_address_density(companies)
    idf = build_word_idf(companies)

    candidates = find_national_candidates("Soul Mama Islington", by_first_word, density, 5, threshold=0.9, idf=idf, top_n=5)
    assert candidates == []  # shares first word but not similar enough overall


def test_find_national_candidates_empty_when_no_first_word_match():
    companies = [make_company("1", "Totally Different Name Ltd", "1 Road", "AB1 1AA")]
    by_first_word = build_companies_by_first_word(companies)
    density = build_address_density(companies)
    idf = build_word_idf(companies)

    candidates = find_national_candidates("Soul Mama Islington", by_first_word, density, 5, threshold=0.9, idf=idf, top_n=5)
    assert candidates == []


def test_find_national_candidates_carries_date_of_creation():
    company = make_company("1", "Soul Mama Islington Limited", "71-75 Shelton Street", "WC2H 9JQ")
    company["date_of_creation"] = "2026-03-19"
    by_first_word = build_companies_by_first_word([company])
    density = build_address_density([company])
    idf = build_word_idf([company])

    candidates = find_national_candidates("Soul Mama Islington", by_first_word, density, 5, threshold=0.9, idf=idf, top_n=5)
    assert candidates[0].date_of_creation == "2026-03-19"


def test_find_national_candidates_defaults_to_national_strategy():
    company = make_company("1", "Soul Mama Islington Limited", "71-75 Shelton Street", "WC2H 9JQ")
    by_first_word = build_companies_by_first_word([company])
    density = build_address_density([company])
    idf = build_word_idf([company])

    candidates = find_national_candidates("Soul Mama Islington", by_first_word, density, 5, threshold=0.9, idf=idf, top_n=5)
    assert candidates[0].match_strategy == "national"


def test_find_national_candidates_accepts_custom_strategy_label():
    """The operator-prefix search (Aramark @ School case) reuses this
    same function but wants candidates labelled distinctly for audit
    purposes, not lumped in with an ordinary full-name national search."""
    company = make_company("1", "Aramark Limited", "1 Some Road", "AB1 1AA")
    by_first_word = build_companies_by_first_word([company])
    density = build_address_density([company])
    idf = build_word_idf([company])

    candidates = find_national_candidates(
        "Aramark", by_first_word, density, 5, threshold=0.9, idf=idf, top_n=5, strategy="operator-prefix",
    )
    assert candidates[0].match_strategy == "operator-prefix"


def test_operator_prefix_search_finds_contract_caterer_real_case():
    """End-to-end reproduction of the real Aramark case: a full-string
    national search on "Aramark @ Drayton Manor High School" would dilute
    below threshold, but searching just the extracted operator prefix
    finds the company at a perfect score."""
    company = make_company("1", "Aramark Limited", "1 Some Road", "AB1 1AA")
    by_first_word = build_companies_by_first_word([company])
    density = build_address_density([company])
    idf = build_word_idf([company])

    full_name_candidates = find_national_candidates(
        "Aramark @ Drayton Manor High School", by_first_word, density, 5, threshold=0.9, idf=idf, top_n=5,
    )
    assert full_name_candidates == []  # diluted below threshold by the site name

    prefix = extract_operator_prefix("Aramark @ Drayton Manor High School")
    prefix_candidates = find_national_candidates(
        prefix, by_first_word, density, 5, threshold=0.9, idf=idf, top_n=5, strategy="operator-prefix",
    )
    assert len(prefix_candidates) == 1
    assert prefix_candidates[0].company_number == "1"
    assert prefix_candidates[0].name_similarity_score == 1.0


# --- merging both channels ---

def test_merge_candidates_deduplicates_by_company_number_keeping_higher_score():
    from fsa_pipeline.matcher import Candidate

    district = [Candidate(company_number="1", company_name="A", name_similarity_score=0.5,
                           postcode_district="NG17", address_company_count=1, is_high_density_address=False,
                           match_strategy="district")]
    national = [Candidate(company_number="1", company_name="A", name_similarity_score=0.95,
                           postcode_district="", address_company_count=1, is_high_density_address=False,
                           match_strategy="national")]

    merged = merge_candidates(district, national, top_n=5, establishment_first_seen_date="2026-01-01")
    assert len(merged) == 1
    assert merged[0].match_strategy == "national"
    assert merged[0].name_similarity_score == 0.95


def test_merge_candidates_combines_distinct_companies_and_sorts():
    from fsa_pipeline.matcher import Candidate

    district = [Candidate(company_number="1", company_name="A", name_similarity_score=0.4,
                           postcode_district="NG17", address_company_count=1, is_high_density_address=False)]
    national = [Candidate(company_number="2", company_name="B", name_similarity_score=0.95,
                           postcode_district="", address_company_count=1, is_high_density_address=False,
                           match_strategy="national")]

    merged = merge_candidates(district, national, top_n=5, establishment_first_seen_date="2026-01-01")
    assert [c.company_number for c in merged] == ["2", "1"]  # highest score first


def test_merge_candidates_respects_top_n():
    from fsa_pipeline.matcher import Candidate

    district = [Candidate(company_number=str(i), company_name="A", name_similarity_score=0.1 * i,
                           postcode_district="NG17", address_company_count=1, is_high_density_address=False)
                for i in range(10)]

    merged = merge_candidates(district, [], top_n=3, establishment_first_seen_date="2026-01-01")
    assert len(merged) == 3


def test_merge_candidates_address_match_outranks_higher_scoring_national_coincidence():
    """The real Favourite Grill case end-to-end: the national channel's
    exact-name match ("Favourite Grill Ltd") is a coincidental namesake
    in a different town (score 1.0, but no address match); the district
    channel's low-name-score candidate ("Best Grill Bristol Ltd") shares
    the establishment's exact address. The address match must win."""
    from fsa_pipeline.matcher import Candidate

    district = [Candidate(company_number="best-grill", company_name="Best Grill Bristol Ltd",
                           name_similarity_score=0.17, postcode_district="BS7", address_company_count=2,
                           is_high_density_address=False, match_strategy="district",
                           address_matches_establishment=True, date_of_creation="2025-04-12")]
    national = [Candidate(company_number="favourite-grill", company_name="Favourite Grill Ltd",
                           name_similarity_score=1.0, postcode_district="SS8", address_company_count=1,
                           is_high_density_address=False, match_strategy="national",
                           address_matches_establishment=False, date_of_creation="2025-02-05")]

    merged = merge_candidates(district, national, top_n=5, establishment_first_seen_date="2026-08-25")
    assert merged[0].company_number == "best-grill"
