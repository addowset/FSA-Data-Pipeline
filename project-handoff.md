# Opendo — project handoff

Context carried over from the strategy thread. Covers decisions, constraints and
open issues that are **not** recoverable from the repo or the READMEs.

Last updated: 23 October 2026
Reconciled against README.md and CLAUDE.md on 23 October 2026. Four items were
stale or wrong and are corrected below; nothing was deleted.

---

## 1. Infrastructure (the bit that prompted this file)

- **Domain:** `opendo.co.uk`, registered at **Cloudflare**. DNS is managed in
  Cloudflare, so any deployment instructions should assume Cloudflare DNS and
  Cloudflare Pages as the obvious hosting route.
- **Email:** Google Workspace, `andrew@opendo.co.uk`.
- **A previous domain, `opdproperties.co.uk`, was deliberately rejected** for
  this — it reads as property spam to the target audience. Don't reintroduce it.

### DNS records already configured

| Type | Name | Value |
|---|---|---|
| MX | `@` | `smtp.google.com` priority 1 |
| TXT | `@` | `v=spf1 include:_spf.google.com ~all` |
| TXT | `google._domainkey` | (2048-bit DKIM key from Google admin) |
| TXT | `_dmarc` | `v=DMARC1; p=none; sp=reject; rua=mailto:dmarc@opendo.co.uk; fo=1; adkim=r; aspf=r` |
| TXT | `*._domainkey` | `v=DKIM1; p=` (null key, blocks spoofed selectors) |

`dmarc@opendo.co.uk` exists as a Google Workspace alias.

**Gotcha already hit and fixed:** `_dmarc.*` is not a wildcard — DNS only treats
`*` as a wildcard in the leading label. Subdomain policy is handled by the `sp=`
tag on the main `_dmarc` record instead. Don't re-add a `_dmarc.*` record.

**Still to do:** move `p=none` to `p=quarantine` after a couple of weeks of clean
DMARC reports. `sp=reject` stays as is.

**Deployment, 23 October 2026:** the one-page site at `website/index.html` goes
on Cloudflare Pages, free plan, direct upload. Because the domain is registered
at Cloudflare, Registrar requires the zone to be on Cloudflare DNS, so the apex
works with no nameserver change and no CNAME at a third party. The flip side is
that the mail records above live in the same panel you will be clicking around
in: export DNS records before touching anything.

---

## 2. Open data issues — do not let these get lost

### 2.1 The 60-day incorporation lag was circular (RESOLVED, 19 September 2026)

A lead-list analysis reported "median 60 days from incorporation to FSA
registration" for confirmed new venues. The number reproduced, but the
distribution had a cliff: 222 rows at 0-59 days, 117 at 60-119, 73 at 120-179,
then **1** at 180-239.

The cause was the one suspected. `classifier.py` uses
`new_venue_max_incorporation_age_days = 180` as an input to the NEW_VENUE
decision, so the lag was measured on a population selected for short lag. The
cliff was the threshold showing through, not a property of UK hospitality.

**Recomputed across all Companies House matches regardless of classification:**

| Population | n | Median |
|---|---|---|
| All matches, every classification | 1,375 | **303 days** |
| Matches with an exact registered-address hit, every classification | 470 | **204 days** |

Use one of those two in any customer-facing claim. Neither is as flattering as
60 days; roughly half of these venues still register within six months of
incorporation, which is early enough for the pitch to stand.

A first attempt at the fix gave 86 days and was *also* circular: the "exact
match for this establishment's address" reason string is only emitted after the
`qualifies_as_new_venue` gate, so that pool held no UNKNOWN rows at all. The
470-row union of both address-match wordings is the honest version.

Full working is on the Method & caveats tab of
`exports/leads_all_three_categories.xlsx`.

### 2.2 FHRSID numbering (LOGGED, DO NOT ACT)

Two competing theories about whether a low FHRSID on a newly-appearing record
indicates backlog publication rather than a new venue. Per-authority ID pools
was falsified (120 of 121 authorities with low IDs also have high IDs).
National-sequential survives but with ~12 counter-examples that may themselves
be mislabelled operator changes at old premises.

Affects ~12 rows in 6,665. Deliberately parked. Revisit only if a customer
complains an old venue appeared as a new opening.

**Contradicted by README.md, and the README is the better-evidenced of the
two.** Its design notes (search for "A second review's FHRSID-based
classification idea") record the opposite conclusion: the national-sequential
mechanism was tested directly and rejected. Every low-FHRSID row classified
NEW_VENUE or OPERATOR_CHANGE was pulled and checked, which surfaced seven
genuine `recently_incorporated` new venues with real 2026 incorporation dates
at FHRSIDs as low as 67,705. A "low FHRSID = backlog" rule would have
reclassified those as backlog - a precision regression, not an improvement. The
README concludes numbering is very likely per-authority, which is the theory
this file records as falsified. Both documents agree the action is to do
nothing, but anyone reopening this should start from the README's version.

### 2.3 Scheduled job silently missed a run (FIXED, 30 September 2026)

Found by noticing, not by alert: the machine slept through the 11:00 Task
Scheduler trigger, `StartWhenAvailable` did not catch up, and nothing
downstream ran or alerted. Every check in `monitor_pipeline.py` reads the
database, which only holds today's rows if the pipeline reached its last stage,
so none of the monitoring could fire on the one failure where the pipeline
never starts.

Fixed with `scripts/check_daily_run.py`, which reads log-file presence rather
than the database, registered as its own Task Scheduler task (`FSA Data
Pipeline Run Check`, 13:15 daily) so it is not downstream of the failure it
exists to catch. A daily status email followed on 1 October. See README.md,
"Scheduling".

**Not the same event as the 18-30 September data gap.** That was the FSA API
failing to update - a known fault at their end, reported by Andrew and since
fixed. It left twelve days with no insertions and then a 3,626-row catch-up
dump on 30 September, which any weekly figure has to exclude as a publication
spike rather than count as a week of trading.

---

## 3. Business decisions and why

- **Trigger is FSA registration + Companies House, not planning applications.**
  Class E (since Sept 2020) means shop→café needs no planning permission, so
  planning misses the most common case. Planning still catches pubs, hot-food
  takeaways, external works.
- **Fit-out trades are excluded as buyers** (kitchen equipment, shopfitters,
  extraction, EPOS). Those decisions are made before FSA registration. Don't
  reintroduce them without new evidence.
- **Three test categories:** coffee roasters with wholesale arms, hospitality
  insurance brokers, cleaning & waste contractors.
- **Operator changes may matter more than new openings.** A venue changing hands
  puts every supplier contract in play with an operator already trading. Nobody
  else sells this signal. Include operator-change rows in email samples.
- **Unmatched (sole-trader) venues stay in the feed.** They skew toward
  independents, which is what small suppliers can actually win. Filtering to
  Companies House matches only would discard the majority of real new openings.
- **Deliverable volume: about 230 venues a week nationally** across the widest
  venue set - cafes, restaurants, pubs, bars, hotels, takeaways and food shops -
  measured over the three clean weeks to 22 October 2026 (234 / 199 / 230). For
  the narrower set a coffee roaster can sell to, about **131 a week**. This
  still clears the territory-exhaustion threshold, which was the top-ranked
  failure mode.
- **The earlier "~750 hospitality venues/week, safe to state in outreach" is
  withdrawn.** That was a pre-filter count: it included rows with incomplete
  addresses, chains, schools and hospitals, FHRSID re-issues and every business
  type. It overstates the sellable number by roughly 3x. These recipients
  receive the actual file, so an inflated count in an email is the one error
  they are guaranteed to catch. Do not quote it.

---

## 4. Compliance constraints (non-negotiable)

- **PECR:** B2B cold email to corporate subscribers only. Sole traders and
  unincorporated partnerships need consent — exclude them. Every email carries
  real identification and an opt-out line. Maintain a suppression list.
- **No inferred email addresses.** Every address in the lead list was read from
  the company's own website, with the source URL recorded. Don't let any tool
  pattern-match `firstname@domain`.
- **Company/premises-level data only.** No personal data about individuals in
  the pipeline. This is what keeps the product out of UK GDPR's difficult areas.
- **OGL attribution** required for FHRS and Companies House data.
- **Ordnance Survey caveat - CLOSED 23 October 2026.** Some council-derived
  coordinates carry PSMA licensing restrictions, so shipping them to a paying
  customer redistributes OS-derived geometry under a licence we do not hold.
  This was the only compliance item in this file with no counterpart in the
  repo. `latitude` and `longitude` have been dropped from the CSV export
  (`fsa_pipeline/csv_export.py`, `EXPORT_FIELDNAMES`); 331 tests pass and a
  real export confirms a 29-column header. `google_maps_url` stays - it is
  built from the address and postcode, a Maps search query with no coordinates
  in it, so it carries no OS data. The columns remain in the database and in
  the export's SELECT: this is export scope, not collection, the same treatment
  as NOISE_BUSINESS_TYPES. If coordinates are ever wanted again, geocode from
  the address independently rather than passing these through.

---

## 5. Outreach design

- **Different email per category**, each built to extract that category's
  specific unknown: roasters → how do they find these today; brokers → is the
  venue already insured by registration; cleaning/waste → is this anyone's job
  and can they absorb the volume.
- **The ask is an action, not an opinion:** "reply with your postcode and radius
  and I'll send you the weekly list." Hypothetical-future questions produce
  polite lies.
  - *Qualified 22 October.* Variant C's pivot now opens "if there were more of
    them, would this be useful to you?", which is a hypothetical-future question
    of exactly the kind this rule warns against. Accepted anyway: the second
    half ("do you deliver that far?") is a factual question about their own
    business, the call to action is still an action, and for a genuinely thin
    patch an action-only ask promises volume that is not there. Revisit if C
    produces warm replies and no territory requests.
- Plain text, **no tracking pixels**, one link maximum. Replies are the metric.
- **Ramp:** 5/day, then 10, 15, 25. Interleave categories daily — one category
  per day confounds a bad deliverability day with a category effect.
- Reply to everyone within hours, **including the nos** — thread engagement is
  the strongest deliverability signal, and the refusals contain the findings.
- Record bounces separately; bite rate is replies over *delivered*.
- Cap the 12 Coversure franchise rows at four or five (correlated responses).

### Decision rules

Moved to the **Results** tab of `exports/leads_all_three_categories.xlsx` on
23 October 2026, so they sit beside the bite-rate formulas they are read
against. Unchanged in substance. The thresholds were set against about 190
sendable rows and the list is now 174, which leaves "fewer than 8 replies" a
shade under 5% either way.

Cleaning & waste has only 27 usable rows — underpowered. Read it for what people
*say*, not for a rate.

---

## 6. Things already checked and found wrong (don't redo)

- "Awaiting inspection" status ≠ new business. It's a stock, not a flow —
  includes inspection backlogs and re-registrations. New records are detected by
  **diffing snapshots**, not by status.
- Bulk XML extracts lag by up to several days (`ExtractDate` varies per
  authority). Use the live API for priority authorities where latency matters.
- The Companies House *incorporation* check on supplier leads was close to a
  no-op — 102 of 115 were already obvious from "Ltd" in the name, and the 13 that
  needed a lookup were the 13 that failed. The **trading-status** check is worth
  keeping (caught one dissolved company).
- BIBA's "find insurance" is a phone referral line, not a searchable directory.
  Assume trade association member-finders are lead routing, not public data.
- Highland's 131-record spike was a backlog dump, not a re-upload. Classifier
  handled it correctly.

---

## 7. Current status (23 October 2026)

Lead list: 235 rows, 190 with a published email address, **174 cleared to
send** - Variant A 98, B 63, C 13. Thirteen rows are "do not send" (reviewed
and rejected), three are "no coverage" (Sheffield and Southampton, where the
council barely publishes at all), 45 have no published address.

Email copy approved for all three variants, counted on local authority rather
than postcode area. Six test drafts went to Andrew's own addresses on 22
October; two revised Variant C drafts are in the account unsent. A `greeting`
column decides per row whether a first name is used - 11 rows where the name
also appears in the mailbox, "Hi," for the other 163.

A one-page website is drafted at `website/index.html` and not yet deployed.
Until it is, the Workspace footer links to a domain with nothing on it.

**Still not sent.** This remains the binding constraint on the whole project.
The pipeline has been ahead of the business since September.
