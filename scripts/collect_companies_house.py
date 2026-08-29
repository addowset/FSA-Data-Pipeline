#!/usr/bin/env python
"""Daily Companies House hospitality-incorporation collection job.

Fetches every company matching the configured SIC codes incorporated in
the trailing window (see config.toml), via the Advanced Search endpoint,
and archives each page's raw JSON response unmodified (gzip-compressed)
under raw/companies-house/<date>/. Same never-overwrite, resume-by-skip
discipline as collect_fhrs_bulk.py.

Requires COMPANIES_HOUSE_API_KEY in the environment -- see
fsa_pipeline/companies_house.py for how to get one.

--full-history does a one-time pull of every matching company regardless
of incorporation date (confirmed real scale 2026-08-28: 259,986 active
hospitality companies nationally). This exists because ground-truth
checking found genuine matcher misses where the correct company was
incorporated 5 months, 18 months, and 6 years before the FHRS record
appeared -- no daily rolling window would have been wide enough.

Confirmed by direct testing 2026-08-29: the Advanced Search API enforces
a hard start_index + size <= 10,000 ceiling per query (the classic
Elasticsearch default max_result_window), so a single no-date-bound query
can never reach past the first 10,000 matches no matter the page size --
the first overnight attempt at this ran into exactly that wall. Fixed by
compute_date_slices (fsa_pipeline/companies_house.py), which recursively
bisects the date range until every slice's own hit count is safely under
the ceiling, then paginates normally within each slice. Archives to
fullhistory_<from>_<to>_page_NNNN.json.gz so slices never collide with
each other or with the regular daily page_NNNN.json.gz files. Run once
(or occasionally) for backfill; the normal daily run stays narrow
(config.toml's incorporated_window_days), since companies_current
accumulates and never deletes, so daily collection only needs to catch
what's new since the window was last covered.

Usage:
    python scripts/collect_companies_house.py [--date YYYY-MM-DD] [--full-history]
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline.archive import AlreadyArchived, day_dir, write_gzip_atomic
from fsa_pipeline.companies_house import (
    MissingApiKey,
    build_session,
    compute_date_slices,
    fetch_page,
    get_api_key,
    hits_from_page_bytes,
    incorporated_date_window,
)
from fsa_pipeline.config import load_config
from fsa_pipeline.logging_utils import setup_logger

QUERY_FILENAME = "_query.json"
MANIFEST_FILENAME = "_manifest.json"
FULL_HISTORY_MANIFEST_FILENAME = "_manifest_fullhistory.json"


def page_filename(index: int) -> str:
    return f"page_{index:04d}.json.gz"


def full_history_page_filename(slice_from: str, slice_to: str, index: int) -> str:
    return f"fullhistory_{slice_from}_{slice_to}_page_{index:04d}.json.gz"


def get_or_fetch(session, config, day_path: Path, filename: str, incorporated_from, incorporated_to, start_index: int) -> bytes:
    dest = day_path / filename
    if dest.exists():
        with gzip.open(dest, "rb") as f:
            return f.read()

    content = fetch_page(session, config, incorporated_from, incorporated_to, start_index)
    write_gzip_atomic(content, dest)
    return content


def run_daily(date_str: str) -> int:
    config = load_config()
    logger = setup_logger("companies_house_collect", config.log_dir / f"companies_house_collect_{date_str}.log")

    try:
        api_key = get_api_key()
    except MissingApiKey as e:
        logger.error(str(e))
        return 1

    session = build_session(api_key, config)
    as_of = dt.date.fromisoformat(date_str)
    incorporated_from, incorporated_to = incorporated_date_window(config, as_of)

    day_path = day_dir(config.ch_raw_dir, date_str)

    query_path = day_path / QUERY_FILENAME
    if not query_path.exists():
        query_path.write_text(
            json.dumps(
                {
                    "sic_codes": config.ch_sic_codes,
                    "incorporated_from": incorporated_from,
                    "incorporated_to": incorporated_to,
                    "page_size": config.ch_page_size,
                    "requested_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    logger.info("collecting companies incorporated %s to %s, SIC codes %s", incorporated_from, incorporated_to, config.ch_sic_codes)

    try:
        first_page = get_or_fetch(session, config, day_path, page_filename(0), incorporated_from, incorporated_to, 0)
    except Exception:
        logger.exception("could not fetch page 0, aborting run")
        return 1

    hits = hits_from_page_bytes(first_page)
    num_pages = max(1, (hits + config.ch_page_size - 1) // config.ch_page_size)
    logger.info("%d total hits, %d page(s)", hits, num_pages)

    failed = []
    fetched = 1  # page 0

    for index in range(1, num_pages):
        try:
            get_or_fetch(session, config, day_path, page_filename(index), incorporated_from, incorporated_to, index * config.ch_page_size)
            fetched += 1
            logger.info("page %d fetched/archived", index)
        except AlreadyArchived:
            fetched += 1
        except Exception as e:
            failed.append({"page": index, "error": str(e)})
            logger.error("page %d FAILED: %s", index, e)

        time.sleep(config.ch_request_delay_seconds)

    manifest = {
        "date": date_str, "incorporated_from": incorporated_from, "incorporated_to": incorporated_to,
        "hits": hits, "pages_total": num_pages, "pages_fetched": fetched, "failed": failed,
    }
    (day_path / MANIFEST_FILENAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info("done: %d/%d pages, %d hits, %d failed", fetched, num_pages, hits, len(failed))
    return 1 if failed else 0


def run_full_history(date_str: str) -> int:
    config = load_config()
    logger = setup_logger("companies_house_collect", config.log_dir / f"companies_house_collect_{date_str}_fullhistory.log")

    try:
        api_key = get_api_key()
    except MissingApiKey as e:
        logger.error(str(e))
        return 1

    session = build_session(api_key, config)
    day_path = day_dir(config.ch_raw_dir, date_str)

    logger.info("computing safe date slices (Elasticsearch 10,000-result-window workaround)")
    slices = compute_date_slices(session, config, None, None)
    total_hits = sum(hits for _, _, hits in slices)
    logger.info("%d slice(s), %d total hits", len(slices), total_hits)

    failed_slices = []
    fetched_pages = 0
    slice_manifest = []

    for slice_index, (slice_from, slice_to, hits) in enumerate(slices, start=1):
        num_pages = max(1, (hits + config.ch_page_size - 1) // config.ch_page_size)
        logger.info("[%d/%d] %s to %s: %d hits, %d page(s)", slice_index, len(slices), slice_from, slice_to, hits, num_pages)

        slice_failed = []
        for index in range(num_pages):
            filename = full_history_page_filename(slice_from, slice_to, index)
            try:
                get_or_fetch(session, config, day_path, filename, slice_from, slice_to, index * config.ch_page_size)
                fetched_pages += 1
            except AlreadyArchived:
                fetched_pages += 1
            except Exception as e:
                slice_failed.append({"page": index, "error": str(e)})
                logger.error("  %s to %s page %d FAILED: %s", slice_from, slice_to, index, e)

            time.sleep(config.ch_request_delay_seconds)

        if slice_failed:
            failed_slices.append({"from": slice_from, "to": slice_to, "failed_pages": slice_failed})

        slice_manifest.append({"from": slice_from, "to": slice_to, "hits": hits, "pages": num_pages, "failed": slice_failed})

    manifest = {
        "date": date_str, "total_hits": total_hits, "slice_count": len(slices),
        "pages_fetched": fetched_pages, "slices": slice_manifest,
    }
    (day_path / FULL_HISTORY_MANIFEST_FILENAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info("done: %d slices, %d pages fetched, %d total hits, %d slices with failures",
                len(slices), fetched_pages, total_hits, len(failed_slices))
    return 1 if failed_slices else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Date to collect for, YYYY-MM-DD (default: today)")
    parser.add_argument("--full-history", action="store_true", help="One-time pull of all matching companies, no incorporation-date bound")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    date_str = args.date or dt.date.today().isoformat()
    dt.date.fromisoformat(date_str)
    sys.exit(run_full_history(date_str) if args.full_history else run_daily(date_str))


if __name__ == "__main__":
    main()
