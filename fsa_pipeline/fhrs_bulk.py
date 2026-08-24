"""Fetches the FHRS local-authority index and per-authority bulk XML files.

Stage 1 scope only: download raw bytes and archive them unmodified. No XML
parsing of establishment records happens here — that's stage 2. The one
exception is the authorities index itself, which we do parse, because we
need each authority's FileName URL to know what to download.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

from fsa_pipeline.config import Config


class FetchError(Exception):
    """Raised when a download fails integrity checks or returns no data."""


@dataclass(frozen=True)
class Authority:
    local_authority_id: int
    code: str  # LocalAuthorityIdCode, e.g. "857"
    name: str
    friendly_name: str
    region_name: str
    file_url: str
    establishment_count: int
    url: str = ""
    email: str = ""

    @property
    def archive_filename(self) -> str:
        stem = self.friendly_name or self.code
        return f"{self.code}_{stem}.xml.gz"


def fetch_raw(session: requests.Session, url: str, timeout: float) -> bytes:
    """GET a URL and return raw bytes, with basic integrity checks.

    Retries for transient HTTP errors are handled by the session's mounted
    Retry adapter. This function only handles the final response.
    """
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    content = response.content

    if not content:
        raise FetchError(f"empty response body from {url}")
    if not content.lstrip().startswith(b"<?xml") and not content.lstrip().startswith(b"<"):
        raise FetchError(f"response from {url} does not look like XML (first 80 bytes: {content[:80]!r})")

    declared_length = response.headers.get("Content-Length")
    if declared_length is not None and int(declared_length) != len(content):
        raise FetchError(
            f"length mismatch for {url}: Content-Length={declared_length} but received {len(content)} bytes"
        )

    return content


def fetch_authorities_raw(session: requests.Session, config: Config) -> bytes:
    return fetch_raw(session, config.authorities_url, config.timeout_seconds)


def parse_authorities(xml_bytes: bytes) -> list[Authority]:
    """Parse the authorities/xml response, excluding placeholder/nation rows.

    The live endpoint includes 5 non-authority rows (LocalAuthorityId -1..-4,
    representing "All"/England/Scotland/Wales/Northern Ireland aggregates)
    with no FileName and EstablishmentCount=1. These are filtered out.
    """
    root = ET.fromstring(xml_bytes)
    authorities: list[Authority] = []

    for el in root.findall("WebLocalAuthorityAPI"):
        local_authority_id = int(el.findtext("LocalAuthorityId", "0"))
        file_url = (el.findtext("FileName") or "").strip()

        if local_authority_id <= 0 or not file_url:
            continue

        authorities.append(
            Authority(
                local_authority_id=local_authority_id,
                code=(el.findtext("LocalAuthorityIdCode") or "").strip(),
                name=(el.findtext("Name") or "").strip(),
                friendly_name=(el.findtext("FriendlyName") or "").strip(),
                region_name=(el.findtext("RegionName") or "").strip(),
                file_url=file_url,
                establishment_count=int(el.findtext("EstablishmentCount", "0")),
                url=(el.findtext("Url") or "").strip(),
                email=(el.findtext("Email") or "").strip(),
            )
        )

    return authorities


def fetch_authority_bulk(session: requests.Session, authority: Authority, config: Config) -> bytes:
    return fetch_raw(session, authority.file_url, config.timeout_seconds)
