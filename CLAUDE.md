# Project brief: UK new-venue signal pipeline

Build a local, unattended data pipeline that detects newly registered UK food
and drink businesses and classifies them as new openings or operator changes.

I am a developer. Explain your reasoning, but don't over-explain basic Python.
Ask me before making significant architectural choices. Work in the stages
listed at the bottom — don't build everything at once.

## The commercial context (read this, it drives the design)

I may sell a weekly feed of new venue openings to suppliers (coffee roasters,
drinks wholesalers, cleaning and waste contractors, insurance and finance
brokers). The value of this product is **latency and precision**: telling
someone about a venue a week late, or sending them a venue that has been
trading since 2009, both destroy the product.

I have not validated demand yet. So the priority order is:

1. **Never lose data.** The historical archive is the asset. It cannot be
   backfilled later. Correctness of storage beats every other concern.
2. **Get national daily collection running today**, even if parsing and
   classification are incomplete.
3. Everything else.

If you find yourself choosing between shipping a feature and protecting the
archive, protect the archive.

## Critical design constraints

**Verify, don't assume.** My knowledge of these APIs is second-hand and has
already been wrong twice. Read the actual FSA API guidance PDF and inspect
real responses before writing parsers. If a field I describe below doesn't
exist or behaves differently, tell me rather than working around it silently.

**Raw before parsed.** Every HTTP response is written to disk unmodified,
in a dated directory, before anything parses it. Never overwrite. Never
delete. Parsers will improve; I want to reprocess history when they do.

**Snapshots, not events.** FHRS is a state snapshot with no reliable
registration date. New businesses are detected by diffing consecutive
snapshots, not by reading a date field.

**Company-level data only.** Do not collect, store, or infer personal data
about individuals. Business names, addresses, postcodes, company numbers,
SIC codes. No named individuals, no contact details, no directors' personal
addresses. This keeps the project outside UK GDPR's difficult areas and it
is not negotiable.

**Open Government Licence.** FHRS and Companies House data are OGL. Record
the attribution requirement somewhere in the repo so I don't forget it when
I publish anything.

## Data sources

### 1. FHRS (Food Standards Agency) — primary

- Open data landing page: https://ratings.food.gov.uk/open-data
- API guidance PDF is linked from there — read it first.
- Two access routes: per-local-authority bulk XML downloads, and a live
  JSON/XML API at api1-ratings.food.gov.uk.
- Roughly 390 local authorities. I want **all of them**, not just the
  South West.

Collect via bulk XML for full national coverage and archival.

~~Live API for priority authorities (Bristol, BANES, South
Gloucestershire, North Somerset, Somerset, Gloucester, Cheltenham,
Stroud)~~ — **dropped 2026-08-28.** This was meant to cut latency for a
South West sample while validating demand, since the reasoning at the
time was that bulk extracts lag by up to several days. Investigation
(2026-08-25/26, see README "Design notes") found the live API returns
identical FHRSIDs and identical ratings to bulk on every authority
tested — it doesn't reduce time-to-detection for new registrations,
because the bottleneck is each council's own reporting cadence to FSA,
not API response latency. The one real benefit it did surface (better
postcode completeness) is covered nationally by a separate
`backfill_postcodes.py` job instead. Once stage 7 (CSV export) supports
postcode-area/authority filtering, a curated regional sample for
potential buyers can be sliced directly from the national pipeline
already running, with no separate collection pathway needed.

**Log the `ExtractDate` from each bulk file's header, per authority, every
day.** I need to derive the real refresh cadence per authority empirically —
nobody publishes it. This also becomes a freshness monitor.

Fields I expect (verify): FHRSID, LocalAuthorityBusinessID, BusinessName,
BusinessType, BusinessTypeID, address lines, PostCode, RatingValue,
RatingDate, LocalAuthorityCode, LocalAuthorityName, geocode.

Note: `RatingValue` can be a number or a string such as "AwaitingInspection",
"Exempt", "AwaitingPublication". Handle both.

### 2. Companies House — classifier

- Free REST API, requires registering for an API key. I'll supply it via
  environment variable; never commit it.
- I want incorporation date, company number, company name, registered
  office postcode, SIC codes, company status.
- Filter to hospitality SIC codes in the 56xxx range (licensed restaurants,
  unlicensed restaurants and cafés, takeaways, pubs and bars, event
  catering, and similar). **Look up the exact codes and confirm them with
  me** — don't take my range on trust.
- There is also a streaming API. Investigate whether it's a better fit than
  polling and tell me what you find; don't just pick one.

## What the pipeline must do

### Collection (build first)
Daily job. Fetch all FHRS bulk files, write raw to `raw/fhrs/YYYY-MM-DD/`.
Fetch recent Companies House incorporations for hospitality SIC codes,
write raw to `raw/companies-house/YYYY-MM-DD/`. Polite rate limiting,
honest User-Agent with my contact email, retries with backoff, resume
after partial failure. (Live API for priority authorities dropped
2026-08-28 — see "Data sources" above.)

### Parsing and storage
Parse raw into a local database. SQLite is fine to start; tell me if you
think Postgres is warranted and why. Schema should keep an immutable
observation log (what we saw, when) separate from derived current state.

### Diffing (the core)
Compare consecutive FHRS snapshots per authority. Emit:

- **INSERT** — an FHRSID not present in the previous snapshot. This is the
  new-registration signal.
- **UPDATE** — a field changed on an existing FHRSID. Backlog inspections
  land here (RatingValue changes from AwaitingInspection to a number), which
  is why they don't pollute the new-registration feed.
- **DELETE** — an FHRSID that disappeared. Usually closure or de-registration.

Guard against bulk re-uploads: if an authority produces an implausible spike
of INSERTs in one day (say, more than 3x its trailing median, or more than
some absolute threshold), flag it as a suspected data artefact and quarantine
it rather than emitting it as signal.

### Classification
For each INSERT, attempt to match against Companies House and assign a
classification with a confidence grade **and a human-readable reason string**:

- `NEW_VENUE` — matched to a company incorporated recently, no prior FHRS
  record at that address.
- ~~`OWNERSHIP_CHANGE`~~ **renamed `OPERATOR_CHANGE`, 2026-09-09.** As
  built, this detects venue turnover — a different FHRS record previously
  existed at the same address, or a newly incorporated company at an
  address with an existing food business — not a change of legal control.
  The two aren't the same: a same-owner rebrand under a new company name
  (real case: "Favourite Grill" replaced by "Cheap Grill Limited" at the
  identical premises, one operator's Ltd swapped for another with no
  actual sale) fires identically to a genuine change of ownership,
  because this signal never looks at Companies House officer/director
  data (see the "no named individuals" rule above) to tell them apart.
  `OWNERSHIP_CHANGE` overclaimed what was actually known; `OPERATOR_CHANGE`
  doesn't. Still potentially the most valuable signal — treat it as a
  first-class output, not an edge case. (A separate, opt-in,
  disabled-by-default officer-churn signal was later added as a narrow,
  privacy-scoped exception to the no-named-individuals rule — see README
  "Design notes" — but it only adds nuance to `OPERATOR_CHANGE` events
  already detected here, it doesn't change what triggers this category.)
  **Confidence downgraded to MEDIUM, 2026-09-10, when the predecessor
  match is uncorroborated by any Companies House match AND the new
  record kept essentially the same trading name** — 224 of 6,367 events
  (the "The Cabin" case: a predecessor with one observation ever,
  replaced within a day). "Essentially the same" means byte-identical,
  or near-identical after normalization — including a single-letter typo
  correction, widened same day after "Cornelly Pizza"/"CONELLY PIZZA"
  (one letter apart) was missed by exact matching. Same-name-with-no-
  company-evidence can't be told apart from an FSA/local-authority
  record correction (same business, re-issued FHRSID) from data alone —
  see README "Design notes" for the numbers. Still emitted, still
  `OPERATOR_CHANGE`, just not `HIGH` — same "flag, don't discard"
  treatment as `recently_incorporated`.
- `UNKNOWN` — no company match. Do **not** discard these: many independent
  cafés are sole traders and never incorporate. Lower confidence, still in
  the feed.

**Matching warning:** do not match on registered office address equality.
Small hospitality companies routinely register at their accountant's office.
Match on company name similarity plus postcode district, and detect and
exclude addresses hosting implausibly many food businesses (that's an
accountant, not a food court). Address normalisation will be fiddly; this is
expected and is the hard part. Store match evidence so I can audit decisions.

### Metrics I need reported
- New records per authority per week (this is my market-size arithmetic).
- Ratio of INSERTs by classification.
- ExtractDate age per authority, and derived cadence.
- Parse failure rate per authority.

### Monitoring
Alert me (email or a local log I check — keep it simple) when:
- Any authority's ExtractDate is older than 2x its own historical median.
- Any authority's daily record count deviates more than 50% from its
  trailing 28-day median.
- Parse failure rate moves materially.
- A collection job fails or doesn't complete.

Include a handful of canary FHRSIDs that must always parse to known values.

### Output
CSV export, filterable by postcode area, local authority, business type,
classification and date range. Columns: business name, address, postcode,
business type, first-seen date, classification, confidence, reason, link to
FHRS record, company number and incorporation date where matched.

That's it. **No web UI, no dashboard, no REST API, no billing, no email
sending.** If I get customers I'll build delivery then. Don't build it now.

## Stack

Python. Runs locally on my machine, cron or a scheduler. Boring, readable,
well-tested code over clever code. Configuration in a file, secrets in env
vars. A README that lets me pick this up again after two weeks away.

Tests matter most on the diff logic and the address matching — those are
where silent wrongness costs me a customer.

## Build order

Do these in sequence. Show me working output at each stage before moving on.

1. **Collection and raw archive only.** FHRS bulk, all authorities, daily,
   raw to disk. Get this running today. Nothing else matters until it is.
2. Parsing into the database, plus ExtractDate logging.
3. The diff engine, with the bulk re-upload guard.
4. Companies House collection and the matcher.
5. Classification, confidence and reasons.
6. Metrics and monitoring.
7. CSV export.

Start with stage 1. Ask me anything that's ambiguous before you write it.
