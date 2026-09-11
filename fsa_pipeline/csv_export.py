"""Stage 7 of the brief: CSV export, the actual customer-facing
deliverable.

Reads only from the database, no new collection or computation of
classification evidence -- everything here is a projection over what
stage 5 already decided. Quarantined INSERT events (the bulk-reupload
guard, fsa_pipeline/diff_engine.py) are always excluded: they're a
suspected data artefact, never real signal, and this export is exactly
the point where sending one to a customer would do real damage.

No filtering by match status (a Companies House match is a
classification signal, not a contact channel -- see README "Design
notes"; a supplier reaches a venue by turning up, and an unmatched
record skews toward the independent, owner-operated businesses that are
often the better prospect, not the worse one). `match_status` is a
plain visible column instead, same treatment as `classification` and
`confidence` -- let the buyer filter it themselves if they want to.

`address_completeness` exists because match status turned out not to be
the only axis a lead's usability depends on: checked against real data
2026-09-11, every one of the ~95,000 establishments whose only postcode
comes from the live-API backfill has NO address lines at all (business
name and a bare district code only) -- a completely different kind of
"not fully usable" than an unmatched company. See _address_completeness
below for the exact rule and its real-data grounding.
"""

from __future__ import annotations

import sqlite3

from fsa_pipeline.matcher import normalize_postcode_area

FHRS_URL_TEMPLATE = "https://ratings.food.gov.uk/business/{fhrsid}"

# Column order for the CSV. Matches the brief's exact list (business
# name, address, postcode, business type, first-seen date,
# classification, confidence, reason, link to FHRS record, company
# number, incorporation date "where matched") plus four additions,
# confirmed with the user before building: fhrsid and authority_name
# (both useful for a buyer auditing/filtering a row, and authority_name
# doubles as the "local authority" filter's own column), match_status
# and address_completeness (see module docstring).
EXPORT_FIELDNAMES = [
    "fhrsid",
    "authority_name",
    "business_name",
    "address",
    "postcode",
    "address_completeness",
    "business_type",
    "first_seen_date",
    "classification",
    "confidence",
    "reason",
    "match_status",
    "company_number",
    "incorporation_date",
    "fhrs_url",
]


def join_address(line1: str | None, line2: str | None, line3: str | None, line4: str | None) -> str:
    return ", ".join(line for line in (line1, line2, line3, line4) if line)


def _postcode_completeness(postcode: str | None) -> str:
    """"full" or "district_only" or "none" -- a length-based heuristic,
    not a strict validator (a handful of real malformed postcodes, e.g.
    field-truncated or address-bled values, could pass as "full" here;
    confirmed real but rare, 231 of 511,869 non-null bulk postcodes,
    0.05% -- not worth a stricter parser for a descriptive CSV column)."""
    if not postcode:
        return "none"
    cleaned = postcode.strip().upper().replace(" ", "")
    if not cleaned:
        return "none"
    return "full" if len(cleaned) >= 5 else "district_only"


def address_completeness(
    address_line_1: str | None, address_line_2: str | None, address_line_3: str | None,
    address_line_4: str | None, postcode: str | None,
) -> str:
    """One of "full" / "postcode_only" / "district_only" / "no_address".

    Grounded in the real cross-tab checked before building this (2026-09-11):
    address_line_1 alone is a bad completeness signal -- 31.8% of
    establishments have it NULL, but nearly a third of those (97,358)
    have real content in lines 2-4 instead (the council shifted the
    address up a field). COALESCE across all four lines is the real
    "is there a street address at all" test. Separately: not one of the
    ~95,000 live-API-backfilled postcodes is a full postcode -- all of
    them are bare districts (see fsa_pipeline/matcher.py's
    normalize_postcode_district docstring) -- and those same rows have
    zero address lines, 100% of the time. So a full postcode dominates
    usefulness over street-line presence in practice: a satnav gets you
    to the building on a full postcode alone, but only to a wide area on
    a bare district regardless of what the street line says."""
    has_street = any((address_line_1, address_line_2, address_line_3, address_line_4))
    pc = _postcode_completeness(postcode)
    if pc == "none":
        return "no_address"
    if pc == "district_only":
        return "district_only"
    return "full" if has_street else "postcode_only"


def fhrs_url(fhrsid: int) -> str:
    return FHRS_URL_TEMPLATE.format(fhrsid=fhrsid)


def build_row(record: dict) -> dict:
    """record is one raw row from fetch_records's query (or an
    equivalent dict in tests) -- see EXPORT_FIELDNAMES for the shape of
    what this returns."""
    postcode = record["post_code"] or record["postcode_from_live_api"]
    match_status = "Yes" if record["evidence_company_number"] else "No"
    return {
        "fhrsid": record["fhrsid"],
        "authority_name": record["authority_name"],
        "business_name": record["business_name"],
        "address": join_address(
            record["address_line_1"], record["address_line_2"], record["address_line_3"], record["address_line_4"],
        ),
        "postcode": postcode or "",
        "address_completeness": address_completeness(
            record["address_line_1"], record["address_line_2"], record["address_line_3"],
            record["address_line_4"], postcode,
        ),
        "business_type": record["business_type"],
        "first_seen_date": record["first_seen_date"],
        "classification": record["classification"],
        "confidence": record["confidence"],
        "reason": record["reason"],
        "match_status": match_status,
        "company_number": record["evidence_company_number"] or "",
        "incorporation_date": record["incorporation_date"] or "",
        "fhrs_url": fhrs_url(record["fhrsid"]),
    }


def fetch_records(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    """Every non-quarantined classified INSERT event, joined to its
    establishment, authority, and (if matched) the matched company's
    incorporation date. since/until filter on first_seen_date (inclusive)
    and are pushed to SQL; every other filter (postcode area, authority,
    business type, classification) is applied in Python over this
    result by the caller -- see scripts/export_csv.py -- since the
    dataset is a few thousand rows, not large enough to need them
    pushed down too, and postcode-area matching in particular needs the
    same normalize_postcode_area used everywhere else, not a second
    SQL-side implementation of it."""
    query = """
        SELECT c.fhrsid, c.classification, c.confidence, c.reason, c.evidence_company_number,
               e.business_name, e.business_type, e.address_line_1, e.address_line_2,
               e.address_line_3, e.address_line_4, e.post_code, e.postcode_from_live_api,
               e.first_seen_date, e.authority_code, a.name AS authority_name,
               comp.date_of_creation AS incorporation_date
        FROM classifications c
        JOIN establishments_current e ON e.fhrsid = c.fhrsid
        JOIN authorities a ON a.code = e.authority_code
        JOIN diff_events d
          ON d.fhrsid = c.fhrsid AND d.collection_date = c.insert_collection_date
             AND d.authority_code = c.authority_code AND d.event_type = 'INSERT'
        LEFT JOIN companies_current comp ON comp.company_number = c.evidence_company_number
        WHERE d.quarantined = 0
    """
    params: list[str] = []
    if since is not None:
        query += " AND e.first_seen_date >= ?"
        params.append(since)
    if until is not None:
        query += " AND e.first_seen_date <= ?"
        params.append(until)

    cursor = conn.execute(query, params)
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def matches_filters(
    record: dict,
    *,
    postcode_area: str | None = None,
    authority: str | None = None,
    business_type_substring: str | None = None,
    classification: str | None = None,
) -> bool:
    """Applied to a raw fetch_records record (not a built display row --
    it needs authority_code, which isn't an export column). `authority`
    matches either the authority's code exactly, or a case-insensitive
    substring of its name -- code first, since a code is unambiguous and
    the brief's own authority list is keyed by it, falling back to name
    for a human typing "Bristol" rather than "857"."""
    if postcode_area is not None:
        postcode = record["post_code"] or record["postcode_from_live_api"]
        area = normalize_postcode_area(postcode)
        if area != postcode_area.strip().upper():
            return False
    if authority is not None:
        if record["authority_code"] != authority and authority.lower() not in record["authority_name"].lower():
            return False
    if business_type_substring is not None:
        if business_type_substring.lower() not in record["business_type"].lower():
            return False
    if classification is not None:
        if record["classification"] != classification.strip().upper():
            return False
    return True
