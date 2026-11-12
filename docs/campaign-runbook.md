# Campaign runbook — start here

The state of the supplier outreach, the routine for a send day, and the rules
behind it. Read this first in any new session; open README.md sections only
when a task needs them (it documents the pipeline itself, and is long).

Last updated: 12 November 2026. Replaces `project-handoff.md`, retired the same
day: what was still true moved here, what the README already covered was
dropped, and the old file is in git history.

---

## 1. Status (12 November 2026)

**What is being tested.** Whether suppliers want a weekly feed of newly
registered UK food and drink venues (FSA registrations matched to Companies
House). Three categories: coffee roasters with a wholesale arm, hospitality
insurance brokers, commercial cleaning and waste contractors.

**The pipeline** runs daily and unattended: collection at 11:00 by Task
Scheduler, an independent run check at 13:15, a daily status email. README,
"Scheduling".

**The lead list** is `docs/leads_all_three_categories.xlsx`, sheet `Leads`.
168 rows cleared to send, 45 with no published address, 19 "do not send", 3
"no coverage". Decision rules (what reply rate means what) are on the
`Results` tab.

**Sent:** 30 of 168 — 5 on 10 Nov, 10 on 11 Nov, 15 on 12 Nov. No bounces.
Replies so far are all auto-acknowledgements (Roastworks, Horsham, Recorra,
Glen Lyon, Arkwright), filed under the Gmail label `FSA supplier replies auto`.

**To go:** 138, by `send_batch`:

| Date | Rows |
|---|---|
| Tue 17 Nov | 25 |
| Wed 18 Nov | 25 |
| Thu 19 Nov | 24 |
| Tue 24 Nov | 25 |
| Wed 25 Nov | 25 |
| Thu 26 Nov | 14 |

Of the 138: Variant A (regional) 77, B (national) 52, C (thin patch) 9. The
first C rows go on 17 Nov (McClarrons, ETM Recycling).

**Checkpoints.** The first, at 30 sent, was 12 Nov: nothing to act on. Review
replies, bounces and DMARC again after 19 Nov, before the last three days go.

**Website.** `website/index.html`, drafted 23 Oct; whether it is live was not
verified on 12 Nov. The emails carry no link either way (section 5).

Update this section at the end of every send day.

---

## 2. Send-day routine

Andrew sends mid-morning. For send date `D`:

1. **Gate.** `python scripts/send_gate.py --date D`. One verdict per row:
   send, widen, hold or unresolved, judged on the four-week total because
   councils publish in batches. It notes where the workbook's stored figures
   differ from live; that is normal, the bodies use live figures.
2. **Bodies.** `python scripts/draft_bodies.py --date D` writes one `.txt` per
   row to `exports/drafts/D/` and prints a table plus "worth a look" warnings.
   **Read every body.** If the script refuses a row (`CannotDraft`), that is a
   wording decision for Andrew or a move to a later day — never hand-write
   around it.
3. **Gmail drafts.** One per file: `To:` and `Subject:` from the header, body
   after the dashed line, plain text, no Gmail signature (the signature is in
   the body). Then list the drafts and check the count, recipients, no `http`
   and no attachments.
4. **Andrew sends** from Drafts.
5. **Verify** with a Sent search, `in:sent after:YYYY/MM/DD "any use?"`: one
   thread per recipient.
6. **Record** `sent_date_1` as the string `D` on each row, matched by
   `contact_email`, with the workbook closed.
7. **Commit** the workbook: "Day N sent: …".

**Moving a row** to another day: swap its `send_batch` with a later row of the
same category so both days keep their count, and say why in the commit.

---

## 3. Replies

- **Auto-replies** go to the label `FSA supplier replies auto`. Nothing to
  record.
- **A real reply:** fill `replied`, `reply_type` and `territory_requested` on
  the row. Reply wording for the five cases (territory named, yes but no
  territory, C with wider reach, a no, an empty week) and the price decided on
  31 Oct are in `docs/email-variants-category1-roasters.md`, "Reply email".
- **The report** to attach takes about 3 seconds:
  `python scripts/export_report.py --territory "<what they wrote>" --audience roaster|broker|waste`
  (`--territory UK` for national). An unknown place name is an error, not a
  guess: add it to `fsa_pipeline/territories.py`, keys in `normalise()` form.
- **Attachments cannot be added by the Gmail tool** — a file reference is
  silently dropped. The reply draft's first line is
  `[ATTACH: exports/reports/<file>.xlsx]`; Andrew attaches by hand and deletes
  the line.
- **Reply to everyone within hours, including the nos.** A "no thanks" sets
  `send_status` to "do not send" with the reason in `status_reason`, for good.
- **Bounces** go in `bounced`. Bite rate is replies over *delivered*.

---

## 4. Rules from earlier decisions

Don't reopen these without new evidence.

**Business**

- The trigger is FSA registration plus Companies House, not planning:
  since Class E (Sept 2020) a shop-to-café change needs no permission, so
  planning misses the commonest case.
- Fit-out trades (kitchen equipment, shopfitters, extraction, EPOS) are
  excluded as buyers: their decision is made before FSA registration.
- Operator changes may matter more than openings — every supplier contract is
  back in play. Every list shows a takeover where the week has one.
- Unmatched (sole-trader) venues stay in the feed: they are the independents
  small suppliers can actually win.
- **Never quote "~750 venues a week".** It was a pre-filter count, about 3x the
  sellable number. Every figure in an email is computed live by
  `draft_bodies.py`; recipients get the actual file and will check.
- **Incorporation to FSA registration:** median 303 days across all 1,375
  Companies House matches, or 204 days across the 470 with an exact
  registered-address hit. The earlier "60 days" was circular (measured on rows
  selected by a 180-day threshold). Working: the workbook's `Method & caveats`
  tab.

**Compliance**

- PECR: B2B email to corporate subscribers only. Sole traders and
  unincorporated partnerships need consent, so they are excluded.
- Every email carries real identification (name, postal address) and an
  opt-out line. Opt-outs are permanent.
- Only addresses published on the company's own website, with `source_url`
  recorded. Never pattern-guess an address.
- Company and premises-level data only in the pipeline; no personal data.
- OGL attribution on anything shared externally (README, "Data licensing").
- Latitude and longitude are out of every export: some council coordinates
  carry Ordnance Survey (PSMA) restrictions. Geocode from the address if
  coordinates are ever needed.

**Outreach**

- Plain text, no tracking, and no link (section 5 says why).
- The ask is an action — postcode and radius — not an opinion. Variant C's "if
  there were more of them, would this be useful?" is an accepted exception;
  the reasoning is in the copy doc.
- Categories are interleaved every day, so a bad deliverability day is not
  mistaken for a category effect.
- Correlated rows are capped: five Coversure franchise branches were dropped.
- Wording lives in `docs/email-variants-category1-roasters.md` and is
  implemented in `scripts/draft_bodies.py`. Change both together.

---

## 5. Mail setup

- `opendo.co.uk`, registrar and DNS at **Cloudflare**; Google Workspace,
  `andrew@opendo.co.uk`; `dmarc@opendo.co.uk` is an alias. `opdproperties.co.uk`
  was rejected (reads as property spam) — don't bring it back.
- Export the DNS records before touching anything in the Cloudflare panel: the
  mail records sit beside whatever else is being changed.

| Type | Name | Value |
|---|---|---|
| MX | `@` | `smtp.google.com` priority 1 |
| TXT | `@` | `v=spf1 include:_spf.google.com ~all` |
| TXT | `google._domainkey` | 2048-bit DKIM key from Google admin |
| TXT | `_dmarc` | `v=DMARC1; p=quarantine; sp=reject; rua=mailto:dmarc@opendo.co.uk; fo=1; adkim=r; aspf=r` (TTL 300) |
| TXT | `*._domainkey` | `v=DKIM1; p=` (null key, blocks spoofed selectors) |

`p=quarantine` since 10 Nov. `_dmarc.*` is **not** a wildcard (DNS only
treats a leading `*` that way); subdomains are covered by `sp=`. Don't add a
`_dmarc.*` record.

**DMARC reports.** Andrew saves the attachments (`.zip`, `.xml.gz`) into
`docs/dmarc/` (gitignored); then `python3 scripts/dmarc_summary.py docs/dmarc`.
It reads the top level of that folder only. Every message reported from 28 Sep
to 11 Nov passed DMARC; the one SPF failure was a forward, where DKIM still
passed. Baseline summary: `docs/dmarc-baseline-2026-11-05.txt`.

**Gmail, learned the hard way**

- Gmail rewrites links through `google.com/url`, and in plain text a bare
  domain turns into the whole 88-character redirect as visible text. That is
  why the emails carry no link.
- `create_draft` silently drops an attachment given by file reference — see
  `[ATTACH: …]` in section 3.
- The Sent copy never shows the Workspace footer. To see what a recipient
  sees, send a test to an outside address (Andrew's Hotmail).
- Subjects: clients show roughly 60–78 characters. Past 78, `subject_for()`
  puts "Any use?" first.

---

## 6. Working on Andrew's computer

Sessions reach the folder through the Cowork device shell, where it is
mounted at `$HOME/mnt/FSA Data Pipeline`. On the PC it is
`C:\ANDY\Dev\claude\FSA Data Pipeline`.

- **Each new chat starts from scratch.** The folder must be added again
  ("Add folder" in the desktop app), and the shell has no packages and no
  shim. Set-up, once per chat (a minute or so):
  `pip install -q tomli -r requirements.txt`, then create the shim below.
- **Python 3.10 has no `tomllib`.** Run scripts with
  `PYTHONPATH="$HOME/shim:$PWD"`. If `$HOME/shim/tomllib.py` is missing, it is
  two lines: `from tomli import *` and
  `from tomli import load, loads, TOMLDecodeError`.
- **Ask for delete permission on the folder before any git commit.** Without
  it git cannot remove its own lock files and leaves `.git/*.lock` behind;
  grant permission, then remove the lock.
- **Close the workbook in LibreOffice** before a script writes to it
  (`docs/.~lock.*` means it is open).
- **Run tests file by file.** A whole-suite `pytest` run hangs in that shell,
  while each file passes in seconds:
  `for f in tests/test_*.py; do timeout 25 python3 -m pytest -q $f | tail -1; done`
  (with the `PYTHONPATH` above).
- Scratch scripts go in `exports/_scratch/`, drafts in `exports/drafts/<date>/`
  (both gitignored). When copying a file onto the computer, use a new filename
  each time: a reused name can deliver a cached older copy.
- Left untracked on purpose: `SQL/`, `ground_truth_sample_v4.csv`,
  `scripts/supplier_coverage.py`.
