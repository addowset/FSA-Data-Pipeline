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


def test_resolve_many_unions(conn):
    assert territories.resolve_many(conn, ["Bristol", "Leeds"]) == {"Bristol", "Leeds"}


def test_validate_reports_names_missing_from_the_table(conn):
    # The fixture has ten authorities; the real gazetteer names hundreds, so
    # validate() against this cut-down table must report the difference rather
    # than passing silently.
    missing = territories.validate(conn)
    assert "Barnsley" in missing
