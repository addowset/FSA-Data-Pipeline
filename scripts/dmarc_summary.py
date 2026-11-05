# -*- coding: utf-8 -*-
"""Aggregate DMARC aggregate reports and say who has sent as this domain.

The one question these answer that nothing else does: has anything other than
Google Workspace sent mail claiming to be opendo.co.uk? That is the only real
risk in moving the DMARC policy from p=none to p=quarantine, and until now it
has been inference from the SPF record rather than observation.

Takes the report directory as an argument. Google sends .zip, Microsoft sends
.xml.gz; both hold one XML document.
"""
import argparse
import collections
import datetime as dt
import gzip
import io
import ipaddress
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

# Google's published sending ranges, enough to label a source without DNS
# (neither environment here can do a reverse lookup). Anything that falls
# outside these is what we are actually looking for.
GOOGLE = [ipaddress.ip_network(n) for n in (
    '35.190.247.0/24', '64.233.160.0/19', '66.102.0.0/20', '66.249.80.0/20',
    '72.14.192.0/18', '74.125.0.0/16', '108.177.8.0/21', '108.177.96.0/19',
    '142.250.0.0/15', '172.217.0.0/16', '172.253.56.0/21', '173.194.0.0/16',
    '209.85.128.0/17', '216.58.192.0/19', '216.239.32.0/19',
    '2001:4860:4000::/36', '2404:6800:4000::/36', '2607:f8b0:4000::/36',
    '2800:3f0:4000::/36', '2a00:1450:4000::/36', '2c0f:fb50:4000::/36',
)]


def classify(ip_text):
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return 'unparseable'
    for net in GOOGLE:
        if ip.version == net.version and ip in net:
            return 'Google'
    return 'NOT GOOGLE'


def load(path):
    raw = path.read_bytes()
    if path.suffix == '.zip':
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return z.read(z.namelist()[0])
    if path.suffix == '.gz':
        return gzip.decompress(raw)
    return raw


def text(node, path, default=''):
    found = node.find(path)
    return found.text.strip() if found is not None and found.text else default


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    args = parser.parse_args()
    directory = Path(args.directory)

    files = sorted(p for p in directory.iterdir()
                   if p.suffix in ('.zip', '.gz', '.xml'))
    print('reports: %d' % len(files))

    by_source = collections.defaultdict(lambda: {
        'count': 0, 'reporters': set(), 'spf': collections.Counter(),
        'dkim': collections.Counter(), 'disposition': collections.Counter(),
        'header_from': set(), 'spf_domain': set(), 'dkim_domain': set(),
        'first': None, 'last': None})
    policies = collections.Counter()
    windows = []
    failures = []

    for path in files:
        try:
            root = ET.fromstring(load(path))
        except Exception as exc:
            failures.append((path.name, repr(exc)))
            continue

        org = text(root, 'report_metadata/org_name', '?')
        begin = text(root, 'report_metadata/date_range/begin')
        end = text(root, 'report_metadata/date_range/end')
        if begin and end:
            windows.append((int(begin), int(end), org))
        policies['%s p=%s sp=%s pct=%s adkim=%s aspf=%s' % (
            text(root, 'policy_published/domain', '?'),
            text(root, 'policy_published/p', '-'),
            text(root, 'policy_published/sp', '-'),
            text(root, 'policy_published/pct', '-'),
            text(root, 'policy_published/adkim', '-'),
            text(root, 'policy_published/aspf', '-'))] += 1

        for record in root.findall('record'):
            ip = text(record, 'row/source_ip', '?')
            entry = by_source[ip]
            n = int(text(record, 'row/count', '0') or 0)
            entry['count'] += n
            entry['reporters'].add(org)
            entry['spf'][text(record, 'row/policy_evaluated/spf', '?')] += n
            entry['dkim'][text(record, 'row/policy_evaluated/dkim', '?')] += n
            entry['disposition'][
                text(record, 'row/policy_evaluated/disposition', '?')] += n
            hf = text(record, 'identifiers/header_from')
            if hf:
                entry['header_from'].add(hf)
            for auth in record.findall('auth_results/spf'):
                d = text(auth, 'domain')
                if d:
                    entry['spf_domain'].add('%s=%s' % (d, text(auth, 'result', '?')))
            for auth in record.findall('auth_results/dkim'):
                d = text(auth, 'domain')
                if d:
                    entry['dkim_domain'].add('%s=%s' % (d, text(auth, 'result', '?')))
            if begin:
                b = int(begin)
                entry['first'] = b if entry['first'] is None else min(entry['first'], b)
                entry['last'] = b if entry['last'] is None else max(entry['last'], b)

    if failures:
        print('\nUNREADABLE:')
        for name, exc in failures:
            print('  %s  %s' % (name, exc))

    if windows:
        lo = min(w[0] for w in windows)
        hi = max(w[1] for w in windows)
        print('coverage: %s to %s' % (
            dt.datetime.utcfromtimestamp(lo).strftime('%Y-%m-%d'),
            dt.datetime.utcfromtimestamp(hi).strftime('%Y-%m-%d')))
        reporters = collections.Counter(w[2] for w in windows)
        print('reporters: %s' % ', '.join('%s x%d' % kv for kv in reporters.items()))

    print('\npolicy as the reporters saw it:')
    for line, n in policies.most_common():
        print('  x%-3d %s' % (n, line))

    total = sum(e['count'] for e in by_source.values())
    print('\nmessages reported: %d, from %d distinct source(s)'
          % (total, len(by_source)))
    print()
    rows = sorted(by_source.items(), key=lambda kv: -kv[1]['count'])
    for ip, e in rows:
        verdict = classify(ip)
        print('%-24s %-11s %5d msg' % (ip, verdict, e['count']))
        print('     seen %s..%s by %s' % (
            dt.datetime.utcfromtimestamp(e['first']).strftime('%d %b')
            if e['first'] else '?',
            dt.datetime.utcfromtimestamp(e['last']).strftime('%d %b')
            if e['last'] else '?',
            ', '.join(sorted(e['reporters']))))
        print('     DMARC spf=%s dkim=%s disposition=%s' % (
            dict(e['spf']), dict(e['dkim']), dict(e['disposition'])))
        print('     header.from=%s' % (sorted(e['header_from']) or '-'))
        print('     auth spf: %s' % (sorted(e['spf_domain']) or '-'))
        print('     auth dkim: %s' % (sorted(e['dkim_domain']) or '-'))
        print()

    foreign = [(ip, e) for ip, e in rows if classify(ip) == 'NOT GOOGLE']
    print('=' * 68)
    if foreign:
        print('SOURCES OUTSIDE GOOGLE: %d' % len(foreign))
        for ip, e in foreign:
            print('  %-24s %d msg, dkim=%s spf=%s'
                  % (ip, e['count'], dict(e['dkim']), dict(e['spf'])))
        print('Check each before enforcing: a legitimate one needs adding to SPF')
        print('or DKIM first, or p=quarantine will start sending it to spam.')
    else:
        print('No source outside Google in %d reports. Nothing legitimate can'
              % len(files))
        print('break when p=none becomes p=quarantine.')
    hard = sum(e['count'] for _ip, e in rows
               if e['dkim'].get('fail') or e['spf'].get('fail'))
    print('messages failing SPF or DKIM alignment: %d of %d' % (hard, total))


if __name__ == '__main__':
    sys.exit(main())
