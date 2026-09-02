"""Classifies each FHRS INSERT event as NEW_VENUE / OWNERSHIP_CHANGE /
UNKNOWN, with a confidence grade and a human-readable reason.

Per the project brief:

- OWNERSHIP_CHANGE: a different FHRS record previously existed at the
  same address. Detected here by an exact address match (address_line_1
  + effective postcode, normalized) against `establishments_current` --
  the full national baseline, never `observations` -- see the "Stage 5
  design commitment" in README.md, raised by the user before this module
  was written specifically so it couldn't be built the other way.
- NEW_VENUE: matched to a company (stage 4's matcher) incorporated
  recently relative to this record's first-seen date, with no
  address-history predecessor.
- UNKNOWN: neither of the above. Not discarded -- many independent
  cafés never incorporate.

Two bugs caught during design (2026-08-27/28, ground-truth spot checks),
before this logic shipped:

1. A naive address-history check found 316 of 1,709 real INSERT events
   with "a different FHRSID at the same address" -- but many were large
   multi-outlet venues (a college campus, a community centre) where the
   "predecessor" was still active alongside the new record, not replaced
   by it. Requiring the predecessor's last_seen_date to be strictly
   before the new establishment's first_seen_date (no temporal overlap)
   cut this to 136 -- the actual ownership-change signal.
2. The address-history match required an exact address_line_1 string on
   both sides, missing a genuine case ("Rassau Fish Bar" replaced by a
   new FHRSID also named "Rassau Fish Bar", same postcode, no overlap)
   because the newer record's address_line_1 was null. Fixed with a
   fallback: when address_line_1 is missing on either side, match on
   postcode + near-exact name instead (still strict, since a single
   postcode can host many unrelated venues -- confirmed: one Belfast
   postcode hosts 11).

A third gap found the same way (2026-08-28): two real matches ("Soul
Mama Islington", "Mamma Rosa London") were invisible to the matcher
entirely, because their companies were registered via a formation agent
or personal address in a different postcode district to where they
actually trade. Companies House collection was widened to a one-time
full-history pull (no incorporation-date bound) to make these findable
at all, which in turn required a NEW_VENUE incorporation-recency gate --
without one, an 18-month-old company ("Mamma Rosa London", found once
collection was widened) would wrongly count as NEW_VENUE just for
having no FHRS predecessor.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from fsa_pipeline.config import Config
from fsa_pipeline.matcher import Candidate, name_similarity, normalize_company_name


@dataclass(frozen=True)
class Predecessor:
    fhrsid: int
    business_name: str | None
    first_seen_date: str
    last_seen_date: str


@dataclass(frozen=True)
class Classification:
    classification: str  # 'NEW_VENUE' | 'OWNERSHIP_CHANGE' | 'UNKNOWN'
    confidence: str  # 'HIGH' | 'MEDIUM' | 'LOW'
    reason: str
    evidence_company_number: str | None = None
    evidence_predecessor_fhrsid: int | None = None
    evidence_existing_operator_fhrsid: int | None = None


def address_key(address_line_1: str | None, postcode: str | None) -> tuple[str, str] | None:
    if not address_line_1 or not postcode:
        return None
    return (address_line_1.strip().upper(), postcode.strip().upper())


def build_address_index(establishments: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Maps (address_line_1, postcode) -> [establishment dict, ...] --
    the exact-match layer. Only establishments with both fields present
    are indexed; see build_postcode_index for the fallback layer."""
    index: dict[tuple[str, str], list[dict]] = {}
    for est in establishments:
        key = address_key(est.get("address_line_1"), est.get("postcode"))
        if key is None:
            continue
        index.setdefault(key, []).append(est)
    return index


def build_postcode_index(establishments: list[dict]) -> dict[str, list[dict]]:
    """Maps postcode -> [establishment dict, ...], regardless of whether
    address_line_1 is present -- the fallback layer for when it's
    missing on either side of a potential predecessor match."""
    index: dict[str, list[dict]] = {}
    for est in establishments:
        postcode = est.get("postcode")
        if not postcode:
            continue
        index.setdefault(postcode.strip().upper(), []).append(est)
    return index


def _most_recently_departed(candidates: list[dict]) -> dict | None:
    return max(candidates, key=lambda e: e["last_seen_date"]) if candidates else None


def find_predecessor(
    fhrsid: int,
    business_name: str | None,
    address_line_1: str | None,
    postcode: str | None,
    first_seen_date: str,
    address_index: dict,
    postcode_index: dict,
    idf: dict[str, float],
    fallback_threshold: float,
) -> Predecessor | None:
    """The most recently-departed establishment at the same address, if
    any existed and was gone before this one arrived (no overlap). Tries
    an exact address_line_1 + postcode match first; falls back to
    postcode + near-exact name when address_line_1 is missing on either
    side (see module docstring, bug 2)."""
    key = address_key(address_line_1, postcode)
    if key is not None:
        exact_candidates = [
            est for est in address_index.get(key, [])
            if est["fhrsid"] != fhrsid and est["last_seen_date"] < first_seen_date
        ]
        best = _most_recently_departed(exact_candidates)
        if best is not None:
            return Predecessor(
                fhrsid=best["fhrsid"], business_name=best.get("business_name"),
                first_seen_date=best["first_seen_date"], last_seen_date=best["last_seen_date"],
            )

    if not postcode:
        return None

    normalized_target = normalize_company_name(business_name)
    if not normalized_target:
        return None

    scored = []
    for est in postcode_index.get(postcode.strip().upper(), []):
        if est["fhrsid"] == fhrsid or est["last_seen_date"] >= first_seen_date:
            continue
        score = name_similarity(normalized_target, normalize_company_name(est.get("business_name")), idf)
        if score >= fallback_threshold:
            scored.append((score, est))

    if not scored:
        return None

    # Highest name similarity first, most recent departure as tiebreak.
    scored.sort(key=lambda pair: (pair[0], pair[1]["last_seen_date"]), reverse=True)
    _, best = scored[0]
    return Predecessor(
        fhrsid=best["fhrsid"], business_name=best.get("business_name"),
        first_seen_date=best["first_seen_date"], last_seen_date=best["last_seen_date"],
    )


def build_company_operator_index(matched_venues: list[dict]) -> dict[str, list[dict]]:
    """Maps company_number -> [{"fhrsid", "business_name", "first_seen_date"}, ...]
    for every FHRSID that was itself confidently matched to that company
    (score >= medium threshold) -- used to detect "this company already
    operates another FHRS-registered venue" (Soul Mama Stratford case:
    an existing operator opening an additional site is a different signal
    to a first-time venue, even though it'll otherwise look identical)."""
    index: dict[str, list[dict]] = {}
    for venue in matched_venues:
        index.setdefault(venue["company_number"], []).append(venue)
    return index


def find_existing_operator(
    fhrsid: int, company_number: str, first_seen_date: str, operator_index: dict
) -> Predecessor | None:
    others = [
        v for v in operator_index.get(company_number, [])
        if v["fhrsid"] != fhrsid and v["first_seen_date"] < first_seen_date
    ]
    if not others:
        return None
    earliest = min(others, key=lambda v: v["first_seen_date"])
    return Predecessor(
        fhrsid=earliest["fhrsid"], business_name=earliest.get("business_name"),
        first_seen_date=earliest["first_seen_date"], last_seen_date=earliest["first_seen_date"],
    )


def _is_recently_incorporated(date_of_creation: str | None, first_seen_date: str, max_age_days: int) -> bool:
    """A match only counts as NEW_VENUE-worthy evidence if the company was
    incorporated within max_age_days of the FHRS record appearing --
    otherwise, once Companies House collection covers full history (not
    just a rolling window), an old established company would wrongly get
    labelled as a new venue just for having no FHRS predecessor."""
    if not date_of_creation:
        return False
    try:
        created = dt.date.fromisoformat(date_of_creation)
        first_seen = dt.date.fromisoformat(first_seen_date)
    except ValueError:
        return False
    age_days = (first_seen - created).days
    return 0 <= age_days <= max_age_days


def classify(
    *,
    first_seen_date: str,
    postcode: str | None,
    candidates_found: int,
    best_candidate: Candidate | None,
    predecessor: Predecessor | None,
    existing_operator: Predecessor | None,
    config: Config,
) -> Classification:
    if predecessor is not None:
        reason = (
            f"A different FHRS record previously existed at this address: "
            f"\"{predecessor.business_name}\" (FHRSID {predecessor.fhrsid}), active "
            f"{predecessor.first_seen_date} to {predecessor.last_seen_date}, before this record appeared."
        )
        if best_candidate is not None and best_candidate.name_similarity_score >= config.new_venue_medium_threshold:
            reason += (
                f" Corroborated by a Companies House match: \"{best_candidate.company_name}\" "
                f"(similarity {best_candidate.name_similarity_score})."
            )
        return Classification(
            classification="OWNERSHIP_CHANGE",
            confidence="HIGH",
            reason=reason,
            evidence_company_number=best_candidate.company_number if best_candidate else None,
            evidence_predecessor_fhrsid=predecessor.fhrsid,
        )

    if best_candidate is not None and best_candidate.name_similarity_score >= config.new_venue_medium_threshold:
        score = best_candidate.name_similarity_score

        if not _is_recently_incorporated(best_candidate.date_of_creation, first_seen_date, config.new_venue_max_incorporation_age_days):
            reason = (
                f"Matched Companies House company \"{best_candidate.company_name}\" "
                f"({best_candidate.company_number}) at similarity {score}, but it was incorporated "
                f"{best_candidate.date_of_creation or 'at an unknown date'} -- not within "
                f"{config.new_venue_max_incorporation_age_days} days of this record appearing, "
                f"so not treated as new-venue evidence."
            )
            return Classification(
                classification="UNKNOWN", confidence="LOW", reason=reason,
                evidence_company_number=best_candidate.company_number,
            )

        if score >= config.new_venue_high_threshold:
            confidence = "LOW" if (best_candidate.is_high_density_address and score < 0.95) else "HIGH"
        else:
            confidence = "LOW" if best_candidate.is_high_density_address else "MEDIUM"

        reason = (
            f"Matched Companies House company \"{best_candidate.company_name}\" "
            f"({best_candidate.company_number}), incorporated {best_candidate.date_of_creation}, "
            f"name similarity {score}, via {best_candidate.match_strategy} search"
            + (f" in postcode district {best_candidate.postcode_district}." if best_candidate.match_strategy == "district" else ".")
        )
        if best_candidate.is_high_density_address:
            reason += (
                f" Registered address is shared by {best_candidate.address_company_count} companies "
                f"(possible formation agent) -- address isn't corroborating, name similarity alone is."
            )
        if existing_operator is not None:
            reason += (
                f" Note: this company already operates another FHRS-registered venue: "
                f"\"{existing_operator.business_name}\" (FHRSID {existing_operator.fhrsid}), "
                f"first seen {existing_operator.first_seen_date} -- likely an additional site "
                f"from an established operator, not a first-time venue."
            )

        return Classification(
            classification="NEW_VENUE",
            confidence=confidence,
            reason=reason,
            evidence_company_number=best_candidate.company_number,
            evidence_existing_operator_fhrsid=existing_operator.fhrsid if existing_operator else None,
        )

    if postcode is None:
        reason = (
            "No postcode available for this establishment (bulk data and live-API backfill both lack "
            "one); could not attempt company matching or an address-history lookup."
        )
    elif candidates_found == 0:
        reason = (
            "No Companies House company found for this establishment, by postcode-district or "
            "national name search."
        )
    elif best_candidate is not None:
        reason = (
            f"Closest Companies House match was \"{best_candidate.company_name}\" at similarity "
            f"{best_candidate.name_similarity_score}, below the confidence threshold for a venue match."
        )
    else:
        reason = "No usable match evidence found."

    return Classification(classification="UNKNOWN", confidence="LOW", reason=reason)
