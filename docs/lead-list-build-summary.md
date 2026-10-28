# Lead list build — summary, findings and open questions

**For:** the chat that wrote the original brief
**From:** the Cowork session that executed it
**Date:** 17 September 2026, revised 19 September 2026
**Accompanies:** `leads_all_three_categories.xlsx`

You wrote a brief asking for 150 supplier leads across three categories, to test demand for a feed of newly-registered UK hospitality venues. Andrew raised the target to 100 per category. This is what came back, what I found along the way, and the places where your reasoning and mine diverge. I've included the things I got wrong, because some of them are informative about the method rather than just about me.

---

## 1. What exists now

| Category | Firms qualified | With a published email | Cleared to send | Named contact |
|---|---|---|---|---|
| 1. Coffee roasters (wholesale arm) | 115 | 96 (83%) | 95 | 15 (13%) |
| 2. Hospitality insurance brokers | 83 | 66 (80%) | 58 | 20 (24%) |
| 3. Cleaning & waste contractors | 37 | 27 (73%) | 25 | 2 (5%) |
| **Total** | **235** | **189** | **178** | **37** |

"Cleared to send" is the column that matters: it is rows with an email that survived Andrew's sector-appetite review and the trading-status check. The gap between it and the email count is eleven rows he rejected after reading their sites, which would otherwise have produced false negatives, plus one PECR hold (5.4).

Category 3 is split 21 waste / 16 cleaning and is clearly incomplete. Categories 1 and 2 are usable now.

Every row carries: company name, website, email, email type, contact name and role where published, region, **the source URL the email was read from**, qualification notes, and the four blank tracking columns you specified — plus a `bounced` column I added.

---

## 2. The one rule that shaped everything

**No email address in this file was inferred.** Every one was read off a page on that company's own website, and every row names the page. No `firstname@domain` pattern-matching, no bulk email-finder output — which you explicitly warned against, and which I'd have avoided anyway because a plausible-looking column of fake addresses is worse than a short one.

The consequence: 46 of the 235 qualified firms have a blank email cell. They are real, qualified companies that publish no address at all — enquiry form only. They stay in the file, flagged, because that's information rather than absence.

This is also why the yield rates above are meaningful. An 83% / 80% / 73% publication rate is a measured property of each sector, not an artefact of how hard I looked.

---

## 3. Findings that bear on your reasoning

### 3.1 The timing number — withdrawn, recomputed, and then recomputed again

You built the category logic around tolerance for a late signal, and assumed FSA registration lands well after the purchase decision. The first version of this document answered that with **"median 60 days from incorporation to FSA registration"**. That figure was circular and is withdrawn. You spotted the mechanism before I did, and you were right.

`classifier.py` sets `classification = NEW_VENUE` partly *because* a company was incorporated recently:

```python
qualifies_as_new_venue = recently_incorporated is True or existing_operator is not None
```

with `new_venue_max_incorporation_age_days = 180` in `config.toml`. So measuring the lag on NEW_VENUE rows measures the threshold. The giveaway is a cliff rather than a curve — NEW_VENUE rows by 60-day bucket run **245 / 136 / 80 / 1 / 5 / 3 / 22**, falling off a shelf exactly at 180 days, and UNKNOWN contains **no rows at all under 180 days**.

I then proposed a fix — restrict to matches corroborated by an exact registered-address hit, n=314, median 86 days — and argued that selection into that pool was independent of the incorporation date. **That was also wrong**, and it is worth saying why, because it is a subtler version of the same mistake. The reason string I filtered on, *"Registered address is an exact match for this establishment's address"*, is only emitted **after** the `qualifies_as_new_venue` gate. The pool is 144 NEW_VENUE plus 170 OPERATOR_CHANGE and zero UNKNOWN. Same circularity, better disguise. A second string, *"qualified via exact registered-address match, not name similarity"*, is emitted on the other side of the same branch and is 100% UNKNOWN with a median of 605 days. Filtering on either one alone just picks a side of the gate.

The honest figures, on populations the classifier does not select:

| Population | n | Median | ≤90d | ≤180d | ≤365d |
|---|---|---|---|---|---|
| All Companies House matches, every class | 1,375 | **303 days** | 29% | 41% | 54% |
| Address-corroborated, both wordings, every class | 470 | **204 days** | 35% | 47% | 64% |

Use one of those. The second is the better of the two — those are higher-quality matches — but the first is the one nobody can argue with.

Two caveats that sit underneath both. Only **21% of the feed (1,379 of 6,676 rows) has a Companies House match at all**, so every one of these figures describes the matched fifth, not the feed. And matching itself probably skews slightly new, because a young company is more likely to still be registered at its trading address.

**What this does to your reasoning.** It weakens the case for category 2 that I made in the first version of this document, and it weakens it in your direction rather than mine. At a median of around 7 months, a meaningful share of these venues have already opened and already bought their insurance. Your original instinct — that the timing objection is most likely to bite, and that this is exactly what makes a broker null result informative — survives better than my correction to it did. The honest pitch is no longer "we catch them 60 days in"; it is "roughly half of the matched venues are still within six months of incorporation", which is a weaker claim and still worth testing.


### 3.2 The three categories are wildly different in how enumerable they are

This is the finding I didn't expect, and it partly explains the row counts.

- **Roasters:** one directory (Roasterlist) listed ~455 UK coffee roasters. The constraint was enrichment speed, not finding names.
- **Brokers:** no usable directory. But FCA regulation creates conventions — firms must display their reference number — so qualification was cheap once a firm was found.
- **Cleaning and waste:** tens of thousands of firms, no trade list worth scraping, and heavy email obfuscation. Every single name costs its own search.

Your source ranking ("trade show exhibitor directories, then trade association lists, then Google Maps systematically") was written as if it applies uniformly. It doesn't. For roasters a directory did nearly all the work. For brokers, association directories were useless (see 5.1) and search was the only route. For cleaning and waste, none of the first four sources exist and it's search-by-city all the way down.

**Implication for the result:** the categories will have different sample sizes, so compare rates, never counts. And if a category comes back dead, "hard to enumerate" and "low demand" are not the same finding — cleaning and waste is hard to enumerate for reasons that have nothing to do with whether they'd buy.

### 3.3 Email publication behaviour differs by sector, and may be a weak demand signal

Beyond the headline rate, the *kind* of address differs:

- **Roasters** publish `wholesale@` and `trade@` addresses (22 of 96). They actively want trade enquiries.
- **Brokers** publish role addresses and their FCA number, but only 5 of 66 are sales-specific. Regulated, formal, funnel-controlled.
- **Cleaning and waste** overwhelmingly hide behind Cloudflare-protected contact forms. Ten of the 37 qualified firms had no readable address at all.

The form-only pattern in category 3 is worth noting before you read its bite rate. A firm that deliberately hides its email is either heavily spammed already or wants to control intake — either way, a cold email is fighting the funnel they've built.

### 3.4 "Has a salesperson" is very hard to verify from a website

Your criterion is right in principle — someone whose job is finding customers is the person who'd act on a weekly list. But only 37 of 235 rows (16%) name an individual with a relevant role, and the rate collapses in category 3 (2 of 37).

Where it *was* verifiable the rows are much stronger: North Star's senior account manager, Glen Lyon's wholesale account manager, Jericho's named wholesale account manager, Hodgson's named new-business contact, Ascend's and R K Henshall's account executives with direct addresses. If we go again, "publishes a named commercial contact" is a better scoring signal than firm size, and it's cheap because it comes from the same page fetch as the email.

### 3.5 Category 3 is really two categories

Waste contractors and cleaning contractors have different buying rhythms — a recurring collection contract versus a repeating job — different decision-makers and different price points. Pooling them means a null result won't tell you which half was dead. I've split them in the sheet and the Results tab reports them separately.

---

## 4. Where I departed from the brief

**Added a `bounced` column and split the bite-rate denominator.** Bite rate in the sheet is replies over *delivered*, not over sent. Andrew is warming a domain in parallel. Without bounce tracking, a category with no replies is ambiguous between "not interested" and "never arrived", and those call for opposite responses. This is the change I'd defend hardest.

**Swapped the qualification check per category.** You specified a Companies House check on every row. I did it for category 1 and then stopped — see 5.3. For brokers I captured the **FCA firm reference number** instead (66 of 83), which is strictly more informative: it confirms the firm exists, is currently authorised, and is permitted to do general insurance distribution. For waste I captured the **Environment Agency waste carrier registration** where published — but only 5 of 37 firms print it, so that column is sparse, and that sparseness is a fact about the sector rather than a gap.

**Included kitchen extract cleaning (TR19), grease trap servicing and used cooking oil collection in category 3.** You excluded the fit-out trades because they're specified before registration. I judged these to be recurring compliance services a venue re-buys every few months, not fit-out work. This is a judgement call and it's about a third of the cleaning rows — if you disagree, they're filterable on `sub_category` and `qualification_notes`.

**Included 12 Coversure franchise offices in category 2.** Each is a separately owned business with its own principal, so each qualifies. But they share a brand, systems and training, so their responses will be correlated. Flagged in the sheet with a recommendation to cap them at four or five.

---

## 5. Where I was wrong

Worth stating plainly, partly because two of these are about method rather than execution.

### 5.1 I told Andrew BIBA was the best source for category 2. It isn't.

I said the BIBA member directory was "the single best source" and would give "most of your 100 brokers in one place". It won't. BIBA's "find insurance" service is a **phone referral line**, not a searchable directory, and the member-check pages are JS-rendered and redirect to the homepage when fetched. I made the claim before checking it. That cost time and is part of why category 2 came in at 66 rather than 100.

Generalisable lesson: trade association "find a member" services are often lead-routing for members, not public data. Worth assuming that by default.

### 5.2 I invented a confidence-scoring scheme when one already existed in the codebase

For the Companies House matching I built an ad-hoc high/medium/low scheme using string similarity plus my own judgement on address corroboration. Andrew then pointed out his pipeline already has a rigorous one in `classifier.py` — IDF-weighted name similarity, threshold bands, and disqualifying signals for formation-agent addresses and multi-venue chains. Mine used no IDF, so generic tokens like "coffee" and "roasters" carried the same weight as distinctive ones. I patched around that; his approach handles it properly.

The same three words now mean different things in two places, which is a trap if anyone merges them. Better answer for any future category: point the existing `matcher.py` at supplier names rather than rebuild.

### 5.3 The Companies House pass was largely wasted effort, and the numbers show it

I verified 102 of 115 roaster rows. Then Andrew asked what it was actually buying, and the honest check was damning:

**The 13 rows where incorporation genuinely wasn't evident without a lookup are exactly the 13 rows I failed to verify.** For the other 102 I already knew — 96 say "Ltd" or "Limited" in the trading name, 23 print the number in their own footer. The pass confirmed what was already visible and missed every case where the answer mattered.

I claimed one real save for it: Ozone, whose obvious-looking entity turned out to be dissolved. **That claim was overstated.** The dissolved entity, OZONE COFFEE UK LIMITED (13227926), was the wrong company; the right one, OZONE COFFEE ROASTERS UK LIMITED (05528500), is active and always was. So the check corrected my `company_number` column. It did not save a send, because the business was trading the whole time and the email would have arrived fine.

The trading-status check has now been completed across all 102 numbers (see 5.4), and it makes the same point more sharply.

### 5.4 The trading-status check found no dead businesses — it found my own bad matches

This was the one part of the Companies House work I argued was worth keeping. It is now finished: 51 numbers against Andrew's local `companies_current` cache, 51 against the public register. **97 active, 5 not trading.** Reading those five is the whole lesson.

**Four were my matching errors, not dead firms.** Outpost Coffee Roasters, Norfolk Coffee Co, Press Coffee Roasters and Redemption Roasters are all visibly trading — live sites, 2026 copyright, active wholesale pages — while the number I attached to each belongs to a dissolved or liquidated company. Two are obvious once looked at: "The Norfolk Coffee Company Limited" was a **Surrey** company dissolved in 2015 and the business is in Fakenham; "Press Coffee Ltd" was **Harrogate** and the business is in London. Those four numbers have been removed from the rows and nothing has been put in their place, because a wrong number is worse than a blank one. The rows stay set to send.

**One is a genuine hold.** CoffeeAid: COFFEE AID LTD was dissolved on 5 May 2026, yet the shop is still taking orders at the same Newark address the dissolved entity was registered at. That usually means the business continued as a sole trader — an individual subscriber under PECR, which needs consent before a cold email. Set to "do not send".

Four more were benign registered-name mismatches with the entity confirmed active (Adams & Russell trading as Gold Crest Food & Beverages, Wogan Coffee as Brian Wogan Ltd, Kiss the Hippo, Iron & Fire), recorded in `ch_evidence` so nobody re-investigates them.

**Net effect on the send list: one row held out of 102 checked.** Not one dead business among them. So the defence I mounted for this check in the first version of this document — "it catches the dead firm whose bounce would muddy the metric" — did not survive contact with the full run. What the check actually caught was four errors in my own name-matching, which argues for fixing the matcher (5.2) rather than for running the check.


---

## 6. Statistical caveats before anyone reads a result

1. **The categories are unequal (96 / 66 / 27).** Compare rates, never counts.
2. **At these volumes the test resolves only large differences.** You can separate a ~2% category from a ~10% one. You cannot separate 2% from 4%. Two categories within a couple of points of each other means "no clear difference", not a ranking.
3. **Interleave the sends.** One category per day confounds a bad deliverability day with the category effect, and you won't be able to separate them afterwards.
4. **Record bounces from the first send.** See section 4.
5. **Category 3's sub-rows should be read separately** before pooling.
6. **The Coversure cluster is correlated.** Cap it.

---

## 7. Open questions for you

1. **With the corrected median at 204–303 days rather than 60, does the insurance broker category still carry the weight you assigned it?** Your reasoning was that it's the category where the timing objection is most likely to fail, making a null result maximally informative. My first answer said the data rescued brokers. The corrected data does not: at a median of around seven months a good share of these venues have already opened and already bought cover. Your original framing looks better than my revision of it. Does that make brokers more informative as a test, or does it mean the feed needs a tighter subset before a broker would buy it?
2. **Was the Companies House check load-bearing for a reason I've missed?** I've argued it wasn't, and the completed trading-status run (5.4) went further: 102 numbers checked, no dead businesses, one PECR hold, four of my own bad matches exposed. If your reasoning was about defensibility if challenged rather than about lead quality, say so — that changes the answer.
3. **Do you accept kitchen extract and grease trap servicing in category 3, or are they fit-out by your definition?**
4. **Is an asymmetric test acceptable?** 96 / 66 / 27 run next week versus 100 / 100 / 100 run in a month. My instinct is the former, because a directional answer you act on beats a balanced one you're still building. But it's a real trade-off.
5. **Should category 3's two halves be treated as separate categories in the analysis?** That would make it a four-category test with three usable arms.

---

## 8. Reading the spreadsheet

Three tabs.

**Leads** — 235 rows. Yellow columns on the right are for Andrew to fill in as he sends. `regulator_ref` holds the FCA number for brokers and the EA waste carrier registration for waste firms; `company_number` and `ch_match_confidence` apply to category 1 only. `ch_match_confidence` is colour-coded: red is unverified, amber needs a human glance, and `ch_evidence` says exactly what to check.

**Results** — bite rate by category, computed from the tracking columns as they're filled. Replies over delivered. Category 3's waste/cleaning sub-rows are indented and already counted in the parent row.

**Method & caveats** — the provenance rules, what was excluded and why, and the known weak rows.

**Four category 1 rows now have a blank `company_number`** — Outpost, Norfolk Coffee Co, Press Coffee Roasters and Redemption Roasters. That is deliberate; see 5.4. The businesses are trading, the numbers I had were wrong, and nothing has been guessed in their place.

**Rows worth a human look before sending:** the three waste brokers/quote platforms that aren't operators (Business Waste, Commercial Waste Quotes, The Business Waste Company); HEY Service Group, whose published client list is entirely national chains; Extraction Clean UK on a gmail address; Coversure Sheffield as a sole trader; and Clifton Coffee Roasters, where an exact-name company incorporated in January 2026 sits at the same postcode as a 2004 company — probably a restructure, but it's the one row where I'd want a human to pick.
