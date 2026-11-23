#!/usr/bin/env python3
# ==============================================================================
# Tests for the readers of the durable contested record (contested-busy-reader-1):
# nfit-profile's _contested_history_read / _contested_history_bound /
# _contested_history_level and the same functions in nfit-forecast. Both are
# extracted from production source and run on the same months; every case
# first requires the two results to be identical (reader parity).
#
# Covered: a fully sampled 15-minute day reads as fully covered (elapsed
# minutes are converted to observations with the day's interval); a partly
# classified day stays visible as partial; a supported day without readings is
# a day without observed competition; a day without support, a month whose
# record and support were written from different data, and an unknown version
# are unavailable, never quiet; days at another interval; months merged; days
# the history does not hold; and the count-weighted percentile against the
# percentile of the expanded observations.
# ==============================================================================
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
FORECAST = os.environ.get('NFIT_FORECAST') or os.path.abspath(os.path.join(REPO, '..', 'nfit-forecast', 'nfit-forecast'))
PERL_SUBS = ('_safe_dig', '_contested_month_reason', '_contested_history_read', '_contested_history_bound', '_contested_history_level')


def perl_ready():
    if not shutil.which('perl'):
        return False
    return subprocess.run(['perl', '-MJSON', '-e', '1'], capture_output=True).returncode == 0


def load_forecast_reader():
    """The reader block of nfit-forecast, run in isolation (None when the repository is absent)."""
    if not os.path.exists(FORECAST):
        return None
    with open(FORECAST, encoding='utf-8') as fh:
        src = fh.read()
    start = src.index('# Member exposure reader (member-exposure-reader-1)')
    start = src.rindex('# ====', 0, start)
    end = src.index('# --- End of member exposure reader ---')
    ns = {'os': os, 'json': json, 'glob': glob, 'Path': Path, 'datetime': datetime, 'timedelta': timedelta, 're': re, 'sys': sys}
    try:
        import configparser
        ns['configparser'] = configparser
    except ImportError:
        pass
    exec(compile(src[start:end], FORECAST, 'exec'), ns)
    return ns if 'contested_history_read' in ns else None


def perl_read(months, queries):
    """nfit-profile's reader on [(month, data)] for queries
    {vm, population, from, to, figures: [[share, percentile], ...]}: [{result, figures}]."""
    with open(os.path.join(REPO, 'nfit-profile'), encoding='utf-8') as fh:
        source = fh.read()
    subs = ''.join(re.search(r'^(sub %s\s*\{.*?^\})' % name, source, re.S | re.M).group(1) + '\n' for name in PERL_SUBS)
    consts = ''.join(re.search(r'^(my \$%s\s*=.*?;)' % name, source, re.M).group(1) + '\n'
                     for name in ('CONTESTED_BUSY_PROJECTION', 'CONTESTED_BUSY_READER', 'MEMBER_EXPOSURE_PROJECTION'))
    script = 'use strict; use warnings; use JSON; use Time::Piece; use List::Util qw(max min);\n' + consts + subs + r"""
        local $/; my $in = decode_json(<STDIN>);
        my @out;
        foreach my $q (@{ $in->{queries} })
        {
            my $r = _contested_history_read($in->{months}, @{$q}{qw(vm population from to)});
            my @figures = map { my ($level, $n) = _contested_history_level($r->{readings}, @$_);
                                [ _contested_history_bound($r->{readings}, $_->[0]), $level, $n ] } @{ $q->{figures} // [] };
            push @out, { result => $r, figures => \@figures };
        }
        print JSON->new->canonical->allow_nonref->encode(\@out);
    """
    out = subprocess.run(['perl', '-e', script], input=json.dumps({'months': months, 'queries': queries}),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-1500:]
    return json.loads(out.stdout)


def python_read(reader, months, queries):
    out = []
    for q in queries:
        r = reader['contested_history_read'](months, q['vm'], q['population'], q['from'], q['to'])
        figures = []
        for share, percentile in q.get('figures', []):
            level, n = reader['contested_history_level'](r['readings'], share, percentile)
            figures.append([reader['contested_history_bound'](r['readings'], share), level, n])
        out.append({'result': r, 'figures': figures})
    return json.loads(json.dumps(out))


def same(a, b, path=''):
    """Structural equality with numbers compared to 1e-9; returns the first difference or ''."""
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return '' if a is b or a == b and type(a) is type(b) else '%s: %r != %r' % (path, a, b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return '' if abs(a - b) <= 1e-9 else '%s: %r != %r' % (path, a, b)
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return '%s: keys %r != %r' % (path, sorted(a), sorted(b))
        return next((d for d in (same(a[k], b[k], path + '/' + k) for k in sorted(a)) if d), '')
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return '%s: length %d != %d' % (path, len(a), len(b))
        return next((d for d in (same(x, y, '%s[%d]' % (path, i)) for i, (x, y) in enumerate(zip(a, b))) if d), '')
    return '' if a == b else '%s: %r != %r' % (path, a, b)


SIGNATURE = {'rulesHash': 'r1', 'poolRevision': 'p1', 'statesRevision': 's1', 'dataRevision': 'd1',
             'windowStart': '2026-09-01T00:00:00Z', 'windowEndExclusive': '2026-10-01T00:00:00Z'}


def month(days, readings=None, cadence=1, by_date=None, unavailable=None, vm='lpar', population='none',
          marker=None, support=None, record=True, state='2026-08-01T00:00:00Z'):
    """One month: days = [(date, expected minutes, paired, classified)]."""
    data = {
        'Metadata': {'MemberExposureEvidence': {**SIGNATURE, 'projection': 'member-busy-exposure-1', 'schemaVersion': 1, **(support or {})},
                     'ContestedBusyEvidence': {**SIGNATURE, 'projection': 'contested-busy-1', 'schemaVersion': 1, **(marker or {})}},
        'MemberExposure': {vm: [{'stateStartUTC': state, 'fromUTC': '2026-09-01T00:00:00Z', 'populations': {population: {
            'daily': [{'date': d, 'expectedMinutes': e, 'pairedMinutes': p, 'classifiedMinutes': c, 'scarceMinutes': 0, 'exposure': [0]}
                      for d, e, p, c in days]}}}]},
        'ContestedBusy': {},
    }
    if record:
        pop = {'profiles': ['G3-95W15'], 'evaluatedDays': sum(1 for d in days if d[3]), 'cadenceMinutes': cadence}
        if by_date:
            pop['cadenceByDate'] = by_date
        if readings:
            pop['readings'] = readings
        if unavailable:
            pop['unavailable'] = unavailable
        data['ContestedBusy'][vm] = [{'stateStartUTC': state, 'fromUTC': '2026-09-01T00:00:00Z', 'populations': {population: pop}}]
    return data


@unittest.skipUnless(perl_ready(), 'perl with JSON needed')
class ContestedReaderTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.reader = load_forecast_reader()

    def read(self, months, start='2026-09-01', end='2026-09-03', figures=(), vm='lpar', population='none'):
        """Both readers on the same months; they must agree. Returns (result, figures)."""
        q = [{'vm': vm, 'population': population, 'from': start, 'to': end, 'figures': [list(f) for f in figures]}]
        perl = perl_read(months, q)
        if self.reader:
            self.assertEqual(same(perl, python_read(self.reader, months, q)), '')
        return perl[0]['result'], perl[0]['figures']

    def test_the_forecast_reader_is_present(self):
        if os.path.exists(FORECAST):
            self.assertIsNotNone(self.reader, 'nfit-forecast holds no contested reader')

    def test_a_fully_sampled_15_minute_day_is_fully_covered(self):
        # 1440 eligible minutes at one sample per 15 minutes: 96 observations expected and 96 classified.
        m = month([('2026-09-01', 1440, 96, 96), ('2026-09-02', 1440, 96, 96)], cadence=15,
                  readings={'2026-09-02': [[0.5, 0.8, 3]]})
        r, _ = self.read([['2026-09', m]])
        self.assertEqual((r['status'], r['coverage'], r['cadenceMinutes']), ('complete', 1.0, [15]))
        self.assertEqual(r['observations'], {'expected': 192, 'paired': 192, 'classified': 192, 'contested': 3})
        self.assertEqual(r['dayRecords'], {'evaluated': 2, 'full': 2, 'partial': 0, 'unavailable': 0, 'contested': 1})
        self.assertEqual(r['contestedMinutes'], 45)                      # three observations of 15 minutes
        self.assertIs(r['contestedObserved'], True)
        # The same support at other intervals: five and ten minutes are coarse too, and read the same way.
        for cadence, n in ((5, 288), (10, 144)):
            r, _ = self.read([['2026-09', month([('2026-09-01', 1440, n, n), ('2026-09-02', 1440, n, n)], cadence=cadence)]])
            self.assertEqual((r['status'], r['coverage']), ('complete', 1.0), cadence)

    def test_partial_coverage_stays_visible(self):
        m = month([('2026-09-01', 1440, 96, 60), ('2026-09-02', 1440, 96, 96)], cadence=15)
        r, _ = self.read([['2026-09', m]])
        self.assertEqual(r['status'], 'partial')
        self.assertEqual((r['dayRecords']['partial'], r['dayRecords']['full']), (1, 1))
        self.assertEqual(r['coverage'], round(156 / 192, 4))
        self.assertIs(r['contestedObserved'], False)                     # nothing seen, in evidence that is not whole

    def test_a_supported_day_without_readings_had_no_observed_competition(self):
        r, figures = self.read([['2026-09', month([('2026-09-01', 1440, 1440, 1440), ('2026-09-02', 1440, 1440, 1440)])]],
                               figures=[(0.4, 95)])
        self.assertEqual((r['status'], r['contestedObserved'], r['readings']), ('complete', False, []))
        self.assertEqual(figures, [[None, None, 0]])

    def test_readings_without_support_are_not_evidence(self):
        m = month([('2026-09-01', 1440, 1440, 1440)], readings={'2026-09-01': [[0.5, 0.8, 2]], '2026-09-02': [[1.5, 1.8, 9]]})
        r, _ = self.read([['2026-09', m]])
        self.assertEqual(r['readings'], [[0.5, 0.8, 2]])
        self.assertEqual(r['reasons'], {'contested readings without matching support': 1})
        self.assertEqual((r['status'], r['days']['withoutHistory']), ('partial', 1))

    def test_record_and_support_from_different_data_are_unavailable(self):
        days = [('2026-09-01', 1440, 1440, 1440), ('2026-09-02', 1440, 1440, 1440)]
        for change in ({'dataRevision': 'd2'}, {'poolRevision': 'p2'}, {'rulesHash': 'r2'}, {'windowEndExclusive': '2026-09-15T00:00:00Z'}):
            r, _ = self.read([['2026-09', month(days, marker=change)]])
            self.assertEqual((r['status'], r['contestedObserved']), ('unavailable', None), change)
            self.assertEqual(r['reason'], 'the contested record and its support were written from different data')
            self.assertEqual(r['dayRecords']['unavailable'], 2)

    def test_unknown_versions_and_an_absent_record_are_unavailable(self):
        days = [('2026-09-01', 1440, 1440, 1440)]
        for kwargs, reason in (({'marker': {'projection': 'contested-busy-2'}}, 'the contested record has a version this reader does not know'),
                               ({'marker': {'schemaVersion': 2}}, 'the contested record has a version this reader does not know'),
                               ({'support': {'schemaVersion': 2}},
                                'the member exposure supporting the contested record has a version this reader does not know')):
            r, _ = self.read([['2026-09', month(days, **kwargs)]])
            self.assertEqual((r['status'], r['reason'], r['contestedObserved']), ('unavailable', reason, None))
        m = month(days)
        del m['ContestedBusy'], m['Metadata']['ContestedBusyEvidence']
        r, _ = self.read([['2026-09', m]])
        self.assertEqual((r['status'], r['reason']), ('unavailable', 'the month holds no contested record'))
        # Support for the LPAR, and no record of it in a month that has the block.
        r, _ = self.read([['2026-09', month(days, record=False)]])
        self.assertEqual((r['status'], r['reason']), ('unavailable', 'the day has support and no contested record'))
        # No history at all for the LPAR.
        r, _ = self.read([['2026-09', month(days)]], vm='other')
        self.assertEqual((r['status'], r['days']['withHistory']), ('unavailable', 0))
        self.assertIn('history holds no member exposure', r['reason'])

    def test_unavailable_days_keep_their_reason(self):
        m = month([('2026-09-01', 1440, 0, 0), ('2026-09-02', 1440, 1440, 0), ('2026-09-03', 1440, 1440, 1440)],
                  unavailable={'2026-09-01': 'no sample with PhysB', '2026-09-02': 'no pool verdict for any sample'})
        r, _ = self.read([['2026-09', m]], end='2026-09-04')
        self.assertEqual(r['status'], 'partial')
        self.assertEqual(r['reasons'], {'no sample with PhysB': 1, 'no pool verdict for any sample': 1})
        self.assertEqual((r['dayRecords']['evaluated'], r['dayRecords']['unavailable'], r['coverage']), (1, 2, 1.0))

    def test_days_at_another_interval_and_months_are_merged(self):
        first = month([('2026-09-29', 1440, 1440, 1440), ('2026-09-30', 1440, 96, 96)], by_date={'2026-09-30': 15},
                      readings={'2026-09-29': [[0.5, 0.8, 2], [0.6, 0.9, 1]], '2026-09-30': [[0.5, 0.8, 1]]})
        second = month([('2026-10-01', 1440, 288, 288)], cadence=5, readings={'2026-10-01': [[0.6, 0.9, 4], [0.7, 0.7, 1]]})
        r, figures = self.read([['2026-10', second], ['2026-09', first]], start='2026-09-29', end='2026-10-03', figures=[(1, 100), (0, 50)])
        self.assertEqual(r['readings'], [[0.5, 0.8, 3], [0.6, 0.9, 5], [0.7, 0.7, 1]])
        self.assertEqual((r['cadenceMinutes'], r['contestedMinutes']), ([1, 5, 15], 3 * 1 + 1 * 15 + 5 * 5))
        self.assertEqual(r['observations'], {'expected': 1440 + 96 + 288, 'paired': 1824, 'classified': 1824, 'contested': 9})
        self.assertEqual(r['days'], {'window': 4, 'withHistory': 3, 'withoutHistory': 1, 'contested': 3})
        self.assertEqual(r['status'], 'partial')                         # one day of the window is not in history
        self.assertEqual(figures, [[0.9, 0.9, 9], [0.7, 0.6, 9]])

    def test_the_window_limits_what_is_read(self):
        m = month([('2026-09-01', 1440, 1440, 1440), ('2026-09-02', 1440, 1440, 1440)],
                  readings={'2026-09-01': [[0.5, 0.8, 2]], '2026-09-02': [[0.9, 1.0, 7]]})
        r, _ = self.read([['2026-09', m]], start='2026-09-01', end='2026-09-02')
        self.assertEqual((r['readings'], r['status'], r['days']['window']), ([[0.5, 0.8, 2]], 'complete', 1))

    def test_the_count_weighted_percentile_is_the_percentile_of_the_expanded_observations(self):
        rng = random.Random(20261123)
        for _ in range(12):
            readings, expanded = [], []
            for _ in range(rng.randint(1, 40)):
                busy = rng.randint(1, 300) / 100
                consumed = busy + rng.randint(0, 100) / 100
                n = rng.randint(1, 25)
                readings.append([busy, consumed, n])
                expanded += [(busy, consumed)] * n
            figures = [(share, pct) for share in (0, 0.4, 0.65, 1) for pct in (90, 95, 98, 99, 100, 50)]
            r, got = self.read([['2026-09', month([('2026-09-01', 1440, 1440, 1440)], readings={'2026-09-01': readings})]],
                               end='2026-09-02', figures=figures)
            for (share, pct), (bound, level, n) in zip(figures, got):
                values = sorted(b + share * (c - b) for b, c in expanded)
                rank = (len(values) - 1) * pct / 100
                lower = int(rank)
                upper = min(lower + 1, len(values) - 1)
                exact = values[lower] + (values[upper] - values[lower]) * (rank - lower)
                self.assertEqual(n, len(values))
                self.assertGreaterEqual(level, exact - 1e-9)
                self.assertLess(level, exact + 0.01 + 1e-9)
                self.assertGreaterEqual(bound, values[-1] - 1e-9)
                self.assertLess(bound, values[-1] + 0.01 + 1e-9)
                self.assertGreaterEqual(bound, level - 1e-9)


if __name__ == '__main__':
    unittest.main(verbosity=2)
