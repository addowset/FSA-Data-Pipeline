#!/usr/bin/env python
"""Targeted live Companies House lookup for "<operator> @ <site>" names
with no qualifying candidate in the bulk-collected pool.

Companies House collection (collect_companies_house.py) is scoped to
food-service SIC codes + active status (config.toml's [companies_house])
-- deliberate, to keep the pool to ~260K companies instead of ~640K+
every-industry active companies. This misses a real, recurring pattern:
a contract-catering brand's operating name ("Impact Food Group",
"Aramark") often belongs to a group *parent* registered under a
non-food holding-company SIC code (e.g. 64209), not the food-service
code its subsidiaries might carry.

Confirmed real 2026-09-10: the user flagged FHRSID 1981157 ("Impact
Food Group @ John Cabot Academy") -- "IMPACT FOOD GROUP LIMITED" is
real, active, incorporated 2022-07-26, but registered under SIC 64209,
so bulk collection never had a chance to find it. Widening bulk
collection's SIC scope to include holding companies nationally would
mean fetching every holding company in the country (every industry, not
just food) for near-zero per-venue signal -- the same reasoning behind
[companies_house]'s existing active-only decision. Instead: for the
specific, small, bounded set of operator names FHRS itself already told
us matter (fsa_pipeline/matcher.py's extract_operator_prefix, the
"<operator> @ <site>" convention), do a targeted live /search/companies
lookup only when the bulk-collected pool doesn't already have a
qualifying match. Confirmed scale: 63 distinct prefixes across the
current UNKNOWN/OPERATOR_CHANGE pool -- trivial against the rate limit.

Found companies are ingested into companies_current via the same
ingest_companies path as bulk collection, marked source='operator_search'
(see fsa_pipeline/companies_house.py's parse_company_item and
fsa_pipeline/db.py's companies_current schema comment) so a reader can
tell why a row sits outside the declared SIC scope, and so the daily
bulk top-up (which will never re-fetch these -- they're never in its
SIC-scoped query) isn't mistaken for the reason they're current. Once
ingested, match_companies_house.py's existing operator-prefix search
finds them exactly like any other pool company -- no changes needed
there.

Every live response (the name search and, for a qualifying match, the
full company profile) is archived unmodified before parsing, same
discipline as every other data source in this project -- under
raw/companies-house/operator-search/<date>/.

Idempotent per operator prefix, not per day: a prefix already checked
within config.toml's [operator_search].recheck_after_days is skipped
without a live call (whether it was found or not), unless the bulk pool
now already satisfies it locally (checked fresh, every run, at no
network cost) or --force is passed. Only active companies are
considered, consistent with [companies_house]'s existing policy.

Two precision gates, added 2026-09-10 after a first live run surfaced
real false positives (OTIS LIMITED -- elevators, KAMU LTD -- software,
ADDIES LIMITED -- legal -- each an exact coincidental name match for a
single-word operator prefix in a completely unrelated industry, since
this endpoint has no SIC filter to catch that):
1. A prefix must be multi-word (is_multi_word_prefix) to be live-searched
   at all. name_similarity's exact-match short-circuit gives a generic
   single word the same "perfect" score as a genuinely distinctive
   multi-word brand name, with nothing else here to disambiguate it.
   Single-word prefixes are simply never searched -- same outcome as
   "not found" today.
2. A dormant company (sic_codes containing "99999", Companies House's
   own dormant-company convention) is never accepted, even as the
   top-scoring candidate -- it can't plausibly be the entity actually
   running a venue today. The next-best scoring candidate is tried
   instead, not just given up on.

Usage:
    python scripts/lookup_operator_companies.py [--force]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db
from fsa_pipeline.archive import AlreadyArchived, day_dir, write_gzip_atomic
from fsa_pipeline.companies_house import (
    build_session,
    fetch_company_profile,
    get_api_key,
    items_from_search_page_bytes,
    parse_company_item,
    search_companies_by_name,
)
from fsa_pipeline.config import load_config
from fsa_pipeline.logging_utils import setup_logger
from fsa_pipeline.matcher import (
    build_address_density,
    build_companies_by_first_word,
    build_word_idf,
    extract_operator_prefix,
    find_national_candidates,
    name_similarity,
    normalize_company_name,
)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def safe_filename(text: str) -> str:
    """Sanitizes an operator prefix into a filesystem-safe archive
    filename. A hash suffix guards against two different prefixes
    collapsing to the same sanitized string once punctuation/truncation
    strips them down (real risk here: "IFG Group Trading As Dolce" vs
    "IFG Group Trading As Dolce Ltd" are different prefixes for the same
    operator, both seen in production)."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60] or "prefix"
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{slug}_{digest}"


def collect_distinct_prefixes(conn) -> list[str]:
    """Every distinct extracted operator prefix among establishments
    with an FHRS INSERT event on record -- the same population
    match_companies_house.py processes, so anything found here is
    immediately useful to that script's next run. Dedup is on the
    normalized form (so "Impact Food Group" and "IMPACT FOOD GROUP"
    aren't checked twice); the first natural-cased spelling seen is kept
    as the actual live-search query text."""
    rows = conn.execute(
        """
        SELECT DISTINCT e.business_name
        FROM diff_events d
        JOIN establishments_current e ON e.fhrsid = d.fhrsid
        WHERE d.event_type = 'INSERT'
        """
    ).fetchall()

    prefixes: dict[str, str] = {}
    for (business_name,) in rows:
        prefix = extract_operator_prefix(business_name)
        if not prefix:
            continue
        key = normalize_company_name(prefix)
        if key and key not in prefixes:
            prefixes[key] = prefix
    return list(prefixes.values())


# Companies House's own convention for a dormant company (not a real
# SIC 2007 code). A dormant company can't plausibly be the entity
# actually running a venue today -- added 2026-09-10 after a live test
# found both BUSY BEES LIMITED and CHARTWELLS LIMITED (real brand names,
# but this specific dormant shell almost certainly isn't the trading
# entity behind either) carrying this exact code.
_DORMANT_SIC_CODE = "99999"


def is_multi_word_prefix(prefix: str) -> bool:
    """Gate added 2026-09-10 after a live test surfaced real false
    positives: OTIS LIMITED (elevators), KAMU LTD (software), ADDIES
    LIMITED (legal) all exact-matched a single-word operator prefix
    ("Otis", "Kamu", "Addies") purely by coincidence.
    name_similarity's exact-match short-circuit (see matcher.py) bypasses
    IDF weighting entirely, so a generic single word gets the same
    confidence as a genuinely distinctive multi-word brand name, and
    this endpoint has no SIC filter (unlike bulk collection) to catch an
    unrelated industry. A single-word prefix is simply never
    live-searched -- same outcome as "not found" today, no regression,
    just no new coverage for this specific, riskiest case."""
    return len(normalize_company_name(prefix).split()) >= 2


def scored_active_candidates(
    session, config, day_path: Path, prefix: str, idf,
) -> list[tuple[str, float]]:
    """Searches live, archives the raw response, and scores every active
    result by the same name_similarity used everywhere else in this
    project -- the search endpoint's own relevance ranking isn't trusted
    blindly. Returns every (company_number, score) at or above
    config.national_match_threshold, best first -- the caller tries them
    in order so a top-scoring but dormant match doesn't block a
    legitimate runner-up."""
    query_bytes = search_companies_by_name(session, config, prefix)
    dest = day_path / f"{safe_filename(prefix)}_search.json.gz"
    try:
        write_gzip_atomic(query_bytes, dest)
    except AlreadyArchived:
        pass

    normalized_prefix = normalize_company_name(prefix)
    scored = []
    for item in items_from_search_page_bytes(query_bytes):
        if item.get("company_status") != "active":
            continue
        score = name_similarity(normalized_prefix, normalize_company_name(item.get("title")), idf)
        if score >= config.national_match_threshold:
            scored.append((item.get("company_number"), score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def run(force: bool) -> int:
    config = load_config()
    date_str = dt.date.today().isoformat()
    logger = setup_logger("lookup_operator_companies", config.log_dir / f"lookup_operator_companies_{date_str}.log")

    conn = db.connect(config.db_path)
    session = build_session(get_api_key(), config)
    day_path = day_dir(config.ch_raw_dir / "operator-search", date_str)

    company_columns = ("company_number", "company_name", "address_line_1", "postal_code", "date_of_creation")
    companies = [
        dict(zip(company_columns, row))
        for row in conn.execute(
            "SELECT company_number, company_name, address_line_1, postal_code, date_of_creation FROM companies_current"
        ).fetchall()
    ]
    companies_by_first_word = build_companies_by_first_word(companies)
    address_density = build_address_density(companies)
    idf = build_word_idf(companies)

    prefixes = collect_distinct_prefixes(conn)
    logger.info("%d distinct operator prefix(es) found", len(prefixes))

    satisfied_locally = 0
    skipped_single_word = 0
    skipped_recent = 0
    checked_live = 0
    found = 0
    not_found = 0
    dormant_skipped = 0

    for prefix in prefixes:
        normalized_prefix = normalize_company_name(prefix)
        if not normalized_prefix:
            continue

        # Checked fresh every run, at no network cost -- if the bulk
        # pool now satisfies this prefix (a later collection run picked
        # it up some other way), there's nothing to do here regardless
        # of when it was last live-checked.
        local_candidates = find_national_candidates(
            prefix, companies_by_first_word, address_density, config.high_density_address_threshold,
            config.national_match_threshold, idf, top_n=1, strategy="operator-prefix",
        )
        if local_candidates:
            satisfied_locally += 1
            continue

        if not is_multi_word_prefix(prefix):
            skipped_single_word += 1
            continue

        if not force and db.already_operator_searched(conn, normalized_prefix, config.operator_search_recheck_after_days):
            skipped_recent += 1
            continue

        checked_live += 1
        try:
            time.sleep(config.ch_request_delay_seconds)
            candidates = scored_active_candidates(session, config, day_path, prefix, idf)

            accepted = None
            for company_number, score in candidates:
                time.sleep(config.ch_request_delay_seconds)
                profile_bytes = fetch_company_profile(session, config, company_number)
                profile_dest = day_path / f"{safe_filename(prefix)}_profile_{company_number}.json.gz"
                try:
                    write_gzip_atomic(profile_bytes, profile_dest)
                except AlreadyArchived:
                    pass

                profile = json.loads(profile_bytes)
                if _DORMANT_SIC_CODE in (profile.get("sic_codes") or []):
                    logger.info(
                        "skipping dormant candidate for %r: %s (%s)",
                        prefix, profile.get("company_name"), company_number,
                    )
                    dormant_skipped += 1
                    continue

                accepted = (company_number, score, profile)
                break

            if accepted is None:
                db.record_operator_search(conn, normalized_prefix, None, now_iso())
                not_found += 1
                logger.info("no qualifying live match for %r", prefix)
                continue

            company_number, score, profile = accepted
            company = parse_company_item(profile, source="operator_search")
            db.ingest_companies(conn, date_str, [company])
            db.record_operator_search(conn, normalized_prefix, company_number, now_iso())
            found += 1
            logger.info(
                "found %r -> %s (%s), score=%.4f", prefix, company["company_name"], company_number, score,
            )
        except Exception as e:
            logger.error("live lookup failed for %r: %s", prefix, e)
            continue

    logger.info(
        "done: %d prefixes, %d already satisfied locally, %d single-word (skipped), "
        "%d skipped (recently checked), %d checked live (%d found, %d not found, "
        "%d dormant candidates skipped)",
        len(prefixes), satisfied_locally, skipped_single_word, skipped_recent, checked_live,
        found, not_found, dormant_skipped,
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Recheck operator prefixes even if checked recently")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sys.exit(run(args.force))


if __name__ == "__main__":
    main()
