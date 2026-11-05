"""Check a send day's rows against the live data before anything is drafted.

Why this exists. The emails quote a figure and attach the list that proves it,
so the figure has to be true on the morning of the send, not on the morning
the workbook was last rebuilt. On 5 November nine of the sendable rows stood at
nought for the week -- five of them Variant A rows with medians of 3 to 4,
which are normally fine and had simply landed on the wrong side of a council's
publication batch. Which rows those are changes every week, so it cannot be
settled once in a spreadsheet.

It judges on the four-week total, because that is what the emails quote. A
quarter of councils release 80% or more of a month's venues on a single day,
and the median council puts 58% of its month into its biggest day, so a weekly
figure for a small territory is decided by batch timing rather than by trade.
Four weeks is longer than that rhythm. When this was written not one of the 168
sendable rows stood at nought over four weeks, though nine did for the week.

The rule, decided 5 November: a row with nothing over four weeks is sent on its
wider region if one is recorded, and held to the next send day if not. Never
sent empty. The list is non-renewable -- one first contact per company, and no
way to buy another -- so an email with nothing in it does not merely fail, it
spends the only approach we get.

This writes nothing and sends nothing. It reports.

    python scripts/send_gate.py                      # today's batch
    python scripts/send_gate.py --date 2026-11-10
    python scripts/send_gate.py --all                # every batched day
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import load_workbook

from fsa_pipeline import db, subscriptions, territories
from fsa_pipeline.config import load_config
from fsa_pipeline.csv_export import fetch_records, is_noise_business_type
from fsa_pipeline.logging_utils import setup_logger
from fsa_pipeline.report_filter import filter_records

WORKBOOK = Path("docs/leads_all_three_categories.xlsx")
NOTHING = "nothing wider available"

# The report_filter audience sets, keyed by the workbook's category names. A
# roaster sells to cafes, pubs and hotels; a waste contractor also wants
# takeaways, shops and mobile caterers. One audience's noise is another's lead.
AUDIENCE_FOR = {
    "Coffee roasters (wholesale arm)": "roaster",
    "Hospitality insurance brokers": "broker",
    "Commercial cleaning & waste contractors": "waste",
}


def read_rows(path):
    workbook = load_workbook(path, read_only=True)
    rows = list(workbook["Leads"].iter_rows(values_only=True))
    header = list(rows[0])
    index = {key: header.index(key) for key in header if key}
    return [{key: row[position] for key, position in index.items()}
            for row in rows[1:] if row[index["send_status"]] == "send"]


def main(argv=None):
    args = parse_args(argv)
    config = load_config()
    logger = setup_logger(
        "send_gate", config.log_dir / ("send_gate_%s.log" % dt.date.today().isoformat())
    )
    conn = db.connect(config.db_path)

    until = args.until or subscriptions.last_reportable_date(conn)
    end = dt.date.fromisoformat(until)
    week = ((end - dt.timedelta(days=6)).isoformat(), until)
    four = ((end - dt.timedelta(days=27)).isoformat(), until)
    logger.info("week %s to %s; four weeks %s to %s", week[0], week[1], four[0], four[1])

    pools = {}
    for label, (since, upto) in (("week", week), ("four", four)):
        pools[label] = [
            record for record in fetch_records(conn, since=since, until=upto)
            if not is_noise_business_type(record["business_type"])
        ]
        logger.info("%s: %d classified record(s) before per-territory filtering",
                    label, len(pools[label]))

    def count(territory, audience, label):
        """Venues a supplier could sell to. None if the name does not resolve."""
        try:
            authorities = territories.resolve(conn, territory)
        except territories.UnknownTerritory:
            return None
        kept, _removed = filter_records(pools[label], audience=audience,
                                        authorities=authorities)
        return len(kept)

    rows = read_rows(WORKBOOK)
    if args.all:
        wanted = sorted({row["send_batch"] for row in rows if row["send_batch"]})
    else:
        wanted = [args.date or dt.date.today().isoformat()]

    tally = {"send": 0, "widen": 0, "hold": 0, "unresolved": 0}
    for date in wanted:
        batch = [row for row in rows if row["send_batch"] == date]
        if not batch:
            logger.warning("no rows batched for %s", date)
            continue
        print()
        print("=== %s  (%d rows) ===" % (date, len(batch)))
        print("%-28s %-4s %5s %6s  %-7s %s"
              % ("company", "var", "week", "4 wks", "verdict", "note"))
        for row in sorted(batch, key=lambda r: r["company_name"] or ""):
            audience = AUDIENCE_FOR.get(row["category"], "all")
            label = row["region_label"]
            this_week = count(label, audience, "week")
            four_weeks = count(label, audience, "four")

            if four_weeks is None:
                tally["unresolved"] += 1
                print("%-28s %-4s %5s %6s  %-7s %s"
                      % (row["company_name"][:28], row["email_variant"], "-", "-",
                         "UNRESOL", "territories.resolve rejected %r" % label))
                continue

            note = ""
            if four_weeks > 0:
                tally["send"] += 1
                verdict = "send"
                if not this_week:
                    note = ("nothing this week; quote the four-week figure, "
                            "not a weekly one")
            elif row["widen_to"] and row["widen_to"] != NOTHING:
                wider = count(row["widen_to"], audience, "four")
                tally["widen"] += 1
                verdict = "WIDEN"
                note = "nothing in %s over four weeks; %s has %s" % (
                    label, row["widen_to"], wider if wider is not None else "?")
            else:
                tally["hold"] += 1
                verdict = "HOLD"
                note = "nothing in %s over four weeks and nothing wider on file" % label

            stored = row.get("venues_four_weeks")
            if stored is not None and stored != four_weeks:
                note = (note + "; " if note else "") + \
                    "workbook says %s for four weeks, live says %s" % (stored, four_weeks)

            print("%-28s %-4s %5s %6s  %-7s %s"
                  % (row["company_name"][:28], row["email_variant"], this_week,
                     four_weeks, verdict, note))

    print()
    print("send %d   widen %d   hold %d   unresolved %d"
          % (tally["send"], tally["widen"], tally["hold"], tally["unresolved"]))
    if tally["unresolved"]:
        print("An unresolved region_label cannot be drafted from. Add the name to")
        print("fsa_pipeline/territories.py rather than guessing at the patch.")
    conn.close()
    return 1 if tally["unresolved"] else 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=None,
                        help="Send day to check, YYYY-MM-DD (default: today)")
    parser.add_argument("--all", action="store_true",
                        help="Check every batched send day")
    parser.add_argument("--until", default=None,
                        help="End of the windows (default: last reportable date)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
