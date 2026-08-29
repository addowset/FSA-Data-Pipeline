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


# Confirmed by direct testing 2026-08-29, not documented anywhere found:
# Advanced Search enforces start_index + size <= 10,000 for any single
# query (exact boundary -- 9999+1 succeeds, 10000+1 returns HTTP 500),
# the classic Elasticsearch default max_result_window. No page size or
# retry strategy gets past this; a query with more matches than that is
# fundamentally unreachable via offset pagination alone. The fix is to
# slice the query by date range, recursively, until every slice's own
# hit count is safely under the ceiling, then paginate normally within
# each slice. See compute_date_slices.
MAX_RESULT_WINDOW = 10_000
SAFE_SLICE_SIZE = 9_000  # margin below the hard ceiling
EARLIEST_PLAUSIBLE_INCORPORATION_DATE = dt.date(1900, 1, 1)


def fetch_hits_count(session: requests.Session, config: Config, incorporated_from: str | None, incorporated_to: str | None) -> int:
    """A cheap size=1 request purely to learn how many results a date
    range would return, for compute_date_slices."""
    params = {**_base_params(config), "size": 1, "start_index": 0}
    if incorporated_from is not None:
        params["incorporated_from"] = incorporated_from
    if incorporated_to is not None:
        params["incorporated_to"] = incorporated_to
    response = session.get(config.ch_advanced_search_url, params=params, timeout=config.ch_timeout_seconds)
    response.raise_for_status()
    return hits_from_page_bytes(response.content)


def compute_date_slices(
    session: requests.Session,
    config: Config,
    incorporated_from: str | None,
    incorporated_to: str | None,
) -> list[tuple[str, str, int]]:
    """Recursively bisects [incorporated_from, incorporated_to] until
    every slice's hit count is safely under MAX_RESULT_WINDOW. Returns
    (from, to, hits) tuples covering the whole range with no gaps or
    overlaps. incorporated_from=None means EARLIEST_PLAUSIBLE_INCORPORATION_DATE;
    incorporated_to=None means today."""
    from_date = dt.date.fromisoformat(incorporated_from) if incorporated_from else EARLIEST_PLAUSIBLE_INCORPORATION_DATE
    to_date = dt.date.fromisoformat(incorporated_to) if incorporated_to else dt.date.today()

    hits = fetch_hits_count(session, config, from_date.isoformat(), to_date.isoformat())

    if hits <= SAFE_SLICE_SIZE:
        return [(from_date.isoformat(), to_date.isoformat(), hits)] if hits > 0 else []

    if from_date >= to_date:
        # Can't bisect a single day any further -- extremely unlikely for
        # a SIC-filtered slice, but if it happens, this slice's tail
        # beyond the ceiling is simply unreachable via this API. Return
        # it anyway so the caller can at least fetch what it can and log
        # the shortfall, rather than silently dropping the day.
        return [(from_date.isoformat(), to_date.isoformat(), hits)]

    midpoint = from_date + (to_date - from_date) // 2
    left = compute_date_slices(session, config, from_date.isoformat(), midpoint.isoformat())
    right = compute_date_slices(session, config, (midpoint + dt.timedelta(days=1)).isoformat(), to_date.isoformat())
    return left + right


def _base_params(config: Config) -> dict:
    """sic_codes + company_status=active, shared by every query. Active-
    only decided with the user 2026-08-29: a dissolved company from
    decades ago can't plausibly be the match for a business that just
    registered with FSA, OWNERSHIP_CHANGE detection doesn't need it
    either (that's entirely FHRS-side, see classifier.py), and including
    dissolved companies would have meant fetching 641,539 records instead
    of ~260,000 for no matching benefit -- pure false-positive risk for
    the national name-match channel plus 2.5x the storage/search cost."""
    return {"sic_codes": ",".join(config.ch_sic_codes), "company_status": "active"}


def fetch_page(
    session: requests.Session,
    config: Config,
    incorporated_from: str | None,
    incorporated_to: str | None,
    start_index: int,
) -> bytes:
    """incorporated_from/incorporated_to are None for a full-history fetch
    (no date bound at all) -- used for the one-time backfill, see
    scripts/collect_companies_house.py --full-history. The Advanced
    Search API treats an omitted bound as "no limit" on that side."""
    params = {**_base_params(config), "size": config.ch_page_size, "start_index": start_index}
    if incorporated_from is not None:
        params["incorporated_from"] = incorporated_from
    if incorporated_to is not None:
        params["incorporated_to"] = incorporated_to
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
