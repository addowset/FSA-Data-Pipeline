import xml.etree.ElementTree as ET

from fsa_pipeline.fhrs_parse import RecordParseError, compute_fingerprint, parse_bulk_file, parse_establishment

# Real records captured from the live API on 2026-08-20, trimmed to
# illustrate the sparse-field and multi-scheme behaviour this parser must
# handle correctly.
SAMPLE_BULK_XML = b"""<?xml version="1.0"?>
<FHRSEstablishment xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Header>
    <ExtractDate>2026-08-19</ExtractDate>
    <ItemCount>3</ItemCount>
    <ReturnCode>Success</ReturnCode>
  </Header>
  <EstablishmentCollection>
    <EstablishmentDetail>
      <FHRSID>1889918</FHRSID>
      <LocalAuthorityBusinessID>25/00313/FOOD</LocalAuthorityBusinessID>
      <BusinessName>@Cleeve Hill</BusinessName>
      <BusinessType>Farmers/growers</BusinessType>
      <BusinessTypeID>7838</BusinessTypeID>
      <RatingValue>4</RatingValue>
      <RatingKey>fhrs_4_en-GB</RatingKey>
      <RatingDate>2025-11-05</RatingDate>
      <LocalAuthorityCode>857</LocalAuthorityCode>
      <LocalAuthorityName>Bath and North East Somerset</LocalAuthorityName>
      <Scores><Hygiene>5</Hygiene><Structural>0</Structural><ConfidenceInManagement>10</ConfidenceInManagement></Scores>
      <SchemeType>FHRS</SchemeType>
      <NewRatingPending>False</NewRatingPending>
      <Geocode />
    </EstablishmentDetail>
    <EstablishmentDetail>
      <FHRSID>1777436</FHRSID>
      <LocalAuthorityBusinessID>24/00307/FOOD</LocalAuthorityBusinessID>
      <BusinessName>1 Source Supplements</BusinessName>
      <BusinessType>Retailers - other</BusinessType>
      <BusinessTypeID>4613</BusinessTypeID>
      <RatingValue>Exempt</RatingValue>
      <RatingKey>fhrs_exempt_en-GB</RatingKey>
      <RatingDate xsi:nil="true" />
      <LocalAuthorityCode>857</LocalAuthorityCode>
      <LocalAuthorityName>Bath and North East Somerset</LocalAuthorityName>
      <Scores />
      <SchemeType>FHRS</SchemeType>
      <NewRatingPending>False</NewRatingPending>
      <Geocode />
    </EstablishmentDetail>
    <EstablishmentDetail>
      <FHRSID>1608170</FHRSID>
      <LocalAuthorityBusinessID>EHDC15239</LocalAuthorityBusinessID>
      <BusinessName>(CURATED) MOROCCAN MARKET</BusinessName>
      <BusinessType>Retailers - other</BusinessType>
      <BusinessTypeID>4613</BusinessTypeID>
      <AddressLine2>George Street</AddressLine2>
      <AddressLine3>Aberdeen</AddressLine3>
      <PostCode>AB25 1HZ</PostCode>
      <RatingValue>Improvement Required</RatingValue>
      <RatingKey>fhis_improvement_required_en-GB</RatingKey>
      <RatingDate>2023-07-28</RatingDate>
      <LocalAuthorityCode>760</LocalAuthorityCode>
      <LocalAuthorityName>Aberdeen City</LocalAuthorityName>
      <Scores />
      <SchemeType>FHIS</SchemeType>
      <NewRatingPending>False</NewRatingPending>
      <Geocode><Longitude>-2.10076438</Longitude><Latitude>57.14955652</Latitude></Geocode>
    </EstablishmentDetail>
  </EstablishmentCollection>
</FHRSEstablishment>"""


def test_parse_bulk_file_header():
    parsed = parse_bulk_file(SAMPLE_BULK_XML)
    assert parsed.header.extract_date == "2026-08-19"
    assert parsed.header.item_count == 3
    assert parsed.header.return_code == "Success"


def test_parse_bulk_file_establishment_count():
    parsed = parse_bulk_file(SAMPLE_BULK_XML)
    assert len(parsed.establishments) == 3
    assert parsed.skipped_records == 0


def test_numeric_rating_value_parsed_to_int():
    parsed = parse_bulk_file(SAMPLE_BULK_XML)
    cleeve = next(e for e in parsed.establishments if e["fhrsid"] == 1889918)
    assert cleeve["rating_value"] == "4"
    assert cleeve["rating_value_numeric"] == 4
    assert cleeve["hygiene_score"] == 5


def test_exempt_rating_has_no_numeric_value_or_date():
    parsed = parse_bulk_file(SAMPLE_BULK_XML)
    exempt = next(e for e in parsed.establishments if e["fhrsid"] == 1777436)
    assert exempt["rating_value"] == "Exempt"
    assert exempt["rating_value_numeric"] is None
    assert exempt["rating_date"] is None
    assert exempt["hygiene_score"] is None


def test_fhis_scheme_rating_value_is_non_numeric_string():
    """Scotland's FHIS scheme uses a different vocabulary to FHRS -- this
    wasn't in the original brief and must not be coerced into a number."""
    parsed = parse_bulk_file(SAMPLE_BULK_XML)
    scottish = next(e for e in parsed.establishments if e["fhrsid"] == 1608170)
    assert scottish["scheme_type"] == "FHIS"
    assert scottish["rating_value"] == "Improvement Required"
    assert scottish["rating_value_numeric"] is None


def test_sparse_address_lines_do_not_assume_which_are_present():
    parsed = parse_bulk_file(SAMPLE_BULK_XML)
    scottish = next(e for e in parsed.establishments if e["fhrsid"] == 1608170)
    assert scottish["address_line_1"] is None
    assert scottish["address_line_2"] == "George Street"
    assert scottish["address_line_3"] == "Aberdeen"
    assert scottish["address_line_4"] is None


def test_populated_geocode_parsed():
    parsed = parse_bulk_file(SAMPLE_BULK_XML)
    scottish = next(e for e in parsed.establishments if e["fhrsid"] == 1608170)
    assert scottish["longitude"] == -2.10076438
    assert scottish["latitude"] == 57.14955652


def test_empty_geocode_element_yields_none():
    parsed = parse_bulk_file(SAMPLE_BULK_XML)
    cleeve = next(e for e in parsed.establishments if e["fhrsid"] == 1889918)
    assert cleeve["longitude"] is None
    assert cleeve["latitude"] is None


def test_missing_fhrsid_raises():
    el = ET.fromstring("<EstablishmentDetail><BusinessName>No ID</BusinessName></EstablishmentDetail>")
    try:
        parse_establishment(el)
        assert False, "expected RecordParseError"
    except RecordParseError:
        pass


def test_fingerprint_stable_for_identical_input():
    parsed = parse_bulk_file(SAMPLE_BULK_XML)
    a = parsed.establishments[0]
    b = dict(a)
    assert compute_fingerprint(a) == compute_fingerprint(b)


def test_fingerprint_changes_when_a_field_changes():
    parsed = parse_bulk_file(SAMPLE_BULK_XML)
    a = parsed.establishments[0]
    b = dict(a, rating_value="5", rating_value_numeric=5)
    assert compute_fingerprint(a) != compute_fingerprint(b)


def test_fingerprint_ignores_fhrsid_and_authority_fields():
    """fhrsid/authority_code aren't in the fingerprint -- they're identity,
    not comparable state, and are handled separately during ingest."""
    parsed = parse_bulk_file(SAMPLE_BULK_XML)
    a = parsed.establishments[0]
    b = dict(a, fhrsid=999999999)
    assert compute_fingerprint(a) == compute_fingerprint(b)


def test_fingerprint_ignores_geocode():
    """Measured against real data: 86% of detected changes were geocode-only,
    and almost all of those were the Geocode element appearing/disappearing
    between days rather than a real change -- see the comment above
    _FINGERPRINT_FIELDS. A geocode-only difference must not register as a
    change, or the observation log drowns in this noise."""
    parsed = parse_bulk_file(SAMPLE_BULK_XML)
    a = parsed.establishments[0]
    b = dict(a, longitude=-2.10076438, latitude=57.14955652)
    assert compute_fingerprint(a) == compute_fingerprint(b)

    c = dict(a, longitude=None, latitude=None)
    assert compute_fingerprint(a) == compute_fingerprint(c)
