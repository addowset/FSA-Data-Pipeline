"""Fetches recent hospitality-sector incorporations from Companies House's
Advanced Company Search endpoint.

Verified against Companies House's own developer documentation before
writing this (not assumed): auth is HTTP Basic with the API key as
username and a blank password; the Advanced Search response schema is
{"etag", "hits", "items": [...]}` with each item shaped like
{company_name, company_number, company_status, company_subtype,
company_type, date_of_cessation, date_of_creation, kind, links,
registered_office_address: {address_line_1, address_line_2, country,
locality, postal_code, region}, sic_codes: [...]}`. Documented gotcha from
community write-ups (not yet independently confirmed against a live
response -- no API key was available while writing this module, see
README "Design notes"): registered_office_address field population is
inconsistent between companies, and date_of_creation can be absent.

Unlike FHRS's per-authority static bulk files, an Advanced Search query is
a paginated result set for a rolling date window that's re-run daily. Each
page's raw response is archived unmodified, one gzip file per page, using
the same never-overwrite archive.write_gzip_atomic as FHRS. On a same-day
resume, already-downloaded pages are skipped; the total hit count from
page 0 determines how many pages exist.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from fsa_pipeline.config import Config

API_KEY_ENV_VAR = "COMPANIES_HOUSE_API_KEY"


class FetchError(Exception):
    pass


class MissingApiKey(Exception):
    pass


def get_api_key() -> str:
    key = os.environ.get(API_KEY_ENV_VAR)
    if not key:
        raise MissingApiKey(
            f"{API_KEY_ENV_VAR} is not set. Register for a free API key at "
            "developer.company-information.service.gov.uk, then set it as an "
            "environment variable (never commit it)."
        )
    return key


def build_session(api_key: str, config: Config) -> requests.Session:
    session = requests.Session()
    session.auth = (api_key, "")
    session.headers.update({"User-Agent": config.user_agent})

    retry = Retry(
        total=config.ch_max_retries,
        backoff_factor=config.ch_backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def incorporated_date_window(config: Config, as_of: dt.date) -> tuple[str, str]:
    incorporated_from = as_of - dt.timedelta(days=config.ch_incorporated_window_days)
    return incorporated_from.isoformat(), as_of.isoformat()


def fetch_page(
    session: requests.Session,
    config: Config,
    incorporated_from: str,
    incorporated_to: str,
    start_index: int,
) -> bytes:
    params = {
        "sic_codes": ",".join(config.ch_sic_codes),
        "incorporated_from": incorporated_from,
        "incorporated_to": incorporated_to,
        "size": config.ch_page_size,
        "start_index": start_index,
    }
    response = session.get(config.ch_advanced_search_url, params=params, timeout=config.ch_timeout_seconds)
    response.raise_for_status()
    content = response.content

    if not content:
        raise FetchError(f"empty response body for start_index={start_index}")

    return content


def hits_from_page_bytes(page_bytes: bytes) -> int:
    data = json.loads(page_bytes)
    return int(data.get("hits", 0))


def items_from_page_bytes(page_bytes: bytes) -> list[dict]:
    data = json.loads(page_bytes)
    return data.get("items", [])


class RecordParseError(Exception):
    pass


# Fields included in the change-detection fingerprint, in a fixed order.
# Identity (company_number) is deliberately excluded, same reasoning as
# FHRSID in fhrs_parse.py.
_FINGERPRINT_FIELDS = (
    "company_name", "company_status", "company_subtype", "company_type",
    "date_of_creation", "date_of_cessation",
    "address_line_1", "address_line_2", "locality", "region", "postal_code", "country",
    "sic_codes",
)


def parse_company_item(item: dict) -> dict:
    """Normalizes one Advanced Search result item to our column shape.

    registered_office_address field population is documented (by
    community write-ups, not yet independently confirmed against a live
    response) as inconsistent between companies -- every address field is
    read defensively with .get(), same discipline as FHRS's sparse
    address lines.
    """
    company_number = item.get("company_number")
    if not company_number:
        raise RecordParseError("missing company_number")

    address = item.get("registered_office_address") or {}
    sic_codes = item.get("sic_codes") or []

    return {
        "company_number": company_number,
        "company_name": item.get("company_name"),
        "company_status": item.get("company_status"),
        "company_subtype": item.get("company_subtype"),
        "company_type": item.get("company_type"),
        "date_of_creation": item.get("date_of_creation"),
        "date_of_cessation": item.get("date_of_cessation"),
        "address_line_1": address.get("address_line_1"),
        "address_line_2": address.get("address_line_2"),
        "locality": address.get("locality"),
        "region": address.get("region"),
        "postal_code": address.get("postal_code"),
        "country": address.get("country"),
        "sic_codes": json.dumps(sorted(sic_codes)),
    }


def compute_company_fingerprint(company: dict) -> str:
    canonical = json.dumps([company.get(f) for f in _FINGERPRINT_FIELDS], sort_keys=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
