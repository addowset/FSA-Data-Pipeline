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
"""

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

        if fhrsid not in existing:
            stats.first_seen += 1
            to_insert_current.append((fhrsid, authority_code, *values, fingerprint,
                                       collection_date, collection_date, collection_date))
            to_insert_observations.append((fhrsid, authority_code, collection_date, "first_seen",
                                            *values, fingerprint))
        elif existing[fhrsid] != fingerprint:
            stats.changed += 1
            to_update_current.append((*values, fingerprint, collection_date, collection_date, fhrsid))
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
                (fhrsid, authority_code, {col_list}, fingerprint,
                 first_seen_date, last_seen_date, last_changed_date)
            VALUES (?, ?, {placeholders}, ?, ?, ?, ?)
            """,
            to_insert_current,
        )

    if to_update_current:
        set_clause = ", ".join(f"{c} = ?" for c in _CURRENT_COLUMNS)
        conn.executemany(
            f"""
            UPDATE establishments_current
            SET {set_clause}, fingerprint = ?, last_seen_date = ?, last_changed_date = ?
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
