"""Classifies each FHRS INSERT event as NEW_VENUE / OWNERSHIP_CHANGE /
UNKNOWN, with a confidence grade and a human-readable reason.

Per the project brief:

- OWNERSHIP_CHANGE: a different FHRS record previously existed at the
  same address. Detected here by an exact address match (address_line_1
  + effective postcode, normalized) against `establishments_current` --
  the full national baseline, never `observations` -- see the "Stage 5
  design commitment" in README.md, raised by the user before this module
  was written specifically so it couldn't be built the other way.
- NEW_VENUE: matched to a recently-incorporated company (stage 4's
  matcher; companies_current only ever holds recent incorporations by
  construction, so any match found there is inherently recent) with no
  address-history predecessor.
- UNKNOWN: neither of the above. Not discarded -- many independent cafés
  never incorporate.

Bug caught during design (2026-08-27, before this module existed): a
naive address-history check found 316 of 1,709 real INSERT events with
"a different FHRSID at the same address" -- but many were large
multi-outlet venues (a college campus, a community centre) where the
"predecessor" was still active alongside the new record, not replaced by
it. Requiring the predecessor's last_seen_date to be strictly before the
new establishment's first_seen_date (no temporal overlap) cut this to
136 -- the actual ownership-change signal, not shared-premises noise.
"""

from __future__ import annotations

from dataclasses import dataclass

from fsa_pipeline.config import Config
from fsa_pipeline.matcher import Candidate


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


def address_key(address_line_1: str | None, postcode: str | None) -> tuple[str, str] | None:
    if not address_line_1 or not postcode:
        return None
    return (address_line_1.strip().upper(), postcode.strip().upper())


def build_address_index(establishments: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Maps (address_line_1, postcode) -> [establishment dict, ...] across
    every establishment ever seen, active or closed -- establishments_current
    never deletes a row. Each dict needs at least fhrsid, business_name,
    first_seen_date, last_seen_date."""
    index: dict[tuple[str, str], list[dict]] = {}
    for est in establishments:
        key = address_key(est.get("address_line_1"), est.get("postcode"))
        if key is None:
            continue
        index.setdefault(key, []).append(est)
    return index


def find_predecessor(
    fhrsid: int,
    address_line_1: str | None,
    postcode: str | None,
    first_seen_date: str,
    address_index: dict,
) -> Predecessor | None:
    """The most recently-departed establishment at the same address, if
    any existed and was gone before this one arrived (no overlap)."""
    key = address_key(address_line_1, postcode)
    if key is None:
        return None

    candidates = [est for est in address_index.get(key, []) if est["fhrsid"] != fhrsid and est["last_seen_date"] < first_seen_date]
    if not candidates:
        return None

    best = max(candidates, key=lambda e: e["last_seen_date"])  # most recent departure
    return Predecessor(
        fhrsid=best["fhrsid"],
        business_name=best.get("business_name"),
        first_seen_date=best["first_seen_date"],
        last_seen_date=best["last_seen_date"],
    )


def classify(
    *,
    postcode: str | None,
    candidates_found: int,
    best_candidate: Candidate | None,
    predecessor: Predecessor | None,
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
        if score >= config.new_venue_high_threshold:
            confidence = "LOW" if (best_candidate.is_high_density_address and score < 0.95) else "HIGH"
        else:
            confidence = "LOW" if best_candidate.is_high_density_address else "MEDIUM"

        reason = (
            f"Matched Companies House company \"{best_candidate.company_name}\" "
            f"({best_candidate.company_number}), name similarity {score}, in postcode district "
            f"{best_candidate.postcode_district}."
        )
        if best_candidate.is_high_density_address:
            reason += (
                f" Registered address is shared by {best_candidate.address_company_count} companies "
                f"(possible formation agent) -- address isn't corroborating, name similarity alone is."
            )

        return Classification(
            classification="NEW_VENUE",
            confidence=confidence,
            reason=reason,
            evidence_company_number=best_candidate.company_number,
        )

    if postcode is None:
        reason = (
            "No postcode available for this establishment (bulk data and live-API backfill both lack "
            "one); could not attempt company matching or an address-history lookup."
        )
    elif candidates_found == 0:
        reason = (
            "No Companies House company found in this establishment's postcode district within the "
            "recent-incorporation window."
        )
    elif best_candidate is not None:
        reason = (
            f"Closest Companies House match was \"{best_candidate.company_name}\" at similarity "
            f"{best_candidate.name_similarity_score}, below the confidence threshold for a venue match."
        )
    else:
        reason = "No usable match evidence found."

    return Classification(classification="UNKNOWN", confidence="LOW", reason=reason)
