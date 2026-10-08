#!/usr/bin/env python
"""Builds the "Venue Feed" page (a published Artifact -- see
tools/venue_feed_template.html) from a CSV produced by export_csv.py.

Reads only the CSV, never the database: the page is meant to show
exactly what a given export contained, so it rebuilds from that file
rather than re-querying. Defaults to the newest venues_*.csv in
exports/Venue CSV/ (falling back to exports/).

Field names are deliberately short since every row is embedded directly
in the page's HTML -- see the template's JS for the full key.

This only writes the HTML file. Publishing it to the artifact is a
separate step (ask Claude to republish tools/venue_feed.html).

Usage:
    python scripts/export_venue_feed.py [--csv PATH] [--output PATH]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = ROOT / "tools" / "venue_feed_template.html"
DEFAULT_OUTPUT = ROOT / "tools" / "venue_feed.html"
CSV_DIRS = [ROOT / "exports" / "Venue CSV", ROOT / "exports"]

DATA_PLACEHOLDER = "/*__DATA__*/[]/*__DATA__*/"
DATE_PLACEHOLDER = "__GENERATED_AT__"
SOURCE_PLACEHOLDER = "__SOURCE_CSV__"

CSV_NAME_RE = re.compile(r"^venues_(\d{4}-\d{2}-\d{2})\.csv$")


def find_latest_csv(dirs: list[Path] = CSV_DIRS) -> Path:
    """Newest by the date in the filename, not mtime -- moving a file
    between folders (as happened 2026-10-07) changes its mtime."""
    for d in dirs:
        candidates = [p for p in d.glob("venues_*.csv") if CSV_NAME_RE.match(p.name)]
        if candidates:
            return max(candidates, key=lambda p: CSV_NAME_RE.match(p.name).group(1))
    raise FileNotFoundError(f"no venues_YYYY-MM-DD.csv found in {', '.join(str(d) for d in dirs)}")


def csv_row_to_feed_row(r: dict) -> dict:
    return {
        "id": int(r["fhrsid"]),
        "au": r["authority_name"],
        "n": r["business_name"],
        "a": r["address"],
        "pc": r["postcode"],
        "ac": r["address_completeness"],
        "bt": r["business_type"],
        "rst": r["rating_status"],
        "fs": r["first_seen_date"],
        "cl": r["classification"],
        "cf": r["confidence"],
        "rs": r["reason"],
        "pbn": r["previous_business_name"],
        "pls": r["previous_last_seen"],
        "ms": r["match_status"] == "Yes",
        "cnm": r["company_name"],
        "cn": r["company_number"],
        "cst": r["company_status"],
        "ic": r["incorporation_date"],
        "sic": r["sic_codes"],
        "url": r["fhrs_url"],
        "gmu": r["google_maps_url"],
        "chu": r["companies_house_url"],
    }


def build_page(template: str, rows: list[dict], generated_at: str, source_name: str) -> str:
    for placeholder in (DATA_PLACEHOLDER, DATE_PLACEHOLDER, SOURCE_PLACEHOLDER):
        count = template.count(placeholder)
        if count != 1:
            raise ValueError(f"template must contain {placeholder!r} exactly once, found {count}")
    # "</" escaped so a business name containing "</script>" can't end
    # the script block early.
    data = json.dumps(rows, separators=(",", ":")).replace("</", "<\\/")
    return (
        template.replace(DATA_PLACEHOLDER, f"/*__DATA__*/{data}/*__DATA__*/")
        .replace(DATE_PLACEHOLDER, generated_at)
        .replace(SOURCE_PLACEHOLDER, source_name)
    )


def run(csv_path: Path, output_path: Path) -> int:
    match = CSV_NAME_RE.match(csv_path.name)
    if not match:
        print(f"expected a venues_YYYY-MM-DD.csv filename, got {csv_path.name}", file=sys.stderr)
        return 1

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = [csv_row_to_feed_row(r) for r in csv.DictReader(f)]

    page = build_page(TEMPLATE_PATH.read_text(encoding="utf-8"), rows, match.group(1), csv_path.name)
    output_path.write_text(page, encoding="utf-8")

    size_mb = output_path.stat().st_size / 1_048_576
    print(f"wrote {len(rows)} rows from {csv_path.name} ({size_mb:.1f} MB) to {output_path}")
    if size_mb > 12:
        print("  warning: artifact pages are capped at 16 MB -- time to load the data as a separate file", file=sys.stderr)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", help="CSV export to build from (default: newest venues_*.csv in exports/Venue CSV/)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output HTML path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_path = Path(args.csv) if args.csv else find_latest_csv()
    sys.exit(run(csv_path, Path(args.output)))


if __name__ == "__main__":
    main()
