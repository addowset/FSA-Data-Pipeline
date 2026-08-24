"""Parses an FHRS bulk XML file's Header and EstablishmentDetail records.

Verified against real data (not the API guidance, which this environment
couldn't extract text from) across an England, a Scotland, and a Northern
Ireland authority. Findings that shape this parser:

- Address lines (AddressLine1-4) are each independently optional -- a
  record can have AddressLine1+3+4 but no AddressLine2, for example. Do
  not assume any subset is present.
- RatingValue vocabulary differs by scheme. England/Wales/NI use FHRS:
  "0".."5", "AwaitingInspection", "AwaitingPublication", "Exempt".
  Scotland uses FHIS, an entirely different scheme with different values:
  "Pass", "Improvement Required", "Pass and Eat Safe", "Awaiting
  Inspection" (note the space -- different string to FHRS's variant),
  "Exempt". Only FHRS values are ever numeric. SchemeType tells you which
  vocabulary you're looking at; store it, don't infer it from RatingValue.
- RatingDate and Scores are absent (nil or empty) when RatingValue is a
  non-numeric pending/exempt state.
- Geocode is present but empty for many records (no Longitude/Latitude).
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# Fields included in the change-detection fingerprint, in a fixed order.
# Deliberately excludes authority-level fields (LocalAuthorityName etc.)
# that live in the `authorities` table, not per-establishment.
#
# Also deliberately excludes longitude/latitude. Measured against three
# real days of national data (2026-08-20 to 08-23): 86% of all detected
# "changes" were geocode-only, and 99.95% of those were the Geocode
# element simply appearing or disappearing between days (11,986
# null->value, 7,297 value->null out of 19,293 total), not coordinate
# jitter -- FSA's own data is inconsistent about whether it includes
# coordinates for a given record on a given day. Including it in the
# fingerprint would drown the observation log in noise unrelated to any
# real-world change. The columns are still stored and kept fresh in
# establishments_current on every touch (see db.py) -- they just don't
# gate whether a change is logged.
_FINGERPRINT_FIELDS = (
    "local_authority_business_id",
    "business_name",
    "business_type",
    "business_type_id",
    "address_line_1",
    "address_line_2",
    "address_line_3",
    "address_line_4",
    "post_code",
    "rating_value",
    "rating_key",
    "rating_date",
    "scheme_type",
    "new_rating_pending",
    "hygiene_score",
    "structural_score",
    "confidence_in_management_score",
)


class RecordParseError(Exception):
    pass


@dataclass(frozen=True)
class BulkFileHeader:
    extract_date: str | None
    item_count: int | None
    return_code: str | None


@dataclass(frozen=True)
class ParsedBulkFile:
    header: BulkFileHeader
    establishments: list[dict]
    skipped_records: int
    skip_reasons: list[str] = field(default_factory=list)


def _text(el: ET.Element, tag: str) -> str | None:
    child = el.find(tag)
    if child is None:
        return None
    if child.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
        return None
    return (child.text or "").strip() or None


def _int(el: ET.Element, tag: str) -> int | None:
    text = _text(el, tag)
    return int(text) if text is not None else None


def _bool(el: ET.Element, tag: str) -> bool | None:
    text = _text(el, tag)
    if text is None:
        return None
    return text.lower() == "true"


def parse_establishment(el: ET.Element) -> dict:
    fhrsid_text = _text(el, "FHRSID")
    if fhrsid_text is None:
        raise RecordParseError("missing FHRSID")

    rating_value = _text(el, "RatingValue")
    rating_value_numeric = int(rating_value) if rating_value is not None and rating_value.isdigit() else None

    scores = el.find("Scores")
    hygiene_score = _int(scores, "Hygiene") if scores is not None else None
    structural_score = _int(scores, "Structural") if scores is not None else None
    confidence_score = _int(scores, "ConfidenceInManagement") if scores is not None else None

    geocode = el.find("Geocode")
    longitude = float(_text(geocode, "Longitude")) if geocode is not None and _text(geocode, "Longitude") else None
    latitude = float(_text(geocode, "Latitude")) if geocode is not None and _text(geocode, "Latitude") else None

    return {
        "fhrsid": int(fhrsid_text),
        "local_authority_business_id": _text(el, "LocalAuthorityBusinessID"),
        "business_name": _text(el, "BusinessName"),
        "business_type": _text(el, "BusinessType"),
        "business_type_id": _int(el, "BusinessTypeID"),
        "address_line_1": _text(el, "AddressLine1"),
        "address_line_2": _text(el, "AddressLine2"),
        "address_line_3": _text(el, "AddressLine3"),
        "address_line_4": _text(el, "AddressLine4"),
        "post_code": _text(el, "PostCode"),
        "rating_value": rating_value,
        "rating_value_numeric": rating_value_numeric,
        "rating_key": _text(el, "RatingKey"),
        "rating_date": _text(el, "RatingDate"),
        "scheme_type": _text(el, "SchemeType"),
        "new_rating_pending": _bool(el, "NewRatingPending"),
        "hygiene_score": hygiene_score,
        "structural_score": structural_score,
        "confidence_in_management_score": confidence_score,
        "longitude": longitude,
        "latitude": latitude,
    }


def compute_fingerprint(establishment: dict) -> str:
    canonical = json.dumps([establishment.get(f) for f in _FINGERPRINT_FIELDS], sort_keys=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_bulk_file(xml_bytes: bytes) -> ParsedBulkFile:
    root = ET.fromstring(xml_bytes)

    header_el = root.find("Header")
    header = BulkFileHeader(
        extract_date=_text(header_el, "ExtractDate") if header_el is not None else None,
        item_count=_int(header_el, "ItemCount") if header_el is not None else None,
        return_code=_text(header_el, "ReturnCode") if header_el is not None else None,
    )

    collection = root.find("EstablishmentCollection")
    establishments: list[dict] = []
    skipped = 0
    skip_reasons: list[str] = []

    if collection is not None:
        for el in collection.findall("EstablishmentDetail"):
            try:
                establishments.append(parse_establishment(el))
            except RecordParseError as e:
                skipped += 1
                skip_reasons.append(str(e))

    return ParsedBulkFile(header=header, establishments=establishments, skipped_records=skipped, skip_reasons=skip_reasons)
