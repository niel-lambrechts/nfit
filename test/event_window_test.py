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
#   - evidence belongs to the LPAR's current configuration state only: an
#     earlier state is unavailable, never quiet; a restaged cache does not
#     disown an unchanged LPAR's history;
#   - fine and coarse occurrences are not pooled into one percentile;
#   - a missing event day prevents the no-competition verdict.
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
         '[ev]\nmodel = %s\npriority = 10\ndates = %s\nbaseline_period_days = %d\nmin_historical_years = 0\n'
         'min_historical_peaks = %d\nmin_historical_periods = %d\n')
def month_of(analysis, values, month, days=28):
    """A month's workload analysis for a synthetic earlier month: the given analysis with its daily profile series
    re-dated into that month, each day at the profile's value (a level month)."""
    out = json.loads(json.dumps(analysis))
    for vm, block in out.items():
        if isinstance(block, dict) and vm in values:
            block['DailyProfileSeries'] = {profile: {'%s-%02d' % (month, d): v for d in range(1, days + 1)} for profile, v in values[vm].items()}
    return out


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
        # The same event when the model asks for three past occurrences and history holds one.
        cls.too_few = cls.seasonal('2026-09-01:2026-09-01', history=True, occurrences=3)
        # Recency decay assembles its figure outside the sizing chain.
        cls.recency = {name: cls.seasonal('2026-09-01:2026-09-01', model='recency_decay', **kw)
                       for name, kw in (('with', {'history': True}), ('off', {'history': True, 'flags': ['--no-cut-protection']}),
                                        ('none', {'history': False}))}
        # Multiplicative seasonal needs an earlier occurrence in history: twelve days of data, the event on the
        # first and the last, and a synthetic August partition with the event's peak and baseline.
        dates = '2026-08-01:2026-08-01, 2026-09-01:2026-09-01, 2026-09-12:2026-09-12'
        cls.multiplicative = {name: cls.seasonal(dates, history=True, model='multiplicative_seasonal', days=12, baseline=8, prior=True, flags=flags)
                              for name, flags in (('with', []), ('off', ['--no-cut-protection']))}

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    @classmethod
    def seasonal(cls, dates, history, flags=(), model='predictive_peak', days=2, baseline=1, prior=False, occurrences=1, peak=1.05):
        M.DAYS = days
        try:
            root = cls.fixture.make_root()
        finally:
            M.DAYS = 2
        with open(os.path.join(root, 'etc', 'nfit.seasonality.cfg'), 'w') as fh:
            fh.write(EVENT % (model, dates, baseline, occurrences, occurrences))
        if history:
            cls.fixture.run_profile(root, '--update-history', '--min-history-days', '1', '--pool-history', '--force')
        if prior:
            # A synthetic earlier occurrence: the event's peak above its baseline by the given factor, for every profile.
            # At 1.05 the model's demand stays below the level the past window protects, so the protection binds.
            folder = os.path.join(root, 'history', M.SERIAL)
            with open(os.path.join(folder, 'nfit.hist.2026-09.json')) as fh:
                analysis = json.load(fh)['2026-09']['MonthlyWorkloadAnalysis']
            values = {vm: {k: float(v) for k, v in (block.get('ProfileValues') or {}).items()} for vm, block in analysis.items()}
            august = {'2026-08': {
                'Metadata': {'ProcessedStartDate': '2026-08-01', 'ProcessedEndDate': '2026-08-31', 'ProcessedDays': 31},
                'MonthlyWorkloadAnalysis': month_of(analysis, values, '2026-08', 31),
                'SeasonalEventSnapshots': {'ev': {'eventName': 'ev', 'periodEndDate': '2026-08-01', 'generatedOn': '2026-08-02T00:00:00', 'results': {
                    'HistoricPeak': {vm: {k: round(v * peak, 4) for k, v in pv.items()} for vm, pv in values.items()},
                    'HistoricBaseline': values}}}}}
            with open(os.path.join(folder, 'nfit.hist.2026-08.json'), 'w') as fh:
                json.dump(august, fh)
            # The month before it, level: the daily values the August occurrence's baseline is read from.
            july = {'2026-07': {'Metadata': {'ProcessedStartDate': '2026-07-01', 'ProcessedEndDate': '2026-07-31', 'ProcessedDays': 31},
                                'MonthlyWorkloadAnalysis': month_of(analysis, values, '2026-07', 31)}}
            with open(os.path.join(folder, 'nfit.hist.2026-07.json'), 'w') as fh:
                json.dump(july, fh)
        run = cls.fixture.run_profile(root, '--apply-seasonality', 'ev', *flags)
        path, = glob.glob(os.path.join(root, 'output', '*.ev.*.csv'))
        with open(path) as fh:
            rows = {row['VM']: row for row in csv.DictReader(fh)}
        log, = glob.glob(os.path.join(root, 'output', '*.ev.*.log'))
        snapshot = {}
        copies = glob.glob(os.path.join(root, 'output', '*.ev.proposal.*.json'))
        if copies:
            with open(copies[0]) as fh:
                snapshot = json.load(fh)
        stored = glob.glob(os.path.join(root, 'stage', M.SERIAL, '.nfit.store', 'proposals', 'v1', 'ev', '*.json'))
        with open(log) as fh:
            return {'rows': rows, 'log': fh.read(), 'stderr': run.stderr, 'snapshot': snapshot, 'stored': stored, 'copies': copies}

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

    def test_a_seasonal_run_stores_its_proposal_with_the_evidence_and_the_event_window_plan(self):
        for run in (self.with_history, self.recency['with'], self.multiplicative['with'], self.no_history, self.switched_off):
            self.assertEqual((len(run['copies']), len(run['stored'])), (1, 1))             # one snapshot, filed twice
            with open(run['stored'][0], 'rb') as a, open(run['copies'][0], 'rb') as b:
                self.assertEqual(a.read(), b.read())
        doc = self.with_history['snapshot']
        self.assertEqual((doc['model'], doc['schemaVersion']), ('ev', 2))
        member = doc['members']['vmB']
        record = member['profiles']['G3-95W15']['cutProtection']
        self.assertEqual((record['evidence'], record['status'], record['eventWindows']['verdict']), ('eventWindows', 'bound', 'contested'))
        self.assertEqual((record['eventWindows']['usableWindows'], record['eventWindows']['minOccurrences']), (1, 1))
        self.assertEqual(member['eventEvidence']['none']['verdict'], 'contested')
        self.assertEqual(doc['members']['vmS']['eventEvidence']['none']['verdict'], 'noCompetition')
        self.assertNotIn('eventEvidence', doc['members']['vmX'])                          # dedicated: not assessed
        self.assertEqual((member['eventWindow']['pool'], member['eventWindow']['planningProfile']), (7, 'G3-95W15'))
        self.assertEqual(member['eventWindow']['entitlement'], float(self.with_history['rows']['vmB']['NFIT - ENT']))
        # Without evidence the snapshot carries the figures and no evidence record.
        self.assertNotIn('cutProtection', self.no_history['snapshot']['members']['vmB']['profiles']['G3-95W15'])
        log = self.with_history['log']
        self.assertIn('Event-Window Plan (event ev)', log)
        self.assertIn('Event requirements by pool and for the frame (event ev)', log)
        self.assertRegex(log, r'\n  Frame\s+3\s+unknown\s+3\.47\s+1\s+0\.59  not assessed')      # no default pool in this system
        # (3.47: 2.00 + 0.97 + 0.50. The third is the model's forecast; the engine's own profile value, 0.52, was sized before.)
        # vmS met no competition in the past window: its pool-borrowing allowance is withheld, and the plan shows the 0.59
        # cores of borrowing its measured peak relies on. (The measured peak is kept in a predictive run; it was lost before.)
        self.assertRegex(log, r'\n  9\s+1\s+2\.00\s+0\.50\s+1\s+0\.59  guarantees and relied-on borrowing fit under the pool maximum')
        self.assertRegex(log, r'\n  7\s+2\s+4\.00\s+2\.97\s+0\s+0\.00  guarantees and relied-on borrowing fit under the pool maximum')
        self.assertIn('whether the forecast level will meet competition that past occurrences did not', log)

    def test_one_quiet_occurrence_is_not_enough_when_the_model_requires_three(self):
        # vmS met no competition on the one past occurrence in history. With a minimum of one it is found without
        # competition; with the model's minimum of three it has too little evidence, and its figures are those of
        # a seasonal run as it was. What was seen still counts: vmB's cut is limited either way.
        self.assertIn('LPARs          : 2 with work during pool competition, 0 without competition, 1 with too little evidence', self.too_few['log'])
        self.assertRegex(self.too_few['log'], r'vmS\s+too little evidence: 1 usable occurrence of 1 \(minimum 3\)')
        self.assertIn("accepted from 3 usable occurrences (the model's own minimum)", self.too_few['log'])
        self.assertEqual(self.figures(self.too_few, 'vmS'), self.figures(self.switched_off, 'vmS'))
        self.assertEqual(self.figures(self.too_few, 'vmB'), self.figures(self.with_history, 'vmB'))

    def test_usable_occurrences_are_counted_for_the_lpar_not_for_the_system(self):
        quiet = lambda *months: [[m, self.stored(days, from_utc=m + '-01T00:00:00Z')] for m, days in months]
        aug = [('2026-08-30', 1440, 1440, 1440), ('2026-08-31', 1440, 1440, 1440)]
        windows = [['2026-08-30', '2026-09-01']] + self.WINDOWS
        three = quiet(('2026-08', aug), ('2026-09', self.SEPT), ('2026-10', self.OCT))
        t, e = self.trackers(three, windows, min_occurrences=3)
        self.assertEqual((t['status'], e['usableWindows'], e['minOccurrences'], e['occurrencesShort']), ('noCompetition', 3, 3, False))
        # Three occurrences lie in the system's history; for this LPAR one is unusable.
        cases = {
            'an earlier configuration state': [['2026-08', self.stored(aug, virtualProcessors=6, state='2026-07-01T00:00:00Z',
                                                                       from_utc='2026-08-01T00:00:00Z')]] + three[1:],
            'a day missing from history': [['2026-08', self.stored(aug[:1], from_utc='2026-08-01T00:00:00Z')]] + three[1:],
            'a day without PhysB': [['2026-08', self.stored([aug[0], ('2026-08-31', 1440, 0, 0)], from_utc='2026-08-01T00:00:00Z')]] + three[1:],
            'a thinly classified day': [['2026-08', self.stored([aug[0], ('2026-08-31', 1440, 1440, 1000)], from_utc='2026-08-01T00:00:00Z')]] + three[1:],
        }
        for name, months in cases.items():
            t, e = self.trackers(months, windows, min_occurrences=3)
            self.assertEqual((t['status'], e['usableWindows'], e['occurrencesShort']), ('insufficient', 2, True), name)
            self.assertEqual([w['usable'] for w in e['windows']], [False, True, True], name)
        # Two usable occurrences asked of a model that needs three: too few, though each is whole.
        t, e = self.trackers(three[1:], self.WINDOWS, min_occurrences=3)
        self.assertEqual((t['status'], e['usableWindows'], e['occurrencesShort']), ('insufficient', 2, True))
        self.assertEqual(self.trackers(three[1:], self.WINDOWS, min_occurrences=2)[0]['status'], 'noCompetition')
        # Too few occurrences never hides what was seen.
        busy = [['2026-09', self.stored(self.SEPT, self.BUSY_SEPT)]]
        t, e = self.trackers(busy, self.WINDOWS[:1], min_occurrences=3)
        self.assertEqual((t['status'], t['contested'], e['occurrencesShort']), ('contested', 12, True))

    def test_recency_decay_is_protected_after_its_requirement_is_assembled(self):
        row, before = self.recency['with']['rows']['vmB'], self.recency['off']['rows']['vmB']
        self.assertLess(float(before['G3-95W15']), float(row['G3-95W15']))
        self.assertLessEqual(float(row['G3-95W15']), float(row['Current - ENT']))
        self.assertRegex(row['NFIT - Action'], r'Cut limited to %s \(profile %s\): busy work reached this level while pool 7 was contested, '
                         r'on 40 minutes over 1 day of past event windows\.' % (re.escape(row['G3-95W15']), re.escape(before['G3-95W15'])))
        self.assertEqual(row['NFIT - ENT'], row['G3-95W15'])                           # the pair follows the protected figure
        # An LPAR whose requirement is not a cut below a contested level is left exactly as the model had it.
        for vm in ('vmS', 'vmX'):
            self.assertEqual(self.figures(self.recency['with'], vm), self.figures(self.recency['off'], vm), vm)
        # Without evidence in history the model's figures are those of the run with protection switched off.
        for vm in ('vmA', 'vmB', 'vmS', 'vmX'):
            self.assertEqual(self.figures(self.recency['none'], vm), self.figures(self.recency['off'], vm), vm)

    def test_multiplicative_seasonal_receives_the_protection(self):
        row, before = self.multiplicative['with']['rows']['vmB'], self.multiplicative['off']['rows']['vmB']
        self.assertTrue(before['G3-95W15'] and row['G3-95W15'])                        # the model produced figures at all
        self.assertLess(float(before['G3-95W15']), float(row['G3-95W15']))
        self.assertRegex(row['NFIT - Action'], r'^Event window: Cut limited to %s \(profile %s\): .* over 1 day of past event windows\.'
                         % (re.escape(row['G3-95W15']), re.escape(before['G3-95W15'])))
        # Three past windows fall in the months history holds. August's partition has no contested record: its
        # day is a day without evidence, so no LPAR is found to be without competition.
        log = self.multiplicative['with']['log']
        self.assertIn('Past windows   : 3 (2026-08-01, 2026-09-01, 2026-09-12)', log)
        self.assertRegex(log, r'LPARs          : \d+ with work during pool competition, 0 without competition, [1-9]\d* with too little evidence')
        self.assertRegex(log, r'vmB\s+contested: 40 observations over 1 day in 1 of 3 windows; .*, 1 day missing from history')
        self.assertEqual(self.figures(self.multiplicative['with'], 'vmX'), self.figures(self.multiplicative['off'], 'vmX'))

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

    # -- the evidence as the sizing chain receives it (nfit-profile's tracker builder) -------------
    FACTS = {'entitlement': 2.0, 'virtualProcessors': 4, 'capped': False, 'pool': 'SYS:7', 'poolCeiling': 4.0, 'smt': 8}
    START = '2026-08-01T00:00:00Z'                    # the start history recorded for the state

    def trackers(self, months, windows, start_epoch=1785542400, metadata=None, min_occurrences=1):
        """nfit-profile's _event_protection_trackers for one LPAR in its current state (start_epoch is
        2026-08-01T00:00:00Z unless given): per population the tracker's figures, and the summary."""
        with open(os.path.join(REPO, 'nfit-profile'), encoding='utf-8') as fh:
            source = fh.read()
        names = C.PERL_SUBS + ('_contested_event_evidence', '_event_protection_trackers', '_pool_iso', '_contested_floor_for',
                               '_contested_level', '_protection_share_of', '_protection_bound', '_evidence_cadence_of', '_contested_level_on')
        subs = ''.join(re.search(r'^(sub %s\s*\{.*?^\})' % name, source, re.S | re.M).group(1) + '\n' for name in names)
        consts = ''.join(re.search(r'^(my [$%%@]%s\b[^;]*;)' % name, source, re.S | re.M).group(1) + '\n'
                         for name in ('CONTESTED_BUSY_PROJECTION', 'CONTESTED_BUSY_READER', 'MEMBER_EXPOSURE_PROJECTION',
                                      'PEAK_UPLIFT_GATE_MIN_CLASSIFIED', 'PEAK_UPLIFT_GATE_MIN_DAY_CLASSIFIED',
                                      'CONTESTED_LEVEL_MIN_MINUTES', 'CONTESTED_LEVEL_MIN_DAYS', 'CUT_PROTECTION_TOP_READINGS',
                                      'DEFAULT_PERCENTILE', 'CONTESTED_LEVEL_RULE', 'contested_level_flag', 'CUT_PROTECTION_SHARE',
                                      'MEMBER_EXPOSURE_COLUMNS'))
        script = ('use strict; use warnings; use JSON; use Time::Piece; use List::Util qw(max min sum0); use Scalar::Util qw(looks_like_number);\n'
                  + consts + subs + r"""
            local $/; my $in = decode_json(<STDIN>);
            my ($trackers, $summary) = _event_protection_trackers($in->{months}, $in->{db}, { lpar => 1 }, $in->{asof}, $in->{windows},
                                                                  { none => [ 'G3-95W15' ] }, $in->{min});
            my @out = map { my $t = $_; my $floor = _contested_floor_for($t, 'G3-95W15', '-p 95');
                            { cadence => _evidence_cadence_of($t), contested => $t->{protection}{contested},
                              days => scalar(keys %{ $t->{protection}{days} }), observations => scalar(@{ $t->{protection}{all} }),
                              frontier => [ map { [ $_->[0], $_->[1] ] } @{ $t->{protection}{frontier} } ],
                              bound => _protection_bound($t->{protection}, 0.4, $t->{entitlement}),
                              floor => ($floor ? $floor->{level} : undef), status => $t->{eventEvidence}{status} } } @$trackers;
            print JSON->new->canonical->encode({ trackers => \@out, summary => $summary->{lpar}{none} });
        """)
        md = {'entitlement': 2.0, 'virtual_cpus': 4, 'capped': 0, 'pool_id': 7, 'pool_cpu': 4.0, 'smt': 8}
        md.update(metadata or {})
        db = {'lpar': [{'start_epoch': start_epoch, 'end_epoch': 1793491140, 'metadata': md}]}
        out = subprocess.run(['perl', '-e', script], capture_output=True, text=True, timeout=60,
                             input=json.dumps({'months': months, 'db': db, 'asof': 1793491200, 'windows': windows, 'min': min_occurrences}))
        self.assertEqual(out.returncode, 0, out.stderr[-1500:])
        result = json.loads(out.stdout)
        return (result['trackers'][0] if result['trackers'] else None), result['summary']

    def stored(self, days, readings=None, cadence=1, from_utc=None, state=None, **facts):
        """One stored month with one configuration state (FACTS unless overridden; smt=None leaves it out)."""
        m = C.month(days, readings=readings, cadence=cadence, state=state or self.START)
        entry = m['MemberExposure']['lpar'][0]
        entry.update({k: v for k, v in dict(self.FACTS, **facts).items() if v is not None})
        if from_utc:
            entry['fromUTC'] = m['ContestedBusy']['lpar'][0]['fromUTC'] = from_utc
        return m

    @staticmethod
    def joined(first, second):
        """Two states in one month."""
        for block in ('MemberExposure', 'ContestedBusy'):
            first[block]['lpar'] += second[block]['lpar']
        return first

    SEPT = [('2026-09-29', 1440, 1440, 1440), ('2026-09-30', 1440, 1440, 1440)]
    OCT = [('2026-10-30', 1440, 1440, 1440), ('2026-10-31', 1440, 1440, 1440)]
    WINDOWS = [['2026-09-29', '2026-10-01'], ['2026-10-30', '2026-11-01']]
    BUSY_SEPT = {'2026-09-29': [[0.9, 1.2, 8]], '2026-09-30': [[1.0, 1.3, 4]]}
    BUSY_OCT = {'2026-10-30': [[1.1, 1.5, 6]], '2026-10-31': [[0.8, 0.9, 2]]}

    def test_the_current_state_is_recognised_after_a_restage_and_its_windows_are_pooled(self):
        months = [['2026-09', self.stored(self.SEPT, self.BUSY_SEPT)],
                  ['2026-10', self.stored(self.OCT, self.BUSY_OCT, from_utc='2026-10-01T00:00:00Z')]]
        # The same state, staged again later: its start has moved; everything recorded of it is unchanged.
        for start_epoch in (1785542400, 1788220800):
            t, e = self.trackers(months, self.WINDOWS, start_epoch=start_epoch)
            self.assertEqual((t['status'], t['contested'], t['observations'], t['days'], t['cadence']), ('contested', 20, 20, 4, 1))
            self.assertEqual(t['frontier'], [[1.1, 1.5]])
            self.assertEqual(t['bound'], 1.26)                                         # 1.1 + 0.4 x 0.4
            self.assertIsNotNone(t['floor'])                                           # 20 observations on 4 days resolve a 95th percentile
            self.assertEqual(e['reasons'], {})

    def test_evidence_from_before_a_configuration_change_is_unavailable_not_quiet(self):
        # A VP reduction between the two occurrences: September's readings belong to another configuration.
        months = [['2026-09', self.stored(self.SEPT, self.BUSY_SEPT, virtualProcessors=6)],
                  ['2026-10', self.stored(self.OCT, state='2026-10-05T00:00:00Z', from_utc='2026-10-05T00:00:00Z')]]
        t, e = self.trackers(months, self.WINDOWS, start_epoch=1791158400)             # the state that began on 5 October
        self.assertEqual((t['status'], t['contested'], t['frontier'], t['floor']), ('insufficient', 0, [], None))
        self.assertEqual(e['reasons'], {'not the current configuration state': 2})
        self.assertEqual((e['windows'][0]['status'], e['windowsWithEvidence'], e['coverage']), ('unavailable', 1, 0.5))
        # The same for each recorded fact, and for a state whose SMT mode was not recorded.
        for change in ({'entitlement': 1.5}, {'pool': 'SYS:9'}, {'poolCeiling': 6.0}, {'smt': 4}, {'capped': True}, {'smt': None}):
            months[0][1] = self.stored(self.SEPT, self.BUSY_SEPT, state='2026-07-01T00:00:00Z', **change)
            t, e = self.trackers(months, self.WINDOWS, start_epoch=1791158400)
            self.assertEqual((t['status'], t['contested']), ('insufficient', 0), change)

    def test_an_event_crossing_a_state_change_uses_the_days_of_the_current_state_only(self):
        before = self.stored([('2026-10-30', 1440, 1440, 1440)], {'2026-10-30': [[1.6, 1.9, 30]]}, virtualProcessors=6, from_utc='2026-10-01T00:00:00Z')
        after = self.stored([('2026-10-31', 1440, 1440, 1440)], {'2026-10-31': [[0.8, 0.9, 2]]}, state='2026-10-31T00:00:00Z', from_utc='2026-10-31T00:00:00Z')
        t, e = self.trackers([['2026-10', self.joined(before, after)]], [['2026-10-30', '2026-11-01']], start_epoch=1793404800)
        self.assertEqual((t['status'], t['contested'], t['frontier']), ('contested', 2, [[0.8, 0.9]]))
        self.assertEqual(e['reasons'], {'not the current configuration state': 1})
        # A configuration changed and changed back: what lies before the change is another state.
        months = [['2026-09', self.stored(self.SEPT, self.BUSY_SEPT)],
                  ['2026-10', self.joined(self.stored([('2026-10-30', 1440, 1440, 1440)], virtualProcessors=6, state='2026-10-10T00:00:00Z',
                                                      from_utc='2026-10-10T00:00:00Z'),
                                          self.stored([('2026-10-31', 1440, 1440, 1440)], state='2026-10-31T00:00:00Z', from_utc='2026-10-31T00:00:00Z'))]]
        t, e = self.trackers(months, self.WINDOWS, start_epoch=1793404800)
        self.assertEqual((t['status'], t['contested']), ('insufficient', 0))
        self.assertEqual(e['reasons'], {'not the current configuration state': 3})

    def test_fine_and_coarse_occurrences_are_not_pooled_into_one_percentile(self):
        coarse = [('2026-10-30', 1440, 96, 96), ('2026-10-31', 1440, 96, 96)]
        months = [['2026-09', self.stored(self.SEPT, self.BUSY_SEPT)], ['2026-10', self.stored(coarse, self.BUSY_OCT, cadence=15)]]
        t, e = self.trackers(months, self.WINDOWS)
        self.assertEqual((e['mixedCadence'], e['cadenceMinutes'], e['levelSupported']), (True, [1, 15], False))
        self.assertEqual((t['status'], t['contested'], t['cadence']), ('contested', 20, 15))   # read at its coarsest interval
        self.assertIsNone(t['floor'])                                                  # no automatic level across intervals
        self.assertEqual(t['bound'], 1.26)                                             # the maximum needs no weighting
        # Quiet at both intervals: the absence is whole, and the evidence is still coarse, so the
        # fine-data exemption is not open to it (the gate reads the tracker's interval).
        months = [['2026-09', self.stored(self.SEPT)], ['2026-10', self.stored(coarse, cadence=15)]]
        t, e = self.trackers(months, self.WINDOWS)
        self.assertEqual((t['status'], t['cadence'], e['mixedCadence']), ('noCompetition', 15, True))
        # One interval throughout, coarse: a level is possible, and it is labelled with that interval.
        coarse_sept = [('2026-09-29', 1440, 96, 96), ('2026-09-30', 1440, 96, 96)]
        months = [['2026-09', self.stored(coarse_sept, self.BUSY_SEPT, cadence=15)], ['2026-10', self.stored(coarse, self.BUSY_OCT, cadence=15)]]
        t, e = self.trackers(months, self.WINDOWS)
        self.assertEqual((e['mixedCadence'], t['cadence']), (False, 15))
        self.assertIsNotNone(t['floor'])

    def test_a_missing_event_day_prevents_the_no_competition_verdict(self):
        whole = [['2026-09', self.stored(self.SEPT)], ['2026-10', self.stored(self.OCT)]]
        self.assertEqual(self.trackers(whole, self.WINDOWS)[0]['status'], 'noCompetition')
        for october in (self.OCT[:1],                                                  # a day not in history
                        [self.OCT[0], ('2026-10-31', 1440, 0, 0)],                     # a day without PhysB
                        [self.OCT[0], ('2026-10-31', 1440, 1440, 1200)]):              # a day with too little classified
            t, e = self.trackers([whole[0], ['2026-10', self.stored(october)]], self.WINDOWS)
            self.assertEqual(t['status'], 'insufficient', october)
        # No usable day at all: no tracker, and the LPAR is sized as before.
        t, e = self.trackers([['2026-09', self.stored(self.SEPT, virtualProcessors=6, state='2026-07-01T00:00:00Z')]], self.WINDOWS[:1])
        self.assertEqual((t, e['status']), (None, 'unavailable'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
