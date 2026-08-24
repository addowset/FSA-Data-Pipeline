# FSA Data Pipeline

A local, unattended pipeline that detects newly registered UK food and drink
businesses from FHRS data and (eventually) classifies them as new openings
or ownership changes, using Companies House as a corroborating signal.

Full project brief: [CLAUDE.md](CLAUDE.md). Build proceeds in stages; see
"Status" below for where things currently stand.

## Status

**Stage 1 (raw archive) and stage 2 (parsing/storage) of 7: done.**

Stage 1 — fetches the FHRS local-authority index and all 363 authorities'
bulk XML files daily, archives every response unmodified (gzip-compressed)
to `raw/fhrs/<date>/`. First run (2026-08-20): 363/363 succeeded, 37MB
compressed total.

Stage 2 — parses each day's raw archive into `fsa_pipeline.db` (SQLite):
an `establishments_current` table (derived, one row per FHRSID, latest
known values) and an `observations` table (immutable, append-only, one
row per FHRSID per day its data was *new or changed* — see "Design notes"
for why this isn't a full daily snapshot). Also logs `ExtractDate` per
authority per day in `collection_runs`, which is the freshness monitor.
Running daily since 2026-08-20 via a Windows Scheduled Task (11am, catches
up if the machine was off). 5 days in: 612,483 establishments ever seen,
day-over-day churn settling around a few hundred `first_seen` + a few
hundred to ~1,500 `field_changed` per day, 0 collection/parse failures.
`ExtractDate`s observed ranging from the same day back to **2026-04-22**
for one authority — real evidence of the lag your brief flagged.

Not yet built: live-API collection for priority authorities, the diff
engine (INSERT/UPDATE/DELETE classification + bulk-reupload guard),
Companies House matching, classification, metrics/monitoring, CSV export.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
```

## Running the daily collection

```bash
python scripts/collect_fhrs_bulk.py
```

This writes to `raw/fhrs/<today's date>/` and logs to
`logs/fhrs_collect_<date>.log`. It exits non-zero if any authority failed
after retries — check the log and just re-run the same command; authorities
already archived for that date are skipped, so only the failures are
retried.

## Scheduling

Runs automatically once a day via a Windows Scheduled Task named
`FSA Data Pipeline Daily`, calling `scripts/run_daily.ps1` (collection then
parse, in sequence):

```powershell
Get-ScheduledTaskInfo -TaskName "FSA Data Pipeline Daily"   # check status / next run time
Start-ScheduledTask -TaskName "FSA Data Pipeline Daily"     # trigger a run right now
Unregister-ScheduledTask -TaskName "FSA Data Pipeline Daily" -Confirm:$false   # remove it
```

Scheduled for 11am, with `StartWhenAvailable` so a missed run (machine off)
fires as soon as possible afterwards. Runs under your user account with
`LogonType: Interactive` (no stored password) — the machine needs to be on
*and* you logged in (a locked screen is fine) for it to fire.

## Running the daily parse

After collection, parse that day's raw archive into the database:

```bash
python scripts/parse_fhrs_bulk.py
```

Reads `raw/fhrs/<today>/`, never touches the network. Idempotent: an
authority already parsed for that date is skipped, so a partial/failed run
can be re-run safely. Exits non-zero if any authority failed to parse.

## Rebuilding the database

The database is derived, rebuildable state; the raw archive is the actual
asset. To reprocess all history from scratch (after a parser/fingerprint
change, or to recover from a bug in the parsing logic):

```bash
python scripts/rebuild_db.py
```

Wipes `fsa_pipeline.db` and replays every dated directory under
`raw/fhrs/` through `parse_fhrs_bulk.py`, in order.

## Running tests

```bash
pytest
```

## Layout

```
fsa_pipeline/        shared library code (config, HTTP client, FHRS parsing, archive writer, db)
scripts/              entry-point scripts: collect_fhrs_bulk.py, parse_fhrs_bulk.py,
                        rebuild_db.py, run_daily.ps1 (scheduled task entry point)
raw/                  raw archive, gitignored — this is the asset, back it up separately
  fhrs/<date>/        one dated directory per collection run
    _authorities-index.xml.gz   that day's local-authority list, as returned by the API
    _manifest.json               per-run summary: counts, and any authorities that failed
    <code>_<name>.xml.gz         one gzip-compressed raw bulk XML file per authority
logs/                 per-run logs, gitignored
config.toml           non-secret configuration (URLs, timeouts, contact email)
fsa_pipeline.db        SQLite database, gitignored (this is derived state -- rebuildable
                        from raw/ by reparsing, unlike raw/ itself)
```

## Design notes

- **Raw archive is append-only.** Files are never overwritten or deleted.
  Writes go to a temp file and are atomically renamed into place, so a
  crash mid-download can never leave a corrupt file that looks complete.
- **Raw files are gzip-compressed at rest** (~8-10x smaller than the XML as
  received) since the archive grows forever and disk usage compounds. This
  is a storage detail only — decompression is lossless and the archived
  bytes are otherwise byte-identical to what the API returned.
- **The authorities list comes from the live API**
  (`api1-ratings.food.gov.uk/authorities/xml`), not a hardcoded list or URL
  pattern, because each authority's `FileName` field is the FSA's own
  authoritative pointer to its current bulk file. That index is archived
  too, so we keep a daily record of exactly which authorities and URLs
  were current.
- **No establishment-record parsing happens in stage 1.** The only XML
  parsing here is of the authorities index (needed to know what to
  download); everything else is fetched and archived as opaque bytes.
- **`observations` is dedup-on-write, not a full daily snapshot.** With
  611,596 establishments nationally, writing a row per establishment per
  day unconditionally would be ~223M rows/year, ~90-180GB/year in SQLite
  -- mostly exact duplicates, since the raw archive already guarantees
  full daily fidelity. Instead, a row is only appended when a FHRSID is
  new or its content (a hash of its comparable fields) differs from
  `establishments_current`. An unchanged establishment just gets its
  `last_seen_date` bumped. Confirmed against 5 real days of national data:
  day one loaded 611,596 `first_seen` rows; each day after that adds only
  a few hundred `first_seen` + a few hundred to ~1,500 `field_changed`
  rows (~0.2% of the establishment base), not hundreds of thousands.
- **Longitude/latitude are excluded from the change-detection fingerprint.**
  Measured against real data: 86% of all detected "changes" were
  geocode-only, and 99.95% of those were the `Geocode` element simply
  appearing or disappearing between days (FSA's own data is inconsistent
  about including coordinates for a given record), not real jitter or
  relocation. Coordinates are still refreshed in `establishments_current`
  on every touch; they just don't gate whether a change is logged. See
  `fsa_pipeline/fhrs_parse.py` for the full measurement.
  **Incident (2026-08-24):** this fix was deployed while a scheduled run
  was executing, so it started comparing new-formula fingerprints against
  old-formula ones already stored in the database from prior days -- since
  those hash different field sets they can never match, which flagged
  effectively every establishment (611,652 of them) as changed in one run.
  No raw data was affected (raw archive writes are untouched by this class
  of bug by design). Fixed by wiping the derived database and replaying
  all 5 days of raw archive through the corrected code -- see
  `scripts/rebuild_db.py`, kept as a permanent tool for this scenario,
  since the project brief explicitly wants to be able to reprocess history
  when parsing logic changes. Lesson: any future change to
  `_FINGERPRINT_FIELDS` needs either a fingerprint backfill migration or a
  full rebuild before the next scheduled run, not after.
- **FHRS is not one rating scheme.** England/Wales/NI use FHRS (numeric
  0-5, or `AwaitingInspection`/`AwaitingPublication`/`Exempt`). Scotland
  uses FHIS, an entirely different vocabulary (`Pass`, `Improvement
  Required`, `Pass and Eat Safe`, `Awaiting Inspection` -- note the space,
  a different string to FHRS's version, `Exempt`) -- none of it numeric.
  This wasn't in the original brief; found by inspecting a real Scottish
  authority's file. `scheme_type` is stored alongside `rating_value` so
  downstream code can tell which vocabulary it's looking at rather than
  guessing from the string shape.
- **Address lines are independently sparse.** A record can have
  `AddressLine1`, `AddressLine3` and `AddressLine4` but no `AddressLine2`
  -- the parser never assumes a subset is present.

## Data licensing

FHRS and Companies House data are published under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
Any output derived from this pipeline that's shared externally should carry
the attribution: *"Contains public sector information licensed under the
Open Government Licence v3.0."*

## Privacy

This pipeline collects and stores company-level data only: business names,
addresses, postcodes, company numbers, SIC codes. It does not collect,
store, or infer personal data about individuals.
