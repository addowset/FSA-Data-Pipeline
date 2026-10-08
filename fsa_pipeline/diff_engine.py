"""Classifies each day's per-authority changes as INSERT / UPDATE / DELETE,
per the project brief, with a guard against bulk re-upload artefacts.

Built on top of what stage 2 already recorded, not by re-reading raw XML:

- INSERT: an observations row with change_type='first_seen' on this date.
  This is exactly "an FHRSID not present in the previous snapshot" -- but
  only when there *was* a previous snapshot. An authority's first-ever
  successful collection has nothing to diff against, so no INSERT events
  (or any diff at all) are emitted for it -- otherwise every authority's
  bootstrap day would look like a mass new-registration event, which
  would be actively wrong to hand to a customer.
- UPDATE: an observations row with change_type='field_changed' on this
  date. Already correctly excludes the bootstrap day, since a
  field_changed observation can only exist for a FHRSID that already had
  a prior establishments_current row.
- DELETE: an establishments_current row whose last_seen_date equals the
  authority's *previous* successful collection_date (not just "some date
  before today") -- i.e. it was present last time, and didn't get its
  last_seen_date bumped today, meaning it didn't appear in today's file.
  Comparing against the previous run specifically (via collection_runs),
  not an arbitrary earlier date, keeps this correct across gaps in daily
  collection.

Bulk-reupload guard: only applies to INSERT counts, per the brief. See
compute_diff_for_authority for the exact rule and config.toml for the
threshold values and how they were chosen.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass

from fsa_pipeline.config import Config


@dataclass
class DiffResult:
    previous_collection_date: str | None
    insert_fhrsids: list[int]
    update_fhrsids: list[int]
    delete_fhrsids: list[int]
    quarantined: bool
    quarantine_reason: str | None
    batch_publication: bool = False
    reissue_overlap: float | None = None

    @property
    def insert_count(self) -> int:
        return len(self.insert_fhrsids)

    @property
    def update_count(self) -> int:
        return len(self.update_fhrsids)

    @property
    def delete_count(self) -> int:
        return len(self.delete_fhrsids)


def previous_collection_date(conn: sqlite3.Connection, authority_code: str, before_date: str) -> str | None:
    row = conn.execute(
        """
        SELECT MAX(collection_date) FROM collection_runs
        WHERE authority_code = ? AND status = 'ok' AND collection_date < ?
        """,
        (authority_code, before_date),
    ).fetchone()
    return row[0]


def _trailing_insert_counts(
    conn: sqlite3.Connection, authority_code: str, before_date: str, window_days: int
) -> list[int]:
    rows = conn.execute(
        """
        SELECT insert_count FROM diff_runs
        WHERE authority_code = ? AND collection_date < ?
        ORDER BY collection_date DESC
        LIMIT ?
        """,
        (authority_code, before_date, window_days),
    ).fetchall()
    return [r[0] for r in rows]


def _normalize_for_overlap(value: str | None) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def compute_reissue_overlap(
    conn: sqlite3.Connection, authority_code: str, insert_fhrsids: list[int], delete_fhrsids: list[int],
) -> float:
    """Fraction of INSERTs that have a same-day DELETE with the same
    trading name and postcode (both normalized). A bulk re-upload re-keys
    the register, so nearly every insert pairs with a delete: measured
    2026-10-08, Buckinghamshire's 3,162-insert re-issue matched 99.5%,
    while Birmingham's and Manchester's batch-publication days matched
    4-5%. Uses the bulk post_code only, both sides, so the two are
    compared like-for-like (live-API backfill hasn't run yet when the
    diff does). Reads establishments_current, which never deletes a
    departed record. 0.0 when there are no inserts."""
    if not insert_fhrsids:
        return 0.0
    inserts, deletes = set(insert_fhrsids), set(delete_fhrsids)
    delete_keys = set()
    insert_keys = []
    for fhrsid, name, post_code in conn.execute(
        "SELECT fhrsid, business_name, post_code FROM establishments_current WHERE authority_code = ?",
        (authority_code,),
    ):
        key = (_normalize_for_overlap(name), _normalize_for_overlap(post_code))
        if fhrsid in deletes:
            delete_keys.add(key)
        if fhrsid in inserts:
            insert_keys.append(key)
    if not insert_keys:
        return 0.0
    return sum(1 for key in insert_keys if key in delete_keys) / len(insert_keys)


def _check_reupload_guard(insert_count: int, trailing_counts: list[int], config: Config) -> tuple[bool, str | None]:
    """Whether the INSERT count is large enough to look at more closely --
    since 2026-10-08 a TRIGGER only, not the verdict: see
    classify_flagged_run, which decides quarantine vs batch publication."""
    if insert_count > config.reupload_absolute_threshold:
        return True, (
            f"insert_count={insert_count} exceeds absolute threshold "
            f"({config.reupload_absolute_threshold})"
        )

    if len(trailing_counts) < config.reupload_min_history_days:
        return False, None  # not enough history yet to trust a ratio-based guard

    if insert_count < config.reupload_ratio_min_floor:
        return False, None  # ratio is noisy/meaningless at tiny volumes

    median = statistics.median(trailing_counts)
    if median == 0:
        # A zero trailing median isn't "no tolerance for any insert" --
        # it means this authority has no real baseline to compare
        # against (quiet, or batches registrations occasionally), and
        # median * ratio degenerates to 0, which would quarantine any
        # single non-trivial day no matter how ordinary. Confirmed real
        # 2026-09-11: every one of 189 of this project's 190 quarantines
        # to date was exactly this degenerate case, not a genuine spike
        # against a real baseline (zero of 190 were) -- a council
        # batching 46 registrations after weeks of none is normal
        # behaviour, not a data artefact. Fall back to the absolute
        # threshold alone, already checked above.
        return False, None

    limit = median * config.reupload_ratio_threshold
    if insert_count > limit:
        return True, (
            f"insert_count={insert_count} exceeds {config.reupload_ratio_threshold}x "
            f"trailing median ({median}) = {limit:.1f}, over {len(trailing_counts)} prior day(s)"
        )

    return False, None


def classify_flagged_run(
    overlap: float, trigger_reason: str, config: Config,
) -> tuple[bool, bool, str]:
    """(quarantined, batch_publication, reason) for a run the size guard
    flagged. At or above config.reissue_overlap_ratio it's a re-issue:
    quarantined. Below, it's an authority publishing a large batch of
    real registrations (backlog accumulated since its last publication,
    so true registration dates are unknown): kept in the feed but
    flagged batch_publication."""
    if overlap >= config.reissue_overlap_ratio:
        return True, False, (
            f"{trigger_reason}; {overlap:.0%} of inserts match a same-day delete by name and "
            f"postcode (>= {config.reissue_overlap_ratio:.0%}) -- looks like a bulk re-issue"
        )
    return False, True, (
        f"{trigger_reason}; only {overlap:.0%} of inserts match a same-day delete by name and "
        f"postcode (< {config.reissue_overlap_ratio:.0%}) -- looks like a batch publication of "
        f"real registrations, not a re-issue; registration timing unknown"
    )


def compute_diff_for_authority(
    conn: sqlite3.Connection, authority_code: str, collection_date: str, config: Config
) -> DiffResult:
    prev_date = previous_collection_date(conn, authority_code, collection_date)

    if prev_date is None:
        return DiffResult(
            previous_collection_date=None,
            insert_fhrsids=[],
            update_fhrsids=[],
            delete_fhrsids=[],
            quarantined=False,
            quarantine_reason=None,
        )

    insert_fhrsids = [
        r[0]
        for r in conn.execute(
            """
            SELECT fhrsid FROM observations
            WHERE authority_code = ? AND collection_date = ? AND change_type = 'first_seen'
            """,
            (authority_code, collection_date),
        ).fetchall()
    ]

    update_fhrsids = [
        r[0]
        for r in conn.execute(
            """
            SELECT fhrsid FROM observations
            WHERE authority_code = ? AND collection_date = ? AND change_type = 'field_changed'
            """,
            (authority_code, collection_date),
        ).fetchall()
    ]

    delete_fhrsids = [
        r[0]
        for r in conn.execute(
            """
            SELECT fhrsid FROM establishments_current
            WHERE authority_code = ? AND last_seen_date = ?
            """,
            (authority_code, prev_date),
        ).fetchall()
    ]

    trailing_counts = _trailing_insert_counts(conn, authority_code, collection_date, config.median_window_days)
    triggered, trigger_reason = _check_reupload_guard(len(insert_fhrsids), trailing_counts, config)

    quarantined, batch_publication, reason, overlap = False, False, None, None
    if triggered:
        overlap = compute_reissue_overlap(conn, authority_code, insert_fhrsids, delete_fhrsids)
        quarantined, batch_publication, reason = classify_flagged_run(overlap, trigger_reason, config)

    return DiffResult(
        previous_collection_date=prev_date,
        insert_fhrsids=insert_fhrsids,
        update_fhrsids=update_fhrsids,
        delete_fhrsids=delete_fhrsids,
        quarantined=quarantined,
        quarantine_reason=reason,
        batch_publication=batch_publication,
        reissue_overlap=overlap,
    )


def apply_run_flags(
    conn: sqlite3.Connection, authority_code: str, collection_date: str,
    quarantined: bool, batch_publication: bool, reason: str, overlap: float,
) -> None:
    """Rewrites an already-recorded run's verdict, and the quarantined
    flag on that run's INSERT events to match. Used by
    scripts/reevaluate_quarantines.py; the normal path goes through
    record_diff."""
    conn.execute(
        "UPDATE diff_runs SET quarantined = ?, batch_publication = ?, reissue_overlap = ?, "
        "quarantine_reason = ? WHERE authority_code = ? AND collection_date = ?",
        (int(quarantined), int(batch_publication), overlap, reason, authority_code, collection_date),
    )
    conn.execute(
        "UPDATE diff_events SET quarantined = ? "
        "WHERE authority_code = ? AND collection_date = ? AND event_type = 'INSERT'",
        (int(quarantined), authority_code, collection_date),
    )
    conn.commit()


def already_diffed(conn: sqlite3.Connection, authority_code: str, collection_date: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM diff_runs WHERE authority_code = ? AND collection_date = ?",
        (authority_code, collection_date),
    ).fetchone()
    return row is not None


def record_diff(
    conn: sqlite3.Connection, authority_code: str, collection_date: str, result: DiffResult, computed_at: str
) -> None:
    conn.execute(
        """
        INSERT INTO diff_runs
            (authority_code, collection_date, previous_collection_date,
             insert_count, update_count, delete_count, quarantined, quarantine_reason, computed_at,
             batch_publication, reissue_overlap)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(authority_code, collection_date) DO UPDATE SET
            previous_collection_date = excluded.previous_collection_date,
            insert_count = excluded.insert_count,
            update_count = excluded.update_count,
            delete_count = excluded.delete_count,
            quarantined = excluded.quarantined,
            quarantine_reason = excluded.quarantine_reason,
            computed_at = excluded.computed_at,
            batch_publication = excluded.batch_publication,
            reissue_overlap = excluded.reissue_overlap
        """,
        (
            authority_code, collection_date, result.previous_collection_date,
            result.insert_count, result.update_count, result.delete_count,
            int(result.quarantined), result.quarantine_reason, computed_at,
            int(result.batch_publication), result.reissue_overlap,
        ),
    )

    conn.execute(
        "DELETE FROM diff_events WHERE authority_code = ? AND collection_date = ?",
        (authority_code, collection_date),
    )

    events = (
        [(authority_code, collection_date, fid, "INSERT", int(result.quarantined)) for fid in result.insert_fhrsids]
        + [(authority_code, collection_date, fid, "UPDATE", 0) for fid in result.update_fhrsids]
        + [(authority_code, collection_date, fid, "DELETE", 0) for fid in result.delete_fhrsids]
    )
    if events:
        conn.executemany(
            "INSERT INTO diff_events (authority_code, collection_date, fhrsid, event_type, quarantined) "
            "VALUES (?, ?, ?, ?, ?)",
            events,
        )

    conn.commit()
