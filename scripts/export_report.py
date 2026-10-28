"""Build the customer-facing weekly report.

This is the file that gets attached to a reply. export_csv.py is the database
export -- everything, every business type, every classification, for your own
use. This is the other end: one territory, one week, filtered down to venues a
supplier can actually sell to, as a workbook rather than a CSV.

Two modes.

ONE-OFF, for answering a reply. Stateless, records nothing:

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

SUBSCRIBER, for the weekly. Territory and audience come from the subscriber
record, and the window is computed rather than given:

    python scripts/export_report.py --subscriber "Lee & Fletcher"
    python scripts/export_report.py --subscriber 3 --dry-run
    python scripts/export_report.py --subscriber 3 --redo

The window starts the day after that subscriber's last report ended and runs
to the last date the pipeline completely collected. Never "the last seven
days": counting back from whenever the script happens to run opens a gap every
time a send slips, repeats a day every time one comes early, and loses the
current day outright whenever the report is built before the 11:00 collection.
Each report is written to report_issues, which is what the next window is
derived from. See fsa_pipeline/subscriptions.py.

--redo rebuilds the most recent report from its stored window instead of
advancing, for when a send failed. --dry-run says what it would do.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db, subscriptions, territories
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

    conn = db.connect(config.db_path)
    subscriber = None
    issue_to_redo = None

    if args.subscriber:
        try:
            subscriber = subscriptions.get_subscriber(conn, args.subscriber)
        except subscriptions.UnknownSubscriber as exc:
            logger.error("%s", exc)
            return 2
        territory, audience = subscriber["territory"], subscriber["audience"]
        if args.redo:
            issue_to_redo = subscriptions.last_issue(conn, subscriber["id"])
            if issue_to_redo is None:
                logger.error("%s has no report to redo", subscriber["customer_name"])
                return 2
            since, until = issue_to_redo["since"], issue_to_redo["until"]
            logger.info("redoing issue %d, %s to %s -- the window will not advance",
                        issue_to_redo["id"], since, until)
        else:
            try:
                since, until = subscriptions.next_window(conn, subscriber, until=args.until)
            except subscriptions.AlreadyCurrent as exc:
                logger.info("%s", exc)
                logger.info("nothing to do. Use --redo to rebuild the last report.")
                return 0
            except subscriptions.SubscriptionError as exc:
                logger.error("%s", exc)
                return 2
        logger.info("subscriber %d, %s: %s to %s",
                    subscriber["id"], subscriber["customer_name"], since, until)
    else:
        territory, audience = args.territory, args.audience
        until = args.until or dt.date.today().isoformat()
        since = args.since or (
            dt.date.fromisoformat(until) - dt.timedelta(days=args.days - 1)
        ).isoformat()

    try:
        authorities = territories.resolve(conn, territory)
    except territories.UnknownTerritory as exc:
        logger.error("%s", exc)
        return 2

    if args.dry_run:
        logger.info("dry run: would build %s (%d authorities), audience %s, %s to %s",
                    territory, len(authorities), audience, since, until)
        return 0

    records = [
        record for record in fetch_records(conn, since=since, until=until)
        if not is_noise_business_type(record["business_type"])
    ]
    logger.info("%d classified record(s) in %s to %s before filtering",
                len(records), since, until)

    kept, removed = filter_records(records, audience=audience, authorities=authorities)
    logger.info("%d venue(s) after filtering for %s (%d authorities), audience %s",
                len(kept), territory, len(authorities), audience)
    for reason, count in removed.items():
        if count and reason != "outside territory":
            logger.info("  removed %d: %s", count, reason)

    rows = sorted((build_report_row(record) for record in kept), key=sort_key)

    customer = args.customer or (subscriber["customer_name"] if subscriber else None)
    output = Path(args.output) if args.output else default_output(territory, until)
    output.parent.mkdir(parents=True, exist_ok=True)
    stamp = write_report(
        output, rows,
        territory=territory, authority_count=len(authorities),
        since=since, until=until, audience=audience, removed=removed,
        customer=customer,
    )
    logger.info("wrote %d row(s) to %s", len(rows), output)
    logger.info("%s", stamp)

    if subscriber and not issue_to_redo:
        issue_id = subscriptions.record_issue(
            conn, subscriber_id=subscriber["id"], since=since, until=until,
            venue_count=len(rows), output_path=str(output),
        )
        logger.info("recorded as issue %d; the next report will start at %s",
                    issue_id, subscriptions._day_after(until))
        logger.info("mark it sent with: python scripts/subscribers.py sent %d", issue_id)

    if not rows:
        # An empty week is a real result, not a failure, and it still goes out --
        # but the covering email has to say so in its first line. A subscriber
        # who opens an empty attachment expecting venues assumes the report
        # broke, and that is a worse impression than a quiet week.
        entries = sum(count for reason, count in removed.items()
                      if reason != "outside territory")
        logger.warning("NO VENUES in %s for %s to %s.", territory, since, until)
        if entries:
            logger.warning("  %d register entries came up there, but none were venues "
                           "this audience can sell to. Say that in the email -- it is "
                           "a different message from nothing happening at all.", entries)
        else:
            logger.warning("  Nothing was registered in that area at all.")
        logger.warning("  Lead the email with it. Do not let them open an empty file "
                       "expecting venues.")
    return 0


def default_output(territory: str, until: str) -> Path:
    slug = "".join(c if c.isalnum() else "-" for c in territory.lower()).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)
    return Path("exports/reports") / ("new-venues_%s_%s.xlsx" % (slug, until))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--subscriber", default=None,
                        help="Subscriber id or name; territory, audience and the "
                             "window all come from their record")
    parser.add_argument("--territory", default=None,
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
    parser.add_argument("--redo", action="store_true",
                        help="With --subscriber: rebuild the last report from its "
                             "stored window instead of advancing")
    parser.add_argument("--dry-run", action="store_true",
                        help="Say what would be built, build nothing")
    args = parser.parse_args(argv)
    if bool(args.subscriber) == bool(args.territory):
        parser.error("give exactly one of --subscriber or --territory")
    if args.redo and not args.subscriber:
        parser.error("--redo only applies with --subscriber")
    return args


if __name__ == "__main__":
    raise SystemExit(main())
