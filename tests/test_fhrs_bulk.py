from fsa_pipeline.fhrs_bulk import FetchError, fetch_raw, parse_authorities

# Trimmed real sample from https://api1-ratings.food.gov.uk/authorities/xml,
# kept close to the live shape: placeholder/nation rows with no FileName
# (which must be excluded), plus two real authorities.
SAMPLE_AUTHORITIES_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<ArrayOfWebLocalAuthorityAPI xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <WebLocalAuthorityAPI>
    <LocalAuthorityId>-1</LocalAuthorityId>
    <LocalAuthorityIdCode>^</LocalAuthorityIdCode>
    <Name>All</Name>
    <FriendlyName />
    <FileName />
    <EstablishmentCount>1</EstablishmentCount>
  </WebLocalAuthorityAPI>
  <WebLocalAuthorityAPI>
    <LocalAuthorityId>-1</LocalAuthorityId>
    <LocalAuthorityIdCode>england</LocalAuthorityIdCode>
    <Name>England</Name>
    <FriendlyName />
    <FileName />
    <EstablishmentCount>1</EstablishmentCount>
  </WebLocalAuthorityAPI>
  <WebLocalAuthorityAPI>
    <LocalAuthorityId>197</LocalAuthorityId>
    <LocalAuthorityIdCode>760</LocalAuthorityIdCode>
    <Name>Aberdeen City</Name>
    <FriendlyName>aberdeen-city</FriendlyName>
    <RegionName>Scotland</RegionName>
    <FileName>https://ratings.food.gov.uk/OpenDataFiles/FHRS760en-GB.xml</FileName>
    <EstablishmentCount>2202</EstablishmentCount>
  </WebLocalAuthorityAPI>
  <WebLocalAuthorityAPI>
    <LocalAuthorityId>282</LocalAuthorityId>
    <LocalAuthorityIdCode>857</LocalAuthorityIdCode>
    <Name>Bath and North East Somerset</Name>
    <FriendlyName>bath-and-north-east-somerset</FriendlyName>
    <RegionName>South West</RegionName>
    <FileName>https://ratings.food.gov.uk/OpenDataFiles/FHRS857en-GB.xml</FileName>
    <EstablishmentCount>1841</EstablishmentCount>
  </WebLocalAuthorityAPI>
</ArrayOfWebLocalAuthorityAPI>"""


def test_parse_authorities_excludes_placeholder_rows():
    authorities = parse_authorities(SAMPLE_AUTHORITIES_XML)

    codes = {a.code for a in authorities}
    assert codes == {"760", "857"}


def test_parse_authorities_reads_expected_fields():
    authorities = parse_authorities(SAMPLE_AUTHORITIES_XML)
    banes = next(a for a in authorities if a.code == "857")

    assert banes.name == "Bath and North East Somerset"
    assert banes.friendly_name == "bath-and-north-east-somerset"
    assert banes.file_url == "https://ratings.food.gov.uk/OpenDataFiles/FHRS857en-GB.xml"
    assert banes.establishment_count == 1841


def test_archive_filename_is_stable_and_readable():
    authorities = parse_authorities(SAMPLE_AUTHORITIES_XML)
    banes = next(a for a in authorities if a.code == "857")

    assert banes.archive_filename == "857_bath-and-north-east-somerset.xml.gz"


class _FakeResponse:
    def __init__(self, content: bytes, headers: dict | None = None):
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        pass


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response

    def get(self, url, timeout):
        return self._response


def test_fetch_raw_rejects_empty_body():
    session = _FakeSession(_FakeResponse(b""))
    try:
        fetch_raw(session, "https://example.com", timeout=1)
        assert False, "expected FetchError"
    except FetchError:
        pass


def test_fetch_raw_rejects_non_xml_body():
    session = _FakeSession(_FakeResponse(b"<html>not xml-ish enough? actually starts with <"))
    # This one *does* start with '<' so should pass the shape check.
    fetch_raw(session, "https://example.com", timeout=1)

    session = _FakeSession(_FakeResponse(b"Internal Server Error"))
    try:
        fetch_raw(session, "https://example.com", timeout=1)
        assert False, "expected FetchError"
    except FetchError:
        pass


def test_fetch_raw_rejects_content_length_mismatch():
    session = _FakeSession(_FakeResponse(b"<xml/>", headers={"Content-Length": "999"}))
    try:
        fetch_raw(session, "https://example.com", timeout=1)
        assert False, "expected FetchError"
    except FetchError:
        pass
