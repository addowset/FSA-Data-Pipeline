"""Stage 7 of the brief: CSV export, the actual customer-facing
deliverable.

Reads only from the database, no new collection or computation of
classification evidence -- everything here is a projection over what
stage 5 already decided. Quarantined INSERT events (the bulk-reupload
guard, fsa_pipeline/diff_engine.py) are always excluded: they're a
suspected data artefact, never real signal, and this export is exactly
the point where sending one to a customer would do real damage.

Also always excluded: an establishment whose business_name contains
"closed" (case-insensitive). Real case found 2026-09-11 reviewing a
Highland records: councils sometimes annotate a closure directly in the
name field ("Isle of Muck Tearoom/craftshop CLOSED") rather than
removing the record -- 19 of 21 real "closed"-containing names in the
archive are genuine closure markers, not part of a real trading name
(the 2 exceptions, "The Closed Shop" and "Behind Closed Doors", are an
accepted, rare false-positive cost against a much worse one -- a
closure showing up in a "new venue" feed).

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

`reason` is a short, customer-facing summary built compositionally from
already-clean structured fields (never by sanitizing the free-text
diagnostic string, which is fragile). `reason_detail` carries the full
original diagnostic text (name-similarity scores, match strategy,
threshold arithmetic) for a buyer or analyst who wants it -- useful, but
the wrong first thing to show: "Closest match was THE BOND BAKERY LTD at
similarity 0.0777" reads as a confession that the matcher is unreliable,
not as a status. See customer_reason() for the exact composition rules.

`NOISE_BUSINESS_TYPES` are excluded from the CSV by default (not from
the database -- collection keeps everything, this is an export-layer
decision only). Confirmed 2026-09-11 via two independent reviews of a
real export: Schools, Hospitals/Childcare, Manufacturers, Distributors,
Farmers, and Importers/Exporters aren't buyers this product's typical
customer (a drinks wholesaler, a coffee roaster) can sell to -- 810 real
rows. Unlike the business-type *filter* (matches_filters,
--business-type), which narrows an already-intentional selection, this
is a default scope decision -- --include-all-types opts back in, kept
available because a contract-catering feed selling into exactly these
categories is a plausible second product later.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from urllib.parse import quote

from fsa_pipeline.matcher import normalize_postcode_area

FHRS_URL_TEMPLATE = "https://ratings.food.gov.uk/business/{fhrsid}"
COMPANIES_HOUSE_URL_TEMPLATE = "https://find-and-update.company-information.service.gov.uk/company/{number}"
GOOGLE_MAPS_URL_TEMPLATE = "https://www.google.com/maps/search/?api=1&query={query}"

# Excluded from the CSV export by default -- see module docstring for
# why (confirmed 810 real rows, two independent reviews agreeing these
# aren't buyers this product's typical customer can sell to). The exact
# 14 real business_type values were confirmed against the database
# 2026-09-11 -- these six, verbatim, not a substring match.
NOISE_BUSINESS_TYPES = {
    "School/college/university",
    "Hospitals/Childcare/Caring Premises",
    "Manufacturers/packers",
    "Distributors/Transporters",
    "Farmers/growers",
    "Importers/Exporters",
}


def is_noise_business_type(business_type: str | None) -> bool:
    return business_type in NOISE_BUSINESS_TYPES

# Column order: identity/location, authority, business type/rating,
# timing, classification summary, predecessor (operator-change), company
# match, links. Matches the brief's exact list (business name, address,
# postcode, business type, first-seen date, classification, confidence,
# reason, link to FHRS record, company number, incorporation date "where
# matched") plus the rest, confirmed with the user 2026-09-11 after a
# second Claude session's review of a real export -- see README "Design
# notes" for which of that review's suggestions were verified real
# (company_name, previous_operator fields, lat/long, days_since_first_seen,
# rating_status, sic_codes, postcode_source, authority_extract_date, the
# two URL columns, splitting reason) versus checked and found not to
# apply as suggested (company_status -- currently constant "active" by
# construction, matched companies are never anything else; "town" --
# address lines aren't consistently ordered enough to extract one
# reliably, not built).
EXPORT_FIELDNAMES = [
    "fhrsid",
    "business_name",
    "address",
    "postcode",
    "postcode_source",
    "address_completeness",
    "latitude",
    "longitude",
    "google_maps_url",
    "authority_name",
    "authority_extract_date",
    "business_type",
    "rating_status",
    "first_seen_date",
    "days_since_first_seen",
    "classification",
    "confidence",
    "reason",
    "reason_detail",
    "previous_business_name",
    "previous_fhrsid",
    "previous_last_seen",
    "match_status",
    "company_name",
    "company_number",
    "company_status",
    "incorporation_date",
    "sic_codes",
    "companies_house_url",
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


# Real distinct rating_value strings checked 2026-09-11 (both spaced and
# unspaced variants exist in the wild, e.g. "AwaitingInspection" and
# "Awaiting Inspection" -- normalize before matching, don't assume one
# form). Numeric strings and the two qualitative "rated" values collapse
# to "Rated"; the brief's own note ("RatingValue can be a number or a
# string such as AwaitingInspection, Exempt, AwaitingPublication") is
# exactly what this maps.
_RATED_VALUES = {"0", "1", "2", "3", "4", "5", "Pass", "Pass and Eat Safe", "Improvement Required"}


def rating_status(rating_value: str | None) -> str:
    if not rating_value:
        return "Unknown"
    if rating_value in _RATED_VALUES:
        return "Rated"
    normalized = rating_value.replace(" ", "").lower()
    if normalized == "awaitinginspection":
        return "Awaiting Inspection"
    if normalized == "awaitingpublication":
        return "Awaiting Publication"
    if normalized == "exempt":
        return "Exempt"
    return "Unknown"


def days_since_first_seen(first_seen_date: str | None, as_of: dt.date | None = None) -> int | None:
    if not first_seen_date:
        return None
    as_of = as_of or dt.date.today()
    return (as_of - dt.date.fromisoformat(first_seen_date)).days


def fhrs_url(fhrsid: int) -> str:
    return FHRS_URL_TEMPLATE.format(fhrsid=fhrsid)


def companies_house_url(company_number: str | None) -> str:
    if not company_number:
        return ""
    return COMPANIES_HOUSE_URL_TEMPLATE.format(number=company_number)


def google_maps_url(address: str, postcode: str | None) -> str:
    query = ", ".join(part for part in (address, postcode) if part)
    if not query:
        return ""
    return GOOGLE_MAPS_URL_TEMPLATE.format(query=quote(query))


def format_sic_codes(sic_codes_json: str | None) -> str:
    if not sic_codes_json:
        return ""
    try:
        codes = json.loads(sic_codes_json)
    except (TypeError, ValueError):
        return ""
    return ", ".join(codes)


def customer_reason(record: dict) -> str:
    """Short, customer-facing summary -- composed from clean structured
    fields, never by sanitizing the free-text diagnostic string (see
    module docstring for why). Mirrors, but simplifies, the same
    branches classify() itself uses (fsa_pipeline/classifier.py), so it
    always stays a plain-language restatement of the real decision, not
    an independent guess at one."""
    classification = record["classification"]
    company_name = record.get("company_name")
    previous_business_name = record.get("previous_business_name")

    if classification == "NEW_VENUE":
        if company_name:
            return f"New registration, matched to Companies House company \"{company_name}\"."
        return "New registration."

    if classification == "OPERATOR_CHANGE":
        if previous_business_name and previous_business_name != record["business_name"]:
            return f"Operator change -- this address previously traded as \"{previous_business_name}\"."
        if previous_business_name:
            return "Possible operator change -- kept the same trading name as the previous record; not company-confirmed."
        return "Operator change."

    # UNKNOWN
    if record.get("evidence_company_number"):
        return f"Matched to Companies House company \"{company_name}\", but not confirmed as a new registration."
    return "No confirmed company match."


def build_row(record: dict) -> dict:
    """record is one raw row from fetch_records's query (or an
    equivalent dict in tests) -- see EXPORT_FIELDNAMES for the shape of
    what this returns."""
    postcode = record["post_code"] or record["postcode_from_live_api"]
    match_status = "Yes" if record["evidence_company_number"] else "No"
    address = join_address(
        record["address_line_1"], record["address_line_2"], record["address_line_3"], record["address_line_4"],
    )
    return {
        "fhrsid": record["fhrsid"],
        "business_name": record["business_name"],
        "address": address,
        "postcode": postcode or "",
        "postcode_source": record["postcode_source"] or "",
        "address_completeness": address_completeness(
            record["address_line_1"], record["address_line_2"], record["address_line_3"],
            record["address_line_4"], postcode,
        ),
        "latitude": record["latitude"] if record["latitude"] is not None else "",
        "longitude": record["longitude"] if record["longitude"] is not None else "",
        "google_maps_url": google_maps_url(address, postcode),
        "authority_name": record["authority_name"],
        "authority_extract_date": record["authority_extract_date"] or "",
        "business_type": record["business_type"],
        "rating_status": rating_status(record["rating_value"]),
        "first_seen_date": record["first_seen_date"],
        "days_since_first_seen": days_since_first_seen(record["first_seen_date"]),
        "classification": record["classification"],
        "confidence": record["confidence"],
        "reason": customer_reason(record),
        "reason_detail": record["reason"],
        "previous_business_name": record["previous_business_name"] or "",
        "previous_fhrsid": record["previous_fhrsid"] or "",
        "previous_last_seen": record["previous_last_seen"] or "",
        "match_status": match_status,
        "company_name": record["company_name"] or "",
        "company_number": record["evidence_company_number"] or "",
        "company_status": record["company_status"] or "",
        "incorporation_date": record["incorporation_date"] or "",
        "sic_codes": format_sic_codes(record["sic_codes"]),
        "companies_house_url": companies_house_url(record["evidence_company_number"]),
        "fhrs_url": fhrs_url(record["fhrsid"]),
    }


def fetch_records(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    """Every non-quarantined, non-closure classified INSERT event,
    joined to its establishment, authority, matched company (if any),
    and predecessor establishment (if any, for OPERATOR_CHANGE's
    previous_* columns -- establishments_current never deletes a row
    when a business closes/is replaced, confirmed 2026-09-11, so this
    join always finds a departed predecessor's last-known values).
    since/until filter on first_seen_date (inclusive) and are pushed to
    SQL; every other filter (postcode area, authority, business type,
    classification) is applied in Python over this result by the caller
    -- see scripts/export_csv.py -- since the dataset is a few thousand
    rows, not large enough to need them pushed down too, and
    postcode-area matching in particular needs the same
    normalize_postcode_area used everywhere else, not a second SQL-side
    implementation of it."""
    query = """
        SELECT c.fhrsid, c.classification, c.confidence, c.reason, c.evidence_company_number,
               e.business_name, e.business_type, e.address_line_1, e.address_line_2,
               e.address_line_3, e.address_line_4, e.post_code, e.postcode_from_live_api,
               e.postcode_source, e.longitude, e.latitude, e.rating_value,
               e.first_seen_date, e.authority_code, a.name AS authority_name,
               (SELECT MAX(cr.extract_date) FROM collection_runs cr
                WHERE cr.authority_code = e.authority_code AND cr.status = 'ok') AS authority_extract_date,
               comp.company_name, comp.company_status, comp.date_of_creation AS incorporation_date,
               comp.sic_codes,
               prev.business_name AS previous_business_name, prev.fhrsid AS previous_fhrsid,
               prev.last_seen_date AS previous_last_seen
        FROM classifications c
        JOIN establishments_current e ON e.fhrsid = c.fhrsid
        JOIN authorities a ON a.code = e.authority_code
        JOIN diff_events d
          ON d.fhrsid = c.fhrsid AND d.collection_date = c.insert_collection_date
             AND d.authority_code = c.authority_code AND d.event_type = 'INSERT'
        LEFT JOIN companies_current comp ON comp.company_number = c.evidence_company_number
        LEFT JOIN establishments_current prev ON prev.fhrsid = c.evidence_predecessor_fhrsid
        WHERE d.quarantined = 0
          AND e.business_name NOT LIKE '%closed%'
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
