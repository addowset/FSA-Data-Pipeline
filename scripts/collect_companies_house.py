#!/usr/bin/env python
"""Daily Companies House hospitality-incorporation collection job.

Fetches every company matching the configured SIC codes incorporated in
the trailing window (see config.toml), via the Advanced Search endpoint,
and archives each page's raw JSON response unmodified (gzip-compressed)
under raw/companies-house/<date>/. Same never-overwrite, resume-by-skip
discipline as collect_fhrs_bulk.py.

Requires COMPANIES_HOUSE_API_KEY in the environment -- see
fsa_pipeline/companies_house.py for how to get one.

Usage:
    python scripts/collect_companies_house.py [--date YYYY-MM-DD]
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
    fetch_page,
    get_api_key,
    hits_from_page_bytes,
    incorporated_date_window,
)
from fsa_pipeline.config import load_config
from fsa_pipeline.logging_utils import setup_logger

QUERY_FILENAME = "_query.json"
MANIFEST_FILENAME = "_manifest.json"


def page_filename(index: int) -> str:
    return f"page_{index:04d}.json.gz"


def get_or_fetch_page(session, config, logger, day_path: Path, index: int, incorporated_from: str, incorporated_to: str) -> bytes:
    dest = day_path / page_filename(index)
    if dest.exists():
        logger.info("page %d already archived, reading from disk", index)
        with gzip.open(dest, "rb") as f:
            return f.read()

    content = fetch_page(session, config, incorporated_from, incorporated_to, index * config.ch_page_size)
    write_gzip_atomic(content, dest)
    logger.info("page %d fetched and archived (%d bytes)", index, len(content))
    return content


def run(date_str: str) -> int:
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
        first_page = get_or_fetch_page(session, config, logger, day_path, 0, incorporated_from, incorporated_to)
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
            get_or_fetch_page(session, config, logger, day_path, index, incorporated_from, incorporated_to)
            fetched += 1
        except AlreadyArchived:
            fetched += 1
        except Exception as e:
            failed.append({"page": index, "error": str(e)})
            logger.error("page %d FAILED: %s", index, e)

        time.sleep(config.ch_request_delay_seconds)

    manifest = {
        "date": date_str,
        "incorporated_from": incorporated_from,
        "incorporated_to": incorporated_to,
        "hits": hits,
        "pages_total": num_pages,
        "pages_fetched": fetched,
        "failed": failed,
    }
    (day_path / MANIFEST_FILENAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info("done: %d/%d pages, %d hits, %d failed", fetched, num_pages, hits, len(failed))

    if failed:
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Date to collect for, YYYY-MM-DD (default: today)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    date_str = args.date or dt.date.today().isoformat()
    dt.date.fromisoformat(date_str)
    sys.exit(run(date_str))


if __name__ == "__main__":
    main()
