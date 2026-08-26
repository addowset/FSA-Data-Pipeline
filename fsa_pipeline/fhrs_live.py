"""Fetches establishment data from the live FHRS Establishments API.

Used only for postcode backfill (see fsa_pipeline/db.py and
scripts/backfill_postcodes.py), not as a general replacement for bulk
collection. Verified against real data (2026-08-25/26, see README "Design
notes"): the live API returns the same FHRSID set as the bulk file for
every authority tested, and the same RatingValue for every shared
FHRSID -- it is not a fresher source of new registrations or rating
changes for the authorities tested, only of postcodes bulk sometimes
lacks (and even then, only partially -- some gaps are missing at the
source and the live API doesn't have them either).

Endpoint confirmed by direct testing, not assumed: GET
https://api.ratings.food.gov.uk/Establishments?localAuthorityId=<internal
LocalAuthorityId, NOT the LocalAuthorityIdCode used in bulk file URLs>&
pageNumber=<n>&pageSize=<n>, with an x-api-version: 2 header (same
requirement as documented for the live search API generally). Response
is {"establishments": [...], "meta": {"totalCount", "totalPages", ...}}.
"""

from __future__ import annotations

import json

import requests

from fsa_pipeline.config import Config


class FetchError(Exception):
    pass


def fetch_page(
    session: requests.Session,
    config: Config,
    local_authority_id: int,
    page_number: int,
) -> bytes:
    params = {
        "localAuthorityId": local_authority_id,
        "pageNumber": page_number,
        "pageSize": config.live_page_size,
    }
    response = session.get(config.live_establishments_url, params=params, timeout=config.live_timeout_seconds)
    response.raise_for_status()
    content = response.content

    if not content:
        raise FetchError(f"empty response body for localAuthorityId={local_authority_id} page={page_number}")

    return content


def meta_from_page_bytes(page_bytes: bytes) -> dict:
    return json.loads(page_bytes).get("meta", {})


def postcodes_from_page_bytes(page_bytes: bytes) -> dict[int, str | None]:
    """Maps FHRSID -> PostCode (or None if the live API also has none) for
    every establishment in this page. Only extracts what postcode backfill
    needs -- this is deliberately not a full establishment parser."""
    data = json.loads(page_bytes)
    result = {}
    for item in data.get("establishments", []):
        fhrsid = item.get("FHRSID")
        if fhrsid is None:
            continue
        result[int(fhrsid)] = item.get("PostCode") or None
    return result
