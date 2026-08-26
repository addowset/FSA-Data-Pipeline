"""SQLite schema and access for the parsed FHRS data.

Two kinds of tables, per the project brief:

- `establishments_current`: derived current state. One row per FHRSID,
  holding its latest known values. Overwritten in place as new data
  arrives -- this table has no history.
- `observations`: immutable, append-only log of when something was first
  seen or changed. Rows are never updated or deleted.

To keep `observations` from growing by ~600k rows/day (most of them exact
duplicates of the day before), a row is only appended when a FHRSID is new
or its fingerprint (a hash of its comparable fields) differs from what's
already in `establishments_current`. An unchanged establishment still gets
its `last_seen_date` bumped in `establishments_current` -- that's what lets
stage 3's diff engine later detect an establishment that stopped appearing
(no bump = implicit absence).

This module does NOT classify changes as new-registration vs
ownership-change vs closure -- that classification, and the bulk-reupload
guard, is stage 3's diff engine. This module only records what changed and
when.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from fsa_pipeline.companies_house import compute_company_fingerprint
from fsa_pipeline.fhrs_bulk import Authority
from fsa_pipeline.fhrs_parse import compute_fingerprint

SCHEMA = """
CREATE TABLE IF NOT EXISTS authorities (
    code TEXT PRIMARY KEY,
    local_authority_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    friendly_name TEXT,
    region_name TEXT,
    url TEXT,
    email TEXT,
    first_seen_date TEXT NOT NULL,
    last_seen_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    authority_code TEXT NOT NULL,
    collection_date TEXT NOT NULL,
    extract_date TEXT,
    item_count INTEGER,
    return_code TEXT,
    raw_file_path TEXT NOT NULL,
    status TEXT NOT NULL,
    skipped_records INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    parsed_at TEXT NOT NULL,
    UNIQUE(authority_code, collection_date)
);

CREATE TABLE IF NOT EXISTS establishments_current (
    fhrsid INTEGER PRIMARY KEY,
    authority_code TEXT NOT NULL,
    local_authority_business_id TEXT,
    business_name TEXT,
    business_type TEXT,
    business_type_id INTEGER,
    address_line_1 TEXT,
    address_line_2 TEXT,
    address_line_3 TEXT,
    address_line_4 TEXT,
    post_code TEXT,
    rating_value TEXT,
    rating_value_numeric INTEGER,
    rating_key TEXT,
    rating_date TEXT,
    scheme_type TEXT,
    new_rating_pending INTEGER,
    hygiene_score INTEGER,
    structural_score INTEGER,
    confidence_in_management_score INTEGER,
    longitude REAL,
    latitude REAL,
    -- Postcode backfill (see fsa_pipeline/fhrs_live.py). post_code above
    -- is always exactly what the bulk XML says, untouched by backfill --
    -- these three are additive and never overwrite it. Effective postcode
    -- for any downstream use (matcher, address-history lookups, territory
    -- filtering) is COALESCE(post_code, postcode_from_live_api).
    postcode_source TEXT,
    postcode_from_live_api TEXT,
    postcode_backfill_checked_at TEXT,
    fingerprint TEXT NOT NULL,
    first_seen_date TEXT NOT NULL,
    last_seen_date TEXT NOT NULL,
    last_changed_date TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_establishments_current_authority
    ON establishments_current(authority_code);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fhrsid INTEGER NOT NULL,
    authority_code TEXT NOT NULL,
    collection_date TEXT NOT NULL,
    change_type TEXT NOT NULL,
    local_authority_business_id TEXT,
    business_name TEXT,
    business_type TEXT,
    business_type_id INTEGER,
    address_line_1 TEXT,
    address_line_2 TEXT,
    address_line_3 TEXT,
    address_line_4 TEXT,
    post_code TEXT,
    rating_value TEXT,
    rating_value_numeric INTEGER,
    rating_key TEXT,
    rating_date TEXT,
    scheme_type TEXT,
    new_rating_pending INTEGER,
    hygiene_score INTEGER,
    structural_score INTEGER,
    confidence_in_management_score INTEGER,
    longitude REAL,
    latitude REAL,
    fingerprint TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_fhrsid ON observations(fhrsid);
CREATE INDEX IF NOT EXISTS idx_observations_authority_date
    ON observations(authority_code, collection_date);

CREATE TABLE IF NOT EXISTS diff_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    authority_code TEXT NOT NULL,
    collection_date TEXT NOT NULL,
    previous_collection_date TEXT,
    insert_count INTEGER NOT NULL,
    update_count INTEGER NOT NULL,
    delete_count INTEGER NOT NULL,
    quarantined INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT,
    computed_at TEXT NOT NULL,
    UNIQUE(authority_code, collection_date)
);

CREATE TABLE IF NOT EXISTS diff_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    authority_code TEXT NOT NULL,
    collection_date TEXT NOT NULL,
    fhrsid INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    quarantined INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_diff_events_authority_date
    ON diff_events(authority_code, collection_date);
CREATE INDEX IF NOT EXISTS idx_diff_events_fhrsid ON diff_events(fhrsid);

-- Companies House data. Same dedup-on-write pattern as the FHRS tables
-- above: companies_current holds latest known values per company_number,
-- company_observations is an immutable append-only log of first-seen/
-- changed events. See fsa_pipeline/companies_house.py for field
-- provenance and verification notes.
CREATE TABLE IF NOT EXISTS companies_current (
    company_number TEXT PRIMARY KEY,
    company_name TEXT,
    company_status TEXT,
    company_subtype TEXT,
    company_type TEXT,
    date_of_creation TEXT,
    date_of_cessation TEXT,
    address_line_1 TEXT,
    address_line_2 TEXT,
    locality TEXT,
    region TEXT,
    postal_code TEXT,
    country TEXT,
    sic_codes TEXT,
    fingerprint TEXT NOT NULL,
    first_seen_date TEXT NOT NULL,
    last_seen_date TEXT NOT NULL,
    last_changed_date TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companies_current_postal_code ON companies_current(postal_code);

CREATE TABLE IF NOT EXISTS company_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_number TEXT NOT NULL,
    collection_date TEXT NOT NULL,
    change_type TEXT NOT NULL,
    company_name TEXT,
    company_status TEXT,
    company_subtype TEXT,
    company_type TEXT,
    date_of_creation TEXT,
    date_of_cessation TEXT,
    address_line_1 TEXT,
    address_line_2 TEXT,
    locality TEXT,
    region TEXT,
    postal_code TEXT,
    country TEXT,
    sic_codes TEXT,
    fingerprint TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_company_observations_company_number
    ON company_observations(company_number);

CREATE TABLE IF NOT EXISTS companies_house_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_date TEXT NOT NULL UNIQUE,
    hits INTEGER,
    pages_parsed INTEGER,
    status TEXT NOT NULL,
    skipped_records INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    parsed_at TEXT NOT NULL
);

-- Matcher evidence (stage 4b). Derived/rebuildable, unlike observations/
-- company_observations -- rerunning the matcher for a FHRSID replaces its
-- prior candidates rather than appending to them, since a "match" is a
-- computation over current data, not an observed fact. See
-- fsa_pipeline/matcher.py for the matching approach and its reasoning.
CREATE TABLE IF NOT EXISTS company_match_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fhrsid INTEGER NOT NULL,
    authority_code TEXT NOT NULL,
    insert_collection_date TEXT NOT NULL,
    candidates_found INTEGER NOT NULL,
    best_match_company_number TEXT,
    best_match_score REAL,
    matched_at TEXT NOT NULL,
    UNIQUE(fhrsid, insert_collection_date)
);

CREATE TABLE IF NOT EXISTS company_match_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fhrsid INTEGER NOT NULL,
    insert_collection_date TEXT NOT NULL,
    rank INTEGER NOT NULL,
    company_number TEXT NOT NULL,
    company_name TEXT NOT NULL,
    name_similarity_score REAL NOT NULL,
    postcode_district TEXT NOT NULL,
    address_company_count INTEGER NOT NULL,
    is_high_density_address INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_company_match_candidates_fhrsid
    ON company_match_candidates(fhrsid, insert_collection_date);

-- Postcode backfill tracking (see fsa_pipeline/fhrs_live.py). Runs are
-- per (authority, day), for idempotency, same pattern as collection_runs.
-- Events are an immutable audit trail, deliberately separate from
-- `observations` -- a backfill is us correcting our own data, not a
-- change FSA's own data made, and must never be misread as one.
CREATE TABLE IF NOT EXISTS postcode_backfill_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    authority_code TEXT NOT NULL,
    run_date TEXT NOT NULL,
    candidates_checked INTEGER NOT NULL,
    resolved_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    run_at TEXT NOT NULL,
    UNIQUE(authority_code, run_date)
);

CREATE TABLE IF NOT EXISTS postcode_backfill_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fhrsid INTEGER NOT NULL,
    authority_code TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    resolved INTEGER NOT NULL,
    postcode_value TEXT
);
CREATE INDEX IF NOT EXISTS idx_postcode_backfill_events_fhrsid
    ON postcode_backfill_events(fhrsid);
"""

# Columns added to establishments_current after it was already in use in
# production (2026-08-26). CREATE TABLE IF NOT EXISTS above is a no-op
# against an existing table, so these need an explicit migration -- see
# _ensure_columns, called from connect().
_ESTABLISHMENTS_CURRENT_MIGRATIONS = {
    "postcode_source": "TEXT",
    "postcode_from_live_api": "TEXT",
    "postcode_backfill_checked_at": "TEXT",
}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, sql_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
    conn.commit()

_CURRENT_COLUMNS = (
    "local_authority_business_id", "business_name", "business_type", "business_type_id",
    "address_line_1", "address_line_2", "address_line_3", "address_line_4", "post_code",
    "rating_value", "rating_value_numeric", "rating_key", "rating_date", "scheme_type",
    "new_rating_pending", "hygiene_score", "structural_score", "confidence_in_management_score",
    "longitude", "latitude",
)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _ensure_columns(conn, "establishments_current", _ESTABLISHMENTS_CURRENT_MIGRATIONS)
    return conn


def upsert_authorities(conn: sqlite3.Connection, authorities: list[Authority], as_of_date: str) -> None:
    conn.executemany(
        """
        INSERT INTO authorities (code, local_authority_id, name, friendly_name, region_name, url, email,
                                  first_seen_date, last_seen_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            local_authority_id = excluded.local_authority_id,
            name = excluded.name,
            friendly_name = excluded.friendly_name,
            region_name = excluded.region_name,
            url = excluded.url,
            email = excluded.email,
            last_seen_date = excluded.last_seen_date
        """,
        [
            (a.code, a.local_authority_id, a.name, a.friendly_name, a.region_name, a.url, a.email,
             as_of_date, as_of_date)
            for a in authorities
        ],
    )
    conn.commit()


def record_collection_run(
    conn: sqlite3.Connection,
    *,
    authority_code: str,
    collection_date: str,
    extract_date: str | None,
    item_count: int | None,
    return_code: str | None,
    raw_file_path: str,
    status: str,
    skipped_records: int = 0,
    error_message: str | None = None,
    parsed_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO collection_runs (authority_code, collection_date, extract_date, item_count,
                                      return_code, raw_file_path, status, skipped_records,
                                      error_message, parsed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(authority_code, collection_date) DO UPDATE SET
            extract_date = excluded.extract_date,
            item_count = excluded.item_count,
            return_code = excluded.return_code,
            raw_file_path = excluded.raw_file_path,
            status = excluded.status,
            skipped_records = excluded.skipped_records,
            error_message = excluded.error_message,
            parsed_at = excluded.parsed_at
        """,
        (authority_code, collection_date, extract_date, item_count, return_code, raw_file_path,
         status, skipped_records, error_message, parsed_at),
    )
    conn.commit()


def already_parsed(conn: sqlite3.Connection, authority_code: str, collection_date: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM collection_runs WHERE authority_code = ? AND collection_date = ? AND status = 'ok'",
        (authority_code, collection_date),
    ).fetchone()
    return row is not None


@dataclass
class IngestStats:
    first_seen: int = 0
    changed: int = 0
    unchanged: int = 0


def ingest_establishments(
    conn: sqlite3.Connection,
    authority_code: str,
    collection_date: str,
    establishments: list[dict],
) -> IngestStats:
    stats = IngestStats()

    existing = dict(
        conn.execute(
            "SELECT fhrsid, fingerprint FROM establishments_current WHERE authority_code = ?",
            (authority_code,),
        ).fetchall()
    )

    to_insert_current = []
    to_update_current = []
    to_touch_last_seen = []
    to_insert_observations = []

    for est in establishments:
        fhrsid = est["fhrsid"]
        fingerprint = compute_fingerprint(est)
        values = tuple(est[c] for c in _CURRENT_COLUMNS)
        # Not part of _CURRENT_COLUMNS/_FINGERPRINT_FIELDS deliberately: it
        # must never affect change detection, and postcode_from_live_api /
        # postcode_backfill_checked_at (set only by the backfill job, see
        # fsa_pipeline/fhrs_live.py) must never be touched by this bulk
        # ingest path at all, or a backfilled postcode could be silently
        # wiped out the next time bulk reports no postcode for the same
        # establishment (which, per real data, is the common case).
        postcode_source = "bulk" if est["post_code"] else None

        if fhrsid not in existing:
            stats.first_seen += 1
            to_insert_current.append((fhrsid, authority_code, *values, fingerprint, postcode_source,
                                       collection_date, collection_date, collection_date))
            to_insert_observations.append((fhrsid, authority_code, collection_date, "first_seen",
                                            *values, fingerprint))
        elif existing[fhrsid] != fingerprint:
            stats.changed += 1
            to_update_current.append((*values, fingerprint, postcode_source, collection_date, collection_date, fhrsid))
            to_insert_observations.append((fhrsid, authority_code, collection_date, "field_changed",
                                            *values, fingerprint))
        else:
            stats.unchanged += 1
            # Geocode isn't part of the fingerprint (see _FINGERPRINT_FIELDS),
            # so it's refreshed here even on an "unchanged" row -- otherwise
            # establishments_current would go stale on coordinates forever
            # once everything else about a record stopped changing.
            to_touch_last_seen.append((est["longitude"], est["latitude"], collection_date, fhrsid))

    col_list = ", ".join(_CURRENT_COLUMNS)
    placeholders = ", ".join("?" * len(_CURRENT_COLUMNS))

    if to_insert_current:
        conn.executemany(
            f"""
            INSERT INTO establishments_current
                (fhrsid, authority_code, {col_list}, fingerprint, postcode_source,
                 first_seen_date, last_seen_date, last_changed_date)
            VALUES (?, ?, {placeholders}, ?, ?, ?, ?, ?)
            """,
            to_insert_current,
        )

    if to_update_current:
        set_clause = ", ".join(f"{c} = ?" for c in _CURRENT_COLUMNS)
        conn.executemany(
            f"""
            UPDATE establishments_current
            SET {set_clause}, fingerprint = ?, postcode_source = ?, last_seen_date = ?, last_changed_date = ?
            WHERE fhrsid = ?
            """,
            to_update_current,
        )

    if to_touch_last_seen:
        conn.executemany(
            "UPDATE establishments_current SET longitude = ?, latitude = ?, last_seen_date = ? WHERE fhrsid = ?",
            to_touch_last_seen,
        )

    if to_insert_observations:
        conn.executemany(
            f"""
            INSERT INTO observations
                (fhrsid, authority_code, collection_date, change_type, {col_list}, fingerprint)
            VALUES (?, ?, ?, ?, {placeholders}, ?)
            """,
            to_insert_observations,
        )

    conn.commit()
    return stats


_COMPANY_CURRENT_COLUMNS = (
    "company_name", "company_status", "company_subtype", "company_type",
    "date_of_creation", "date_of_cessation",
    "address_line_1", "address_line_2", "locality", "region", "postal_code", "country",
    "sic_codes",
)


def already_parsed_companies_house(conn: sqlite3.Connection, collection_date: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM companies_house_runs WHERE collection_date = ? AND status = 'ok'",
        (collection_date,),
    ).fetchone()
    return row is not None


def record_companies_house_run(
    conn: sqlite3.Connection,
    *,
    collection_date: str,
    hits: int | None,
    pages_parsed: int | None,
    status: str,
    skipped_records: int = 0,
    error_message: str | None = None,
    parsed_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO companies_house_runs (collection_date, hits, pages_parsed, status, skipped_records, error_message, parsed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(collection_date) DO UPDATE SET
            hits = excluded.hits,
            pages_parsed = excluded.pages_parsed,
            status = excluded.status,
            skipped_records = excluded.skipped_records,
            error_message = excluded.error_message,
            parsed_at = excluded.parsed_at
        """,
        (collection_date, hits, pages_parsed, status, skipped_records, error_message, parsed_at),
    )
    conn.commit()


def ingest_companies(conn: sqlite3.Connection, collection_date: str, companies: list[dict]) -> IngestStats:
    """Same dedup-on-write pattern as ingest_establishments -- see db.py
    module docstring and fsa_pipeline/companies_house.py."""
    stats = IngestStats()

    existing = dict(conn.execute("SELECT company_number, fingerprint FROM companies_current").fetchall())

    to_insert_current = []
    to_update_current = []
    to_touch_last_seen = []
    to_insert_observations = []

    for company in companies:
        company_number = company["company_number"]
        fingerprint = compute_company_fingerprint(company)
        values = tuple(company[c] for c in _COMPANY_CURRENT_COLUMNS)

        if company_number not in existing:
            stats.first_seen += 1
            to_insert_current.append((company_number, *values, fingerprint,
                                       collection_date, collection_date, collection_date))
            to_insert_observations.append((company_number, collection_date, "first_seen", *values, fingerprint))
        elif existing[company_number] != fingerprint:
            stats.changed += 1
            to_update_current.append((*values, fingerprint, collection_date, collection_date, company_number))
            to_insert_observations.append((company_number, collection_date, "field_changed", *values, fingerprint))
        else:
            stats.unchanged += 1
            to_touch_last_seen.append((collection_date, company_number))

    col_list = ", ".join(_COMPANY_CURRENT_COLUMNS)
    placeholders = ", ".join("?" * len(_COMPANY_CURRENT_COLUMNS))

    if to_insert_current:
        conn.executemany(
            f"""
            INSERT INTO companies_current
                (company_number, {col_list}, fingerprint, first_seen_date, last_seen_date, last_changed_date)
            VALUES (?, {placeholders}, ?, ?, ?, ?)
            """,
            to_insert_current,
        )

    if to_update_current:
        set_clause = ", ".join(f"{c} = ?" for c in _COMPANY_CURRENT_COLUMNS)
        conn.executemany(
            f"""
            UPDATE companies_current
            SET {set_clause}, fingerprint = ?, last_seen_date = ?, last_changed_date = ?
            WHERE company_number = ?
            """,
            to_update_current,
        )

    if to_touch_last_seen:
        conn.executemany(
            "UPDATE companies_current SET last_seen_date = ? WHERE company_number = ?",
            to_touch_last_seen,
        )

    if to_insert_observations:
        conn.executemany(
            f"""
            INSERT INTO company_observations
                (company_number, collection_date, change_type, {col_list}, fingerprint)
            VALUES (?, ?, ?, {placeholders}, ?)
            """,
            to_insert_observations,
        )

    conn.commit()
    return stats


def already_matched(conn: sqlite3.Connection, fhrsid: int, insert_collection_date: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM company_match_runs WHERE fhrsid = ? AND insert_collection_date = ?",
        (fhrsid, insert_collection_date),
    ).fetchone()
    return row is not None


def record_match(
    conn: sqlite3.Connection,
    fhrsid: int,
    authority_code: str,
    insert_collection_date: str,
    candidates: list,
    matched_at: str,
) -> None:
    best = candidates[0] if candidates else None

    conn.execute(
        """
        INSERT INTO company_match_runs
            (fhrsid, authority_code, insert_collection_date, candidates_found,
             best_match_company_number, best_match_score, matched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fhrsid, insert_collection_date) DO UPDATE SET
            candidates_found = excluded.candidates_found,
            best_match_company_number = excluded.best_match_company_number,
            best_match_score = excluded.best_match_score,
            matched_at = excluded.matched_at
        """,
        (
            fhrsid, authority_code, insert_collection_date, len(candidates),
            best.company_number if best else None,
            best.name_similarity_score if best else None,
            matched_at,
        ),
    )

    conn.execute(
        "DELETE FROM company_match_candidates WHERE fhrsid = ? AND insert_collection_date = ?",
        (fhrsid, insert_collection_date),
    )

    if candidates:
        conn.executemany(
            """
            INSERT INTO company_match_candidates
                (fhrsid, insert_collection_date, rank, company_number, company_name,
                 name_similarity_score, postcode_district, address_company_count, is_high_density_address)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (fhrsid, insert_collection_date, rank, c.company_number, c.company_name,
                 c.name_similarity_score, c.postcode_district, c.address_company_count,
                 int(c.is_high_density_address))
                for rank, c in enumerate(candidates, start=1)
            ],
        )

    conn.commit()


def get_postcode_backfill_candidates(conn: sqlite3.Connection, recheck_after_days: int) -> list[tuple[int, str]]:
    """FHRSIDs with no bulk postcode that either haven't been checked
    against the live API yet, or were checked long enough ago to be
    worth rechecking (a gap might resolve outside the normal bulk
    cycle -- see README "Design notes")."""
    rows = conn.execute(
        """
        SELECT fhrsid, authority_code FROM establishments_current
        WHERE post_code IS NULL
          AND (
                postcode_backfill_checked_at IS NULL
                OR julianday('now') - julianday(postcode_backfill_checked_at) >= ?
              )
        """,
        (recheck_after_days,),
    ).fetchall()
    return rows


def already_backfilled(conn: sqlite3.Connection, authority_code: str, run_date: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM postcode_backfill_runs WHERE authority_code = ? AND run_date = ? AND status = 'ok'",
        (authority_code, run_date),
    ).fetchone()
    return row is not None


def record_postcode_backfill_run(
    conn: sqlite3.Connection,
    *,
    authority_code: str,
    run_date: str,
    candidates_checked: int,
    resolved_count: int,
    status: str,
    error_message: str | None = None,
    run_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO postcode_backfill_runs
            (authority_code, run_date, candidates_checked, resolved_count, status, error_message, run_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(authority_code, run_date) DO UPDATE SET
            candidates_checked = excluded.candidates_checked,
            resolved_count = excluded.resolved_count,
            status = excluded.status,
            error_message = excluded.error_message,
            run_at = excluded.run_at
        """,
        (authority_code, run_date, candidates_checked, resolved_count, status, error_message, run_at),
    )
    conn.commit()


def apply_postcode_backfill(
    conn: sqlite3.Connection,
    fhrsid: int,
    authority_code: str,
    postcode_value: str | None,
    checked_at: str,
) -> None:
    """Records a backfill check. If postcode_value is given, sets
    postcode_from_live_api and postcode_source='live_api' -- but only
    while post_code (the bulk column) is still NULL, so this can never
    clobber a real bulk value even under a rare race. Always records an
    audit event, resolved or not, and updates postcode_backfill_checked_at
    either way so an unresolved case isn't queried again until the
    recheck interval passes."""
    if postcode_value:
        conn.execute(
            """
            UPDATE establishments_current
            SET postcode_from_live_api = ?, postcode_source = 'live_api', postcode_backfill_checked_at = ?
            WHERE fhrsid = ? AND post_code IS NULL
            """,
            (postcode_value, checked_at, fhrsid),
        )
    else:
        conn.execute(
            "UPDATE establishments_current SET postcode_backfill_checked_at = ? WHERE fhrsid = ?",
            (checked_at, fhrsid),
        )

    conn.execute(
        """
        INSERT INTO postcode_backfill_events (fhrsid, authority_code, checked_at, resolved, postcode_value)
        VALUES (?, ?, ?, ?, ?)
        """,
        (fhrsid, authority_code, checked_at, int(bool(postcode_value)), postcode_value),
    )
    conn.commit()
