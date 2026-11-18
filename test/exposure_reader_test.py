#!/usr/bin/env python3
# ==============================================================================
# Tests for the nfit-forecast member exposure reader (member-exposure-reader-1).
# The reader block is extracted from ../nfit-forecast/nfit-forecast (or
# $NFIT_FORECAST) and run in isolation, so the assertions exercise production
# source without the forecasting dependencies.
#
# Covered: exact answers at stored thresholds (including a 0 row), bounds
# between stored thresholds, support bounds below the lowest stored threshold,
# a candidate above every threshold, a randomised property check that the
# bounds always contain the true value, unavailable answers with reasons,
# the as-of window, differences against the installed entitlement, the
# default profile window from TIER and hint (with the resolver and the TIER
# file parser checked case by case against nfit-profile's own subroutines),
# and an end-to-end check on history written by nfit-profile against an
# independent minute-level reference.
# ==============================================================================
import glob
import json
import os
import random
import re
import configparser
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
FORECAST = os.environ.get('NFIT_FORECAST') or os.path.abspath(os.path.join(REPO, '..', 'nfit-forecast', 'nfit-forecast'))


def load_reader():
    with open(FORECAST, encoding='utf-8') as fh:
        src = fh.read()
    start = src.index('# Member exposure reader (member-exposure-reader-1)')
    start = src.rindex('# ====', 0, start)
    end = src.index('# --- End of member exposure reader ---')
    ns = {'os': os, 'json': json, 'glob': glob, 'Path': Path, 'datetime': datetime, 'timedelta': timedelta, 're': re,
          'configparser': configparser, 'sys': sys}
    exec(compile(src[start:end], FORECAST, 'exec'), ns)
    return ns


R = load_reader() if os.path.exists(FORECAST) else None
COLUMNS = ['aboveMinutes', 'excessCoreMinutes', 'aboveScarceMinutes', 'aboveCompetitionMinutes',
           'aboveOwnWidthMinutes', 'aboveUnclassifiedMinutes', 'longestJointRunMinutes']


def columns_at(minutes, e):
    """The seven stored columns of one day at threshold e from minute tuples
    (busy, scarce, competition, own width, classified); runs are within the day."""
    out = [0, 0.0, 0, 0, 0, 0, 0]
    run = 0
    for b, scarce, comp, own, classified in minutes:
        if b is None or b <= e + 1e-9:
            run = 0
            continue
        out[0] += 1
        out[1] += b - e
        out[4] += own
        out[5] += not classified
        if scarce:
            out[2] += 1
            out[3] += comp
            run += 1
            out[6] = max(out[6], run)
        else:
            run = 0
    return out


def stored_day(date, minutes, thresholds):
    rows = []
    for t in thresholds:
        c = columns_at(minutes, t)
        rows.append([c[0], round(c[1], 4)] + c[2:] if c[0] else 0)
    paired = [m for m in minutes if m[0] is not None]
    return {'date': date, 'expectedMinutes': len(minutes), 'pairedMinutes': len(paired),
            'classifiedMinutes': sum(1 for m in paired if m[4]), 'scarceMinutes': sum(1 for m in paired if m[1]),
            'exposure': rows}


def write_history(root, days, thresholds, installed=0.5, vm='vmA', population='none', profiles=('G3-95W15',), hint=None):
    """A two-month history with one state; days = {date: minutes}."""
    hist = os.path.join(root, 'history', 'SYNRD')
    os.makedirs(hist, exist_ok=True)
    by_month = {}
    for date in sorted(days):
        by_month.setdefault(date[:7], []).append(stored_day(date, days[date], thresholds))
    th = [{'value': t, 'basis': 'installedEntitlement' if t == installed else 'monthlyProfileValue',
           'profiles': [] if t == installed else ['G3-95W15']} for t in thresholds]
    for month, daily in by_month.items():
        doc = {month: {
            'MemberExposure': {vm: [{'stateStartUTC': '2026-09-01T00:00:00Z', 'pool': 'SYNRD:44', 'scope': 'user',
                                     'entitlement': installed, 'virtualProcessors': 1, 'capped': False, 'poolCeiling': 8,
                                     'populations': {population: {'profiles': list(profiles), 'thresholds': th, 'daily': daily}}}]},
            'Metadata': {'MemberExposureEvidence': {'projection': 'member-busy-exposure-1', 'columns': COLUMNS,
                                                    'status': 'complete', 'availableAt': '2026-11-12T00:00:00Z'}}}}
        if hint:
            doc[month]['MonthlyWorkloadAnalysis'] = {vm: {'Hint': hint}}
        with open(os.path.join(hist, f'nfit.hist.{month}.json'), 'w') as fh:
            json.dump(doc, fh)
    return hist


def random_minutes(rng, n=240):
    out = []
    for _ in range(n):
        if rng.random() < 0.03:
            out.append((None, False, False, False, False))
            continue
        b = round(max(0.0, rng.gauss(0.2, 0.15)), 4)
        scarce = rng.random() < 0.15
        classified = scarce or rng.random() < 0.95
        out.append((b, scarce, scarce and rng.random() < 0.8, b > 0.45, classified))
    return out


@unittest.skipUnless(R, 'nfit-forecast source not found')
class ExposureReaderTests(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='exp-reader-')
        self.rng = random.Random(20261114)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def query(self, hist, e, **kw):
        return R['exposure_query'](hist, kw.pop('vm', 'vmA'), e, **kw)

    def truth(self, days, e, start, end):
        tot = [0, 0.0, 0, 0, 0, 0, 0]
        for date, minutes in days.items():
            if start <= date < end:
                c = columns_at(minutes, e)
                for k in range(6):
                    tot[k] += c[k]
                tot[6] = max(tot[6], c[6])
        return tot

    def test_exact_at_a_stored_threshold(self):
        days = {'2026-09-%02d' % d: random_minutes(self.rng) for d in range(1, 11)}
        hist = write_history(self.root, days, [0.5, 0.3, 0.12])
        r = self.query(hist, 0.12, as_of='2026-09-11', start='2026-09-01')
        st = r['states'][0]
        self.assertEqual((r['status'], st['status'], st['days']['exact']), ('exact', 'exact', 10))
        want = self.truth(days, 0.12, '2026-09-01', '2026-09-11')
        for k, c in enumerate(COLUMNS):
            self.assertAlmostEqual(st['exposure'][c]['lower'], want[k], places=3)
            self.assertAlmostEqual(st['exposure'][c]['upper'], want[k], places=3)

    def test_a_zero_row_is_an_exact_zero(self):
        days = {'2026-09-01': [(0.1, False, False, False, True)] * 30}
        hist = write_history(self.root, days, [0.5, 0.3])
        st = self.query(hist, 0.3, as_of='2026-09-02', start='2026-09-01')['states'][0]
        self.assertEqual(st['status'], 'exact')
        self.assertTrue(all(st['exposure'][c] == {'lower': 0, 'upper': 0} for c in COLUMNS))

    def test_bounds_always_contain_the_true_value(self):
        checked = 0
        for trial in range(60):
            days = {'2026-09-%02d' % d: random_minutes(self.rng, 120) for d in range(1, 4)}
            thresholds = sorted({0.5} | {round(self.rng.uniform(0.02, 0.48), 4) for _ in range(self.rng.randint(0, 4))}, reverse=True)
            root = tempfile.mkdtemp(prefix='exp-prop-', dir=self.root)
            hist = write_history(root, days, thresholds)
            for _ in range(8):
                e = round(self.rng.uniform(0.005, 0.7), 4)
                st = self.query(hist, e, as_of='2026-09-04', start='2026-09-01')['states'][0]
                want = self.truth(days, e, '2026-09-01', '2026-09-04')
                for k, c in enumerate(COLUMNS):
                    lo, up = st['exposure'][c]['lower'], st['exposure'][c]['upper']
                    self.assertLessEqual(lo, want[k] + 1e-3, (trial, e, c, thresholds))
                    self.assertGreaterEqual(up, want[k] - 1e-3, (trial, e, c, thresholds))
                    checked += 1
        self.assertGreater(checked, 3000)

    def test_bounds_between_and_beyond_stored_thresholds(self):
        days = {'2026-09-01': random_minutes(self.rng)}
        hist = write_history(self.root, days, [0.5, 0.3, 0.12])
        mid = self.query(hist, 0.2, as_of='2026-09-02', start='2026-09-01')['states'][0]
        self.assertEqual((mid['status'], mid['boundBasis']), ('bounded', 'storedThresholds'))
        at = lambda e: columns_at(days['2026-09-01'], e)
        self.assertEqual((mid['exposure']['aboveMinutes']['lower'], mid['exposure']['aboveMinutes']['upper']),
                         (at(0.3)[0], at(0.12)[0]))
        below = self.query(hist, 0.05, as_of='2026-09-02', start='2026-09-01')['states'][0]
        self.assertEqual(below['boundBasis'], 'support')                          # nothing stored below 0.05
        self.assertEqual(below['exposure']['aboveMinutes']['upper'], below['support']['pairedMinutes'])
        above = self.query(hist, 0.9, as_of='2026-09-02', start='2026-09-01')['states'][0]
        self.assertEqual(above['exposure']['aboveMinutes']['lower'], 0)
        self.assertNotIn('againstInstalled', above)                               # not a cut

    def test_differences_against_the_installed_entitlement(self):
        days = {'2026-09-01': random_minutes(self.rng)}
        hist = write_history(self.root, days, [0.5, 0.12])
        st = self.query(hist, 0.12, as_of='2026-09-02', start='2026-09-01')['states'][0]
        p, q = columns_at(days['2026-09-01'], 0.12), columns_at(days['2026-09-01'], 0.5)
        self.assertEqual(st['againstInstalled']['newlyAboveGuaranteeMinutes'], {'lower': p[0] - q[0], 'upper': p[0] - q[0]})
        self.assertAlmostEqual(st['againstInstalled']['additionalBorrowingCoreMinutes']['lower'], p[1] - q[1], places=3)
        self.assertEqual(st['againstInstalled']['newlyExposedCompetitionMinutes']['upper'], p[3] - q[3])

    def test_window_and_as_of(self):
        days = {'2026-08-31': random_minutes(self.rng), '2026-09-01': random_minutes(self.rng), '2026-09-02': random_minutes(self.rng)}
        hist = write_history(self.root, days, [0.5, 0.12])
        r = self.query(hist, 0.12, as_of='2026-09-02', start='2026-08-31')
        self.assertEqual(r['states'][0]['days']['exact'], 2)                      # across two partitions, end exclusive
        self.assertEqual(r['window'], {'from': '2026-08-31', 'toExclusive': '2026-09-02'})
        self.assertEqual(r['coverage'], {'windowDays': 2, 'daysWithHistory': 2, 'daysWithoutHistory': 0})
        late = self.query(hist, 0.12, as_of='2026-09-06', start='2026-09-01')
        self.assertEqual(late['coverage']['daysWithoutHistory'], 3)                 # 09-03 to 09-05 not stored
        self.assertIn('(5 days, 3 not yet in history and not counted)', '\n'.join(R['format_exposure_query'](late)))
        default = self.query(hist, 0.12)
        self.assertEqual(default['window']['toExclusive'], '2026-09-03')          # the day after the last stored day
        # The partitions were written after the window: flagged as a retrospective read.
        self.assertTrue(all(p['availableAfterAsOf'] is True for p in r['states'][0]['provenance']))
        self.assertNotIn('retrospective', '\n'.join(R['format_exposure_query'](r)))   # planner text: JSON only

    def test_unavailable_answers_carry_a_reason(self):
        self.assertEqual(self.query(os.path.join(self.root, 'none'), 0.1)['reason'], 'no history directory')
        hist = write_history(self.root, {'2026-09-01': random_minutes(self.rng)}, [0.5])
        self.assertEqual(self.query(hist, 0.1, vm='vmZ')['reason'], 'the LPAR has no member exposure in history')
        r = self.query(hist, 0.1, profile='B3-95W15')
        self.assertEqual(r['status'], 'unavailable')
        self.assertIn('profile B3-95W15 is not stored', r['reason'])
        r = self.query(hist, 0.1, as_of='2026-10-02', start='2026-10-01')
        self.assertEqual((r['status'], r['reason']), ('unavailable', 'no stored day in the window'))
        text = '\n'.join(R['format_exposure_query'](r))
        self.assertIn('No answer: history holds no day for this LPAR in the period asked.', text)


    # -- planner text ------------------------------------------------------------------
    def test_planner_text_states_a_recommendation_for_each_case(self):
        day = lambda b, scarce, comp: [(b, scarce, comp, False, True)] * 20 + [(0.05, False, False, False, True)] * 20
        cases = {
            'competition': (day(0.3, True, True), 'Review before applying. The cut removes the guarantee in 20 of the 20 minutes'),
            'open pool': (day(0.3, False, False), 'No objection from the pool evidence: the pool had spare capacity'),
            'alone': (day(0.3, True, False), 'No objection from the pool evidence: the pool filled only when this LPAR filled it alone.'),
        }
        for name, (minutes, want) in cases.items():
            root = tempfile.mkdtemp(prefix='exp-text-', dir=self.root)
            hist = write_history(root, {'2026-09-01': minutes}, [0.5, 0.12])
            text = '\n'.join(R['format_exposure_query'](self.query(hist, 0.12, as_of='2026-09-02', start='2026-09-01')))
            self.assertIn('Proposal       : reduce entitlement from 0.50 to 0.12 cores', text, name)
            self.assertIn(want, text, name)
            for heading in ('  The LPAR\n', '  The pool\n', '  The LPAR and the pool together\n', '  Recommendation\n'):
                self.assertIn(heading, text, name)
            for jargon in ('credible scarcity', 'storedThresholds', 'paired', 'population'):
                self.assertNotIn(jargon, text, name)

    # -- default profile window from TIER and hint ------------------------------------
    def test_default_profile_follows_tier_then_hint(self):
        days = {'2026-09-01': random_minutes(self.rng)}
        hist = write_history(self.root, days, [0.5, 0.12])
        r = self.query(hist, 0.12)                                              # no TIER, no hint: G3, all hours
        self.assertEqual((r['profile'], r['profileSelection']['policy']['PlanningPatternSource']), ('G3-95W15', 'DEFAULT_PATTERN_G'))
        self.assertIn('Profile        : G3-95W15, all hours (tier 3, all-hours pattern by default', '\n'.join(R['format_exposure_query'](r)))
        r = self.query(hist, 0.12, tier_overrides={'vmA': 'B2'})               # a configured TIER wins, even when not stored
        self.assertEqual((r['profile'], r['status']), ('B2-98W10', 'unavailable'))
        self.assertIn('profile B2-98W10 is not stored', r['reason'])
        batch = tempfile.mkdtemp(prefix='exp-batch-', dir=self.root)
        hist = write_history(batch, days, [0.5, 0.12], population='batch', profiles=('B2-98W10', 'B3-95W15'), hint='O3')
        r = self.query(hist, 0.12, tier_overrides={'vmA': 'b2'}, tier_config='etc/nfit.vms.cfg')
        self.assertEqual((r['profile'], r['status'], r['states'][0]['populationUsed']), ('B2-98W10', 'exact', 'batch'))
        self.assertIn('configured TIER B2', '\n'.join(R['format_exposure_query'](r)))
        r = self.query(hist, 0.12)                                              # hint O3 gives only the O pattern
        self.assertEqual((r['profile'], r['profileSelection']['hint']), ('O3-95W15', 'O3'))
        r = self.query(hist, 0.12, profile='B3-95W15')                          # an explicit profile overrides
        self.assertEqual((r['profile'], r['profileSelection']['source'], r['status']), ('B3-95W15', 'requestedProfile', 'exact'))
        r = self.query(hist, 0.12, population='batch')
        self.assertEqual((r['profileSelection']['source'], r['status']), ('requestedPopulation', 'exact'))


def perl_ready():
    return (shutil.which('perl') is not None and
            subprocess.run(['perl', '-MJSON::PP', '-e1'], capture_output=True).returncode == 0)


PERL_PARITY = r'''
use strict; use warnings; use JSON::PP qw(encode_json decode_json);
my ($src_path, $cfg_path) = @ARGV;
open my $fh, '<', $src_path or die; my $src = do { local $/; <$fh> }; close $fh;
my $code = '';
for my $name (qw(_resolve_vm_tier_pattern_policy parse_vms_config _ha_clusters)) {
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "cannot extract $name\n";
    $code .= "$1\n";
}
our $MISSION_CRITICAL_PROFILE_NAME = 'P-99W1';
eval "no strict 'vars'; $code; 1" or die $@;
my $in = decode_json(do { local $/; <STDIN> });
my @profiles = map { { name => $_->[0], csv_output => $_->[1] } } @{ $in->{profiles} };
my @out;
for my $case (@{ $in->{cases} }) {
    my $r = _resolve_vm_tier_pattern_policy($case->[0], $case->[1], \@profiles);
    $r->{ResolvedPolicyTier} = "$r->{ResolvedPolicyTier}";
    push @out, $r;
}
my $overrides = parse_vms_config($cfg_path)->{tiers};
print encode_json({ resolved => \@out, overrides => $overrides });
'''


@unittest.skipUnless(R and perl_ready(), 'nfit-forecast source or perl with JSON unavailable')
class TierResolutionParity(unittest.TestCase):
    # The shared case table: both programs must resolve every case identically.
    CONFIGURED = ['', 'G1', 'O2', 'B4', 'P', 'P-99W1', 'g2', ' o 1 ', 'G3-custom', 'X9', 'G5', 'O', 'B0', 'PX']
    HINTS = ['', 'O3', 'B1', 'G2', 'P', 'VIO', 'o', '  b2 ', 'X1', None]

    def test_resolver_and_tier_file_match_nfit_profile(self):
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.read(os.path.join(REPO, 'etc', 'nfit.profiles.cfg'), encoding='utf-8')
        profiles = [[n, parser.get(n, 'csv_output', fallback='true').strip().lower() != 'false'] for n in parser.sections()]
        profiles.append(['P-99W1', True])
        cases = [[c, h or ''] for c in self.CONFIGURED for h in self.HINTS]
        cfg = os.path.join(tempfile.mkdtemp(prefix='tier-cfg-'), 'nfit.vms.cfg')
        with open(cfg, 'w') as fh:
            fh.write('# comment\n[vmA]\ntier = g2   # trailing\n[ vm B ]\n; note\nTIER=O 3\n[vmC]\nother = 1\n'
                     '[vmD]\ntier = P\ntier = B1\n[vmE]\n\ntier= b4-custom\n')
        p = subprocess.run(['perl', '-e', PERL_PARITY, os.path.join(REPO, 'nfit-profile'), cfg],
                           input=json.dumps({'profiles': profiles, 'cases': cases}), capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        perl = json.loads(p.stdout)
        defs = [tuple(x) for x in profiles]
        for (configured, hint), want in zip(cases, perl['resolved']):
            got = R['resolve_vm_tier_pattern_policy'](configured, hint, defs)
            got['ResolvedPolicyTier'] = str(got['ResolvedPolicyTier'])
            self.assertEqual(got, want, (configured, hint))
        self.assertEqual(R['parse_vm_tier_overrides'](cfg), perl['overrides'])
        self.assertEqual(len(cases), 140)


# -- end to end on history written by nfit-profile ----------------------------------------
try:
    sys.path.insert(0, HERE)
    import member_exposure_test as MX
except Exception:              # the nfit-profile fixture is optional here
    MX = None


@unittest.skipUnless(R and MX and MX.perl_ready(), 'nfit-profile fixture or perl unavailable')
class ExposureReaderEndToEnd(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        MX.MemberExposureTests.roots = []
        cls.root = MX.MemberExposureTests.make_root()
        MX.MemberExposureTests.run_profile(cls.root, '--update-history', '--min-history-days', '1', '--pool-history', '--force')
        cls.hist = os.path.join(cls.root, 'history', MX.SERIAL)
        with open(os.path.join(cls.hist, f'nfit.hist.{MX.MONTH}.json')) as fh:
            cls.month = json.load(fh)[MX.MONTH]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def reference_totals(self, vm, pop, e):
        ref = MX.reference(vm, pop, [e])
        tot = [sum(v[4][0][k] for v in ref.values()) for k in range(6)] + [max(v[4][0][6] for v in ref.values())]
        return tot

    def test_exact_at_stored_threshold_and_bounded_between(self):
        th = self.month['MemberExposure']['vmA'][0]['populations']['none']['thresholds']
        stored = th[1]['value']                                             # the highest stored profile value
        exact = R['exposure_query'](self.hist, 'vmA', stored, as_of='2026-09-03', start='2026-09-01', population='none')['states'][0]
        self.assertEqual(exact['status'], 'exact')
        want = self.reference_totals('vmA', 'none', stored)
        for k, c in enumerate(COLUMNS):
            self.assertAlmostEqual(exact['exposure'][c]['lower'], want[k], places=3, msg=c)
        between = round((th[1]['value'] + th[2]['value']) / 2, 4) if len(th) > 2 else round(stored - 0.05, 4)
        r = R['exposure_query'](self.hist, 'vmA', between, as_of='2026-09-03', start='2026-09-01', population='none')['states'][0]
        self.assertEqual(r['status'], 'bounded')
        want = self.reference_totals('vmA', 'none', between)
        for k, c in enumerate(COLUMNS):
            self.assertLessEqual(r['exposure'][c]['lower'], want[k] + 1e-3, c)
            self.assertGreaterEqual(r['exposure'][c]['upper'], want[k] - 1e-3, c)


if __name__ == '__main__':
    unittest.main()
