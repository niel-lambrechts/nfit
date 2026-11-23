#!/usr/bin/env python3
# ==============================================================================
# Tests for event-window cut protection in a seasonal run (v10 stage 3.6):
# the evidence of the event's own past windows, read from the ContestedBusy
# history, enters the sizing chain of a seasonal model.
#
# The synthetic system of member_exposure_test (two days; an LPAR whose busy
# work met pool competition on the first) with an event dated on that day:
#   - history written with --update-history --pool-history, then the seasonal
#     run: the cut is limited by the event's evidence and says so;
#   - --no-cut-protection and a system without contested history give the
#     figures of a seasonal run as it was before, and the rationale says the
#     evidence was not used;
#   - only the event's windows that history can hold and that ended by the
#     run's date are read;
#   - a past window lived capped or in another pool is not evidence.
# ==============================================================================
import csv
import glob
import json
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import member_exposure_test as M
import contested_reader_test as C

EVENT = ('[Global]\nmin_history_days = 1\nseasonal_lookback_days = 1\n'
         '[ev]\nmodel = predictive_peak\npriority = 10\ndates = %s\nbaseline_period_days = 1\n')
COLUMNS = ('G3-95W15', 'O3-95W15', 'B3-95W15', 'NFIT - ENT', 'NFIT - VCPU')


@unittest.skipUnless(M.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class EventWindowTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fixture = M.MemberExposureTests
        cls.fixture.roots = []
        cls.with_history = cls.seasonal('2026-09-01:2026-09-01', history=True)
        cls.switched_off = cls.seasonal('2026-09-01:2026-09-01', history=True, flags=['--no-cut-protection'])
        cls.no_history = cls.seasonal('2026-09-01:2026-09-01', history=False)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    @classmethod
    def seasonal(cls, dates, history, flags=()):
        root = cls.fixture.make_root()
        with open(os.path.join(root, 'etc', 'nfit.seasonality.cfg'), 'w') as fh:
            fh.write(EVENT % dates)
        if history:
            cls.fixture.run_profile(root, '--update-history', '--min-history-days', '1', '--pool-history', '--force')
        run = cls.fixture.run_profile(root, '--apply-seasonality', 'ev', *flags)
        path, = glob.glob(os.path.join(root, 'output', '*.ev.*.csv'))
        with open(path) as fh:
            rows = {row['VM']: row for row in csv.DictReader(fh)}
        log, = glob.glob(os.path.join(root, 'output', '*.ev.*.log'))
        with open(log) as fh:
            return {'rows': rows, 'log': fh.read(), 'stderr': run.stderr}

    def figures(self, run, vm):
        return {k: run['rows'][vm][k] for k in COLUMNS}

    def test_a_cut_is_limited_by_the_evidence_of_the_past_event_window(self):
        row = self.with_history['rows']['vmB']
        before = self.switched_off['rows']['vmB']
        self.assertLess(float(before['G3-95W15']), float(row['G3-95W15']))             # the bound raised the cut's target
        self.assertLessEqual(float(row['G3-95W15']), float(row['Current - ENT']))      # never above the installed entitlement
        self.assertRegex(row['NFIT - Action'], r'^Event window: Cut limited to %s \(profile %s\): busy work reached this level while '
                         r'pool 7 was contested, on 40 minutes over 1 day of past event windows\.' % (
                             re.escape(row['G3-95W15']), re.escape(before['G3-95W15'])))
        self.assertNotIn('past event windows', before['NFIT - Action'])

    def test_the_rationale_states_the_windows_and_the_lpars_with_evidence(self):
        log = self.with_history['log']
        self.assertIn('Event-Window Evidence (event ev)', log)
        self.assertIn('Past windows   : 1 (2026-09-01)', log)
        self.assertIn('LPARs          : 2 with work during pool competition, 1 without competition, 0 with too little evidence, 0 without evidence', log)
        self.assertRegex(log, r'vmB\s+contested: 40 observations over 1 day in 1 of 1 window; 100\.0% of eligible time classified')
        self.assertNotRegex(log, r'\n  vmS\s+contested')                               # its competition fell on the second day
        self.assertNotIn('Event-Window Evidence', self.switched_off['log'])

    def test_without_evidence_the_event_is_sized_as_before(self):
        run, reason = self.no_history, 'history holds no contested record'
        self.assertIn('Event-window evidence not used: ' + reason, run['stderr'])
        self.assertIn('Not used: ' + reason, run['log'])
        self.assertIn('The event is sized without cut protection, as before.', run['log'])
        # The same day's event with and without history differs only by the evidence: without it, the
        # figures are those of the run with cut protection switched off.
        for vm in ('vmA', 'vmB', 'vmS', 'vmX'):
            self.assertEqual(self.figures(self.no_history, vm), self.figures(self.switched_off, vm), vm)

    def test_an_lpar_without_competition_in_the_window_keeps_no_borrowing_allowance_it_did_not_need(self):
        # vmS met no competition on the event day and its evidence is whole: the absence is supported, so
        # the pool-borrowing allowance is withheld as the standard models withhold it.
        self.assertLessEqual(float(self.with_history['rows']['vmS']['G3-95W15']), float(self.switched_off['rows']['vmS']['G3-95W15']))
        self.assertNotIn('Cut limited', self.with_history['rows']['vmS']['NFIT - Action'])

    def test_only_past_windows_that_history_can_hold_are_read(self):
        with open(os.path.join(REPO, 'nfit-profile'), encoding='utf-8') as fh:
            source = fh.read()
        subs = ''.join(re.search(r'^(sub %s\s*\{.*?^\})' % name, source, re.S | re.M).group(1) + '\n'
                       for name in ('_event_past_windows', 'find_all_historical_periods', '_get_recurring_monthly_period'))
        script = ('use strict; use warnings; use JSON; use Time::Piece; use Time::Seconds;\n' + subs +
                  'my $cfg = { dates => "2026-05-29:2026-05-31, 2026-08-30:2026-08-31, 2026-09-29:2026-09-30, 2026-10-30:2026-10-31" };\n'
                  'print JSON->new->canonical->encode([ map { _event_past_windows("ev", $cfg, "2026-08", Time::Piece->strptime($_, "%Y-%m-%d")) } '
                  '"2026-10-15", "2026-10-31", "2026-08-01" ]);')
        out = subprocess.run(['perl', '-e', script], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        mid_october, end_october, early = json.loads(out.stdout)
        # May lies before the first month of history; October's window has not ended by mid-October.
        self.assertEqual(mid_october, [['2026-08-30', '2026-09-01'], ['2026-09-29', '2026-10-01']])
        self.assertEqual(end_october, mid_october + [['2026-10-30', '2026-11-01']])
        self.assertEqual(early, [])

    def test_a_window_lived_capped_or_in_another_pool_is_not_evidence(self):
        with open(os.path.join(REPO, 'nfit-profile'), encoding='utf-8') as fh:
            source = fh.read()
        names = C.PERL_SUBS + ('_contested_event_evidence',)
        subs = ''.join(re.search(r'^(sub %s\s*\{.*?^\})' % name, source, re.S | re.M).group(1) + '\n' for name in names)
        consts = ''.join(re.search(r'^(my \$%s\s*=.*?;)' % name, source, re.M).group(1) + '\n'
                         for name in ('CONTESTED_BUSY_PROJECTION', 'CONTESTED_BUSY_READER', 'MEMBER_EXPOSURE_PROJECTION',
                                      'PEAK_UPLIFT_GATE_MIN_CLASSIFIED', 'PEAK_UPLIFT_GATE_MIN_DAY_CLASSIFIED',
                                      'CONTESTED_LEVEL_MIN_MINUTES', 'CONTESTED_LEVEL_MIN_DAYS'))
        script = ('use strict; use warnings; use JSON; use Time::Piece; use List::Util qw(max min);\n' + consts + subs +
                  'local $/; my $in = decode_json(<STDIN>); '
                  'my $same = sub { my ($s) = @_; return 0 if $s->{capped}; return ((split /:/, ($s->{pool} // ""))[-1] // "") eq "7" ? 1 : 0; }; '
                  'print JSON->new->canonical->encode(_contested_event_evidence($in->{months}, "lpar", "none", $in->{windows}, { days => 1, state => $same }));')

        def evidence(pool, capped):
            m = C.month([('2026-09-29', 1440, 1440, 1440), ('2026-09-30', 1440, 1440, 1440)], readings={'2026-09-30': [[0.9, 1.2, 20]]})
            m['MemberExposure']['lpar'][0].update(pool='SYS:%s' % pool, capped=capped)
            out = subprocess.run(['perl', '-e', script], capture_output=True, text=True, timeout=60,
                                 input=json.dumps({'months': [['2026-09', m]], 'windows': [['2026-09-29', '2026-10-01']]}))
            self.assertEqual(out.returncode, 0, out.stderr[-800:])
            return json.loads(out.stdout)

        e = evidence(7, False)
        self.assertEqual((e['status'], e['observations']['contested'], len(e['dayRows'])), ('contested', 20, 2))
        for pool, capped in ((9, False), (7, True)):
            e = evidence(pool, capped)
            self.assertEqual((e['status'], e['readings'], e['dayRows']), ('unavailable', [], []))
            self.assertEqual(e['reasons'], {'configuration state not comparable with the current one': 2})
            self.assertEqual((e['eligibleMinutes'], e['classifiedMinutes']), (2880, 0))     # eligible days, nothing usable


if __name__ == '__main__':
    unittest.main(verbosity=2)
