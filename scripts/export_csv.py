#!/usr/bin/env python
"""Stage 7 of the brief: CSV export, the customer-facing deliverable.

Reads only from the database, never touches raw files or the network.
Exports every non-quarantined classified INSERT event (see
fsa_pipeline/csv_export.py's module docstring for why quarantined events
are always excluded, and why there's no filter on match status).

Filterable by postcode area, local authority, business type,
classification, and first-seen date range, per the brief. All filters
are optional and combine with AND; omitting all of them exports
everything.

Usage:
    python scripts/export_csv.py [--postcode-area NG] [--authority 857]
        [--business-type takeaway] [--classification NEW_VENUE]
        [--since 2026-08-20] [--until 2026-09-10] [--output PATH]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db
from fsa_pipeline.config import load_config
from fsa_pipeline.csv_export import EXPORT_FIELDNAMES, build_row, fetch_records, matches_filters
from fsa_pipeline.logging_utils import setup_logger


def run(args: argparse.Namespace) -> int:
    config = load_config()
    logger = setup_logger("export_csv", config.log_dir / f"export_csv_{dt.date.today().isoformat()}.log")

    conn = db.connect(config.db_path)

    records = fetch_records(conn, since=args.since, until=args.until)
    logger.info("%d classified INSERT event(s) before postcode/authority/type/classification filters", len(records))

    filtered = [
        r for r in records
        if matches_filters(
            r,
            postcode_area=args.postcode_area,
            authority=args.authority,
            business_type_substring=args.business_type,
            classification=args.classification,
        )
    ]
    logger.info("%d row(s) after filtering", len(filtered))

    if args.output:
        output_path = Path(args.output)
    else:
        config.export_output_dir.mkdir(parents=True, exist_ok=True)
        output_path = config.export_output_dir / f"venues_{dt.date.today().isoformat()}.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_FIELDNAMES)
        writer.writeheader()
        for record in filtered:
            writer.writerow(build_row(record))

    logger.info("wrote %d row(s) to %s", len(filtered), output_path)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--postcode-area", default=None, help='Postcode area, e.g. "NG" (matches NG17, NG1, ...)')
    parser.add_argument("--authority", default=None, help='Authority code (exact) or name substring, e.g. "857" or "Bristol"')
    parser.add_argument("--business-type", default=None, help='Case-insensitive substring, e.g. "takeaway"')
    parser.add_argument("--classification", default=None, choices=["NEW_VENUE", "OPERATOR_CHANGE", "UNKNOWN"])
    parser.add_argument("--since", default=None, help="First-seen date lower bound, YYYY-MM-DD, inclusive")
    parser.add_argument("--until", default=None, help="First-seen date upper bound, YYYY-MM-DD, inclusive")
    parser.add_argument("--output", default=None, help="Output CSV path (default: exports/venues_<today>.csv)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for label, value in [("--since", args.since), ("--until", args.until)]:
        if value is not None:
            dt.date.fromisoformat(value)  # raises ValueError with a clear message if malformed
    sys.exit(run(args))


if __name__ == "__main__":
    main()
