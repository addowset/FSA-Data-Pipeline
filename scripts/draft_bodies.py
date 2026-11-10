# -*- coding: utf-8 -*-
"""Build the outreach email bodies for a send day, from the live data.

    python scripts/draft_bodies.py                      # today's batch
    python scripts/draft_bodies.py --date 2026-11-11
    python scripts/draft_bodies.py --date 2026-11-11 --summary-only

Writes one .txt per supplier under exports/drafts/<send date>/ and prints a
summary table. It sends nothing and touches no mailbox: the files are what gets
pasted into a draft. Run scripts/send_gate.py first -- that decides whether a
row should go at all, this one writes the words.

Every figure is computed rather than retyped. The recipient is offered the
actual spreadsheet, so an inflated count is the one error they are guaranteed
to catch, and the honesty is the product.

NO LINK IN THE BODY, deliberately. A bare domain in the signature comes out
of Gmail as the full "https://www.google.com/url?q=...&source=gmail" redirect
as visible text, and an explicit HTML anchor still has its href rewritten the
same way while the plain-text alternative loses the line entirely. Tested 10
November 2026; see docs/email-variants-category1-roasters.md. The sending
address carries the domain, so nothing is lost.

VENUE ORDER follows the merge rules in docs/email-variants-category1-roasters.md:
new openings first, one takeover last, so the "changes of hand" claim is
evidenced rather than asserted. Within each group the picks round-robin across
councils, because for a regional supplier the list is also showing that the
patch is covered -- five venues from five councils reads as a region, five from
one town reads as all there is.

ADDRESS FORMATTING is most of this file, because FSA address lines have no
fixed meaning by position. The town is sometimes the last line (Darlington,
LONDON), sometimes the second-to-last with a county after it (Sidcup then
Kent), sometimes the council name sits last (Brighouse then Calderdale), and
sometimes the whole address including the postcode is crammed into line 1.
Taking the last line as the town gives "31 Commercial Street, Calderdale HD6
1AF", a council presented as a town; taking a fixed position is wrong about as
often.

Dropping every line that names the council fails differently: for a unitary
authority the council IS the town, so removing "Darlington" promotes the road
to town. What separates them is whether the candidate looks like a street,
which is cheap to test -- a leading house number, or a word like Road, Street
or Unit. So: counties are dropped outright, the town is the last line that does
not look like a street, a line naming the council is used only when nothing
better remains, and a non-street line sitting ahead of a street is a premises
name rather than a town. Case is corrected throughout, because the register
holds plenty of LONDON and FOLKESTONE and a shouted town in a cold email reads
as a mail merge.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import re
import sys
import textwrap
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import load_workbook

from fsa_pipeline import db, subscriptions, territories
from fsa_pipeline.config import load_config
from fsa_pipeline.csv_export import fetch_records, is_noise_business_type
from fsa_pipeline.report_filter import filter_records
from fsa_pipeline.report_xlsx import tidy_name

WORKBOOK = Path('docs/leads_all_three_categories.xlsx')
OUT_ROOT = Path('exports/drafts')
WIDTH = 76
MAX_STREET = 42
DASH = '—'    # the committed copy uses a real em dash, not two hyphens
BULLET = '•'

# Per-category voice. The copy document is written for roasters; the trade
# noun, the venue types and the "how does it reach you" question change for the
# other two, and the verb follows the rule recorded against Variant C --
# deliver for roasters, cover for brokers and for cleaning and waste. The
# broker and waste wording here was approved 10 November 2026.
VOICE = {
    'Coffee roasters (wholesale arm)': dict(
        audience='roaster',
        trade='a roaster',
        venues='the cafés, restaurants, bars and hotels',
        question=('How do you hear about a new café opening at the moment: '
                  'does someone drive round looking, or does it come to you?'),
        verb='deliver'),
    'Hospitality insurance brokers': dict(
        audience='broker',
        trade='a broker',
        venues='the cafés, restaurants, bars, hotels and takeaways',
        question=('How does a new venue reach you at the moment: does it come '
                  'through an introducer, or do you go looking?'),
        verb='cover'),
    'Commercial cleaning & waste contractors': dict(
        audience='waste',
        trade='a waste contractor',
        venues='the cafés, restaurants, bars, hotels, takeaways and food shops',
        question=('How do you hear about a new site opening at the moment: does '
                  'someone go looking, or does it come to you?'),
        verb='cover'),
}

# Counties and county-like names that appear as an address line but are not a
# town. The council's own name is handled separately, from the record itself.
COUNTIES = {
    'avon', 'bedfordshire', 'berkshire', 'buckinghamshire', 'cambridgeshire',
    'cheshire', 'cleveland', 'cornwall', 'cumbria', 'derbyshire', 'devon',
    'dorset', 'durham', 'co durham', 'county durham', 'east sussex', 'essex',
    'gloucestershire', 'greater manchester', 'hampshire', 'herefordshire',
    'hertfordshire', 'humberside', 'isle of wight', 'kent', 'lancashire',
    'leicestershire', 'lincolnshire', 'merseyside', 'middlesex', 'norfolk',
    'north humberside', 'north yorkshire', 'northamptonshire',
    'northumberland', 'nottinghamshire', 'oxfordshire', 'rutland',
    'shropshire', 'somerset', 'south humberside', 'south yorkshire',
    'staffordshire', 'suffolk', 'surrey', 'tyne and wear', 'tyne & wear',
    'warwickshire', 'west midlands', 'west sussex', 'west yorkshire',
    'wiltshire', 'worcestershire', 'scotland', 'wales', 'england',
    'united kingdom', 'uk',
}

# A line that looks like a street is not the town. Covers the two shapes that
# occur: a leading house or unit number, and a street-type word.
STREET_WORDS = (
    'road', 'street', 'lane', 'avenue', 'way', 'drive', 'close', 'place',
    'row', 'court', 'square', 'parade', 'terrace', 'crescent', 'gardens',
    'walk', 'mews', 'hill', 'rise', 'grove', 'bank', 'wharf', 'quay',
    'unit', 'floor', 'premises', 'estate', 'park ', 'centre', 'arcade',
    'precinct', 'retail', 'industrial', 'business',
)

TEMPLATE = """{greeting}

{opening}

{n} came up in {region} this week. {lead_in}:

{venue_lines}

{provenance}

{pitch}

{cta}

Andrew Dowsett
Opendo, 54 Beverley Road, Bristol BS7 0JN
Reply 'no thanks' and I won't contact you again."""


def wrap(text):
    """Re-flow to WIDTH, collapsing any whitespace already in the text."""
    return textwrap.fill(' '.join(text.split()), width=WIDTH)


def fold(value):
    """Lower-case and strip accents, so "Stanwell Cafe" matches "Stanwell
    Café" -- the register spells the same premises both ways on different
    lines of the same record."""
    stripped = unicodedata.normalize('NFKD', (value or '').lower())
    return ''.join(c for c in stripped if not unicodedata.combining(c)).strip(' ,.')


def looks_like_a_street(line):
    low = ' %s ' % line.lower()
    if re.match(r'^\s*\d+[a-z]?\b', line.lower()):
        return True
    return any(' %s' % word in low for word in STREET_WORDS)


def clean_name(value):
    """Collapse the duplicated trading names the register carries, such as
    "Rochester Health Club The Fuel Bar/The Fuel Bar"."""
    name = tidy_name((value or '').strip())
    if '/' in name:
        parts = [p.strip() for p in name.split('/') if p.strip()]
        if len(parts) == 2 and parts[1].lower() in parts[0].lower():
            name = parts[0]
    return name


def address_parts(record):
    """-> (name, street, town, postcode). See the module docstring."""
    postcode = (record['postcode_from_live_api'] or record['post_code'] or '').strip()
    name = clean_name(record['business_name'])
    authority = (record['authority_name'] or '').lower()
    # "Edinburgh (City of)" should also match a line reading "Edinburgh"; the
    # display form keeps its capitals for when it is used as the town.
    authority_display = re.sub(r'\s*\(.*?\)\s*', '',
                               record['authority_name'] or '').strip()
    authority_bare = authority_display.lower()

    lines, council_line = [], None
    for key in ('address_line_1', 'address_line_2',
                'address_line_3', 'address_line_4'):
        line = (record[key] or '').strip()
        if not line:
            continue
        if postcode:                   # line 1 sometimes holds the whole address
            line = re.sub(re.escape(postcode), '', line, flags=re.I).strip(' ,')
        if not line:
            continue
        low = line.lower().strip(' ,')
        if fold(line) == fold(name):   # a line repeating the trading name
            continue
        if low in COUNTIES:            # never the town when anything else exists
            continue
        if low in (authority, authority_bare):
            council_line = authority_display or tidy_name(line)
            continue
        lines.append(tidy_name(line))

    town, cut = '', len(lines)
    for position in range(len(lines) - 1, -1, -1):
        if looks_like_a_street(lines[position]):
            continue
        if position == 0 and len(lines) > 1:
            # A non-street line ahead of a street is the premises -- a pub
            # name, a unit, a building -- not the town. Leave it in the street
            # and let the council name supply the town.
            break
        town, cut = lines[position], position
        break
    if not town and council_line:
        town = council_line
    elif not town:
        cut = len(lines)

    street_parts = lines[:cut]
    street = ', '.join(street_parts)
    if len(street) > MAX_STREET and street_parts:
        street = street_parts[-1]      # drop the premises, keep the street
    return name, street, town, postcode


def venue_line(record):
    name, street, town, postcode = address_parts(record)
    tail = ' '.join(p for p in (town, postcode) if p)
    if (record['classification'] == 'OPERATOR_CHANGE'
            and record['previous_business_name']):
        return '%s %s took over from %s, %s' % (
            name, DASH, clean_name(record['previous_business_name']), tail)
    return ', '.join(p for p in (name, street, tail) if p)


def _round_robin_by_council(records):
    buckets = collections.OrderedDict()
    for record in sorted(records, key=lambda r: (r['authority_name'] or '',
                                                 r['business_name'] or '')):
        buckets.setdefault(record['authority_name'], []).append(record)
    out, exhausted = [], False
    while not exhausted:
        exhausted = True
        for bucket in buckets.values():
            if bucket:
                out.append(bucket.pop(0))
                exhausted = False
    return out


def order_venues(records, want=5):
    """New openings first, one takeover last, spread across councils within
    each group. A takeover last evidences the "changes of hand" claim; without
    one the line would be asserted and nothing in the list would show it."""
    new = _round_robin_by_council(
        [r for r in records if r['classification'] == 'NEW_VENUE'])
    takeovers = _round_robin_by_council(
        [r for r in records if r['classification'] == 'OPERATOR_CHANGE'
         and r['previous_business_name']])
    if takeovers and new:
        return new[:want - 1] + takeovers[:1]
    return (takeovers or new)[:want]


def build(row, index, pools, conn):
    voice = VOICE[row[index['category']]]
    label = row[index['region_label']]
    authorities = territories.resolve(conn, label)
    week, _ = filter_records(pools['week'], audience=voice['audience'],
                             authorities=authorities)
    four, _ = filter_records(pools['four'], audience=voice['audience'],
                             authorities=authorities)
    new = [r for r in week if r['classification'] == 'NEW_VENUE']
    picked = order_venues(week, 5)

    subject = '%d new food & drink venues in %s this week %s any use?' % (
        len(week), label, DASH)
    body = TEMPLATE.format(
        greeting=row[index['greeting']],
        opening=wrap(
            "I've created a report that reads every new food-business "
            'registration across 363 UK local authorities and picks out %s, '
            'flagging each one as a new opening or a change of hands.'
            % voice['venues']),
        n=len(week),
        region=label,
        lead_in='All of them' if len(week) <= 5 else 'Five of them',
        venue_lines='\n'.join('%s %s' % (BULLET, venue_line(r)) for r in picked),
        provenance=wrap(
            "Those are from this week's run. %d are brand-new openings, the "
            'rest changes of hand; %s has produced %d over the last four weeks.'
            % (len(new), label, len(four))),
        pitch=wrap("I'm not selling anything %s I just want to know if it's "
                   'any use to %s. %s'
                   % (DASH, voice['trade'], voice['question'])),
        cta=wrap("Reply and I'll send this week's report over %s for the whole "
                 'country, or tell me your postcode and how far you %s and '
                 "I'll cut it down to your patch. Free, nothing to sign."
                 % (DASH, voice['verb'])))
    return dict(subject=subject, body=body, week=week, four=four, new=new,
                picked=picked, label=label, authorities=authorities)


def main(argv=None):
    args = parse_args(argv)
    send_date = args.date or dt.date.today().isoformat()
    config = load_config()
    conn = db.connect(config.db_path)

    until = args.until or subscriptions.last_reportable_date(conn)
    end = dt.date.fromisoformat(until)
    windows = {'week': ((end - dt.timedelta(days=6)).isoformat(), until),
               'four': ((end - dt.timedelta(days=27)).isoformat(), until)}
    pools = {}
    for name, (since, upto) in windows.items():
        pools[name] = [r for r in fetch_records(conn, since=since, until=upto)
                       if not is_noise_business_type(r['business_type'])]

    rows = list(load_workbook(WORKBOOK, read_only=True)['Leads']
                .iter_rows(values_only=True))
    header = list(rows[0])
    index = {k: header.index(k) for k in header if k}

    batch = [r for r in rows[1:]
             if r[index['send_batch']] == send_date
             and r[index['send_status']] == 'send']
    if not batch:
        print('no rows batched to send on %s' % send_date)
        return 1

    out_dir = Path(args.out) if args.out else OUT_ROOT / send_date
    if not args.summary_only:
        out_dir.mkdir(parents=True, exist_ok=True)

    print('send day %s   week %s to %s   four weeks %s to %s'
          % (send_date, windows['week'][0], windows['week'][1],
             windows['four'][0], windows['four'][1]))
    print()
    print('%-28s %-26s %4s %4s %5s %4s %s'
          % ('company', 'region', 'wk', 'new', '4wks', 'cncl', 'file'))
    warnings = []
    for row in sorted(batch, key=lambda r: r[index['company_name']] or ''):
        built = build(row, index, pools, conn)
        company = row[index['company_name']]
        councils = {r['authority_name'] for r in built['picked']}
        slug = re.sub(r'[^\w]+', '-', company).strip('-').lower()

        name = '%s.txt' % slug
        if not args.summary_only:
            (out_dir / name).write_text(
                'To: %s\nSubject: %s\n%s\n%s\n'
                % (row[index['contact_email']], built['subject'],
                   '-' * 70, built['body']),
                encoding='utf-8')
        print('%-28s %-26s %4d %4d %5d %4d %s'
              % (company[:28], str(built['label'])[:26], len(built['week']),
                 len(built['new']), len(built['four']), len(councils), name))

        if len(councils) == 1 and len(built['authorities']) > 5:
            warnings.append(
                '%s: all %d listed venues are in %s, out of %d authorities -- '
                'the list will not read as regional'
                % (company, len(built['picked']), sorted(councils)[0],
                   len(built['authorities'])))
        if not any(r['classification'] == 'OPERATOR_CHANGE'
                   and r['previous_business_name'] for r in built['picked']):
            warnings.append(
                '%s: no takeover in the list, so "the rest changes of hand" is '
                'asserted rather than shown' % company)
        for record in built['picked']:
            _n, _s, town, postcode = address_parts(record)
            if not postcode:
                warnings.append('%s: no postcode for %s'
                                % (company, clean_name(record['business_name'])))
            elif not town:
                warnings.append('%s: no town for %s, postcode only'
                                % (company, clean_name(record['business_name'])))

    if warnings:
        print()
        print('worth a look before sending:')
        for line in warnings:
            print('  - %s' % line)
    if not args.summary_only:
        print()
        print('bodies written to %s' % out_dir)
    conn.close()
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--date', default=None,
                        help='Send day, YYYY-MM-DD (default: today)')
    parser.add_argument('--out', default=None,
                        help='Output directory (default: exports/drafts/<date>)')
    parser.add_argument('--until', default=None,
                        help='End of the windows (default: last reportable date)')
    parser.add_argument('--summary-only', action='store_true',
                        help='Print the table and warnings, write no files')
    return parser.parse_args(argv)


if __name__ == '__main__':
    raise SystemExit(main())
