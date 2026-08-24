#!/usr/bin/env python
"""Daily FHRS bulk collection job.

Fetches the national local-authority index, then every authority's bulk XML
file, and archives each raw response unmodified (gzip-compressed) under
raw/fhrs/<date>/. Never overwrites an existing archive file: if a file for
today already exists, that authority is treated as already collected and
skipped, so an interrupted run can simply be re-run to pick up where it
left off.

No parsing of establishment records happens here (stage 2). This script's
only job is: get the bytes, get them onto disk unmodified, don't lose any.

Usage:
    python scripts/collect_fhrs_bulk.py [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline.archive import AlreadyArchived, day_dir, write_gzip_atomic
from fsa_pipeline.config import load_config
from fsa_pipeline.fhrs_bulk import fetch_authorities_raw, fetch_authority_bulk, parse_authorities
from fsa_pipeline.http_client import build_session
from fsa_pipeline.logging_utils import setup_logger

AUTHORITIES_INDEX_FILENAME = "_authorities-index.xml.gz"
MANIFEST_FILENAME = "_manifest.json"


def get_or_fetch_authorities_raw(session, config, index_dest: Path, logger: logging.Logger) -> bytes:
    if index_dest.exists():
        logger.info("authorities index already archived, reading from disk")
        with gzip.open(index_dest, "rb") as f:
            return f.read()

    logger.info("fetching authorities index from %s", config.authorities_url)
    raw = fetch_authorities_raw(session, config)
    write_gzip_atomic(raw, index_dest)
    logger.info("archived authorities index (%d bytes)", len(raw))
    return raw


def run(date_str: str) -> int:
    config = load_config()
    logger = setup_logger("fhrs_collect", config.log_dir / f"fhrs_collect_{date_str}.log")
    session = build_session(config)

    day_path = day_dir(config.raw_dir, date_str)
    index_dest = day_path / AUTHORITIES_INDEX_FILENAME

    try:
        authorities_raw = get_or_fetch_authorities_raw(session, config, index_dest, logger)
    except Exception:
        logger.exception("could not obtain authorities index, aborting run")
        return 1

    authorities = parse_authorities(authorities_raw)
    logger.info("%d authorities to collect for %s", len(authorities), date_str)

    succeeded: list[str] = []
    skipped: list[str] = []
    failed: list[dict] = []

    for i, authority in enumerate(authorities, start=1):
        dest = day_path / authority.archive_filename

        if dest.exists():
            skipped.append(authority.code)
            continue

        try:
            content = fetch_authority_bulk(session, authority, config)
            write_gzip_atomic(content, dest)
        except AlreadyArchived:
            skipped.append(authority.code)
        except Exception as e:
            failed.append({"code": authority.code, "name": authority.name, "error": str(e)})
            logger.error("[%d/%d] %s (%s) FAILED: %s", i, len(authorities), authority.code, authority.name, e)
        else:
            succeeded.append(authority.code)
            logger.info(
                "[%d/%d] %s (%s) ok, %d bytes",
                i, len(authorities), authority.code, authority.name, len(content),
            )
        finally:
            time.sleep(config.request_delay_seconds)

    manifest = {
        "date": date_str,
        "run_completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "authorities_total": len(authorities),
        "fetched_this_run": len(succeeded),
        "already_present": len(skipped),
        "failed": failed,
    }
    (day_path / MANIFEST_FILENAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info(
        "done: %d fetched, %d already present, %d failed (of %d total)",
        len(succeeded), len(skipped), len(failed), len(authorities),
    )

    if failed:
        logger.warning("run incomplete: re-run the same command to retry only the failed authorities")
        return 1

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        help="Date to collect for, YYYY-MM-DD (default: today). "
        "Only useful for re-running a partial/failed collection under the same day's directory.",
        default=None,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    date_str = args.date or dt.date.today().isoformat()
    dt.date.fromisoformat(date_str)  # validate format early
    sys.exit(run(date_str))


if __name__ == "__main__":
    main()
