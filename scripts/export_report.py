"""Build the customer-facing weekly report.

This is the file that gets attached to a reply. export_csv.py is the database
export -- everything, every business type, every classification, for your own
use. This is the other end: one territory, one week, filtered down to venues a
supplier can actually sell to, as a workbook rather than a CSV.

Usage:
    python scripts/export_report.py --territory Yorkshire --audience broker
    python scripts/export_report.py --territory "the East Midlands" --days 7
    python scripts/export_report.py --territory UK --since 2026-10-16 --until 2026-10-22
    python scripts/export_report.py --territory Cheshire --customer "R K Henshall"

--territory takes what a supplier would write: a region ("the North West"), a
county ("Cambridgeshire", "notts"), a city ("Bristol"), an exact council name,
or UK. Unknown names are an error rather than a guess -- a report quietly built
on a half-understood territory is worse than one that was never sent, because
nobody finds out until the customer reads it. Add new names to
fsa_pipeline/territories.py.

--audience decides which premises types count. A roaster sells to cafes, pubs
and hotels; a waste contractor also wants takeaways, shops and mobile caterers.
One audience's noise is another's lead.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db, territories
from fsa_pipeline.config import load_config
from fsa_pipeline.csv_export import fetch_records, is_noise_business_type
from fsa_pipeline.logging_utils import setup_logger
from fsa_pipeline.report_filter import AUDIENCES, filter_records
from fsa_pipeline.report_xlsx import build_report_row, write_report


def sort_key(row: dict):
    """Council, then venue. A supplier reads their own patch geographically,
    and a list sorted by date tells them nothing they can act on."""
    return (row["Council"] or "", row["Venue"] or "")


def main(argv=None):
    args = parse_args(argv)
    config = load_config()
    logger = setup_logger(
        "export_report", config.log_dir / ("export_report_%s.log" % dt.date.today().isoformat())
    )

    until = args.until or dt.date.today().isoformat()
    if args.since:
        since = args.since
    else:
        since = (dt.date.fromisoformat(until) - dt.timedelta(days=args.days - 1)).isoformat()

    conn = db.connect(config.db_path)
    try:
        authorities = territories.resolve(conn, args.territory)
    except territories.UnknownTerritory as exc:
        logger.error("%s", exc)
        return 2

    records = [
        record for record in fetch_records(conn, since=since, until=until)
        if not is_noise_business_type(record["business_type"])
    ]
    logger.info("%d classified record(s) in %s to %s before filtering",
                len(records), since, until)

    kept, removed = filter_records(records, audience=args.audience, authorities=authorities)
    logger.info("%d venue(s) after filtering for %s (%d authorities), audience %s",
                len(kept), args.territory, len(authorities), args.audience)
    for reason, count in removed.items():
        if count and reason != "outside territory":
            logger.info("  removed %d: %s", count, reason)

    rows = sorted((build_report_row(record) for record in kept), key=sort_key)

    output = Path(args.output) if args.output else default_output(args.territory, until)
    output.parent.mkdir(parents=True, exist_ok=True)
    stamp = write_report(
        output, rows,
        territory=args.territory, authority_count=len(authorities),
        since=since, until=until, audience=args.audience, removed=removed,
        customer=args.customer,
    )
    logger.info("wrote %d row(s) to %s", len(rows), output)
    logger.info("%s", stamp)
    if not rows:
        logger.warning("No venues in this territory and period. Do not send an empty "
                       "report -- widen the period or the territory first.")
    return 0


def default_output(territory: str, until: str) -> Path:
    slug = "".join(c if c.isalnum() else "-" for c in territory.lower()).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)
    return Path("exports/reports") / ("new-venues_%s_%s.xlsx" % (slug, until))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--territory", required=True,
                        help='What the supplier said their patch is: "Yorkshire", '
                             '"Cambridgeshire", "Bristol", an exact council name, or "UK"')
    parser.add_argument("--audience", default="all", choices=sorted(AUDIENCES),
                        help="Which premises types count (default: all)")
    parser.add_argument("--days", type=int, default=7,
                        help="Length of the window ending at --until (default: 7)")
    parser.add_argument("--since", default=None, help="Start date, YYYY-MM-DD")
    parser.add_argument("--until", default=None, help="End date, YYYY-MM-DD (default: today)")
    parser.add_argument("--customer", default=None,
                        help="Name to put on the About tab, e.g. the supplier's company")
    parser.add_argument("--output", default=None, help="Output path (.xlsx)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
