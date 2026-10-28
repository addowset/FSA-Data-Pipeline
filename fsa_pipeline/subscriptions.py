"""Who gets a weekly report, and which week they are owed.

The problem this exists to stop is quiet. Before it, every report was built
for "the last seven days" counted back from whenever the script happened to
run, with no memory of what that customer last received. Three ways that goes
wrong, and the third needs no carelessness at all:

  gap      - send on Tuesday the 3rd (28 Oct to 3 Nov), then not until
             Thursday the 12th (6 Nov to 12 Nov). The 4th and 5th are in
             nobody's report, and nothing says so.
  overlap  - send on Tuesday the 3rd, then Monday the 9th (3 Nov to 9 Nov).
             The 3rd goes out twice and the same venues appear in consecutive
             reports, which reads like you cannot count.
  lost day - collection runs at 11:00. Generate at 09:00 and today's venues do
             not exist yet, but next week's window starts tomorrow, so that
             day is skipped permanently. This one happens on a perfect weekly
             schedule.

So the start of a window is never a parameter. It is derived from a fact: the
end date of the last report that subscriber actually received, plus a day. A
gap cannot open however irregularly the reports go out, because the next one
always begins where the last one stopped.

The end of a window is the last date the pipeline has *completely* collected,
not today, which is what kills the third case.
"""
from __future__ import annotations

import datetime as dt
import sqlite3


class SubscriptionError(Exception):
    """Base for the things a caller is expected to handle."""


class UnknownSubscriber(SubscriptionError):
    pass


class AlreadyCurrent(SubscriptionError):
    """This subscriber already has a report covering every collected day.

    Distinct from a week with no venues in it. "No new days have been
    collected since your last report" means do not generate anything; "no
    venues came up in these seven days" is a real report that says so.
    """


# A collection date counts as complete when at least this share of authorities
# returned ok. Not 100%: a single council's server being down should not stop a
# week's reports going out, and the diff engine already handles an authority
# missing a day.
COMPLETENESS = 0.9


def last_complete_collection_date(conn: sqlite3.Connection,
                                  threshold: float = COMPLETENESS) -> str | None:
    """The most recent date the pipeline collected nearly all authorities on.

    This is the right end for a report window. Using today instead loses any
    venue first seen on a day whose collection has not run yet, permanently,
    because the next window starts after today regardless.
    """
    authorities = conn.execute("SELECT COUNT(*) FROM authorities").fetchone()[0]
    if not authorities:
        return None
    row = conn.execute(
        """SELECT collection_date FROM collection_runs
           GROUP BY collection_date
           HAVING SUM(status = 'ok') >= ?
           ORDER BY collection_date DESC LIMIT 1""",
        (threshold * authorities,),
    ).fetchone()
    return row[0] if row else None


# --- subscribers ------------------------------------------------------------

def add_subscriber(conn, *, customer_name: str, territory: str, audience: str,
                   email: str | None = None, started_date: str | None = None,
                   notes: str | None = None) -> int:
    """Add a subscriber. started_date is the day *before* their first window.

    Set it to the end date of the one-off report they were sent when they
    replied, so their first weekly report carries straight on from the sample
    they have already seen -- no gap, and nothing they have read twice.
    """
    started_date = started_date or dt.date.today().isoformat()
    cursor = conn.execute(
        """INSERT INTO report_subscribers
           (customer_name, email, territory, audience, active, started_date, notes)
           VALUES (?, ?, ?, ?, 1, ?, ?)""",
        (customer_name, email, territory, audience, started_date, notes),
    )
    conn.commit()
    return cursor.lastrowid


def get_subscriber(conn, ref: str | int) -> dict:
    """Look a subscriber up by id or by name, case-insensitively."""
    row = None
    if isinstance(ref, int) or str(ref).isdigit():
        row = conn.execute(
            "SELECT * FROM report_subscribers WHERE id = ?", (int(ref),)
        ).fetchone()
    if row is None:
        rows = conn.execute(
            "SELECT * FROM report_subscribers WHERE LOWER(customer_name) = LOWER(?)",
            (str(ref),),
        ).fetchall()
        if len(rows) > 1:
            raise UnknownSubscriber(
                "%r matches %d subscribers -- use the id instead" % (ref, len(rows))
            )
        row = rows[0] if rows else None
    if row is None:
        raise UnknownSubscriber("no subscriber %r" % (ref,))
    return _as_dict(conn, "report_subscribers", row)


def list_subscribers(conn, active_only: bool = True) -> list[dict]:
    query = "SELECT * FROM report_subscribers"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY customer_name"
    return [_as_dict(conn, "report_subscribers", row) for row in conn.execute(query)]


def set_active(conn, subscriber_id: int, active: bool) -> None:
    conn.execute(
        "UPDATE report_subscribers SET active = ? WHERE id = ?",
        (1 if active else 0, subscriber_id),
    )
    conn.commit()


# --- issues -----------------------------------------------------------------

def last_issue(conn, subscriber_id: int) -> dict | None:
    row = conn.execute(
        """SELECT * FROM report_issues WHERE subscriber_id = ?
           ORDER BY until DESC, id DESC LIMIT 1""",
        (subscriber_id,),
    ).fetchone()
    return _as_dict(conn, "report_issues", row) if row else None


def next_window(conn, subscriber: dict, until: str | None = None) -> tuple[str, str]:
    """The window this subscriber is owed next.

    Starts the day after their last report ended, or at started_date for the
    first one. Ends at the last completely collected date unless overridden.
    Raises AlreadyCurrent when there is no new day to report on.
    """
    end = until or last_complete_collection_date(conn)
    if end is None:
        raise SubscriptionError(
            "no collection date is complete yet -- the pipeline has not run"
        )
    previous = last_issue(conn, subscriber["id"])
    if previous:
        start = _day_after(previous["until"])
    else:
        start = _day_after(subscriber["started_date"])
    if start > end:
        raise AlreadyCurrent(
            "%s already has a report covering up to %s, and %s is the last "
            "completely collected day" % (subscriber["customer_name"],
                                          previous["until"] if previous else start, end)
        )
    return start, end


def record_issue(conn, *, subscriber_id: int, since: str, until: str,
                 venue_count: int, output_path: str | None = None) -> int:
    cursor = conn.execute(
        """INSERT INTO report_issues
           (subscriber_id, since, until, venue_count, generated_at, output_path)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (subscriber_id, since, until, venue_count,
         dt.datetime.now().isoformat(timespec="seconds"), output_path),
    )
    conn.commit()
    return cursor.lastrowid


def mark_sent(conn, issue_id: int, sent_date: str | None = None) -> None:
    conn.execute(
        "UPDATE report_issues SET sent_date = ? WHERE id = ?",
        (sent_date or dt.date.today().isoformat(), issue_id),
    )
    conn.commit()


def issues_for(conn, subscriber_id: int) -> list[dict]:
    return [
        _as_dict(conn, "report_issues", row)
        for row in conn.execute(
            "SELECT * FROM report_issues WHERE subscriber_id = ? ORDER BY since",
            (subscriber_id,),
        )
    ]


def unsent_issues(conn) -> list[dict]:
    """Reports generated but never marked sent. Worth checking before a run:
    an unsent issue has already moved the window on, so the venues in it will
    not appear in the next report either."""
    return [
        _as_dict(conn, "report_issues", row)
        for row in conn.execute(
            "SELECT * FROM report_issues WHERE sent_date IS NULL ORDER BY since"
        )
    ]


# --- helpers ----------------------------------------------------------------

def _day_after(date_string: str) -> str:
    return (dt.date.fromisoformat(date_string) + dt.timedelta(days=1)).isoformat()


def _as_dict(conn, table: str, row) -> dict:
    columns = [info[1] for info in conn.execute("PRAGMA table_info(%s)" % table)]
    return dict(zip(columns, row))
