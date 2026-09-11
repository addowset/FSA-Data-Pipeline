"""Stage 6 of the brief: "Metrics I need reported" + "Monitoring".

Deliberately a pure reporting/alerting pass -- every function here reads
tables that an earlier stage already writes (collection_runs, diff_runs,
classifications, companies_house_runs, establishments_current). No new
collection, no new derived state, nothing written back to the database.
See scripts/monitor_pipeline.py for the orchestration that calls these
and writes the metrics/alert logs.

Two distinct kinds of function:
- Metrics (new_records_per_authority, classification_ratio,
  extract_date_ages, parse_failure_rate): reporting only, no verdict.
- Checks (check_extract_date_staleness, check_record_count_deviation,
  check_missing_authorities, check_parse_failures,
  check_companies_house_run, check_canaries): each returns whether
  something is wrong and a human-readable reason, same shape as
  diff_engine.py's bulk-reupload guard (_check_reupload_guard) --
  deliberately reused rather than reinvented, including its min-history/
  min-floor gates against noise at low volumes or with no history yet.

Collection-job-failure detection has a real gap to design around: an
authority whose raw file was never fetched leaves ZERO trace in
collection_runs (not even a failure row) -- see check_missing_authorities,
which cross-references the full authorities table instead of only
scanning collection_runs for bad rows.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import statistics

from fsa_pipeline.canaries import CANARIES
from fsa_pipeline.config import Config


# --- metrics (reporting only) ---

def new_records_per_authority(conn: sqlite3.Connection, since_date: str, until_date: str) -> dict[str, int]:
    """Sum of INSERT counts per authority over [since_date, until_date]
    -- the brief's "new records per authority per week" (this is the
    user's own market-size arithmetic). Quarantined bulk-reupload
    artefacts are excluded -- they're a known data glitch, not real
    signal, same exclusion the classifier and CSV export should use."""
    rows = conn.execute(
        """
        SELECT authority_code, SUM(insert_count) FROM diff_runs
        WHERE collection_date BETWEEN ? AND ? AND quarantined = 0
        GROUP BY authority_code
        """,
        (since_date, until_date),
    ).fetchall()
    return {code: total for code, total in rows}


def classification_ratio(conn: sqlite3.Connection, since_date: str, until_date: str) -> dict[str, int]:
    """Counts by "CLASSIFICATION/CONFIDENCE" for every classification
    whose INSERT event falls in [since_date, until_date] -- the brief's
    "ratio of INSERTs by classification"."""
    rows = conn.execute(
        """
        SELECT classification, confidence, COUNT(*) FROM classifications
        WHERE insert_collection_date BETWEEN ? AND ?
        GROUP BY classification, confidence
        """,
        (since_date, until_date),
    ).fetchall()
    return {f"{cls}/{conf}": n for cls, conf, n in rows}


def extract_date_ages(conn: sqlite3.Connection, as_of_date: str) -> list[tuple[str, str, int | None]]:
    """(authority_code, name, age_days) for every known authority's most
    recent successful ExtractDate as of as_of_date. age_days is None for
    an authority that has never had a single successful collection --
    that's check_missing_authorities' concern, not a staleness number."""
    rows = conn.execute(
        """
        SELECT a.code, a.name, MAX(c.extract_date)
        FROM authorities a
        LEFT JOIN collection_runs c
          ON c.authority_code = a.code AND c.status = 'ok' AND c.collection_date <= ?
        GROUP BY a.code, a.name
        ORDER BY a.code
        """,
        (as_of_date,),
    ).fetchall()
    as_of = dt.date.fromisoformat(as_of_date)
    result = []
    for code, name, extract_date in rows:
        age = None if extract_date is None else (as_of - dt.date.fromisoformat(extract_date)).days
        result.append((code, name, age))
    return result


def parse_failure_rate(conn: sqlite3.Connection, collection_date: str) -> tuple[int, int]:
    """(failed_count, attempted_count) for one collection_date.
    "Attempted" means a collection_runs row exists (ok or parse_error);
    an authority whose raw file was never fetched at all doesn't reach
    collection_runs and isn't counted here -- see check_missing_authorities."""
    failed, attempted = conn.execute(
        "SELECT SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END), COUNT(*) "
        "FROM collection_runs WHERE collection_date = ?",
        (collection_date,),
    ).fetchone()
    return (failed or 0, attempted or 0)


# --- checks (verdict + reason) ---

def extract_date_cadence(conn: sqlite3.Connection, authority_code: str) -> float | None:
    """Median number of days between this authority's own successive
    distinct ExtractDates -- the empirically-derived refresh cadence the
    brief asks for (nobody publishes this). None if fewer than 2 distinct
    dates are on record yet."""
    rows = conn.execute(
        "SELECT DISTINCT extract_date FROM collection_runs "
        "WHERE authority_code = ? AND status = 'ok' AND extract_date IS NOT NULL "
        "ORDER BY extract_date",
        (authority_code,),
    ).fetchall()
    dates = [dt.date.fromisoformat(r[0]) for r in rows]
    if len(dates) < 2:
        return None
    gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
    return statistics.median(gaps)


def check_extract_date_staleness(
    conn: sqlite3.Connection, authority_code: str, as_of_date: str, config: Config,
) -> tuple[bool, str | None]:
    """Ratio-based check against the authority's own cadence, with an
    absolute-days fallback for an authority that doesn't have enough
    history to compute one yet.

    The fallback matters more than it looks: checked against real data
    2026-09-11, the worst real cases (River Tees, 141d stale; Hull and
    Goole Port, 135d; Dumfries and Galloway, 110d) have shown only ONE
    distinct ExtractDate in the whole archive -- they'd be silently
    invisible to the ratio check forever (it needs >=2 distinct dates to
    compute a cadence at all), which is exactly backwards: the
    authorities with no evidence of ever refreshing are the ones most
    worth flagging, not the ones safest to skip."""
    row = conn.execute(
        "SELECT MAX(extract_date) FROM collection_runs "
        "WHERE authority_code = ? AND status = 'ok' AND collection_date <= ?",
        (authority_code, as_of_date),
    ).fetchone()
    latest_extract_date = row[0] if row else None
    if latest_extract_date is None:
        return False, None  # never successfully collected -- check_missing_authorities' concern

    age_days = (dt.date.fromisoformat(as_of_date) - dt.date.fromisoformat(latest_extract_date)).days

    distinct_count = conn.execute(
        "SELECT COUNT(DISTINCT extract_date) FROM collection_runs "
        "WHERE authority_code = ? AND status = 'ok' AND extract_date IS NOT NULL",
        (authority_code,),
    ).fetchone()[0]
    if distinct_count < config.monitoring_cadence_min_history:
        if age_days > config.monitoring_staleness_fallback_absolute_days:
            return True, (
                f"ExtractDate age={age_days}d exceeds the {config.monitoring_staleness_fallback_absolute_days}d "
                f"absolute fallback (only {distinct_count} distinct ExtractDate(s) on record -- not enough "
                f"history yet to compute this authority's own cadence, latest ExtractDate {latest_extract_date})"
            )
        return False, None

    if age_days < config.monitoring_staleness_min_age_days:
        return False, None  # ratio is meaningless when the absolute gap is trivial

    cadence = extract_date_cadence(conn, authority_code)
    if cadence is None:
        return False, None

    limit = cadence * config.monitoring_staleness_ratio
    if age_days > limit:
        return True, (
            f"ExtractDate age={age_days}d exceeds {config.monitoring_staleness_ratio}x "
            f"historical median cadence ({cadence}d) = {limit:.1f}d "
            f"(latest ExtractDate {latest_extract_date}, over {distinct_count} prior refresh(es))"
        )
    return False, None


def _trailing_item_counts(
    conn: sqlite3.Connection, authority_code: str, before_date: str, window_days: int,
) -> list[int]:
    rows = conn.execute(
        "SELECT item_count FROM collection_runs "
        "WHERE authority_code = ? AND collection_date < ? AND status = 'ok' AND item_count IS NOT NULL "
        "ORDER BY collection_date DESC LIMIT ?",
        (authority_code, before_date, window_days),
    ).fetchall()
    return [r[0] for r in rows]


def check_record_count_deviation(
    item_count: int | None, trailing_counts: list[int], config: Config,
) -> tuple[bool, str | None]:
    """Deviation in EITHER direction (unlike the bulk-reupload guard,
    which only cares about spikes) -- a sudden drop matters just as much
    here: a truncated file, or an authority silently reporting less."""
    if item_count is None:
        return False, None
    if item_count < config.monitoring_record_count_min_floor:
        return False, None  # too small for a percentage to mean anything
    if len(trailing_counts) < config.monitoring_record_count_min_history_days:
        return False, None  # not enough history yet

    median = statistics.median(trailing_counts)
    if median == 0:
        return False, None

    deviation = abs(item_count - median) / median
    if deviation > config.monitoring_record_count_deviation_ratio:
        direction = "above" if item_count > median else "below"
        return True, (
            f"item_count={item_count} is {deviation:.0%} {direction} its trailing median "
            f"({median:.0f}), over {len(trailing_counts)} prior day(s)"
        )
    return False, None


def check_record_count_deviation_for_authority(
    conn: sqlite3.Connection, authority_code: str, collection_date: str, item_count: int | None, config: Config,
) -> tuple[bool, str | None]:
    """Convenience wrapper: fetches the trailing history and checks it in
    one call, same shape as diff_engine.py's compute_diff_for_authority
    wrapping its own trailing-counts helper -- callers shouldn't need to
    know the trailing window is a private lookup."""
    trailing = _trailing_item_counts(conn, authority_code, collection_date, config.monitoring_record_count_median_window_days)
    return check_record_count_deviation(item_count, trailing, config)


def check_missing_authorities(
    conn: sqlite3.Connection, collection_date: str, manifest: dict | None = None,
) -> list[tuple[str, str, str]]:
    """(authority_code, name, reason) for every known authority with NO
    collection_runs row at all for collection_date. collection_runs alone
    can never reveal this -- a raw file that was never fetched leaves no
    trace there (see module docstring). `manifest` is that day's
    raw/fhrs/<date>/_manifest.json, already parsed by the caller -- when
    an authority appears in manifest["failed"], its actual fetch error is
    used as the reason; otherwise the reason is generic (raw file present
    but never parsed, or collection itself never ran)."""
    all_authorities = conn.execute("SELECT code, name FROM authorities").fetchall()
    collected = {
        r[0] for r in conn.execute(
            "SELECT authority_code FROM collection_runs WHERE collection_date = ?",
            (collection_date,),
        ).fetchall()
    }
    manifest_failures = {
        f["code"]: f.get("error", "unknown fetch error")
        for f in (manifest or {}).get("failed", [])
    }

    missing = []
    for code, name in all_authorities:
        if code in collected:
            continue
        if code in manifest_failures:
            missing.append((code, name, f"collection failed: {manifest_failures[code]}"))
        else:
            missing.append((code, name, "no collection_runs row for this date (never fetched, or fetched but never parsed)"))
    return missing


def check_parse_failures(conn: sqlite3.Connection, collection_date: str, config: Config) -> list[str]:
    """A hard parse_error is always an alert. skipped_records is a
    per-record recovery within an otherwise-successful parse (see
    fsa_pipeline/fhrs_parse.py) -- only worth flagging past
    max_skipped_record_ratio, not on any single skip."""
    alerts = []
    rows = conn.execute(
        "SELECT authority_code, status, error_message, item_count, skipped_records "
        "FROM collection_runs WHERE collection_date = ?",
        (collection_date,),
    ).fetchall()
    for authority_code, status, error_message, item_count, skipped_records in rows:
        if status != "ok":
            alerts.append(f"{authority_code}: parse failed -- {error_message}")
            continue
        if skipped_records and item_count:
            ratio = skipped_records / item_count
            if ratio > config.monitoring_max_skipped_record_ratio:
                alerts.append(
                    f"{authority_code}: {skipped_records}/{item_count} records skipped ({ratio:.1%}), "
                    f"over {config.monitoring_max_skipped_record_ratio:.1%} threshold"
                )
    return alerts


def check_companies_house_run(conn: sqlite3.Connection, collection_date: str) -> str | None:
    """Companies House collection is one national job per day (unlike
    FHRS's per-authority rows), so this is a single presence + status
    check, not a cross-reference against a list of expected rows."""
    row = conn.execute(
        "SELECT status, error_message FROM companies_house_runs WHERE collection_date = ?",
        (collection_date,),
    ).fetchone()
    if row is None:
        return f"no companies_house_runs row for {collection_date} -- collection may not have run"
    status, error_message = row
    if status != "ok":
        return f"companies_house collection status={status}: {error_message}"
    return None


def check_canaries(conn: sqlite3.Connection) -> list[str]:
    """Only stable identity fields are compared -- see
    fsa_pipeline/canaries.py's module docstring for why (never a
    legitimately-changing field like RatingValue)."""
    alerts = []
    for canary in CANARIES:
        row = conn.execute(
            "SELECT authority_code, business_name, local_authority_business_id "
            "FROM establishments_current WHERE fhrsid = ?",
            (canary.fhrsid,),
        ).fetchone()
        if row is None:
            alerts.append(
                f"canary FHRSID {canary.fhrsid} ({canary.expected_business_name!r}) is MISSING from "
                f"establishments_current -- parser or ingest may be broken"
            )
            continue

        authority_code, business_name, local_authority_business_id = row
        mismatches = []
        if authority_code != canary.authority_code:
            mismatches.append(f"authority_code={authority_code!r} expected {canary.authority_code!r}")
        if business_name != canary.expected_business_name:
            mismatches.append(f"business_name={business_name!r} expected {canary.expected_business_name!r}")
        if local_authority_business_id != canary.expected_local_authority_business_id:
            mismatches.append(
                f"local_authority_business_id={local_authority_business_id!r} "
                f"expected {canary.expected_local_authority_business_id!r}"
            )
        if mismatches:
            alerts.append(f"canary FHRSID {canary.fhrsid}: {'; '.join(mismatches)}")
    return alerts
