#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/selected_demand_test.py
# PURPOSE  : The selected forecast in nfit-profile's sizing chain (v10 step f),
#            end to end on the event-window fixture. An adaptive event runs
#            through its predictive source event; a selection written into
#            history as nfit-forecast writes it supplies the demand of the pairs
#            it holds a usable value for.
#            E1  a selection equal to the source model's demand gives the
#                predictive run's figures: the same chain, protection and VIOS
#                rules, applied once;
#            E2  a different selected value is the published pair's starting
#                demand, unchanged (no growth added), the other pairs keep the
#                source model's, the planning profile does not move, and the
#                modes are labelled (bootstrap without an interval);
#            E3  a stale selection (its occurrence observed) is not used: the
#                run gives the predictive figures and says why, once;
#            E5  nothing is stored in history; the proposal is stored under the
#                adaptive event, with each profile's demand source and the
#                checks the run does not make.
#            Also: the profile-definitions fingerprint nfit-forecast states of a
#            profile file is the one nfit-profile states of the same file.
# USAGE    : python3 test/selected_demand_test.py
# ==============================================================================
import csv
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import member_exposure_test as M
import event_window_test as E
import profile_demand_test as P

FORECAST = os.environ.get('NFIT_FORECAST') or os.path.abspath(os.path.join(REPO, '..', 'nfit-forecast', 'nfit-forecast'))
# The predictive source event of the profile-demand fixture (past occurrences in history, the next one configured),
# and an adaptive event that sizes through it.
CONFIG = (E.EVENT % ('predictive_peak', P.DATES + ', ' + P.NEXT_DATES, 8, 2, 1)
          + '[ad]\nmodel = adaptive_peak\nsource_event = ev\npriority = 20\nmin_historical_peaks = 2\n')
PROFILE = 'G3-95W15'
SIZED = ('G3-95W15', 'O3-95W15', 'B3-95W15', 'P-99W1', 'NFIT - ENT', 'NFIT - VCPU', 'NFIT - Action', 'VM_Comments')


def forecast_block(source, start, end):
    """The decoder block of nfit-forecast (None when the source is absent)."""
    if not os.path.exists(FORECAST):
        return None
    with open(FORECAST, encoding='utf-8') as fh:
        text = fh.read()
    if '# Zone 2 record decoder (zone2-record-decoder-1)' not in text:
        return None
    a = text.rindex('# ====', 0, text.index('# Zone 2 record decoder (zone2-record-decoder-1)'))
    b = text.index('# --- End of Zone 2 record decoder ---')
    ns = {'os': os, 'json': json, 'sys': sys}
    exec(compile(text[a:b], FORECAST, 'exec'), ns)
    return ns


@unittest.skipUnless(M.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class SelectedDemandTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fixture = M.MemberExposureTests
        cls.fixture.roots = []
        # The September occurrence is in history (as after a history update), so the trend is fitted through it and
        # the source model's projection is for October's occurrence, which no history holds.
        case = P.Case('occurrence', models=('predictive_peak',),
                      target_event={'eventName': 'ev', 'periodEndDate': '2026-09-12', 'generatedOn': '2026-09-13T00:00:00'})
        cls.root, cls.folder = case.root, case.folder
        cls.scratch = tempfile.mkdtemp(prefix='selected-demand-')
        with open(os.path.join(cls.root, 'etc', 'nfit.seasonality.cfg'), 'w') as fh:
            fh.write(CONFIG)
        # vmA and vmB are a VIOS pair: the pair is sized from its two own event targets, in both runs.
        with open(os.path.join(cls.root, 'etc', 'nfit.vms.cfg'), 'w') as fh:
            fh.write('[GLOBAL]\nvios_sizing = combined\n[vmA]\nlpar_type = vios\nvios_peer = vmB\n[vmB]\nlpar_type = vios\n')
        # (--force: the case has just stored this bucket's record; the run is repeated with protection on.)
        cls.predictive = cls.run_event('ev', 'predictive', '--force')
        cls.record = cls.stored_record()
        meta = cls.record['_meta']
        cls.target = {'occurrenceStart': meta['forecast_target_start'], 'occurrenceEnd': meta['forecast_target_date'], 'observed': False}
        cls.data_end = meta['AnalysisContext']['analysis_end'][:10]
        cls.lpars = sorted(vm for vm in cls.record if not vm.startswith('_'))

        # E1: every pair selected at the source model's own demand.
        cls.write_selection({vm: {p: cls.pair(v) for p, v in cls.record[vm]['forecast'].items()} for vm in cls.lpars})
        cls.history_before = cls.history_digest()
        cls.equal = cls.run_event('ad', 'equal')
        cls.history_after = cls.history_digest()

        # E2: three pairs differ: a tournament winner above the model, a bootstrap median below it, one with no
        # eligible forecast. Everything else is selected at the model's demand.
        sel = {vm: {p: cls.pair(v) for p, v in cls.record[vm]['forecast'].items()} for vm in cls.lpars}
        cls.chosen = {'vmB': round(cls.record['vmB']['forecast'][PROFILE] * 1.5, 4), 'vmA': round(cls.record['vmA']['forecast'][PROFILE] * 0.8, 4)}
        sel['vmB'][PROFILE] = cls.pair(cls.chosen['vmB'], model='holt_event', low=cls.chosen['vmB'] * 0.9, high=cls.chosen['vmB'] * 1.1)
        sel['vmA'][PROFILE] = cls.pair(cls.chosen['vmA'], model='ensemble_median', mode='bootstrap', low=None, high=None)
        sel['vmS'][PROFILE] = dict(cls.pair(None), chosen_model='', selection_mode='no eligible forecast', chosen_source='none')
        # vmB is a VIOS planned on its own profile: its selected demand there is higher too.
        cls.vios_profile = cls.predictive['trace']['vmB']['planningProfile']
        if cls.vios_profile != PROFILE:
            raised = round(cls.record['vmB']['forecast'][cls.vios_profile] * 1.5, 4)
            sel['vmB'][cls.vios_profile] = cls.pair(raised, model='holt_event', low=raised * 0.9, high=raised * 1.1)
        cls.write_selection(sel)
        cls.changed = cls.run_event('ad', 'changed')

        # E3: the same selection, its occurrence already observed.
        cls.write_selection(sel, target=dict(cls.target, observed=True))
        cls.stale = cls.run_event('ad', 'stale')

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    # -- fixture helpers ---------------------------------------------------------------
    @classmethod
    def run_event(cls, event, name, *flags):
        trace = os.path.join(cls.scratch, name + '.json')
        before = set(glob.glob(os.path.join(cls.root, 'output', '*')))
        run = cls.fixture.run_profile(cls.root, '--apply-seasonality', event, '--research-runq-trace', trace, *flags)
        new = set(glob.glob(os.path.join(cls.root, 'output', '*'))) - before
        path, = [p for p in new if re.search(r'\.%s\.[^/]*\.csv$' % event, p) and '.proposal.' not in p and '.capacity-plan.' not in p]
        with open(path) as fh:
            rows = {row['VM']: row for row in csv.DictReader(fh)}
        log = max(glob.glob(os.path.join(cls.root, 'output', '*.log')), key=os.path.getmtime)
        with open(log) as fh:
            text = fh.read()
        copies = [p for p in new if '.%s.proposal.' % event in p]
        snapshot = {}
        if copies:
            with open(copies[0]) as fh:
                snapshot = json.load(fh)
        with open(trace) as fh:
            members = json.load(fh)['members']
        return {'rows': rows, 'log': text, 'stderr': run.stderr, 'snapshot': snapshot, 'trace': members}

    @classmethod
    def history(cls):
        out = {}
        for path in sorted(glob.glob(os.path.join(cls.folder, 'nfit.hist.*.json'))):
            with open(path) as fh:
                out.update(json.load(fh))
        return out

    @classmethod
    def history_digest(cls):
        digest = hashlib.sha256()
        for path in sorted(glob.glob(os.path.join(cls.folder, 'nfit.hist.*.json'))):
            with open(path, 'rb') as fh:
                digest.update(fh.read())
        return digest.hexdigest()

    @classmethod
    def stored_record(cls):
        found = None
        for data in cls.history().values():
            found = ((((data.get('SeasonalEventSnapshots') or {}).get('ev') or {}).get('ModelForecasts') or {}).get('predictive_peak')) or found
        return found

    @classmethod
    def pair(cls, value, model='event_multiplier', mode='tournament', low=0.0, high=99.0):
        return {'chosen_model': model, 'chosen_source': 'Zone 3', 'forecast': value, 'pi_lower': low, 'pi_upper': high,
                'confidence_score': 55, 'selection_mode': mode, 'observations': 6, 'rationale': 'test',
                'quantity': 'profile-demand', 'candidates_stored_in': '2026-09', 'target_occurrence': dict(cls.target)}

    @classmethod
    def write_selection(cls, selection, target=None):
        """The block nfit-forecast writes: AdaptiveForecast.selection and selection_meta of the adaptive event."""
        meta = cls.record['_meta']
        path = os.path.join(cls.folder, 'nfit.hist.2026-09.json')
        with open(path) as fh:
            data = json.load(fh)
        target = target or dict(cls.target)
        for by_profile in selection.values():
            for entry in by_profile.values():
                entry['target_occurrence'] = dict(target)
        data['2026-09'].setdefault('SeasonalEventSnapshots', {})['ad'] = {'eventName': 'ad', 'AdaptiveForecast': {
            'candidates': {}, 'selection': selection,
            'selection_meta': {'semantics': 'selected-demand-1', 'quantity': 'profile-demand', 'event': 'ad', 'source_event': 'ev',
                               'written_to': '2026-09', 'candidates_stored_in': '2026-09', 'target_occurrence': target,
                               'data_ends': cls.data_end, 'profile_definitions': meta['profile_definitions'],
                               'exclusion_definitions': meta['exclusion_definitions'],
                               'record_definitions': {'profile_definitions': meta['profile_definitions'],
                                                      'exclusion_definitions': meta['exclusion_definitions']},
                               'live_candidates_added': 0, 'generated_on': '2026-09-30T00:00:00'}}}
        with open(path, 'w') as fh:
            json.dump(data, fh)

    def terms(self, run, vm, profile=PROFILE):
        return run['trace'][vm]['profiles'][profile]

    # -- E1 --------------------------------------------------------------------------------
    def test_a_selection_equal_to_the_source_demand_gives_the_predictive_figures(self):
        self.assertIn('Selected forecast:', self.equal['stderr'])
        for vm in ('vmA', 'vmB'):
            self.assertEqual(self.equal['snapshot']['members'][vm]['role'], self.predictive['snapshot']['members'][vm]['role'], vm)
        for vm in self.lpars:
            for column in SIZED:
                if column in self.predictive['rows'][vm]:
                    self.assertEqual(self.equal['rows'][vm][column], self.predictive['rows'][vm][column], (vm, column))
            self.assertEqual(self.equal['trace'][vm]['planningProfile'], self.predictive['trace'][vm]['planningProfile'], vm)
            for profile, terms in self.predictive['trace'][vm]['profiles'].items():
                mine = self.terms(self.equal, vm, profile)
                for key in ('SizingInputModelDemand', 'BasePlanningDemand', 'GuaranteeDemand', 'CutProtectionUnprotected', 'CutProtectionApplied'):
                    self.assertEqual(mine.get(key), terms.get(key), (vm, profile, key))
                self.assertTrue(mine['SizingInputSource'].startswith('selected forecast: Selected: event_multiplier (tournament)'), (vm, profile))

    # -- E2 --------------------------------------------------------------------------------
    def test_a_selected_value_is_the_pair_demand_and_nothing_else_moves(self):
        b = self.terms(self.changed, 'vmB')
        self.assertAlmostEqual(float(b['SizingInputModelDemand']), self.chosen['vmB'], places=4)
        self.assertEqual(b['SizingInputSource'], 'selected forecast: Selected: holt_event (tournament)')
        # No growth is added to it: the starting demand before the additive is the selected value itself.
        self.assertAlmostEqual(float(b['DownsizedPhysC_PreGrowth']), self.chosen['vmB'], places=4)
        self.assertAlmostEqual(float(b['DownsizedPhysC']), self.chosen['vmB'], places=4)
        a = self.terms(self.changed, 'vmA')
        self.assertAlmostEqual(float(a['SizingInputModelDemand']), self.chosen['vmA'], places=4)
        self.assertEqual(a['SizingInputSource'], 'selected forecast: Selected: bootstrap median')
        # No eligible forecast: the source model's demand, as in the predictive run.
        s, before = self.terms(self.changed, 'vmS'), self.terms(self.predictive, 'vmS')
        self.assertEqual((s['SizingInputSource'], s['SizingInputModelDemand']), (before['SizingInputSource'], before['SizingInputModelDemand']))
        self.assertEqual(self.changed['rows']['vmS'][PROFILE], self.predictive['rows']['vmS'][PROFILE])
        # The measurement profile is the measured value of the window in both runs.
        for vm in self.lpars:
            self.assertEqual(self.changed['rows'][vm].get('Peak_P-99W1'), self.predictive['rows'][vm].get('Peak_P-99W1'), vm)
            self.assertEqual(self.changed['trace'][vm]['planningProfile'], self.predictive['trace'][vm]['planningProfile'], vm)
        # The published figure follows the demand: higher for vmB, not higher for vmA.
        self.assertGreater(float(self.changed['rows']['vmB'][PROFILE]), float(self.predictive['rows']['vmB'][PROFILE]))
        self.assertLessEqual(float(self.changed['rows']['vmA'][PROFILE]), float(self.predictive['rows']['vmA'][PROFILE]))
        # The VIOS pair is sized once, from the two own targets of this run (one of them from a selected demand).
        members = self.changed['snapshot']['members']
        roles = {vm: members[vm]['role'] for vm in ('vmA', 'vmB')}
        own = {vm: members[vm]['profiles'][roles[vm]['profile']]['entitlement'] for vm in roles}
        for vm, role in roles.items():
            self.assertEqual((role['type'], role['mode']), ('viosPair', 'combined'))
            self.assertAlmostEqual(role['targetEntitlement'], own['vmA'] + own['vmB'] + role['margin'] * (own['vmA'] + own['vmB']), places=2)
            self.assertAlmostEqual(float(self.changed['rows'][vm]['NFIT - ENT']), role['entitlement'], places=2)
        self.assertNotEqual(own['vmB'], self.predictive['snapshot']['members']['vmB']['profiles'][roles['vmB']['profile']]['entitlement'])
        # Labelled in the CSV and the rationale.
        self.assertIn('profiles selected; occurrence %s to %s' % (self.target['occurrenceStart'], self.target['occurrenceEnd']),
                      self.changed['rows']['vmB']['DemandSource'])
        self.assertIn('Selected Forecast (event ad, through ev)', self.changed['log'])
        self.assertRegex(self.changed['log'], r'Sized from the selection: \d+ pairs \(1 bootstrap median, \d+ tournament\)\.')
        self.assertRegex(self.changed['log'], r'\n\s+1  no eligible forecast\n')
        self.assertIn('Selected Forecast  The event', self.changed['log'])                       # Key Terminology
        # The proposal: each profile's demand source; bootstrap without an interval.
        members = self.changed['snapshot']['members']
        ds_b, ds_a, ds_s = (members[vm]['profiles'][PROFILE]['demandSource'] for vm in ('vmB', 'vmA', 'vmS'))
        self.assertEqual((ds_b['source'], ds_b['model'], ds_b['mode']), ('selection', 'holt_event', 'tournament'))
        self.assertAlmostEqual(ds_b['demand'], self.chosen['vmB'], places=4)
        self.assertAlmostEqual(ds_b['sourceModelDemand'], self.record['vmB']['forecast'][PROFILE], places=4)
        self.assertEqual((ds_a['mode'], ds_a['piLower'], ds_a['piUpper']), ('bootstrap', None, None))
        self.assertEqual((ds_s['source'], ds_s['reason']), ('source model', 'no eligible forecast'))

    # -- E3 --------------------------------------------------------------------------------
    def test_a_stale_selection_is_not_used(self):
        self.assertEqual(self.stale['stderr'].count('Selected forecast not used:'), 1)
        self.assertIn('is already observed in history', self.stale['stderr'])
        for vm in self.lpars:
            for column in SIZED:
                if column in self.predictive['rows'][vm]:
                    self.assertEqual(self.stale['rows'][vm][column], self.predictive['rows'][vm][column], (vm, column))
            self.assertTrue(self.stale['rows'][vm]['DemandSource'].startswith('Source model only: its target occurrence'), vm)
        self.assertEqual(self.stale['snapshot']['context']['selection']['status'], 'withheld')

    # -- E5 --------------------------------------------------------------------------------
    def test_nothing_is_stored_and_the_proposal_is_the_adaptive_events(self):
        self.assertEqual(self.history_after, self.history_before)
        stored = glob.glob(os.path.join(self.root, 'stage', M.SERIAL, '.nfit.store', 'proposals', 'v1', 'ad', '*.json'))
        self.assertTrue(stored)
        self.assertEqual(self.equal['snapshot']['model'], 'ad')
        self.assertEqual(self.equal['snapshot']['context']['notAssessed'], ['PowerHA role sizing (standard runs only)', 'frame capacity'])
        self.assertIn('Not assessed in this run: PowerHA role sizing (standard runs only) and frame capacity.', self.equal['log'])
        # The predictive run's own outputs are left as they were.
        self.assertTrue(glob.glob(os.path.join(self.root, 'stage', M.SERIAL, '.nfit.store', 'proposals', 'v1', 'ev', '*.json')))
        for name in self.lpars:
            self.assertNotIn(name, self.changed['stderr'].split('Selected forecast')[-1].split('\n')[0])

    # -- the fingerprint parity --------------------------------------------------------------
    def test_both_programs_state_the_same_profile_definitions(self):
        ns = forecast_block(None, None, None)
        if ns is None:
            self.skipTest('nfit-forecast source with the decoder block not found')
        profiles = os.path.join(self.root, 'etc', 'nfit.profiles.cfg')
        self.assertEqual(ns['profile_definitions_fingerprint'](profiles), self.record['_meta']['profile_definitions'])
        self.assertEqual(ns['exclusion_definitions_fingerprint']({}), self.record['_meta']['exclusion_definitions'])
        # A file without the measurement profile: both inject it and copy it to P-99W1 alike.
        reduced = os.path.join(self.scratch, 'reduced.cfg')
        with open(profiles) as src, open(reduced, 'w') as dst:
            text = src.read()
            dst.write(re.sub(r'\[(?:Peak_P-99W1|P-99W1)\][^\[]*', '', text))
        perl = subprocess.run(['perl', '-e', PERL_FINGERPRINT, os.path.join(REPO, 'nfit-profile'), reduced],
                              capture_output=True, text=True, env=dict(os.environ, PERL5LIB=os.path.join(REPO, '.claude', 'perl5')))
        self.assertEqual(perl.returncode, 0, perl.stderr)
        self.assertEqual(ns['profile_definitions_fingerprint'](reduced), perl.stdout.strip())
        self.assertEqual(ns['exclusion_definitions_fingerprint']({'exclude_dates': ' 2026-01-02 ,2026-01-01'}),
                         hashlib.md5(b'2026-01-01,2026-01-02').hexdigest())


# nfit-profile's own reading of a profile file and its normalisation (the subs and the main-program block, extracted).
PERL_FINGERPRINT = r'''
my ($src_path, $cfg) = @ARGV;
open my $fh, '<', $src_path or die; my $src = do { local $/; <$fh> }; close $fh;
my $code = "package UUT; use strict; use warnings; our \$MANDATORY_PEAK_PROFILE_FOR_HINT = 'Peak_P-99W1'; our \$LEGACY_MANDATORY_PEAK_PROFILE_FOR_HINT = 'P-99W1'; our \$MISSION_CRITICAL_PROFILE_NAME = 'P-99W1';\n";
$src =~ /^(my \$DEFAULT_MANDATORY_PEAK_PROFILE_FLAGS = .*?;)$/m or die "no default flags\n"; my $d = $1; $d =~ s/^my /our /; $code .= "$d\n";
for my $name (qw(load_profile_definitions _profile_definitions_fingerprint)) { $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "no $name\n"; $code .= "$1\n"; }
$src =~ /^(# Ensure the mandatory peak helper profile exists.*?^\@profiles = \(\@ordered, \@rest\);)$/ms or die "no normalisation block\n";
my $block = $1; $block =~ s/^\s*print STDERR .*$//mg; $block =~ s/\bmy \$MANDATORY_PEAK_ABSRUNQ_KEY;\s*\{.*?\n\}\n//s;
$code .= "sub fingerprint_of { my (\$path) = \@_; our \@profiles = load_profile_definitions(\$path);\n$block\nreturn _profile_definitions_fingerprint(\\\@profiles); }\n";
eval "$code 1;" or die "harness: $@\n";
print UUT::fingerprint_of($cfg), "\n";
'''


if __name__ == '__main__':
    unittest.main()
