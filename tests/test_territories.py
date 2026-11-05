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

@pytest.fixture
def abolished_conn():
    """Authorities for the abolished counties, plus the names that nearly match
    them. Solihull contains "hull", Newcastle-under-Lyme is in Staffordshire not
    on the Tyne, and Hull and Goole Port is a port health authority rather than
    a council, so a substring search for any of these picks up the wrong rows."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE authorities (code INTEGER, name TEXT, region_name TEXT)")
    conn.executemany("INSERT INTO authorities VALUES (?, ?, ?)", [
        (1, "Bristol", "South West"),
        (2, "Bath and North East Somerset", "South West"),
        (3, "North Somerset", "South West"),
        (4, "South Gloucestershire", "South West"),
        (5, "Hull City", "Yorkshire and Humberside"),
        (6, "East Riding of Yorkshire", "Yorkshire and Humberside"),
        (7, "North East Lincolnshire", "Yorkshire and Humberside"),
        (8, "North Lincolnshire", "Yorkshire and Humberside"),
        (9, "Hull and Goole Port", "Yorkshire and Humberside"),
        (10, "Solihull", "West Midlands"),
        (11, "Newcastle Upon Tyne", "North East"),
        (12, "Gateshead", "North East"),
        (13, "North Tyneside", "North East"),
        (14, "South Tyneside", "North East"),
        (15, "Sunderland", "North East"),
        (16, "Newcastle-Under-Lyme", "West Midlands"),
    ])
    return conn


def test_avon_is_the_four_former_avon_councils(abolished_conn):
    """Avon was scrapped in 1996, so a Bristol supplier's trade area has no
    current county name. These four are still the area."""
    assert territories.resolve(abolished_conn, "Avon") == {
        "Bristol", "Bath and North East Somerset", "North Somerset",
        "South Gloucestershire"}


def test_the_bristol_area_matches_avon(abolished_conn):
    assert territories.resolve(abolished_conn, "the Bristol area") == \
           territories.resolve(abolished_conn, "Avon")
    assert territories.resolve(abolished_conn, "greater Bristol") == \
           territories.resolve(abolished_conn, "avon")


def test_bristol_alone_is_still_just_bristol(abolished_conn):
    """A supplier who writes "Bristol" means Bristol. Widening it silently
    would put venues in a report the email never promised."""
    assert territories.resolve(abolished_conn, "Bristol") == {"Bristol"}


def test_humberside_excludes_the_port_health_authority(abolished_conn):
    """Hull and Goole Port is a port health authority, not a council, and would
    double-count the Hull registrations."""
    got = territories.resolve(abolished_conn, "Humberside")
    assert "Hull and Goole Port" not in got
    assert "Solihull" not in got
    assert got == {"Hull City", "East Riding of Yorkshire",
                   "North East Lincolnshire", "North Lincolnshire"}


def test_tyneside_is_the_four_tyne_boroughs(abolished_conn):
    """Sunderland is Wearside. Newcastle-under-Lyme is in Staffordshire."""
    got = territories.resolve(abolished_conn, "Tyneside")
    assert got == {"Newcastle Upon Tyne", "Gateshead", "North Tyneside",
                   "South Tyneside"}
    assert "Sunderland" not in got
    assert "Newcastle-Under-Lyme" not in got


def test_an_abolished_county_combines_with_a_region(abolished_conn):
    """"Avon and the North East" has to union, not pick one."""
    got = territories.resolve(abolished_conn, "Avon and the North East")
    assert "Bristol" in got and "Sunderland" in got

def test_every_gazetteer_key_is_reachable():
    """resolve() normalises before looking up, so a key that is not already in
    normalised form can never match. Two were added in one afternoon -- "the
    bristol area" and "the scottish borders" -- both silently dead, because a
    lookup that misses falls through to UnknownTerritory and looks exactly like
    a name nobody has added yet."""
    unreachable = []
    for name, mapping in (("CITIES", territories.CITIES),
                          ("COUNTIES", territories.COUNTIES),
                          ("REGION_SYNONYMS", territories.REGION_SYNONYMS),
                          ("REGION_GROUPS", territories.REGION_GROUPS),
                          ("ABBREVIATIONS", territories.ABBREVIATIONS),
                          ("NATIONAL", {key: None for key in territories.NATIONAL})):
        for key in mapping:
            if territories.normalise(key) != key:
                unreachable.append("%s[%r] would need to be %r"
                                   % (name, key, territories.normalise(key)))
    assert not unreachable, "unreachable gazetteer keys: " + "; ".join(unreachable)
