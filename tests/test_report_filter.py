import pytest

from fsa_pipeline.report_filter import (
    AUDIENCES,
    company_is_corroborated,
    filter_records,
    is_chain,
    is_institution,
    is_reissue,
)


def record(**overrides):
    base = {
        "fhrsid": 1,
        "authority_name": "Bristol",
        "business_name": "The Cafe",
        "business_type": "Restaurant/Cafe/Canteen",
        "classification": "NEW_VENUE",
        "address_line_1": "1 High Street",
        "address_line_2": "Clifton",
        "address_line_3": None,
        "address_line_4": None,
        "post_code": "BS8 1AA",
        "postcode_from_live_api": None,
        "previous_business_name": None,
        "reason": "",
        "evidence_company_number": None,
    }
    base.update(overrides)
    return base


# --- chains -----------------------------------------------------------------

@pytest.mark.parametrize("name,address", [
    ("Greggs", "22 Northumberland Street"),
    ("Costa Coffee", "Unit 4, The Mall"),
    ("Spar", "1 Main Road"),
])
def test_obvious_chains_are_removed(name, address):
    assert is_chain(name, address)


def test_chain_in_the_address_is_caught():
    # The case the first version missed: the register spells the operator
    # MORRISON, singular, and buries it in the address lines.
    assert is_chain("Canteen Flaxby Produce", "WM MORRISON PRODUCE LTD YORK ROAD, FLAXBY")


@pytest.mark.parametrize("name", [
    "The Sparrow Cafe",      # 'spar' inside 'sparrow'
    "Danisa Bakery",         # 'nisa' inside 'Danisa'
    "Costalot Cafe",         # 'costa' inside 'Costalot'
    "Cobblestone Cafe",
])
def test_independents_are_not_mistaken_for_chains(name):
    # Deleting a real independent from a customer's list is a worse failure
    # than leaving a chain in it, so these matter more than the cases above.
    assert not is_chain(name, "12 High Street, Leeds")


# --- institutions -----------------------------------------------------------

def test_institution_by_name():
    assert is_institution("St Mary's Primary School Kitchen", "1 Church Lane")


def test_institution_by_address():
    # Typed as a cafe, sitting inside a hospital.
    assert is_institution("The Garden Cafe", "Royal Infirmary Hospital, Trust Road")


def test_ordinary_venue_is_not_an_institution():
    assert not is_institution("The Garden Cafe", "12 Garden Road, Hull")


# --- re-issues --------------------------------------------------------------

@pytest.mark.parametrize("new,old", [
    ("The Rutland Hub", "Rutland Hub"),
    ("Jacksons Ltd", "Jacksons Fish Bar"),
    ("Pinot and Picasso", "Pinot & Picasso Liverpool"),   # ampersand vs 'and'
])
def test_reissues_are_removed(new, old):
    assert is_reissue("OPERATOR_CHANGE", new, old)


@pytest.mark.parametrize("new,old", [
    ("L10 Sports Bar", "JB's Bar"),
    ("Cafe25", "Jack Dempsie's"),
])
def test_real_takeovers_are_kept(new, old):
    assert not is_reissue("OPERATOR_CHANGE", new, old)


def test_a_new_venue_is_never_a_reissue():
    assert not is_reissue("NEW_VENUE", "Anything", "Anything")


# --- company corroboration --------------------------------------------------

def test_exact_address_match_is_corroborated():
    assert company_is_corroborated(
        "Registered address is an exact match for this establishment's address"
    )


def test_formation_agent_address_is_not_corroborated():
    assert not company_is_corroborated(
        "Registered address is shared by 8 companies (possible formation agent) "
        "-- address isn't corroborative"
    )


def test_name_similarity_alone_is_not_corroborated():
    assert not company_is_corroborated("Name similarity 0.91")


# --- the filter as a whole --------------------------------------------------

def test_unknown_classification_is_removed():
    kept, counts = filter_records([record(classification="UNKNOWN")], audience="all")
    assert kept == []
    assert counts["unclassified"] == 1


def test_incomplete_address_is_removed():
    kept, counts = filter_records(
        [record(address_line_1=None, address_line_2=None, post_code="BS8")],
        audience="all",
    )
    assert kept == []
    assert counts["incomplete address"] == 1


def test_audience_decides_the_venue_types():
    takeaway = record(business_type="Takeaway/sandwich shop")
    assert filter_records([takeaway], audience="roaster")[0] == []
    assert len(filter_records([takeaway], audience="broker")[0]) == 1


def test_territory_filters_before_anything_else():
    kept, counts = filter_records([record()], audience="all", authorities={"Leeds"})
    assert kept == []
    assert counts["outside territory"] == 1
    # and nothing else was counted, because the record never got that far
    assert sum(v for k, v in counts.items() if k != "outside territory") == 0


def test_a_good_record_survives():
    kept, counts = filter_records([record()], audience="all", authorities={"Bristol"})
    assert len(kept) == 1
    assert not any(counts.values())


def test_unknown_audience_is_an_error():
    with pytest.raises(ValueError):
        filter_records([record()], audience="florist")


def test_every_audience_includes_the_core_venues():
    for name, types in AUDIENCES.items():
        assert "Restaurant/Cafe/Canteen" in types, name
