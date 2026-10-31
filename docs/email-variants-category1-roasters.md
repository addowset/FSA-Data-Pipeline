# Supplier emails — three variants

**For:** the 95 coffee roasters cleared to send in `leads_all_three_categories.xlsx`
**Built from:** `venues_2026-10-01.csv`
**Date:** 2 October 2026, corrected 7 October

---

## First, a correction to what I told you yesterday

I said that for roughly half your regional roasters you couldn't honestly fill five venue lines. **That was wrong, and the error was mine.** I measured per *postcode area* (BS, NE, CF), but a roaster's patch is a region made of several areas. Measured at region level, 22 of your 23 named regions clear five comfortably. Only one doesn't.

| Region | Venue registrations per fortnight |
|---|---|
| London | 72 |
| Sussex / Surrey / Kent | 56 |
| Scotland | 46 |
| East Midlands | 45 |
| Cardiff / Wales | 42 |
| South Wales + Bristol | 41 |
| Norfolk / East Anglia | 36 |
| Glasgow / Scotland | 30 |
| Kent / SE | 29 |
| Somerset / SW | 28 |
| Devon / Cornwall | 25 |
| North Wales | 20 |
| Cambridgeshire | 18 |
| Northern Ireland / Belfast / Co Down | 15 |
| Tyne & Wear | 12 |
| Newcastle | 11 |
| Sheffield | 6 |
| **Cumbria** | **2.5 — the one exception** |

**National figures, for the national variant.** Corrected 14 October — the earlier line here said 335 a week, which counted takeaways, retailers, FHRSID re-issues, chains and rows with no printable address. Deliverable venues only, median across the four usable weeks, from 363 local authorities:

| Venue set | Per week | Range | New openings | Changes of hand |
|---|---|---|---|---|
| Roasters (cafés, restaurants, pubs/bars, hotels, other catering) | **129** | 98–133 | 68 | 53 |
| Brokers (the above plus takeaways) | **181** | 145–189 | — | — |
| Cleaning & waste (the above plus retailers and mobile caterers) | **219** | 175–232 | — | — |

Use the figure for the category you are writing to. The roaster number is the one that belongs in Variant B.

---

## Before you send anything: dating the venues

**Corrected 7 October.** I originally wrote that the pipeline stopped running between 17 and 30 September. It didn't — the FSA API wasn't updating. Andrew raised it with them, it was a known fault, and it has since been fixed. The cause was upstream.

The consequence for this email is unchanged, though, and it's worth being precise about why. `first_seen_date` records when *your pipeline first saw a record*, not when the venue registered. Those are normally within a day of each other. During an upstream stall they diverge, and **755 venue registrations in the 1 October file carry `first_seen_date = 2026-09-30` when they actually surfaced somewhere across the preceding fortnight.**

I looked for a second date to recover the real one. There isn't one:

- `authority_extract_date` doesn't help — it tracks the extract, not the venue. In normal operation it sits two to three weeks *after* `first_seen_date`, so it's recording the most recent extract a record appeared in, not the first.
- `rating_status` is only a weak hint. "Awaiting Inspection" is 90% of the backlog batch against 82% of clean days — a real difference, but nowhere near enough to date a row on.

So those 755 rows can't be dated more precisely than "sometime in the previous fortnight", and they should be excluded from any "appeared in the last fortnight" selection. Every figure in this document comes from the clean 21 August – 17 September window.

**Worth building, now that you know this can happen.** A volume guard: compare each day's venue-event count against the trailing median, and mark rows on any day that runs well above it as date-unreliable. It's a few lines, and it means the next upstream stall doesn't quietly drop three-week-old venues into an email that calls them new. That failure is expensive in a way the others aren't — a roaster who drives to a "new" café and finds it's been open a month doesn't reply to tell you, they just write you off.

---

*Revised 22 October, all three variants. Two changes.*

*First, the subject lines now say "food & drink venues" rather than "venues" or "venue
registrations". Andrew's point: a subject line that opens with a bare number and the word
"venues" does not tell a roaster what kind of venue, and "registrations" is pipeline language
that means nothing to the reader.*

*Second, every figure below was recomputed on venues_2026-10-22.csv over three whole clean weeks
(2-8, 9-15, 16-22 October) counted on local authority rather than postcode area. The previous
figures used four weeks with the FSA outage in the middle of them, and postcode areas that cross
county boundaries - a Cambridge roaster was shown three venues of which two were in Essex and
Lincolnshire. National per week for the roaster venue set is now 131 (131/109/142), brokers 193,
cleaning and waste 230.*

*One case is still unapproved copy. For 5 Variant A and 5 Variant C suppliers, their patch
produced nothing at all in the week to 22 October, so "[N] came up in [region] this week" has no
list to attach to. Those rows quote the three-week window instead - "[N] came up in [region] over
the last three weeks" - which follows Andrew's own suggestion of 15 October but has not been read
in context. An empty week on a single-authority patch is normal arithmetic, not a data fault.*

---

---

*Corrected 28 October: the three templates said "over the last month" while every draft generated
since 22 October has said "over the last three weeks", because the counting window is three weeks
and "month" is not true. The decision was recorded in the test sheet's findings at the time and
never made it back into the templates, so the doc and the sends had quietly disagreed for six days.*

## Reply email — drafted 27 October 2026

The message that goes out with the report attached. Four of them, because what you send depends
on what the supplier said, not on which variant they received.

*Written 27 October. Three changes to the first draft, all Andrew's. The weekly offer moved from
third paragraph to second, because an opt-in buried under housekeeping is not really an offer.
"Otherwise this is the only one you'll get" cut: it is shaped like an ultimatum, and "say the
word and I'll add you" already carries the opt-in without the snap. And "cut down to" rather than
"cut to" throughout — the second time this correction has been made, so it is house style now,
not a one-off.*

### 1. They named a territory

The main case, and the one the whole campaign is aiming at.

```
Hi [Name],

Attached: [N] new venues in [territory] for the week to [date] — [N_new]
new openings and [N_old] changes of hands, with the previous occupier named
where the council recorded one.

I can send a new report every week if you like. Say the word and I'll add
you to receive it.

The second tab says what was taken out before you saw it and what each
column means. The file is correct as at [stamp] — a council can amend a
registration after publishing it, so if something doesn't match what you
find on the ground, tell me and I'll look at it.

Andrew Dowsett
Opendo, 54 Beverley Road, Bristol BS7 0JN
```

Delivery, then the offer, then housekeeping. It closes on "tell me and I'll look at it" rather
than on the ask, which is the better last line for a relationship you are trying to start.

### 2. They said yes but not where

"Sounds interesting, send it over." Paragraph one and the ask are replaced; the rest is draft 1.

```
Attached: all [N] new venues across the UK for the week to [date] —
[N_new] new openings and [N_old] changes of hands.

That's the national list, because I don't know your patch yet. Tell me the
area you cover and the next one will be cut down to it — for most suppliers
that takes it from [N] rows to something readable in a minute.
```

### 3. Variant C, and their reach is wider than their website said

The reply that proves the Variant C question was worth asking. Opening only; the rest is draft 1.

```
Good to know — [wider area] rather than just [original], then.

Attached: [N] new venues across [wider area] for the week to [date]. That's
[N] against the [small N] I quoted you, which is the difference your own
answer made.
```

### 4. A no

Answer these too. Thread engagement is the strongest deliverability signal available, and the
refusals are where the findings are — a category that produces twenty polite nos with the same
reason has told you more than one that produces two yeses.

```
Understood, thanks for coming back to me — I'll leave you alone.

One thing if you have a second, and no obligation: was it the timing,
the area, or just not something you'd use? I'm trying to work out whether
this is any use to anyone in your trade and a straight no is more useful
to me than silence.

Andrew Dowsett
Opendo
```

### 5. A week with nothing in it

Weekly means weekly, so this still goes out. But the first line has to say there is nothing in
it. A subscriber who opens an empty attachment expecting venues does not think "quiet week", they
think the report is broken — and that is a worse impression than the quiet week itself.

```
Hi [Name],

Nothing this week. No cafés, restaurants, pubs or hotels registered in
[territory] between [since] and [until].

[[N] food businesses did register there — takeaways and shops rather than
venues you'd sell to. The attached file's second tab lists what came up and
why it was filtered out.]

The file's attached so you've got the week on record, but it's empty — I'd
rather say so here than have you open it wondering what went wrong.

Andrew Dowsett
Opendo, 54 Beverley Road, Bristol BS7 0JN
```

Two versions of the middle paragraph, and the difference matters to the reader:

- **Registrations happened, none were sellable.** Cambridgeshire produced 30 register entries in
  the week to 22 October and not one was a café, restaurant, pub or hotel. That is a fact about
  the filter working, and it is worth telling them.
- **Nothing registered at all.** Rarer, and a different message: their patch was quiet, not their
  filter narrow. Drop the middle paragraph entirely.

`scripts/export_report.py` tells you which case you are in. On an empty report it logs the venue
count, the number of register entries that were in the territory anyway, and a reminder to lead
the email with it.

### Decisions behind these

**The weekly is opt-in, not opt-out.** "Say the word and I'll add you" means nobody is subscribed
by default. When they do say the word, add them with `scripts/subscribers.py add`, passing
`--from` set to the end date of the sample report they already received, so their first weekly
carries straight on from it with no gap and nothing they have read twice. The alternative — sending weekly until told to stop — keeps more names on the list,
but it makes "yes, weekly" a deliberate act rather than a reply they would have sent anyway, and
that is a far stronger signal than a reply on its own. The decision rules on the workbook's
Results tab treat 15 territory requests as the threshold for real; a weekly opt-in is better
evidence than a territory request.

**The accuracy stamp appears in the body as well as in the file.** `scripts/export_report.py`
logs it on every run: *"Correct at the time of report creation, 23 October 2026 at 13:32"*. In the
email it reads as care rather than cover, and it frames any discrepancy the customer finds as a
question rather than a mistake. The reason it is needed at all: a council can amend or withdraw a
registration after publishing it, so a report sent on Tuesday may read differently by Friday.

*The second original reason has been dealt with at source. The FSA's terms require any hygiene
rating shown to be current or to carry the date it was accurate as of, and a stale one can be an
offence. `rating_status` was dropped from the CSV export on 23 October and never appears in the
customer workbook, so the obligation does not arise.*

**No discovery question in drafts 1 to 3.** Variants A and B already asked how they hear about
openings today. If they answered it, asking again is annoying; if they ignored it, asking again in
the email that finally delivers what they wanted risks the one moment of goodwill the campaign
gets. Ask in the second exchange instead. If it goes in anywhere it goes at the end of draft 1 and
nowhere else.

**The sign-off lives in the body, not in a Google Workspace footer.** Workspace appends its footer
to every outgoing message, so by the fourth exchange the quoted chain carries four copies of the
postal address, and in some clients it lands below the quoted text, detached from the signature.
It also cannot vary, and the cold email's "Reply 'no thanks' and I won't contact you again" is the
wrong register in a reply to someone who has just said yes. The deciding reason is visibility:
an appended footer is not in the draft, so the message being reviewed is not the message that
sends, and the PECR identity and address are invisible at exactly the moment they are being
checked. The cost is that it can now be forgotten — it is in the template, and every draft is
reviewed before sending.

**Every one of these is a reply in the existing thread**, so the subject line stays as whatever
the supplier wrote. No new subject, no "Re: Re:".

**House style: "cut down to", never "cut to".**

### Price, decided 31 October 2026

**£10 a month, any size patch.** Quoted only when a supplier asks, never led with.

The wording, and why each part of it is there:

> If you want it every week, it's £10 a month, whatever size patch you want. It's an
> introductory price — it'll likely be higher for people who come later — but it won't go up on
> you automatically; if it ever changes I'd talk to you first.

"Introductory" and "no automatic increase" pull against each other, because introductory normally
promises a rise. The resolution is that it is introductory *for new customers* while *this*
customer's price does not move without a conversation. That gives an early subscriber a reason to
say yes now, commits Andrew to consultation rather than to a frozen price forever, and does not
promise an increase that then has to be delivered.

**The sample stays free and the covering email says so**, because the cold email promised "free,
nothing to sign" and that promise has to be visibly kept at the moment money is first mentioned.
Hence "That one's free, as promised" immediately before the price.

**Price is not mentioned to anyone who did not ask, for the duration of the experiment.** Not
squeamishness: the decision rules on the workbook's Results tab are keyed to reply counts and
territory requests, and a price changes the question from "do you want this?" to "is this worth
£10?". Those have very different yes-rates, and at sixty per category the two effects cannot be
separated. Quoting price to everyone would leave a dead category and a mispriced one looking
identical. Answering a direct question honestly while not leading with price is ordinary
commercial behaviour, and nobody is told the ongoing feed is free.

Revisit after the experiment, when there is a reply rate to price against.

### The weekly average is the wrong statistic when the weeks swing

Found three times now, each time in a live draft:

- Leeds: weeks of 3, 0 and 11. The draft said "11 came up this week" and "Leeds has averaged
  about 3 a week" two paragraphs apart. Both true, and together they read as a contradiction.
- The East of England: weeks of 15, 5, 13 and 4. The cold email quoted "about 11 a week" and the
  report that followed had 4 in it.

A median describes a stable series and misleads on a volatile one, which is most small
territories. Where the weeks swing, give the series instead:

> the last four weeks have run 15, 5, 13 and 4 — so nine on average, but it swings about more
> than a single number suggests

Longer, and it reads as candour rather than hedging, because the alternative is quoting eleven
and delivering four. Still to decide: whether the merge should pick between median and series
automatically on some measure of spread, or whether every quoted figure becomes a range.

### Still to settle

- The attachment filename. `export_report.py` currently writes
  `new-venues_the-north-west_2026-10-22.xlsx`, which is fine in a folder and scruffy in an inbox.
  Something closer to `Opendo - new venues - North West - w-e 22 Oct 2026.xlsx` reads better on
  the receiving end.
- Whether `export_report.py` should print the finished paragraph — venue count, new/takeover
  split, territory, date, stamp — so the numbers are pasted rather than retyped. It already has
  every value.


## Variant A — regional (48 roasters; 97 suppliers across all three categories)

*Revised 14 October after a line-by-line pass with Andrew. Five changes: the opener names the
report and the venue types it picks out (and 363 authorities, not 359 — the count has drifted
twice in three weeks, so re-check it at send time); "this week's run" replaces "came out of it
this morning", so the send does not have to happen on the day the report was generated; the
"developer, not a salesperson" paragraph is gone, with its one load-bearing clause folded into
the question; the question now sits above the offer rather than below it, which moves it from 77%
to 55% of the way down the email; and the call to action no longer makes the postcode a condition
of getting anything. 181 words, down from 209.*

> **Subject:** [N] new food & drink venues in [region] this week — any use?

```
Hi [Name],

I've created a report that reads every new food-business registration across
363 UK local authorities and picks out the cafés, restaurants, bars and
hotels, flagging each one as a new opening or a change of hands.

[N] came up in [region] this week. [Five of them / All of them]:

• [venue], [street], [town] [postcode]
• [venue], [street], [town] [postcode]
• [venue], [street], [town] [postcode]
• [venue], [street], [town] [postcode]
• [venue] — took over from [previous name], [town] [postcode]

Those are from this week's run. [N_new] are brand-new openings, the rest
changes of hand; [region] has averaged about [N_week] a week over the last
three weeks.

I'm not selling anything — I just want to know if it's any use to a roaster.
How do you hear about a new café opening at the moment: does someone drive
round looking, or does it come to you?

Reply and I'll send this week's report over — for the whole country, or tell
me your postcode and how far you deliver and I'll cut it down to your patch.
Free, nothing to sign.

Andrew Dowsett
Opendo, 54 Beverley Road, Bristol BS7 0JN
Reply 'no thanks' and I won't contact you again.
```

---

## Variant B — national (37 roasters; 60 across all three categories)

Two-thirds of your roasters sell nationally, so "[region]" is meaningless to them. The pitch changes: for a national supplier the count is the hook but the *sortability* is the value, because they need to split the list between reps.

*Revised 14 October. The five changes from Variant A, plus two of its own.*

*First, the numbers were wrong and are now merge fields. The old text said 670 a fortnight, 274 new openings, 396 changes of hand, 335 a week — all computed before the filters were tightened, so they counted takeaways, retailers, FHRSID re-issues, chains and rows with no usable address. The honest figures for the venue set a roaster can sell to are about **131 a week** (range 109–142 across the three clean weeks to 22 October; the 129 quoted here on 14 October came from four weeks with the FSA outage inside them), of which a median of **68 are new openings** and **53 changes of hand**. The old numbers overstated it by 2.6x. These recipients receive the actual file, so an inflated count in the email is the one error they are guaranteed to catch.*

*Second, the sortability paragraph has moved into the call to action, because it describes what the recipient will receive rather than being an argument in its own right. That drops the question from 72% to 59% of the way down, which matches Variant A's 61%.*

> **Subject:** [N] new food & drink venues across the UK last week — any use?

```
Hi [Name],

I've created a report that reads every new food-business registration across
363 UK local authorities and picks out the cafés, restaurants, bars and
hotels, flagging each one as a new opening or a change of hands.

[N] came up across the UK last week — [N_new] brand-new openings and [N_old]
changes of hand. Five at random:

• [venue], [street], [town] [postcode]
• [venue], [street], [town] [postcode]
• [venue], [street], [town] [postcode]
• [venue], [street], [town] [postcode]
• [venue] — took over from [previous name], [town] [postcode]

Those are from this week's run, which has averaged about [N_week] a week over
the last three weeks.

I'm not selling anything — I just want to know if it's any use to a roaster.
How does a new café opening reach you at the moment: does someone go looking,
or does it come in on its own?

Reply and I'll send this week's report over. It's a spreadsheet — name, full
address, postcode, and whether it's a new opening or a takeover — so it sorts
by territory however your patches are drawn. Free, nothing to sign.

Andrew Dowsett
Opendo, 54 Beverley Road, Bristol BS7 0JN
Reply 'no thanks' and I won't contact you again.
```

---

## Variant C — thin local patch (10 roasters; 21 across all three categories)

For a supplier whose territory is a single named patch that does **not** produce enough, and who does not ship nationally, so there is no wider list to fall back on. Twenty-one of the 178 sendable suppliers.

The temptation is to leave these out. Don't. Variant A would promise volume that isn't there, and your email explicitly offers to put them on the weekly list — a kept promise that disappoints is worse than no email at all in a small industry. But a thin patch is often a *mis-stated* patch: a website saying "Nottingham" frequently means Nottinghamshire and Derbyshire in practice. That is the question this email exists to ask, and only they can answer it.

*Revised 15 October. The changes from A and B, plus three of C's own.*

*One question, not two. C had both "how far do you deliver?" and the discovery question that A and B
ask. The reach question is the one that can change C's verdict, and asking a supplier about their
buying habits is premature when you do not yet know whether you can serve them at all. Two questions
in one cold email halves the odds of either being answered. If the reach turns out wide, ask the
other one in the reply.*

*"I'm not selling anything" is gone, because paragraph two already opens with "I'm writing with a
caveat rather than a pitch" — the same move, better put, and earlier. Including both would be saying
it twice.*

*The question and the call to action are now one act. For a C recipient the answer to "how far do you
deliver" IS the conversion, so there is nothing to gain by separating them.*

*174 words, down from 241 — and 157 for the five suppliers whose widening clause gets cut. The
shortest of the three variants, which is right for the weakest offer.*

*Four further changes on 15 October, all Andrew's. "A question rather than a pitch" instead of "a
caveat rather than a pitch" — it signals the ask in the first six words while keeping the contrast
that carries the no-sell signal, which is why C needs no separate "I'm not selling anything". "All
of this week's" instead of "all of them", because "all of them" could be read as all that has ever
been found. "I'd rather say so than dress it up" cut, because the admission already demonstrates
straightness and announcing it tells rather than shows. And "about your patch rather than mine"
became "one only you can answer" — the original borrowed a my-patch-versus-your-patch contrast from
sales-speak, and Andrew has no patch; he has a national dataset. The replacement says the thing
that was meant and happens to be literally true.*

*Revised 22 October, Variant C only. Three changes, all Andrew's.*

*"Only" is gone from the subject line. "Only 7 new venues in Cambridgeshire in three weeks" is
negative twice over: the honesty is already carried by naming the period, and the word on top of
it argues the recipient out of opening the email.*

*The question was wrong, and this is the substantive change. "How far do you actually deliver?"
asks whether they want more of the thing — it never asks whether the thing is any use. A supplier
could answer "the whole of Yorkshire" and Andrew would still not know what he set out to learn.
The pivot now asks both, in that order, and the second question is the one they can answer in a
word:*

*"That's probably not enough to be worth your time. If there were more of them, would this be
useful to you? Yorkshire and the Humber runs at about 13 a week — do you cover that far?"*

*This reinstates two questions, which the 15 October revision cut to one. The reasoning then was
that two questions in a cold email halve the odds of either being answered. It still holds for
unrelated questions; these two are one conditional proposition - would more help, and is there
more to be had - and the reply "yes, we cover all of Yorkshire" answers both at once.*

*Third, "deliver" only fits a roaster. A broker does not deliver to Harrogate and nor does a waste
contractor, so the verb is per category: deliver for roasters, cover for brokers and for cleaning
and waste.*

*For the two C suppliers with no wider region to offer (Lucid in Belfast, Ouseburn in Newcastle),
the widening sentence is replaced rather than cut, because the reach question is still live - the
patch may be mis-stated on their own website:*

*"That's probably not enough to be worth your time. If there were more of them, would this be
useful to you? And is Newcastle actually your patch, or do you deliver further than that?"*

> **Subject:** [N] new food & drink venues in [region] [this week / in three weeks] — any use?

```
Hi [Name],

I've created a report that reads every new food-business registration across
363 UK local authorities and picks out the cafés, restaurants, bars and
hotels, flagging each one as a new opening or a change of hands.

I'm writing with a question rather than a pitch: [region] is quiet. [N] came
up in this week's run, and it's averaged about [N_week] a week over the last
three weeks. That's all of this week's:

• [venue], [street], [town] [postcode]
• [venue] — took over from [previous name], [town] [postcode]

That's probably not enough to be worth your time. If there were more of them,
would this be useful to you? [widen_to] runs at about [widen_per_week] a week
— do you [deliver / cover] that far?

Reply and I'll send this week's report over, cut down to whatever your patch
actually is. Free, nothing to sign.

Andrew Dowsett
Opendo, 54 Beverley Road, Bristol BS7 0JN
Reply 'no thanks' and I won't contact you again.
```

### The widening offer, per supplier

The `widen_to` and `widen_per_week` columns in the leads workbook hold these. Read the right-hand column before you send: **widening does not always help.**

| Supplier | Patch | Per week now | Widen to | Per week then |
|---|---|---|---|---|
| Carvetii Coffee | CA LA | 0 | the North West | 9 |
| Smith Street Coffee Roasters | S | 0.5 | Yorkshire | 7 |
| Coversure Sheffield | S | 0.5 | Yorkshire | 10 |
| Coversure Bristol | BS | 0.5 | the South West | 8 |
| Coversure Southampton | SO | 0 | Hampshire and Dorset | 6 |
| ETM Recycling | BS | 1 | the South West | 10 |
| Birnbeck Insurance | BA BS TA | 1 | the South West | 8 |
| Hot Numbers Coffee | CB PE | 1.5 | East Anglia | 7 |
| Silver Oak Coffee | CB PE | 1.5 | East Anglia | 7 |
| Coversure Leicester | LE | 2.5 | the East Midlands | 7 |
| Coversure Nottingham | NG | 2 | the East Midlands | 7 |
| Westinsure Plymouth | EX PL TA | 2.5 | the South West | 8 |
| CSW Insurance Brokers | DG DH NE TD | 2 | the North East and southern Scotland | 6 |
| Coversure Cramlington | NE TD | 2 | the North East and Borders | 4 |
| Wrexham Bean Co | CH LL SY | 2.5 | North Wales and Cheshire | 3 |
| Windrush Insurance Brokers | OX | 1 | Oxfordshire and the Thames Valley | 3 |
| Ouseburn Coffee Co | NE | 1 | the North East | **1 — no gain** |
| Pumphreys Coffee | NE SR | 1 | the North East | **1 — no gain** |
| Lucid Coffee Roasters | BT | 2.5 | — | **nothing wider exists** |
| McCabe's Coffee | BT | 2.5 | — | **nothing wider exists** |
| Ristretto Coffee Roasters | BT | 2.5 | — | **nothing wider exists** |

Five of the 21 have nowhere useful to go. For the two Newcastle roasters the whole North East runs at 1–2 a week for the roaster venue set, and it is violently lumpy: 2, 11, 1, 1 across the four usable weeks. For the three Northern Ireland roasters, BT *is* the whole country — one postcode area covering 1.9m people, and there is no wider UK region to widen into.

For those five, cut the widening paragraph entirely and let the email be what it is: a short, honest note saying the volume is low, here is exactly what it is, is it any use. That is a legitimate question and a null answer from them is a real finding — it tells you the product needs density, which is the single most important thing you do not yet know.

---

## Merge rules

**`[Name]`** — only 14 of 95 roasters publish a named contact (8 national, 6 regional). For the other 81, the line is `Hi,` on its own. Not "Hi there", which reads like software; not "Hi Sir/Madam"; and never a blank that leaves `Hi ,`.

**`[N]`, `[N_new]`, `[N_week]`** — computed per recipient, never hard-coded. If a region produced three, say three and list three. The honesty is the product.

**Venue lines** — up to five. Lead with new openings, finish with one takeover so the "changed hands" claim is evidenced rather than asserted. If there are no takeovers that fortnight, drop that line rather than reaching back further.

**`[region]`** — from the `region` column. Where it names a city and a country ("Aberdeen / Scotland", "Glasgow / Scotland"), use the country: the wider figure is both more accurate to what you'd send them and a stronger number.

**Which variant each supplier gets** — the `email_variant` column in the leads workbook, assigned from the coverage report rather than by eye:

- **B** if the supplier names no home region at all, *or* names one that is too thin but ships nationally. For the second group the national list is the honest answer, so lead with the national number rather than their weak local one.
- **C** if the supplier names a home region, it is too thin, and they do *not* ship nationally. There is no fallback, so the email has to be about the thinness.
- **A** otherwise — including the marginal cases at three or four a week, which are enough for an honest regional email with a smaller number in it.

Sendable split: **A 97, B 60, C 21.** Note how small B has become. Under the old binary rule any supplier whose region mentioned the UK counted as national, which swept 64 of 95 roasters into the generic email; naming a home region now wins, so B is only the firms with no local patch at all.

**Carvetii Coffee (Cumbria)** — now a Variant C row. Cumbria produced a median of zero a week, so there is nothing to widen the window to either. Widen the patch to the North West instead, which runs at nine, and ask whether their van gets that far. Don't pad it from Lancashire without asking.

**Freshness** — "those came out of it this morning" has to be true. Regenerate the export the morning you send, or cut the line.

**PECR** — the postal address and the opt-out line are both required on every message, so neither is optional in any variant. The address is 54 Beverley Road, Bristol BS7 0JN.

**The deliverable is an attachment, and it goes in the reply, not the cold email.** So the opening email carries no attachment, no link and no tracking pixel — which is about as clean as a cold send from a young domain can be. The report itself arrives in your reply, inside a thread the recipient started, which both Google and Microsoft treat far more leniently than an unsolicited first contact. Send it as `.xlsx`: `.csv` is sometimes stripped or flagged by corporate filters, and `.zip` very often is.

**A bare "yes" gets the national file.** The call to action promises the whole country by default, so have that file ready on the morning you send rather than generating it per reply. Only the recipients who volunteer a postcode need a cut-down version.

---

## One thing worth deciding before you send

Both variants offer the feed free. That measures whether roasters find it *interesting*; it does not measure whether they'd *pay*. You will get people happily accepting a free weekly list who would never spend £40 a month on it.

That's probably the right trade for a cold email — asking for money in message one gets no replies at all, and a dead category tells you something either way. But it means a good reply rate answers "is this interesting to roasters", not "is this a business". If you want a read on the second question, the place to ask is in the reply to someone who says yes, not in the opening email.
