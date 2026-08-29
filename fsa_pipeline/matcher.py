"""Matches FHRS INSERT events against Companies House companies.

Per the brief's explicit warning: never match on registered-office address
equality. Small hospitality companies routinely register at their
accountant's office, and formation agents host huge numbers of unrelated
companies at one address -- confirmed against real data (2026-08-24):
71-75 Shelton Street, WC2H 9JQ hosts 31 of our 1,993 companies; three
addresses host 10+. Matching on address equality there would produce
constant false positives.

Instead: match on company name similarity, narrowed by postcode
*district* (the outward code, e.g. "NG17" from "NG17 3GA") rather than
full address or exact postcode -- coarse enough to not be defeated by
minor address-formatting differences between FHRS and Companies House,
tight enough to keep the candidate search local. Address density is
still computed and stored as evidence (not used to gate matching itself),
so stage 5's classification logic can discount an address-based signal
("a company incorporated at the same address as an existing food
business") when that address is a known high-density one.

Second channel added 2026-08-28: district-only search has a real blind
spot, confirmed by ground-truth checking -- "Soul Mama Islington"
(trades N1) and "Mamma Rosa London" (trades N19) were both registered
via addresses in a completely different postcode district (a formation
agent, and what looks like a personal address), so no amount of tuning
the district search would ever find them. find_national_candidates
searches nationally by name only, independent of district, with a much
stricter similarity floor (config.toml's national_match_threshold) since
there's no geographic corroboration behind it. To keep this tractable
against a ~260K-company national pool (see collect_companies_house.py
--full-history), it's blocked by the first normalized word of the name
rather than scored against everything -- a real, documented trade-off:
it will miss a match whose first word itself diverges (a typo, or a
genuinely different leading word), but every real case that motivated
this channel shared an exact leading phrase with its legal name.

This module only finds and scores candidates -- it does not decide
NEW_VENUE / OWNERSHIP_CHANGE / UNKNOWN. That classification is stage 5,
which will consume this module's output (fsa_pipeline/db.py's
company_match_candidates table).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

_LEGAL_SUFFIXES = {
    "LIMITED", "LTD", "LLP", "PLC", "CIC", "LP", "UNLIMITED",
}


def normalize_postcode_district(postcode: str | None) -> str | None:
    """Extracts the postcode district (outward code) from a UK postcode,
    tolerant of missing/irregular spacing. Returns None if the input
    doesn't look like a plausible postcode.

    UK postcodes always end in a 3-character inward code (a digit then
    two letters) -- stripping that off the whitespace-normalized postcode
    gives the outward code regardless of how the input was spaced.
    """
    if not postcode:
        return None

    cleaned = postcode.strip().upper().replace(" ", "")
    if not (5 <= len(cleaned) <= 7):
        return None

    outward, inward = cleaned[:-3], cleaned[-3:]
    if not outward or not outward[0].isalpha():
        return None
    if not (inward[0].isdigit() and inward[1].isalpha() and inward[2].isalpha()):
        return None

    return outward


def normalize_company_name(name: str | None) -> str:
    """Uppercases, strips legal suffixes (LIMITED/LTD/LLP/...) and
    punctuation, so a Companies House legal name and an FHRS trading name
    can be compared on roughly equal footing."""
    if not name:
        return ""

    n = name.upper().replace("&", " AND ")
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    words = re.sub(r"\s+", " ", n).strip().split(" ")

    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()

    return " ".join(words)


def name_similarity(a: str, b: str) -> float:
    """0.0-1.0 similarity between two already-normalized names."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


@dataclass
class Candidate:
    company_number: str
    company_name: str
    name_similarity_score: float
    postcode_district: str
    address_company_count: int
    is_high_density_address: bool
    match_strategy: str = "district"  # 'district' | 'national'
    date_of_creation: str | None = None


def build_address_density(companies: list[dict]) -> dict[str, int]:
    """Maps a normalized (address_line_1, postal_code) key to how many
    distinct companies share it."""
    counts: dict[str, int] = {}
    for company in companies:
        key = _address_key(company)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _address_key(company: dict) -> str:
    return f"{(company.get('address_line_1') or '').strip().upper()}|{(company.get('postal_code') or '').strip().upper()}"


def build_companies_by_district(companies: list[dict]) -> dict[str, list[dict]]:
    by_district: dict[str, list[dict]] = {}
    for company in companies:
        district = normalize_postcode_district(company.get("postal_code"))
        if district is None:
            continue
        by_district.setdefault(district, []).append(company)
    return by_district


def build_companies_by_first_word(companies: list[dict]) -> dict[str, list[dict]]:
    """Blocking index for the national channel: normalized name's first
    word -> companies. Keeps a ~260K-company national search tractable
    without scoring against everything for every INSERT event."""
    by_first_word: dict[str, list[dict]] = {}
    for company in companies:
        normalized = normalize_company_name(company.get("company_name"))
        if not normalized:
            continue
        first_word = normalized.split(" ", 1)[0]
        by_first_word.setdefault(first_word, []).append(company)
    return by_first_word


def _make_candidate(company: dict, score: float, district: str | None, address_density: dict, high_density_threshold: int, strategy: str) -> Candidate:
    density = address_density.get(_address_key(company), 1)
    return Candidate(
        company_number=company["company_number"],
        company_name=company.get("company_name") or "",
        name_similarity_score=round(score, 4),
        postcode_district=district or (normalize_postcode_district(company.get("postal_code")) or ""),
        address_company_count=density,
        is_high_density_address=density >= high_density_threshold,
        match_strategy=strategy,
        date_of_creation=company.get("date_of_creation"),
    )


def find_candidates(
    business_name: str,
    postcode: str,
    companies_by_district: dict[str, list[dict]],
    address_density: dict[str, int],
    high_density_threshold: int,
    top_n: int = 5,
) -> list[Candidate]:
    district = normalize_postcode_district(postcode)
    if district is None:
        return []

    same_district = companies_by_district.get(district, [])
    if not same_district:
        return []

    normalized_target = normalize_company_name(business_name)

    scored = [
        _make_candidate(
            company, name_similarity(normalized_target, normalize_company_name(company.get("company_name"))),
            district, address_density, high_density_threshold, "district",
        )
        for company in same_district
    ]

    scored.sort(key=lambda c: c.name_similarity_score, reverse=True)
    return scored[:top_n]


def find_national_candidates(
    business_name: str,
    companies_by_first_word: dict[str, list[dict]],
    address_density: dict[str, int],
    high_density_threshold: int,
    threshold: float,
    top_n: int = 5,
) -> list[Candidate]:
    """Name-only search, independent of postcode district. See module
    docstring for why (formation-agent registrations invisible to
    district search) and its blocking-by-first-word trade-off."""
    normalized_target = normalize_company_name(business_name)
    if not normalized_target:
        return []

    first_word = normalized_target.split(" ", 1)[0]
    pool = companies_by_first_word.get(first_word, [])
    if not pool:
        return []

    scored = []
    for company in pool:
        score = name_similarity(normalized_target, normalize_company_name(company.get("company_name")))
        if score >= threshold:
            scored.append(_make_candidate(company, score, None, address_density, high_density_threshold, "national"))

    scored.sort(key=lambda c: c.name_similarity_score, reverse=True)
    return scored[:top_n]


def merge_candidates(district_candidates: list[Candidate], national_candidates: list[Candidate], top_n: int) -> list[Candidate]:
    """Combines both channels' results, deduplicating by company_number
    (keeping the higher-scoring entry if a company appears in both --
    district search already gives a district-corroborated result, which
    is preferable evidence to the same company found nationally)."""
    by_number: dict[str, Candidate] = {}
    for candidate in district_candidates + national_candidates:
        existing = by_number.get(candidate.company_number)
        if existing is None or candidate.name_similarity_score > existing.name_similarity_score:
            by_number[candidate.company_number] = candidate

    merged = sorted(by_number.values(), key=lambda c: c.name_similarity_score, reverse=True)
    return merged[:top_n]
