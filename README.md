# FSA Data Pipeline

A local, unattended pipeline that detects newly registered UK food and drink
businesses from FHRS data and (eventually) classifies them as new openings
or operator changes, using Companies House as a corroborating signal.

Full project brief: [CLAUDE.md](CLAUDE.md). Build proceeds in stages; see
"Status" below for where things currently stand.

## Status

**Stages 1-5 of 7 done and live-verified.** Plus a supplementary
postcode-backfill job, outside the staged build order.

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
signal there. Doesn't decide NEW_VENUE/OPERATOR_CHANGE/UNKNOWN itself —
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

Stage 5 — classifies each FHRS INSERT event as `NEW_VENUE` /
`OPERATOR_CHANGE` / `UNKNOWN` with a confidence grade and a
human-readable reason, honouring the "Stage 5 design commitment" below
(address-history lookup queries `establishments_current`, never
`observations`). `OPERATOR_CHANGE` takes priority when found: a
different FHRSID previously occupied the same normalized address
(`address_line_1` + effective postcode) with **no temporal overlap** —
the departed establishment's `last_seen_date` strictly before the new
one's `first_seen_date`. That overlap requirement mattered in practice:
an unfiltered address match found 316 of 1,709 real INSERT events with
*some* other FHRSID at the same address, but many were large multi-outlet
venues (a college campus, a community centre) where the "predecessor"
was still active alongside the new record — requiring no overlap cut
this to the genuine signal, **136 real operator-change events**, e.g.
"The Castle Inn" replaced by a new FHRSID also named "The Castle Inn" at
the identical address, gone 11 days before the new one appeared.
Otherwise, a strong Companies House match (from stage 4, thresholds
grounded in real score distribution: HIGH ≥0.85, MEDIUM ≥0.6, below
that → `UNKNOWN`) gives `NEW_VENUE`; a match at a high-density
(formation-agent) address gets its confidence downgraded unless the
name match is near-exact. Run against all 1,709 real INSERT events:
**136 OPERATOR_CHANGE, 8 NEW_VENUE (3 HIGH, 5 MEDIUM), 1,565 UNKNOWN**.
23 new tests, all passing.

**Ground-truth-driven fix batch (2026-08-28/29)** — the user hand-checked
5 real matcher/classifier outputs against manual research (Google,
Companies House's own search) before committing to the 75-row ground
truth set, and found 3 distinct real bugs, not just noise:

1. **District-only candidate search missed formation-agent registrations.**
   "Soul Mama Islington" (trades N1) and "Mamma Rosa London" (trades N19)
   were both registered via addresses in a completely different postcode
   district — invisible to district search regardless of tuning. Fixed
   with a second, independent national name-match channel (≥0.9
   similarity, blocked by first word for tractability against a
   quarter-million-company pool).
2. **Address-history matching required an exact `address_line_1`.**
   Missed a genuine "Rassau Fish Bar" replaced by a new FHRSID also named
   "Rassau Fish Bar" at the identical postcode, because the newer
   record's `address_line_1` was null. Fixed with a fallback to postcode
   + near-exact name (≥0.9) when either side is missing.
3. **No incorporation-recency check.** Necessary once Companies House
   collection widened to full history (below) — an 18-month-old company
   ("Mamma Rosa London") would otherwise wrongly count as `NEW_VENUE`
   just for having no FHRS predecessor. Fixed with a 180-day gate (the
   user's reasoning: "someone can have a great idea, get enthusiastic and
   create a company well before actually starting the business — could
   easily be a year," 180 days chosen as deliberately generous).

Also added: an "existing operator, additional venue" flag (a company
already linked to an earlier FHRSID gets noted in the reason rather than
silently read as a first-time venue — the "Soul Mama Stratford" case,
where a second site opens under the *same* company, not a new one), and
a short review-queue log for `NEW_VENUE`/`MEDIUM` classifications
(user's request: "as hands-off as possible... but early on, a list of
companies where the match certainty is worth me checking manually" — a
log file, not email, see "Design notes" for why).

Fix 1 required widening Companies House collection from the rolling
14-day window to a one-time full-history pull (`collect_companies_house.py
--full-history`) — real ground-truth misses were 5 months, 18 months,
and 6 years old, no rolling window would ever have been wide enough.
This surfaced a genuine, previously-undocumented API limitation: **the
Advanced Search endpoint enforces `start_index + size <= 10,000` per
query** (confirmed to the exact boundary — 9999+1 succeeds, 10000+1
returns HTTP 500), the classic Elasticsearch default `max_result_window`.
No page size or retry strategy gets past it; an unbounded query is
fundamentally capped at the first 10,000 matches. The first attempt at
this (an overnight run with plain offset pagination) ran into exactly
that wall after ~132,000 records and failed. Fixed with
`compute_date_slices` — recursively bisects the date range until every
slice's own hit count is safely under the ceiling, then paginates
normally within each slice. Also filtered to `company_status=active`
(confirmed with the user first): unfiltered was 641,539 companies
including everything dissolved back to 1900, active-only is 260,006 —
2.5x less to fetch/store/search, no loss of matching value (a dissolved
company can't be the match for something that just registered with FSA).

Re-verified all 5 ground-truth examples after the fix + full rebuild:
every one now resolves correctly — Soul Mama and Rassau went from
`UNKNOWN` to correctly-matched (`NEW_VENUE`/HIGH and
`OPERATOR_CHANGE`/HIGH respectively); Mamma Rosa and Breakfast Club
stayed `UNKNOWN` but now with real evidence explaining why (matched a
real company, but it's years old) instead of just "no good candidate
nearby"; Oliveira's stayed correctly unmatched despite the 260K-company
pool, no new false positive introduced. Nationally, across all 2,966
INSERT events accumulated so far: **200 NEW_VENUE** (151 HIGH, 40
MEDIUM, 9 LOW, up from 8 before this fix batch), **308 OPERATOR_CHANGE**
(up from 136), **2,458 UNKNOWN**. 44 new tests, 141 total passing.

**Name-similarity rework (2026-09-01)** — a follow-up spot-check of the
Rassau Fish Bar case (above) found the matcher's top-ranked company was
still wrong: `difflib.SequenceMatcher` scored "KHAN SONS FISH BAR LTD"
(shares only the generic phrase "FISH BAR") above "RASSAU TRADING LTD"
(shares the distinctive proper noun "RASSAU"), because raw character
overlap can't tell a word shared by thousands of companies from one
shared by one. Replaced with IDF word-weighting (each shared word scored
by how rare it is across the ~260K-company pool) — see "Design notes"
for the full write-up, the worked example, and a serious performance bug
(an O(vocabulary) scan hidden inside the per-comparison scoring function)
caught and fixed along the way, which had a live rematch run still going
after 3.5 hours before being killed and diagnosed. Fixed version: full
rematch + reclassify of all 3,642 accumulated INSERT events in under 70
seconds combined. Existing 0.6/0.85 thresholds checked against the new
score distribution and kept unchanged. 142 tests passing.

**Exact-address corroboration (2026-09-02)** — working through the
ground-truth CSV's positive matches, the user spotted FHRSID 1981162
("Favourite Grill", Bristol): the top-ranked candidate was a genuine
company also named "Favourite Grill Ltd", but registered in Canvey
Island, Essex -- a coincidental namesake, found via the national channel,
nowhere near the actual venue. Meanwhile "Best Grill Bristol Ltd", low
name similarity but the establishment's *exact* registered address, went
unused. The brief's "never match on address equality" warning is about
never using address as the *sole* signal (a formation agent hosts dozens
of unrelated companies at one address); it isn't a ban on using an exact
address match as one *more* signal alongside name similarity. Added
`address_matches_establishment` (`fsa_pipeline/matcher.py`), gated on the
address NOT being high-density so a formation-agent address never gets
this boost, and used it to re-rank candidates so an exact-address match
outranks a higher-scoring but unrelated name match. The user also caught
a second Companies House record at that same address ("Cheap Grill
Limited", incorporated 2 months before the FHRS record vs. "Best Grill
Bristol"'s year-plus) and reasoned the more-recently-incorporated one is
the more plausible trigger -- added as a tiebreak among multiple
address-matched candidates. `classify()` now treats an exact address
match as independent corroborating evidence, sufficient on its own to
qualify a candidate even when its name similarity is far below the usual
threshold (still subject to the same incorporation-recency gate as
everything else). Re-verified against real data: Favourite Grill now
cites "Cheap Grill Limited" with the address-match noted, correctly
demoting the Canvey Island coincidence; a second real case (Street Bites,
Edinburgh) turned up unprompted -- the previous occupant's own leftover
Companies House record ("Hyderabadi Paradise Ltd", name similarity 0.0)
is now correctly cited via address match instead of an unrelated Kent
company that happened to share the new establishment's trading name. 9
new tests, 152 total passing.

Also clarified with the user what this classification actually means
here (still called `OWNERSHIP_CHANGE` at the time -- see the 2026-09-09
rename below): it is a **venue-turnover** signal ("a new FHRS record
replaced a departed one at this address"), not a legal-ownership signal
-- the brief's "company-level data only, no named individuals" rule
means directors and shareholders are never fetched, so a genuine change
of legal control can't be distinguished from the same owner
re-registering under a new name at the same site. The Companies House
match in the reason string only ever corroborates the *new* occupant; it
says nothing about who ran the old one.

**SIC-scope investigation and the multi-venue discount (2026-09-08)** — the
user asked which of the original brief's restrictions, if relaxed, might
improve match coverage. Investigated the `56xxx`-only Companies House SIC
filter by sweeping all 59 `UNKNOWN` rows in the ground-truth sample
against Companies House **unfiltered by SIC**: 28 had no name-overlapping
company at all (expected -- many independent cafés are sole traders, per
the brief), but 13 turned up an active company excluded purely by SIC
code. Real confirmed examples: "Park Hive CIC" (exact name, exact
postcode district, wrong SIC entirely), "Sourdough Sophia Production
Limited" (SIC `10710`, bread manufacture -- small bakeries often sit
under manufacturing, not food-service, SIC), "Little Dessert Shop
(Wigan) Limited" (SIC `47190`, retail). But also Aldi, Lidl, and Burger
King franchise operators -- all real, all excluded by SIC, but matching
to one giant national/parent company that would hit on every single
store nationwide, not venue-specific evidence.

That surfaced a real gap independent of whether SIC scope ever widens:
nothing discounted a company already confidently matched to *many* FHRS
venues nationally. Added `count_operator_venues` / `is_multi_venue_company`
(`fsa_pipeline/classifier.py`, `multi_venue_company_threshold` in
`config.toml`, default 5) -- mirrors the existing
`is_high_density_address` formation-agent discount, just on the name axis
instead of the address axis. Caught by the test suite before shipping: the
first version reused `is_high_density_address`'s "score >= 0.95 escape
hatch" (a near-exact name match can rescue an ambiguous shared-address
case), which would have let a *perfect* name match bypass the multi-venue
discount entirely -- exactly the failure mode being guarded against,
since every single Aldi store scores 1.0 against "Aldi Stores Limited".
Fixed so multi-venue discounting is unconditional on score. Currently
dormant (0 of 5,880 classified events trigger it) since no SIC widening
has happened yet -- it's a safety net ready for if/when the SIC filter
is ever widened, not something with observable effect today.

Checked real hit-counts before proposing anything (not guessing): a
narrow, food-adjacent SIC addition (`10710` bread manufacture, `47290`
specialised food retail, `46370` food/drink wholesale) would add ~23,000
active companies to the ~260,000-company pool -- a bounded, plausible
widening. The broad retail codes that would actually catch Aldi/Lidl/
Burger King (`47110`, `47190`, `70100`) were deliberately **not**
recommended -- those are enormous, mostly food-irrelevant SIC codes
(all general retail, all corporate head offices) that would balloon the
pool for little genuine per-venue signal, exactly the "chain match, not
venue-specific" problem the discount above exists to catch.

**Implemented the same day**, once the user confirmed the narrow list.
Full-history recollection under the widened SIC set: 280,620 total hits
(up from ~260,000), 19,667 newly first-seen companies. Rematch +
reclassify of the full 5,880-event backlog: +26 events flipped from
`UNKNOWN` to `NEW_VENUE`, `OPERATOR_CHANGE` unchanged (586 -- expected,
it's driven by FHRS address history, company match is only corroboration
there). 148 classifications now cite a company found specifically via
one of the 3 new codes, including genuine `NEW_VENUE`/HIGH matches
("Browny Africa Shop Ltd", "Dam Coffee Ltd", "Tin and Brine Ltd") and one
`OPERATOR_CHANGE` ("Grain Culture Ltd", a bakery, SIC 10710).

Checked the three headline examples from the investigation individually
rather than assuming the widening fixed them -- it fixed one of three,
for an honest and instructive set of reasons:
- **"Park Hive CIC" -- still unmatched, correctly.** Its real SIC (96040)
  was never part of the recommended narrow list; this was always an
  illustrative example of the general phenomenon, not a code the
  widening was meant to catch.
- **"Little Dessert Shop" -- still unmatched, correctly.** The real
  Wigan entity's SIC is `47190` (general non-specialised retail) -- a
  *different* code from the `47290` (specialised food retail) that was
  actually added, and one of the broad retail codes deliberately
  excluded for the same "chain match" reason as Aldi/Lidl. Conflating
  47190 and 47290 informally while summarizing the investigation was a
  real error worth flagging here, not smoothing over.
- **"Sourdough Sophia" -- now collectible, but still not matching, and
  this exposed a real, separate limitation.** All 8 real "Sourdough
  Sophia" companies are now in the pool (SIC 47290 correctly caught
  them) -- but each branch is registered as its own company with the
  branch location baked into the name ("SOURDOUGH SOPHIA CROUCH END
  LTD"), which dilutes national-channel name similarity below the
  strict 0.9 threshold even for the exactly-correct branch. This is a
  pre-existing national-channel limitation, unrelated to SIC scope --
  worth a future look, not addressed here.

**Known gap, documented not built (2026-09-08): national channel misses
multi-branch chains whose company names bake in a location suffix.**
Investigated after "Sourdough Sophia" (FHRSID 1981187, Crouch End)
stayed `UNKNOWN` despite all 8 real "Sourdough Sophia" branch companies
being in the pool. Root cause: `name_similarity` is symmetric
(`shared/union`), so a branch name like "Sourdough Sophia Crouch End
Ltd" scores only 0.52-0.69 against "Sourdough Sophia" -- every extra
word (the location suffix) dilutes the score, well below the national
channel's 0.9 threshold, even though a human reads it as an obvious
match.

**Important: fixing this wouldn't have changed this specific venue's
outcome.** "Sourdough Sophia Crouch End Ltd" was incorporated 405 days
before the FHRS record appeared -- more than double the 180-day
recency gate -- so it would still be rejected as NEW_VENUE evidence
even with perfect name-matching. The case that prompted this
investigation is a false lead for that one venue, though the underlying
matching gap is real for other, more freshly-incorporated cases.

Tested the obvious fix (treat a word-for-word name prefix as a match)
against the real backlog before proposing it, and it's unsafe on its
own: 425 UNKNOWN establishments have a prefix-matching, recently-
incorporated company, but most are coincidences, not chains -- "Bean
There" prefix-matches 9 unrelated companies at 9 different addresses
nationwide (independent businesses landing on the same pun), which a
naive fix would wrongly treat as strong evidence.

What actually separates the real cases from the coincidences, confirmed
against real data: genuine chains share a registered address across
their sibling companies (Sourdough Sophia's 8 companies cluster at 2
addresses; 3 of the 9 "Bean There" companies plausibly are a real small
chain, sharing one address, while the other 6 are each at a unique,
unrelated address). A safe version of this feature would require BOTH a
name-prefix match AND a sibling company (same prefix) at the same
registered address -- not implemented, left here as a validated,
scoped starting point if the multi-branch-chain pattern comes up again.

**Officer-churn signal for OPERATOR_CHANGE (2026-09-08, opt-in, off by
default)** — the user asked to look into improving `OPERATOR_CHANGE`
without the brief's GDPR-driven "no named individuals" restriction. As
built, `OPERATOR_CHANGE` only detects venue turnover (a different FHRS
record previously existed at this address), not genuine change of legal
control — the two look identical without director data. Real
diagnostic check against Companies House's `/officers` endpoint (schema
only, name redacted) confirmed it returns name, date of birth
(month/year), nationality, service address, and (new since the 2024
Economic Crime and Corporate Transparency Act reforms) identity
verification detail including a `preferred_name` — unambiguously
personal data under UK GDPR, public register or not; being public
doesn't exempt processing it from GDPR, and individuals can object to
third-party commercial re-use of it.

Given that, this was scoped narrowly and shipped **disabled by
default** (`config.toml`'s `[officer_churn].enabled = false`):
`fsa_pipeline/officer_churn.py`'s `compute_officer_churn_signal` is the
sole function permitted to read a raw officer list, and it reads only
`appointed_on`/`resigned_on` — never name, DOB, nationality, or address,
even though they're present in the input. Only three numbers
(`officer_count`, `appointed_near_event`, `resigned_near_event`) are
ever persisted, in a new `officer_churn_checks` table; the raw officer
list is discarded the instant the aggregate is computed, never logged.
`scripts/check_officer_churn.py` only ever fetches officers for a
company already cited as `OPERATOR_CHANGE` evidence — never the whole
~283K-company pool — keeping exposure to a few hundred companies, not
the whole collection.

This still means real personal data is processed at fetch time even
though nothing about an individual is retained afterward — smaller and
easier to justify than storing names, but not a compliance non-issue.
**Left disabled; get it reviewed before enabling in a commercial
deployment.** Verified end-to-end against one real company (7 officers,
correctly returned "no churn detected" — a stable board): only the
three-number aggregate ever reached the database. 15 new tests
(including one asserting the signal object structurally cannot carry a
personal field), 171 total passing.

**Operator-prefix matching for contract catering (2026-09-09).** User
spotted FHRSID 1981157 ("Impact Food Group @ John Cabot Academy") while
testing the audit tool: the real matching company ("Impact Food Group
Limited") exists on Companies House, but the "@" separates the actual
operator name from the site name FHRS appends -- the site name dilutes
a full-string national match below threshold, the same class of problem
as a branch's location suffix (see the Sourdough Sophia gap above), but
via an unambiguous, deliberate FHRS convention (`<operator> @ <site>`)
rather than a fuzzy shared-word pattern -- much safer to fix directly.
This specific example turned out to have a second, compounding gap
(Impact Food Group Limited's SIC code, `64209` "activities of other
holding companies", is outside every code this project collects, same
pattern as Little Dessert Shop from the SIC-scope investigation) -- but
the general "@" pattern is real and separately worth fixing regardless.

Quantified before building anything: 119 establishments nationally use
this "@" convention (105 `UNKNOWN`, 14 already correctly
`OPERATOR_CHANGE` via address-history regardless of company matching).
Of the 105 `UNKNOWN`, 20 have an exact (>=0.9) national match on the
operator name alone once the site suffix is stripped, using companies
already in the pool -- Aramark Limited (6 sites), Pabulum Limited,
Holroyd Howe Limited, Vacherin Limited, Thomas Franks Limited, Nourish
Contract Catering Limited, Company of Cooks Ltd, Lunchtime Company Ltd.

`extract_operator_prefix` (`fsa_pipeline/matcher.py`) splits on the
first "@"; `find_national_candidates` gained an optional `strategy`
label so these candidates are marked `operator-prefix` in the evidence
table, distinct from an ordinary full-name national search, for audit
purposes -- the matching logic itself (IDF-weighted score,
first-word blocking, 0.9 threshold) is identical, reused as-is rather
than inventing new scoring. Rematched + reclassified the full
6,367-event backlog: 24 events now cite a real operator-prefix match
(Aramark, Pabulum, Lunchtime Company, ...). Only 1 flipped
classification (added corroboration to an already-correct
`OPERATOR_CHANGE`) -- the other 23 correctly stay `UNKNOWN`, since these
are long-established national caterers rejected by the existing
180-day incorporation-recency gate, not new venues. The real win here
isn't reclassification, it's audit quality: the reason string now says
"matched Aramark Limited, incorporated 1970, not within 180 days" instead
of "no candidates found" or a meaningless low-score district guess. 9
new tests, 180 total passing.

**Incorporation-age gate loosened to an evidence field (2026-09-10).**
User's proposal, testing the audit tool: an FHRS INSERT event with no
predecessor at that address is itself real signal a supplier may want
(a new physical location), even when the corroborating company is old
-- rejecting it outright (the previous 180-day gate) throws that away.
Real motivating case: "Aramark @ Drayton Manor High School" -- Aramark
Limited, incorporated 1970, is clearly not a new *company*, but a new
Aramark-catered school site is still a genuinely new *venue*.

Checked the real numbers before changing anything (604 events were
being suppressed by the gate): median company age 2.6 years, 18% over
10 years old, and -- the important number -- **554 of 604 (92%) were a
company matched to exactly one venue ever, no evidence it operates
anywhere else.** That's the same shape as "Mamma Rosa London," the real
case that motivated this gate in the first place (a venue that had
traded for 18 months before its first FHRS record, confirmed "long
running not new" by the user's own research) -- an old, single-match
company and a genuinely new branch of an established operator are
observationally identical in FHRS + Companies House data alone.

So the gate is now advisory, not absolute, and it's the existing
`find_existing_operator` evidence (already built for the "Soul Mama
Stratford" case) that decides which side of the ambiguity a case falls
on: a corroborating company that ISN'T recently incorporated still
qualifies as `NEW_VENUE` when it's *also* confirmed to already operate
another known FHRS venue (real multi-site evidence) -- otherwise it
stays `UNKNOWN`, now with a clearer reason ("no evidence this company
already operates another FHRS-registered venue") instead of a flat
rejection. A new `recently_incorporated` field (True/False/None --
None means the date was missing, a genuinely different claim from
"confirmed old") is stored on every classification with a corroborating
candidate, `OPERATOR_CHANGE` included, regardless of whether it changes
the outcome -- pure evidence, always kept.

Reclassified the full 6,367-event backlog: only **24 of the 604**
previously-suppressed events actually flip to `NEW_VENUE` -- the
existing `is_multi_venue_company` discount (built for the SIC-scope
work) composed automatically with no extra code: Greggs plc (9 sites)
and Aramark Limited (7 sites) correctly surface as `NEW_VENUE` but at
LOW confidence (chain match, not venue-specific), while genuine smaller
operators (Popeyes, Holroyd Howe, Bean There, Caprinos Pizza, ...) get
full HIGH confidence with an explicit "additional site" note. The other
580 stay `UNKNOWN` as before, just with `recently_incorporated=False`
recorded and a clearer reason. Also fixed a real, unrelated bug found
while touching the audit tool's export: `match_strategy="operator-prefix"`
(added 2026-09-09) was silently collapsing into `"district"` in the
Match Ledger's compact encoding, predating that feature -- fixed, and
the tool now also shows `recently_incorporated` per row.

4 new tests, 184 total passing. Match Ledger republished with the fix
and the new field.

Not yet built: metrics/monitoring, CSV export.

Live-API collection for priority authorities (the original South West
list) was in the brief but **dropped 2026-08-28** — see CLAUDE.md
"Data sources" and the design note below for why.

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

**One-time full-history backfill** (already run 2026-08-29; only needed
again if the database is fully rebuilt and `raw/companies-house/` is
somehow unavailable, or if you want to re-pull from scratch):

```bash
python scripts/collect_companies_house.py --full-history
```

Pulls every active company matching the SIC codes regardless of
incorporation date (~260K), date-sliced to work around the Advanced
Search API's undocumented 10,000-result pagination ceiling — see "Status"
above and "Design notes" for the full story. Writes to
`fullhistory_<slice-from>_<slice-to>_page_NNNN.json.gz` in the same dated
directory as the regular run, so the two never collide. Takes roughly
10 minutes; `rebuild_db.py` will replay it automatically afterwards
(picked up by `parse_companies_house.py`'s glob, same as regular pages).

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

Part of the daily scheduled run (`run_daily.ps1`) as of 2026-08-26. To
run manually:

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

## Running the classifier

After matching, classify each FHRS INSERT event:

```bash
python scripts/classify_insertions.py
```

Reads only from the database, never touches raw files or the network.
Idempotent per (FHRSID, INSERT date); pass `--force` to reclassify
everything (evidence changes as new Companies House data or new FHRS
establishments arrive). Part of the daily scheduled run, last in the
chain — it needs both the matcher's evidence and the current
`establishments_current` baseline.

## Rebuilding the database

The database is derived, rebuildable state; the raw archive is the actual
asset. To reprocess all history from scratch (after a parser/fingerprint
change, or to recover from a bug in the parsing/diffing/matching logic):

```bash
python scripts/rebuild_db.py
```

Wipes `fsa_pipeline.db` and replays every dated directory under
`raw/fhrs/` through `parse_fhrs_bulk.py` then `diff_fhrs.py`, postcode
backfill, every dated directory under `raw/companies-house/` through
`parse_companies_house.py`, matching, then classification, in that
order. Never touches the network — postcode backfill reuses the
already-archived pages under `raw/fhrs-live/<date>/` rather than
re-querying the live API, provided that directory still exists.

**Fixed 2026-08-28**: an earlier version of this script didn't drop
`postcode_backfill_runs`/`postcode_backfill_events`, so a rebuild would
leave stale "already checked" tracking pointing at postcode data that
had just been wiped — the backfill job would then wrongly skip
authorities on the next run, thinking they were already done. All
derived tables are now dropped and replayed together.

## Audit tool ("Match Ledger")

A published, private Artifact for browsing every classified INSERT event
with its full evidence (matched candidates, scores, reason text) and
cross-referencing against the ground-truth CSV, with live editable
verification notes (real match found, real company number, free-text
notes) that persist independently of the CSV. Built 2026-09-08 after
several rounds of the user manually spot-checking rows via ad hoc SQL
queries in conversation -- this replaces that with a searchable,
filterable, self-serve tool.

`scripts/export_audit_data.py` reads `classifications` +
`establishments_current` + the top 3 `company_match_candidates` per
event, cross-references a ground-truth CSV by FHRSID, and writes a
compact JSON export (field names deliberately short -- see the script
docstring -- since this gets embedded directly in the tool's HTML,
multiplied by several thousand rows). `tools/audit_ledger_template.html`
is the tool's actual source (CSS + JS, no data, with a
`/*__DATA__*/[]/*__DATA__*/` placeholder) -- see the comment at the top
of that file for the exact splice-and-republish steps. Verification
notes are stored in the artifact's own live database (a `notes`
collection keyed by FHRSID), not written back to any file in this repo
-- the CSV stays a point-in-time seed, the artifact is the current
state.

To refresh after new data lands (rerun collection/parse/diff/match/
classify first):

```bash
python scripts/export_audit_data.py --ground-truth ground_truth_sample_v4.csv --output tools/audit_data.json
```

Then ask Claude to splice `audit_data.json` into
`tools/audit_ledger_template.html` and republish the artifact -- the
export script only produces the data file, it doesn't publish. The
published tool's URL is recorded in the assistant's memory, not in this
repo (it's
a personal, private artifact, not a build output).

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
                        match_companies_house.py, backfill_postcodes.py, classify_insertions.py,
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
    _manifest_fullhistory.json           one-time full-history backfill summary, if run
    fullhistory_<from>_<to>_page_NNNN.json.gz   one date-sliced backfill page, if run
  fhrs-live/<date>/   one dated directory per postcode-backfill run
    <code>_page_NNNN.json.gz     one gzip-compressed raw live-API response page per authority
logs/                 per-run logs, gitignored
config.toml           non-secret configuration (URLs, timeouts, contact email)
fsa_pipeline.db        SQLite database, gitignored (this is derived state -- rebuildable
                        from raw/ by reparsing, unlike raw/ itself)
```

## Stage 5 design commitment — fulfilled

**The address-history lookup for OPERATOR_CHANGE queries
`establishments_current`, never `observations`.** Raised by the user
2026-08-27, before stage 5 existed, specifically so it couldn't get built
the other way by accident; built 2026-08-27/28 honouring it.
`establishments_current` is the full national baseline (seeded
2026-08-20, ~611K establishments, maintained since) and never deletes a
row — a closed business's address stays fully queryable forever, just
with a `last_seen_date` that stopped advancing. `observations` is a
changelog of *changes only*: 99.2% of all 613,305 establishments
(608,292) have never had a single `field_changed` event — their only row
is the original `first_seen`. `fsa_pipeline/classifier.py`'s
`build_address_index`/`find_predecessor` are built entirely off
`establishments_current` data with no code path touching `observations`
at all, so this can't happen by construction, not just by care --
verified with a test that builds a predecessor via the real `db` module
with zero `field_changed` events on purpose
(`test_predecessor_lookup_works_against_establishment_with_zero_field_changed_events`)
and confirms the lookup still finds it.

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
  removed) before scoring. Originally scored with `difflib.SequenceMatcher`
  -- simple, stdlib, no new dependency, and good enough to find 3 exact
  matches and 10 more at 0.5+ similarity out of 887 real INSERT events on
  first run. The docstring here used to flag a "known rough edge" --
  `SequenceMatcher` giving a misleadingly moderate score to names sharing
  a common word -- as a tolerable trade-off left to stage 5's judgement.
  It wasn't tolerable: it was a real bug (see the next bullet).
- **Name-similarity scoring replaced with IDF word weighting
  (2026-09-01).** The rough edge above turned out to actively invert
  rankings. Real case: for "Rassau Fish Bar", `SequenceMatcher` scored
  "KHAN SONS FISH BAR LTD" (shares only the generic phrase "FISH BAR") at
  0.73, *above* "RASSAU TRADING LTD" (shares the distinctive proper noun
  "RASSAU") at 0.55 -- the wrong company won purely because character-level
  matching has no notion that "FISH BAR" is shared by thousands of
  companies while "RASSAU" appears in exactly 1 of 260,162. Fixed with
  `build_word_idf`/`WordIdf` (`fsa_pipeline/matcher.py`): each shared word
  is weighted by inverse document frequency across the company pool, so a
  shared rare word dominates a shared generic one instead of losing on raw
  character count. After the fix, "RASSAU TRADING LTD" correctly ranks
  #1 (0.47) over "KHAN SONS FISH BAR LTD" (0.25). Score distribution
  shifted meaningfully lower and more bimodal (median dropped to a 0.12
  noise floor; real matches cluster near 1.0, 467/2,593 exact word-set
  matches) -- checked against the existing 0.6/0.85 thresholds before
  reusing them rather than guessing, and they still land in sensible
  places, so were kept unchanged. Reclassifying all 3,642 accumulated
  INSERT events: NEW_VENUE 236 -> 184 (more conservative, as expected once
  generic shared words stop inflating borderline matches), OPERATOR_CHANGE
  345 -> 344, UNKNOWN 3061 -> 3114.
  **Incident (2026-09-01):** the first implementation recomputed
  `max(idf.values())` -- a scan over the full ~105,000-word vocabulary --
  on *every single comparison* instead of once. For a business name
  starting with "The" (a 15,918-company national-search bucket), that's
  billions of redundant scans for one FHRS event; a rematch that should
  take under a minute was still running after 3.5 hours before being
  caught and killed. Fixed by precomputing that default once per run
  (the `WordIdf` dataclass carries it alongside the per-word weights) --
  1173us -> 8.5us per comparison (~140x), full rematch + reclassify of
  all 3,642 events back under 70 seconds combined. Lesson: an aggregate
  computed inside a per-pair scoring function is a trap that character-
  level scoring never had, precisely because `SequenceMatcher` never
  needed a corpus-wide statistic in the first place -- worth double-
  checking complexity by hand whenever a new "score every candidate"
  path touches something that isn't purely local to the pair being
  compared.
- **Address density is evidence, not a filter.** A candidate at a
  high-density address isn't excluded or down-weighted in the matcher
  itself -- it's flagged (`is_high_density_address`) so stage 5's
  classification logic can discount an *address-based* signal there,
  without discarding a genuinely strong *name* match that happens to
  share a formation agent with other companies (plausible for a small
  independent business that also outsources its accounts).
- **An exact registered-address match is corroborating evidence, not a
  primary signal (2026-09-02).** The brief's "never match on address
  equality" warning is about never using address as the *sole* criterion,
  not a ban on using it at all -- see `address_matches_establishment` in
  `fsa_pipeline/matcher.py` (module docstring has the full "Best Grill
  Bristol" / "Favourite Grill" worked example). Gated on the address NOT
  being high-density, so the original warning still holds at a genuine
  formation-agent address; among multiple exact-address matches, the one
  incorporated closest to the establishment's first-seen date wins,
  since that's the more plausible trigger for the FHRS record than a
  long-standing occupant already captured by the separate FHRS
  address-history predecessor check.
- **`OPERATOR_CHANGE` is a venue-turnover signal, not a legal-ownership
  signal -- renamed from `OWNERSHIP_CHANGE` on 2026-09-09 for exactly
  this reason.** It fires on "a different FHRS record previously existed
  at this address", which says nothing about who controls the business --
  a genuine sale to a new owner and the same owner re-registering under a
  new trading name at the same site (real case: "Favourite Grill",
  replaced by a new company, "Cheap Grill Limited", with no actual change
  of trading name) look identical at this level. This is deliberate, not
  a gap to fix: the brief's "company-level data only, no named
  individuals" rule means directors/shareholders are never fetched from
  Companies House, so legal ownership change is not something this
  system can detect without violating its own privacy constraint. The
  Companies House match cited in an `OPERATOR_CHANGE` reason only ever
  corroborates the *new* occupant, never the old one. The old name
  claimed more than was actually known; the user caught this while
  double-checking the officer-churn feature's logic before enabling it
  for real (see below) and asked for the rename.
- **Companies House Advanced Search has a hard, undocumented 10,000-result
  pagination ceiling** -- confirmed by direct testing 2026-08-29, not
  found in any documentation consulted: `start_index + size` cannot
  exceed 10,000 for a single query, to the exact boundary (`9999+1`
  succeeds, `10000+1` returns HTTP 500, consistently, not flaky). This is
  the standard default `max_result_window` for an Elasticsearch-backed
  search index, and it means a query with more matches than that is
  fundamentally unreachable via offset pagination alone -- no page size,
  retry count, or patience gets past it. `compute_date_slices`
  (`fsa_pipeline/companies_house.py`) works around this by recursively
  bisecting the requested date range until every slice's own hit count
  is safely under the ceiling (9,000, leaving margin), then paginating
  normally within each slice. Anyone else hitting deep pagination on this
  API should assume the same ceiling applies.
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
- **Live-API collection for priority authorities, dropped 2026-08-28.**
  The original brief wanted this to cut latency for a South West sample
  (Bristol, BANES, South Gloucestershire, North Somerset, Somerset,
  Gloucester, Cheltenham, Stroud) while demand was unvalidated. The
  investigation above already answers why it wouldn't have helped: same
  FHRSIDs, same ratings as bulk, so no latency win for new-registration
  detection specifically — the bottleneck is council reporting cadence,
  not which FSA endpoint is queried. Discussed with the user 2026-08-28
  once stages 6/7 were imminent: the two things this item could still be
  for are both already covered more generally — postcode completeness by
  `backfill_postcodes.py` (national, not 8 authorities), and "a sample to
  show potential buyers" by stage 7's planned postcode-area/authority
  filtering on the national feed that's already running. See CLAUDE.md
  "Data sources" for the brief's own updated text.
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
  still to do for stage 5's address-history/operator-change lookup and
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
- **`OPERATOR_CHANGE` confidence downgrades to `MEDIUM` when the
  establishment kept its predecessor's exact trading name and no
  Companies House match corroborates a new operator.** Raised by the
  user 2026-09-10 while reviewing FHRSID 1980257 ("The Cabin") in the
  Match Ledger: its predecessor (FHRSID 1496258) had exactly one
  observation ever, was replaced within a day, and both records are
  missing `address_line_1` -- reads more like a local-authority record
  correction (same business re-issued under a new FHRSID) than a real
  change of hands. Checked the real distribution before building
  anything (same discipline as the incorporation-age gate above): of the
  417 `OPERATOR_CHANGE` events with no corroborating company match, 199
  (48%) share the predecessor's exact trading name -- not a rare edge
  case. `_predecessor_name_match` (`fsa_pipeline/classifier.py`) records
  this on every `OPERATOR_CHANGE` classification regardless of outcome
  (`predecessor_name_match`, True/False/None -- None means either name
  was missing, not "different"); it only changes the outcome
  (`HIGH` → `MEDIUM`, with the ambiguity spelled out in the reason text)
  when uncorroborated, since a real company match already settles who
  the new occupant is regardless of the name coincidence. A name
  *change* isn't touched -- that's, if anything, stronger turnover
  evidence, not weaker. Reclassifying the full 6,367-event backlog moved
  exactly the predicted 199 of 633 `OPERATOR_CHANGE` events from `HIGH`
  to `MEDIUM`. The Match Ledger's review queue (previously
  `NEW_VENUE`/not-`HIGH` only) was widened to include these, and the
  classifier's own `_review_queue_<date>.log` likewise now includes
  `OPERATOR_CHANGE`/`MEDIUM`.
- **`predecessor_name_match` widened from byte-exact to near-exact,
  same day, after the user spot-checked FHRSID 1761387 ("Cornelly
  Pizza").** Its predecessor (FHRSID 1964671) traded as "CONELLY PIZZA"
  -- a one-letter FSA/local-authority typo correction, exactly the
  ambiguity this field exists to flag -- but exact string matching
  missed it, because it's not byte-identical. On investigation the user
  had also assumed the matcher should have found "CORNELLY PIZZA LTD"
  on Companies House; it's real (SIC 56103, same street) but dissolved
  2024-11-05, ~21 months before this FHRS event -- correctly excluded by
  the active-only collection filter ([companies_house] "Data sources"
  above), and wouldn't have been strong evidence of current occupancy
  even if collected, so that filter wasn't revisited. Checked the real
  distribution before widening: of the 218 uncorroborated
  `OPERATOR_CHANGE` events with an exact-string name mismatch, 14 (6%)
  are word-level differences (`&`/"and", "Ltd" suffix, stray punctuation,
  double spaces, a corrupted apostrophe) that `name_similarity`
  (word-overlap, already used for the address-history fallback) scores
  >=0.9 -- most actually collapse to an exact match once both sides are
  run through the same `normalize_company_name` used elsewhere for FHRS
  names, since normalization already strips exactly this kind of noise.
  11 more (5%) are a genuine typo *within* a word ("CORNELLY" vs
  "CONELLY" share zero tokens, so word-overlap scores it 0.15 --
  invisible to `name_similarity` no matter the threshold). Added
  `levenshtein_distance` (`fsa_pipeline/matcher.py`) for this second
  case -- character-level edit distance didn't exist anywhere in the
  codebase before, gated to normalized names of length >=4 and distance
  <=2 so it can't fire on coincidentally-close short names ("KFC" vs
  "TFC"). Reclassifying the full backlog moved the predicted 25 more
  events (14 + 11) from `HIGH` to `MEDIUM`: 224 of 633 total.

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
