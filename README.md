# FSA Data Pipeline

A local, unattended pipeline that detects newly registered UK food and drink
businesses from FHRS data and (eventually) classifies them as new openings
or ownership changes, using Companies House as a corroborating signal.

Full project brief: [CLAUDE.md](CLAUDE.md). Build proceeds in stages; see
"Status" below for where things currently stand.

## Status

**Stage 4 of 7 done and live-verified (collection and matcher). Plus a
supplementary postcode-backfill job, outside the staged build order.**

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
the same database). **Live-verified 2026-08-24**: first real run
returned 1,993 companies across 4 pages, 0 collection failures, 0 parse
errors — the documentation-derived parser matched the real schema
exactly. Field presence measured across all 1,993 real records:
`company_name`/`company_number`/`company_status`/`company_type`/
`date_of_creation`/`registered_office_address`/`sic_codes` always
present; `date_of_cessation` never present (expected — these are fresh
incorporations); `company_subtype` in only 0.6%; within the address,
`address_line_1`/`locality`/`postal_code` present ~100%, `address_line_2`
in 32%, `region` in only 12% — same sparse-field pattern already seen in
FHRS. Confirmed the SIC filter is an "any code matches" filter, not
"primary code only" — companies come back with hospitality codes mixed
among several unrelated ones (e.g. a company coded primarily as "Hotels
and similar accommodation" that also carries 56101), which the matcher
will need to keep in mind. 17 tests pass, including the `.env` loader
(secrets in a local gitignored file, never a system environment
variable, never committed).

Stage 4 (matcher half) — for each FHRS INSERT event, finds candidate
Companies House matches: company name similarity (normalized, legal
suffixes like LTD/LIMITED/LLP stripped) among companies in the *same
postcode district* (the outward code, e.g. "NG17" — deliberately not
full address, per the brief's explicit warning against matching on
address equality). Address density (how many companies share a
registered office) is computed and stored as evidence, not used to gate
matching — a candidate can still be the right match at a high-density
address, it just gets flagged so stage 5 can discount an address-based
signal there. Doesn't decide NEW_VENUE/OWNERSHIP_CHANGE/UNKNOWN itself —
that's stage 5, consuming this evidence. Run against all 887 real INSERT
events collected so far: 339 (38%) found at least one same-district
candidate; **3 exact name matches** (e.g. "The Cotswold Cafe" ↔ "THE
COTSWOLD CAFE LIMITED", both incorporated/first-seen within the same
window) plus a further 4 scoring 0.7-0.9 and 9 more at 0.5-0.7 — real,
plausible new-venue signal. The other 62% found nothing in-district,
consistent with the brief's own expectation that most independent cafés
never incorporate. 31 real candidates got flagged for a high-density
address, all at moderate-to-low name-similarity scores, not masquerading
as strong matches. 20 new tests, all passing. See "Design notes" for the
normalization approach and its known rough edges.

Postcode backfill (supplementary, outside the numbered build order) —
investigated 2026-08-25/26 after the user asked whether the FHRS live
Establishments API could replace bulk collection for stale authorities.
It couldn't (see "Design notes" for the full investigation: identical
FHRSIDs and ratings on every authority tested, so no evidence it
surfaces new registrations or rating changes bulk misses), but it
surfaced a bigger, unrelated problem: **17.10% of establishments
nationally (104,798 of 612,974) had no postcode at all in bulk data**,
uncorrelated with staleness. `scripts/backfill_postcodes.py` recovers
what it can from the live API, storing it in a separate column
(`postcode_from_live_api`) that never overwrites or gets overwritten by
the bulk `post_code` column — see "Design notes" for why that separation
matters. First real run (2026-08-26): 104,916 candidates across 360
authorities, **93,745 resolved (89.4%)**, bringing the national
unresolved rate down to 1.82% (11,171). Resolution rate varies sharply
and specifically by council, not by scheme or region — e.g. Falkirk and
the Western Isles resolved ~100%, while Aberdeen City, Edinburgh and
Aberdeenshire resolved 0% (the postcode is genuinely missing from FSA's
system for those councils via any access method, confirmed by testing
the live API directly). 15 new tests, all passing. **Not wired into
`run_daily.ps1`** — deliberately a separate, manually-run job for now;
see "Running the postcode backfill" for how to run it and the scheduling
question still open.

Not yet built: classification (NEW_VENUE/OWNERSHIP_CHANGE/UNKNOWN +
confidence + reason), live-API collection for priority FHRS authorities,
metrics/monitoring, CSV export.

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

Part of the daily scheduled run (`run_daily.ps1`) as of 2026-08-24. To
run manually:

```bash
python scripts/collect_companies_house.py
python scripts/parse_companies_house.py
```

Writes to `raw/companies-house/<today>/` and logs to
`logs/companies_house_collect_<date>.log` / `..._parse_<date>.log`.
Needs `COMPANIES_HOUSE_API_KEY` — set in the local `.env` file (see
"Design notes"; register for a free key at
[developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk)).

## Running the matcher

After diffing and Companies House parsing, find candidate matches for
each FHRS INSERT event:

```bash
python scripts/match_companies_house.py
```

Reads only from the database, never touches raw files or the network.
Idempotent per (FHRSID, INSERT date); pass `--force` to rematch
everything (candidates can change as new Companies House data arrives —
matching isn't a one-time fact the way an observation is).

## Running the postcode backfill

Not part of the daily scheduled run — a supplementary job, run manually
or scheduled separately at whatever cadence you choose:

```bash
python scripts/backfill_postcodes.py
```

Only touches establishments with no postcode in bulk data, skipping any
already checked within `postcode_recheck_after_days` (config.toml,
default 30). Writes to `raw/fhrs-live/<today>/` and logs to
`logs/backfill_postcodes_<date>.log`. Idempotent per (authority, day);
pass `--force` to rerun. First run resolved 89.4% of the national gap —
see "Status" above and "Design notes" below for the full numbers and why
the live API only partially helps.

## Rebuilding the database

The database is derived, rebuildable state; the raw archive is the actual
asset. To reprocess all history from scratch (after a parser/fingerprint
change, or to recover from a bug in the parsing/diffing/matching logic):

```bash
python scripts/rebuild_db.py
```

Wipes `fsa_pipeline.db` and replays every dated directory under
`raw/fhrs/` through `parse_fhrs_bulk.py` then `diff_fhrs.py`, every dated
directory under `raw/companies-house/` through `parse_companies_house.py`,
then `match_companies_house.py` once at the end. Never touches the
network.

**Does not replay postcode backfill** — wiping `establishments_current`
resets `postcode_from_live_api`/`postcode_backfill_checked_at` to empty,
since that table is dropped and rebuilt fresh. Run
`python scripts/backfill_postcodes.py` again afterwards to restore it;
it'll reuse the already-archived raw pages under `raw/fhrs-live/<today>/`
rather than re-querying the API, provided that directory still exists.

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
                        match_companies_house.py, backfill_postcodes.py, rebuild_db.py,
                        run_daily.ps1 (scheduled task entry point)
raw/                  raw archive, gitignored — this is the asset, back it up separately
  fhrs/<date>/        one dated directory per collection run
    _authorities-index.xml.gz   that day's local-authority list, as returned by the API
    _manifest.json               per-run summary: counts, and any authorities that failed
    <code>_<name>.xml.gz         one gzip-compressed raw bulk XML file per authority
  companies-house/<date>/   one dated directory per Companies House collection run
    _query.json                  the exact sic_codes/date-range parameters requested
    _manifest.json               per-run summary: hits, pages, failures
    page_NNNN.json.gz            one gzip-compressed raw Advanced Search response page
  fhrs-live/<date>/   one dated directory per postcode-backfill run
    <code>_page_NNNN.json.gz     one gzip-compressed raw live-API response page per authority
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
- **Companies House parser was built from documentation, then verified
  live once a key was available** (2026-08-24) -- briefly the one
  exception to this project's "inspect real responses before writing
  parsers" rule, since registering for an API key is account creation
  and not something to do on the user's behalf. First real run: 1,993
  companies, 0 parse errors, schema matched exactly. See "Status" above
  for the measured field-presence rates.
- **Secrets live in a local `.env` file, not a system environment
  variable.** `fsa_pipeline/config.py`'s `load_dotenv()` reads `.env` at
  the project root (gitignored) and only sets a variable if it isn't
  already in the real environment, so an explicit `$env:VAR=...` always
  wins. Chosen over `[Environment]::SetEnvironmentVariable(...,"User")`
  to keep the secret fully contained to the project directory --
  deleting the repo removes it, no leftover machine-wide state to clean
  up later.
- **Matching on postcode district + name similarity, never address
  equality** -- the brief's explicit warning, confirmed necessary by real
  data: one address (a known company-formation service) hosts 31 of our
  1,993 companies, three addresses host 10+. Company names are normalized
  (uppercased, legal suffixes like LTD/LIMITED/LLP stripped, punctuation
  removed) before scoring with `difflib.SequenceMatcher` -- simple,
  stdlib, no new dependency, and good enough to find 3 exact matches and
  10 more at 0.5+ similarity out of 887 real INSERT events on first run.
  Known rough edge: `SequenceMatcher` can give a misleadingly moderate
  score to names that share a common word ("Group", "Catering") but
  aren't the same business at all -- this is exactly why the matcher
  stores ranked evidence with scores rather than a bare yes/no, leaving
  the actual confidence judgement to stage 5.
- **Address density is evidence, not a filter.** A candidate at a
  high-density address isn't excluded or down-weighted in the matcher
  itself -- it's flagged (`is_high_density_address`) so stage 5's
  classification logic can discount an *address-based* signal there,
  without discarding a genuinely strong *name* match that happens to
  share a formation agent with other companies (plausible for a small
  independent business that also outsources its accounts).
- **The live FHRS API was investigated as a bulk-collection replacement
  and rejected, but repurposed for postcode backfill.** The user asked
  whether it could fix authority staleness (26 authorities over a week
  stale as of 2026-08-24). Tested against 4 authorities spanning small to
  large, most to moderately stale, Scottish and English: every one showed
  **identical FHRSID sets and identical RatingValues** between live and
  bulk -- no evidence the live API surfaces a new registration, closure,
  or rating change that bulk misses. (One early false alarm: 260/180
  apparent "rating differences" in the English tests turned out to be a
  sentinel placeholder date, `1901-01-01`, that the live API substitutes
  for a null `RatingDate` -- not a real difference, caught by checking
  `RatingValue` itself separately from `RatingDate` before concluding
  anything.) Replacing bulk collection wholesale would add a full new
  JSON parser, not reduce request volume (still one call per authority),
  and use a live-search backend (`"dataSource":"ElasticSearch"`) for a
  bulk-harvesting pattern it doesn't seem designed for -- not recommended.
  What the investigation did surface: **17.10% of establishments
  nationally have no postcode in bulk data at all**, unrelated to
  staleness (West Lindsey is 100% missing despite not being stale), and
  the live API only partially recovers it (0% for West Lindsey
  specifically -- confirmed missing at the source, not an FSA export
  artifact -- but 89.4% nationally once actually run against every
  candidate). That gap, not the original staleness question, is what
  `backfill_postcodes.py` addresses.
- **Backfilled postcodes live in a separate column, never overwriting
  bulk's `post_code`.** This one decision resolves three requirements at
  once: (1) a backfill can never be misread as FHRS data changing, since
  it never touches `post_code`, the fingerprint, or `observations` at
  all -- there is no code path by which it could create a spurious
  `field_changed` event; (2) a backfilled value can never be silently
  wiped out by a later bulk parse that still reports no postcode (the
  likely case, since bulk was the reason it was missing in the first
  place) -- bulk and backfill simply never touch the same column; (3)
  when bulk *does* eventually get a real postcode from the council, the
  existing unmodified `ingest_establishments` logic correctly detects it
  as a genuine `field_changed` event, and `postcode_source` flips to
  `'bulk'` automatically, because that's the column bulk actually writes.
  No merge logic, no special-casing of the core ingest path was needed to
  get any of this right -- keeping the two sources apart was enough.
  Downstream code should read the effective postcode as
  `COALESCE(post_code, postcode_from_live_api)` -- done for the matcher;
  still to do for stage 5's address-history/ownership-change lookup and
  the eventual territory-filtered CSV export, both of which are equally
  postcode-dependent.
- **New columns on an already-populated production table needed a real
  migration, not just a schema change.** `CREATE TABLE IF NOT EXISTS` is
  a no-op against a table that already exists, so adding
  `postcode_source` etc. to the `SCHEMA` string alone would have done
  nothing for the live database. `db._ensure_columns()` (called from
  `connect()`) checks `PRAGMA table_info` and runs `ALTER TABLE ADD
  COLUMN` for whatever's missing -- idempotent, safe to call every time.
  Separately, ~508K existing rows that already had a bulk postcode
  predated this column's existence and would never naturally get
  `postcode_source='bulk'` retroactively (that value is only set on a
  `first_seen`/`field_changed` event, and none of those rows will see
  another one until their underlying data actually changes) -- fixed
  with a one-time `UPDATE ... WHERE post_code IS NOT NULL AND
  postcode_source IS NULL` directly against the production database on
  2026-08-26. A future `rebuild_db.py` replay doesn't need this special
  case: every row goes through `first_seen` on a fresh rebuild, so
  `postcode_source` is set correctly from scratch with no gap.

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
