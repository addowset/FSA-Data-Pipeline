"""Manage who gets a weekly report.

    python scripts/subscribers.py list
    python scripts/subscribers.py add "Lee & Fletcher" --territory "the East Midlands" \\
        --audience roaster --email julie.leeandfletcher@gmail.com --from 2026-10-22
    python scripts/subscribers.py history "Lee & Fletcher"
    python scripts/subscribers.py sent 4
    python scripts/subscribers.py pause "Lee & Fletcher"

--from is the end date of the one-off report they were sent when they replied.
Their first weekly then starts the day after it, so they get no gap and
nothing they have already read. Leave it off and the first window starts
tomorrow, which means anything that landed between their sample and their
subscription is never sent to them.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db, subscriptions, territories
from fsa_pipeline.config import load_config
from fsa_pipeline.report_filter import AUDIENCES


def cmd_list(conn, args):
    rows = subscriptions.list_subscribers(conn, active_only=not args.all)
    if not rows:
        print("No subscribers yet.")
        return 0
    print("%-4s %-28s %-24s %-9s %-7s %s" %
          ("id", "customer", "territory", "audience", "active", "last report"))
    for row in rows:
        last = subscriptions.last_issue(conn, row["id"])
        covered = ("%s (%d venues%s)" % (
            last["until"], last["venue_count"],
            "" if last["sent_date"] else ", NOT SENT")) if last else "none yet"
        print("%-4s %-28s %-24s %-9s %-7s %s" % (
            row["id"], row["customer_name"][:28], row["territory"][:24],
            row["audience"], "yes" if row["active"] else "no", covered))
    unsent = subscriptions.unsent_issues(conn)
    if unsent:
        print()
        print("%d report(s) generated but never marked sent. An unsent issue has "
              "already moved that subscriber's window on, so its venues will not "
              "appear in their next report either:" % len(unsent))
        for issue in unsent:
            print("   issue %s: subscriber %s, %s to %s" %
                  (issue["id"], issue["subscriber_id"], issue["since"], issue["until"]))
    return 0


def cmd_add(conn, args):
    try:
        authorities = territories.resolve(conn, args.territory)
    except territories.UnknownTerritory as exc:
        print("error: %s" % exc)
        return 2
    subscriber_id = subscriptions.add_subscriber(
        conn, customer_name=args.customer_name, territory=args.territory,
        audience=args.audience, email=args.email,
        started_date=args.start, notes=args.notes,
    )
    first = subscriptions._day_after(args.start or dt.date.today().isoformat())
    print("Added subscriber %d: %s" % (subscriber_id, args.customer_name))
    print("  territory %r resolves to %d local authorities" % (args.territory, len(authorities)))
    print("  first report will start at %s" % first)
    if not args.start:
        print("  NOTE: no --from given, so anything registered before today will "
              "never reach them. Pass the end date of the sample report they "
              "already had if you want continuity.")
    return 0


def cmd_history(conn, args):
    subscriber = subscriptions.get_subscriber(conn, args.ref)
    issues = subscriptions.issues_for(conn, subscriber["id"])
    print("%s - %s, %s" % (subscriber["customer_name"], subscriber["territory"],
                           subscriber["audience"]))
    if not issues:
        print("  no reports yet; first window starts %s" %
              subscriptions._day_after(subscriber["started_date"]))
        return 0
    for issue in issues:
        print("  %-4s %s to %s  %4d venues  generated %s  %s" % (
            issue["id"], issue["since"], issue["until"], issue["venue_count"],
            issue["generated_at"][:16],
            "sent %s" % issue["sent_date"] if issue["sent_date"] else "NOT SENT"))
    gaps = _gaps(issues)
    print()
    print("  continuity: %s" % ("unbroken" if not gaps else
                                "GAPS at " + ", ".join(gaps)))
    return 0


def _gaps(issues):
    """Any day between two consecutive reports that neither covered.

    Should always be empty -- next_window derives the start from the previous
    end -- so a gap here means a row was edited or deleted by hand.
    """
    out = []
    for earlier, later in zip(issues, issues[1:]):
        expected = subscriptions._day_after(earlier["until"])
        if later["since"] != expected:
            out.append("%s..%s" % (expected, later["since"]))
    return out


def cmd_sent(conn, args):
    subscriptions.mark_sent(conn, args.issue_id, args.date)
    print("issue %d marked sent" % args.issue_id)
    return 0


def cmd_pause(conn, args):
    subscriber = subscriptions.get_subscriber(conn, args.ref)
    subscriptions.set_active(conn, subscriber["id"], False)
    print("%s paused; no further reports will be generated for them" %
          subscriber["customer_name"])
    return 0


def cmd_resume(conn, args):
    subscriber = subscriptions.get_subscriber(conn, args.ref)
    subscriptions.set_active(conn, subscriber["id"], True)
    last = subscriptions.last_issue(conn, subscriber["id"])
    print("%s resumed" % subscriber["customer_name"])
    if last:
        print("  next report will start at %s, so the pause is backfilled" %
              subscriptions._day_after(last["until"]))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="who is subscribed and what they last received")
    p.add_argument("--all", action="store_true", help="include paused subscribers")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("add", help="add a subscriber")
    p.add_argument("customer_name")
    p.add_argument("--territory", required=True)
    p.add_argument("--audience", default="all", choices=sorted(AUDIENCES))
    p.add_argument("--email", default=None)
    p.add_argument("--from", dest="start", default=None,
                   help="end date of the sample report they already had")
    p.add_argument("--notes", default=None)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("history", help="every report one subscriber has had")
    p.add_argument("ref", help="subscriber id or name")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("sent", help="mark an issue as sent")
    p.add_argument("issue_id", type=int)
    p.add_argument("--date", default=None)
    p.set_defaults(func=cmd_sent)

    p = sub.add_parser("pause", help="stop generating reports for a subscriber")
    p.add_argument("ref")
    p.set_defaults(func=cmd_pause)

    p = sub.add_parser("resume", help="restart, backfilling the paused period")
    p.add_argument("ref")
    p.set_defaults(func=cmd_resume)

    args = parser.parse_args(argv)
    config = load_config()
    conn = db.connect(config.db_path)
    try:
        return args.func(conn, args)
    except subscriptions.SubscriptionError as exc:
        print("error: %s" % exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
