# FSA Data Pipeline

A local, unattended pipeline that detects newly registered UK food and drink
businesses from FHRS data and (eventually) classifies them as new openings
or ownership changes, using Companies House as a corroborating signal.

Full project brief: [CLAUDE.md](CLAUDE.md). Build proceeds in stages; see
"Status" below for where things currently stand.

## Status

**Stages 1-3 of 7 done. Stage 4 (Companies House) built, pending live verification.**

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

Stage 3 — classifies each day's per-authority changes into `diff_events`
(INSERT/UPDATE/DELETE), reading only from the database (never re-touches
raw files). INSERT = `first_seen` on a day that isn't an authority's
bootstrap day (an authority's very first successful collection has no
prior snapshot to diff against, so it emits no events at all — otherwise
day one would look like 611,596 new registrations). UPDATE = `field_changed`.
DELETE = an establishment present in the previous successful run that
didn't get touched today. Includes the bulk-reupload guard from the brief:
an authority's INSERTs get quarantined (flagged, not discarded) if they
exceed an absolute threshold or a multiple of the authority's trailing
median. Run against all 5 real days so far: 887 INSERT / 2,958 UPDATE /
825 DELETE nationally, every per-day sum reconciling exactly against
stage 2's independently-computed `first_seen`/`field_changed` counts, 0
quarantines triggered (real max seen so far is 58/authority/day, well
under the 150 threshold). See "Design notes" for how the threshold
numbers were chosen and their current limitations.

Stage 4 (collection half) — fetches recent hospitality-sector
incorporations (7 SIC codes in the 56xxx division, confirmed with the
user 2026-08-24) via Companies House's Advanced Search REST endpoint
(chosen over the Streaming API — see "Design notes"), a rolling 14-day
trailing window re-fetched daily, same raw-archive-then-parse pattern as
FHRS (`raw/companies-house/<date>/page_NNNN.json.gz`, gzip, never
overwritten; `companies_current`/`company_observations` derived tables in
the same database). **Not yet live-verified**: no Companies House API key
was available while building this, so the parser is built against the
documented response schema (cross-checked against Companies House's own
developer docs, not just community write-ups) rather than an inspected
real response — unlike every other data source in this project so far.
13 tests pass against documentation-derived fixtures. **Not wired into
`run_daily.ps1`** yet, deliberately — it would fail every day until a key
is supplied. See "Running Companies House collection" below for what's
needed to finish verifying this.

Not yet built: the matcher (name similarity + postcode district, the
"hard part" per the brief) and classification, live-API collection for
priority FHRS authorities, metrics/monitoring, CSV export.

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

## Running the daily diff

After parsing, classify that day's changes:

```bash
python scripts/diff_fhrs.py
```

Reads only from the database, never touches raw files or the network.
Idempotent, same resume behaviour as the other scripts. An authority's
first-ever successful collection has no prior snapshot to diff against,
so it's skipped with no events emitted (not treated as a mass INSERT).

## Running Companies House collection

Not yet part of the daily scheduled run. To finish verifying this stage:

1. Register for a free API key at
   [developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk)
   (I can't do this step — it's account creation).
2. Set it as an environment variable: `$env:COMPANIES_HOUSE_API_KEY = "..."`
   (PowerShell) — never commit it.
3. Run collection, then parsing:

```bash
python scripts/collect_companies_house.py
python scripts/parse_companies_house.py
```

Writes to `raw/companies-house/<today>/` and logs to
`logs/companies_house_collect_<date>.log` /
`..._parse_<date>.log`. Once a real response confirms the documented
schema (see "Design notes"), this gets added to `run_daily.ps1`.

## Rebuilding the database

The database is derived, rebuildable state; the raw archive is the actual
asset. To reprocess all history from scratch (after a parser/fingerprint
change, or to recover from a bug in the parsing/diffing logic):

```bash
python scripts/rebuild_db.py
```

Wipes `fsa_pipeline.db` and replays every dated directory under
`raw/fhrs/` through `parse_fhrs_bulk.py`, then `diff_fhrs.py`, in order.

## Running tests

```bash
pytest
```

## Layout

```
fsa_pipeline/        shared library code (config, HTTP client, FHRS + Companies House
                        parsing, archive writer, db)
scripts/              entry-point scripts: collect_fhrs_bulk.py, parse_fhrs_bulk.py,
                        diff_fhrs.py, collect_companies_house.py, parse_companies_house.py,
                        rebuild_db.py, run_daily.ps1 (scheduled task entry point)
raw/                  raw archive, gitignored — this is the asset, back it up separately
  fhrs/<date>/        one dated directory per collection run
    _authorities-index.xml.gz   that day's local-authority list, as returned by the API
    _manifest.json               per-run summary: counts, and any authorities that failed
    <code>_<name>.xml.gz         one gzip-compressed raw bulk XML file per authority
  companies-house/<date>/   one dated directory per Companies House collection run
    _query.json                  the exact sic_codes/date-range parameters requested
    _manifest.json               per-run summary: hits, pages, failures
    page_NNNN.json.gz            one gzip-compressed raw Advanced Search response page
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
- **An authority's bootstrap day emits no diff events.** Diffing needs a
  previous snapshot; the first successful collection for an authority has
  none. `compute_diff_for_authority` looks up the authority's previous
  *successful* `collection_runs` date (skipping over any failed days, so a
  gap in collection doesn't get misread as mass churn) and returns early
  with zero counts if there isn't one.
- **The bulk-reupload guard only fires on real evidence so far, not a
  calibrated threshold.** The brief asks for "more than 3x trailing
  median, or more than some absolute threshold" without specifying the
  numbers. Current defaults (`config.toml`): absolute threshold 150,
  ratio 3x, but the ratio only applies once an authority has
  `reupload_min_history_days` (5) days of prior `diff_runs` history *and*
  today's INSERT count is at least `reupload_ratio_min_floor` (10) — below
  that floor a ratio is noise (an authority jumping from 0 to 4 new
  registrations is trivially normal, not a bulk re-upload). These numbers
  are reasoned from real data (per-authority daily INSERT count across 4
  real days: median 5, max 58, p95 24) but **no actual bulk-reupload event
  has been observed yet** to calibrate against — revisit once one occurs,
  or once more history accumulates. A quarantined day's INSERT events are
  still recorded in `diff_events` (`quarantined=1`), never discarded, per
  "never lose data" — they're just excluded from whatever stage 5+
  eventually treats as customer-facing signal.
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
- **Companies House: Advanced Search over the Streaming API.** Investigated
  both, as the brief asked. The Streaming API (`stream.companieshouse.gov.uk`)
  pushes every company change nationwide with no server-side SIC-code
  filter -- using it here would mean consuming the whole UK company
  firehose and discarding almost all of it client-side, plus building
  connection/resumption/checkpoint handling for a project whose cadence is
  daily, not sub-minute. The REST Advanced Search endpoint takes
  `sic_codes` and `incorporated_from`/`incorporated_to` as direct query
  parameters -- filtering happens server-side, fits the daily-batch
  pattern already built for FHRS, and (bonus, found while investigating)
  its response includes `sic_codes` per company where the older basic
  `/search/companies` endpoint doesn't. Confirmed with the user 2026-08-24.
- **Companies House parser is unverified against a live response** -- the
  one exception to this project's "inspect real responses before writing
  parsers" rule so far. No API key was available while building this
  (registering for one is account creation, which is the user's to do,
  not something to do on their behalf). The response schema and field
  names are cross-checked against Companies House's own developer
  documentation, not just assumed, but "documented" isn't "observed" --
  flagged clearly in `fsa_pipeline/companies_house.py` and here so this
  gap doesn't get silently forgotten. First real run should double-check
  address-field sparseness and the exact shape of `sic_codes`.

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
