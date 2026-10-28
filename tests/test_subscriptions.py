import datetime as dt
import sqlite3

import pytest

from fsa_pipeline import subscriptions


@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE authorities (code INTEGER, name TEXT, region_name TEXT);
        CREATE TABLE collection_runs (
            authority_code INTEGER, collection_date TEXT, status TEXT);
        CREATE TABLE report_subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, customer_name TEXT NOT NULL,
            email TEXT, territory TEXT NOT NULL, audience TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, started_date TEXT NOT NULL, notes TEXT);
        CREATE TABLE report_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT, subscriber_id INTEGER NOT NULL,
            since TEXT NOT NULL, until TEXT NOT NULL, venue_count INTEGER NOT NULL,
            generated_at TEXT NOT NULL, output_path TEXT, sent_date TEXT,
            UNIQUE(subscriber_id, since, until));
    """)
    conn.executemany("INSERT INTO authorities VALUES (?, ?, ?)",
                     [(i, "Authority %d" % i, "South West") for i in range(10)])
    return conn


def collected(conn, date, ok=10):
    conn.executemany(
        "INSERT INTO collection_runs VALUES (?, ?, ?)",
        [(i, date, "ok" if i < ok else "failed") for i in range(10)],
    )


def subscriber(conn, started="2026-10-22"):
    sid = subscriptions.add_subscriber(
        conn, customer_name="Lee & Fletcher", territory="the East Midlands",
        audience="roaster", email="julie@example.com", started_date=started,
    )
    return subscriptions.get_subscriber(conn, sid)


# --- the end of the window --------------------------------------------------

def test_last_complete_collection_date_ignores_a_partial_day(conn):
    collected(conn, "2026-10-26", ok=10)
    collected(conn, "2026-10-27", ok=4)      # the run died half way through
    assert subscriptions.last_complete_collection_date(conn) == "2026-10-26"


def test_a_few_failed_authorities_still_counts_as_complete(conn):
    # One council's server being down must not stop the week's reports.
    collected(conn, "2026-10-27", ok=9)
    assert subscriptions.last_complete_collection_date(conn) == "2026-10-27"


def test_no_collection_at_all(conn):
    assert subscriptions.last_complete_collection_date(conn) is None


# --- the start of the window ------------------------------------------------

def test_first_window_starts_the_day_after_the_sample_they_had(conn):
    collected(conn, "2026-10-28")
    sub = subscriber(conn, started="2026-10-22")
    assert subscriptions.next_window(conn, sub) == ("2026-10-23", "2026-10-28")


def test_second_window_starts_where_the_first_ended(conn):
    collected(conn, "2026-10-28")
    sub = subscriber(conn)
    subscriptions.record_issue(conn, subscriber_id=sub["id"], since="2026-10-23",
                               until="2026-10-28", venue_count=4)
    collected(conn, "2026-11-04")
    assert subscriptions.next_window(conn, sub) == ("2026-10-29", "2026-11-04")


def test_a_late_send_cannot_open_a_gap(conn):
    # The failure this module exists to stop: report on the 3rd, nothing until
    # the 12th. The 4th and 5th must still be covered.
    collected(conn, "2026-11-03")
    sub = subscriber(conn)
    subscriptions.record_issue(conn, subscriber_id=sub["id"], since="2026-10-28",
                               until="2026-11-03", venue_count=9)
    collected(conn, "2026-11-12")
    since, until = subscriptions.next_window(conn, sub)
    assert since == "2026-11-04"          # not 2026-11-06
    assert until == "2026-11-12"


def test_an_early_send_cannot_repeat_a_day(conn):
    collected(conn, "2026-11-03")
    sub = subscriber(conn)
    subscriptions.record_issue(conn, subscriber_id=sub["id"], since="2026-10-28",
                               until="2026-11-03", venue_count=9)
    collected(conn, "2026-11-09")
    since, _ = subscriptions.next_window(conn, sub)
    assert since == "2026-11-04"          # the 3rd is not sent twice


def test_the_current_day_is_not_lost_when_collection_has_not_run(conn):
    # Generating before the 11:00 collection: today is not complete, so the
    # window stops yesterday and today lands in the NEXT report rather than
    # falling down the crack between the two.
    collected(conn, "2026-10-27", ok=10)
    collected(conn, "2026-10-28", ok=0)
    sub = subscriber(conn, started="2026-10-22")
    since, until = subscriptions.next_window(conn, sub)
    assert until == "2026-10-27"
    subscriptions.record_issue(conn, subscriber_id=sub["id"], since=since,
                               until=until, venue_count=3)
    collected(conn, "2026-10-28", ok=10)
    assert subscriptions.next_window(conn, sub)[0] == "2026-10-28"


def test_running_twice_in_a_day_refuses(conn):
    collected(conn, "2026-10-28")
    sub = subscriber(conn)
    subscriptions.record_issue(conn, subscriber_id=sub["id"], since="2026-10-23",
                               until="2026-10-28", venue_count=4)
    with pytest.raises(subscriptions.AlreadyCurrent):
        subscriptions.next_window(conn, sub)


def test_a_week_with_no_venues_is_still_a_real_window(conn):
    # Nothing to report is not the same as nothing to do: the window advances,
    # the issue is recorded with a count of zero, and the next one carries on.
    collected(conn, "2026-10-28")
    sub = subscriber(conn)
    since, until = subscriptions.next_window(conn, sub)
    subscriptions.record_issue(conn, subscriber_id=sub["id"], since=since,
                               until=until, venue_count=0)
    collected(conn, "2026-11-04")
    assert subscriptions.next_window(conn, sub) == ("2026-10-29", "2026-11-04")


def test_no_collection_yet_is_an_error_not_a_guess(conn):
    sub = subscriber(conn)
    with pytest.raises(subscriptions.SubscriptionError):
        subscriptions.next_window(conn, sub)


# --- subscribers ------------------------------------------------------------

def test_lookup_by_id_and_by_name(conn):
    sub = subscriber(conn)
    assert subscriptions.get_subscriber(conn, sub["id"])["customer_name"] == "Lee & Fletcher"
    assert subscriptions.get_subscriber(conn, "lee & fletcher")["id"] == sub["id"]


def test_unknown_subscriber_raises(conn):
    with pytest.raises(subscriptions.UnknownSubscriber):
        subscriptions.get_subscriber(conn, "Nobody")


def test_paused_subscribers_are_hidden_by_default(conn):
    sub = subscriber(conn)
    subscriptions.set_active(conn, sub["id"], False)
    assert subscriptions.list_subscribers(conn) == []
    assert len(subscriptions.list_subscribers(conn, active_only=False)) == 1


def test_resuming_backfills_the_paused_period(conn):
    # A pause must not lose the weeks it covered: the next window still starts
    # the day after the last report, however long ago that was.
    collected(conn, "2026-10-28")
    sub = subscriber(conn)
    subscriptions.record_issue(conn, subscriber_id=sub["id"], since="2026-10-23",
                               until="2026-10-28", venue_count=4)
    subscriptions.set_active(conn, sub["id"], False)
    collected(conn, "2026-12-01")
    subscriptions.set_active(conn, sub["id"], True)
    assert subscriptions.next_window(conn, sub) == ("2026-10-29", "2026-12-01")


# --- issues -----------------------------------------------------------------

def test_the_same_window_cannot_be_recorded_twice(conn):
    sub = subscriber(conn)
    subscriptions.record_issue(conn, subscriber_id=sub["id"], since="2026-10-23",
                               until="2026-10-28", venue_count=4)
    with pytest.raises(sqlite3.IntegrityError):
        subscriptions.record_issue(conn, subscriber_id=sub["id"], since="2026-10-23",
                                   until="2026-10-28", venue_count=4)


def test_unsent_issues_are_findable(conn):
    sub = subscriber(conn)
    issue_id = subscriptions.record_issue(conn, subscriber_id=sub["id"],
                                          since="2026-10-23", until="2026-10-28",
                                          venue_count=4)
    assert len(subscriptions.unsent_issues(conn)) == 1
    subscriptions.mark_sent(conn, issue_id, "2026-10-29")
    assert subscriptions.unsent_issues(conn) == []
