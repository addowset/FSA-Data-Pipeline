import sqlite3

import pytest

from fsa_pipeline import territories


@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE authorities (code INTEGER, name TEXT, region_name TEXT)")
    rows = [
        (1, "Bristol", "South West"),
        (2, "Bath and North East Somerset", "South West"),
        (3, "North Somerset", "South West"),
        (4, "Somerset", "South West"),
        (5, "Leeds", "Yorkshire and Humberside"),
        (6, "Bradford", "Yorkshire and Humberside"),
        (7, "Sheffield", "Yorkshire and Humberside"),
        (8, "Cambridge City", "East Counties"),
        (9, "Fenland", "East Counties"),
        (10, "Edinburgh (City of)", "Scotland"),
    ]
    conn.executemany("INSERT INTO authorities VALUES (?, ?, ?)", rows)
    return conn


def test_region_resolves_from_the_database(conn):
    assert territories.resolve(conn, "Yorkshire") == {"Leeds", "Bradford", "Sheffield"}


def test_region_synonyms(conn):
    assert territories.resolve(conn, "Yorkshire and the Humber") == \
           territories.resolve(conn, "yorkshire")


def test_leading_the_is_ignored(conn):
    assert territories.resolve(conn, "the South West") == territories.resolve(conn, "South West")


def test_city_resolves_to_one_authority(conn):
    assert territories.resolve(conn, "Bristol") == {"Bristol"}


def test_county_can_span_authorities(conn):
    # Somerset the county is three authorities, only one of which is called Somerset.
    assert territories.resolve(conn, "Somerset") == {
        "Somerset", "Bath and North East Somerset", "North Somerset",
    }


def test_abbreviations(conn):
    assert territories.resolve(conn, "cambs") == territories.resolve(conn, "Cambridgeshire")


def test_exact_authority_name_always_works(conn):
    # Even a name the gazetteer has never heard of, as long as it is in the table.
    assert territories.resolve(conn, "Edinburgh (City of)") == {"Edinburgh (City of)"}


def test_national(conn):
    assert len(territories.resolve(conn, "UK")) == 10
    assert territories.resolve(conn, "nationwide") == territories.resolve(conn, "UK")


def test_unknown_territory_raises_rather_than_guessing(conn):
    # The whole point: a report quietly built on a half-understood territory is
    # worse than one that was never sent.
    with pytest.raises(territories.UnknownTerritory):
        territories.resolve(conn, "Narnia")


def test_empty_territory_raises(conn):
    with pytest.raises(territories.UnknownTerritory):
        territories.resolve(conn, "")


def test_several_places_in_one_string(conn):
    # What a supplier actually writes when asked how far they cover.
    assert territories.resolve(conn, "Yorkshire and the South West") == (
        territories.resolve(conn, "Yorkshire") | territories.resolve(conn, "South West")
    )


def test_comma_separated_places(conn):
    assert territories.resolve(conn, "Bristol, Leeds and Bradford") == {
        "Bristol", "Leeds", "Bradford",
    }


def test_an_authority_whose_own_name_contains_and(conn):
    # "Bournemouth, Christchurch and Poole" is one council, not three places,
    # so the exact-name check has to happen before any splitting.
    conn.execute("INSERT INTO authorities VALUES (?, ?, ?)",
                 (11, "Bournemouth, Christchurch and Poole", "South West"))
    assert territories.resolve(conn, "Bournemouth, Christchurch and Poole") == {
        "Bournemouth, Christchurch and Poole",
    }


def test_one_bad_part_fails_the_whole_territory(conn):
    # Returning just the parts that resolved would silently under-cover the
    # supplier, and nobody would find out until they read the report.
    with pytest.raises(territories.UnknownTerritory):
        territories.resolve(conn, "Yorkshire and Narnia")


def test_resolve_many_unions(conn):
    assert territories.resolve_many(conn, ["Bristol", "Leeds"]) == {"Bristol", "Leeds"}


def test_validate_reports_names_missing_from_the_table(conn):
    # The fixture has ten authorities; the real gazetteer names hundreds, so
    # validate() against this cut-down table must report the difference rather
    # than passing silently.
    missing = territories.validate(conn)
    assert "Barnsley" in missing

@pytest.fixture
def midlands_conn():
    """A fixture with both Midlands regions, which the main one lacks."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE authorities (code INTEGER, name TEXT, region_name TEXT)")
    conn.executemany("INSERT INTO authorities VALUES (?, ?, ?)", [
        (1, "Birmingham", "West Midlands"),
        (2, "Coventry", "West Midlands"),
        (3, "Derby", "East Midlands"),
        (4, "Amber Valley", "East Midlands"),
        (5, "Manchester", "North West"),
    ])
    return conn


def test_the_midlands_spans_both_midlands_regions(midlands_conn):
    """"The Midlands" is not a region in the table; it is two of them."""
    assert territories.resolve(midlands_conn, "the Midlands") == {
        "Birmingham", "Coventry", "Derby", "Amber Valley"}


def test_midlands_group_ignores_the_leading_the(midlands_conn):
    assert territories.resolve(midlands_conn, "midlands") == \
           territories.resolve(midlands_conn, "The Midlands")


def test_a_group_combines_with_another_place(midlands_conn):
    """A supplier writing two patches gets the union, not one of them."""
    assert territories.resolve(midlands_conn, "the North West and the Midlands") == {
        "Manchester", "Birmingham", "Coventry", "Derby", "Amber Valley"}


def test_a_group_absorbs_a_part_it_already_contains(midlands_conn):
    """Derbyshire sits inside the East Midlands, so the union is just the group."""
    assert territories.resolve(midlands_conn, "Derby and the Midlands") == \
           territories.resolve(midlands_conn, "the Midlands")


def test_a_group_whose_regions_are_absent_raises(conn):
    """The main fixture has no Midlands rows at all, so this must not return
    an empty set -- a territory that silently resolves to nothing would build
    an empty report and nobody would find out until the customer read it."""
    with pytest.raises(territories.UnknownTerritory):
        territories.resolve(conn, "the Midlands")


def test_southern_scotland_is_the_two_border_councils():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE authorities (code INTEGER, name TEXT, region_name TEXT)")
    conn.executemany("INSERT INTO authorities VALUES (?, ?, ?)", [
        (1, "Dumfries and Galloway", "Scotland"),
        (2, "Scottish Borders", "Scotland"),
        (3, "South Ayrshire", "Scotland"),
        (4, "Edinburgh (City of)", "Scotland"),
    ])
    # Ayrshire is sometimes swept in; this deliberately under-covers, because a
    # territory that over-reaches promises venues the report cannot deliver.
    assert territories.resolve(conn, "southern Scotland") == {
        "Dumfries and Galloway", "Scottish Borders"}
