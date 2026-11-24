#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/model_demand_test.py
# PURPOSE  : Demand and sizing in a seasonal run (v10).
#            1. The stored seasonal forecasts
#               (SeasonalEventSnapshots.<event>.ModelForecasts.<model>) keep the
#               model's demand ('forecast') apart from the figure after the
#               sizing chain ('sized'), with a marker saying so. Switching cut
#               protection off changes the sized figure and the CSV, never the
#               stored demand.
#            2. The sizing chain starts from the seasonal model's demand, not
#               from the engine's own profile base: a seasonal multiplier
#               reaches the published figure although the engine's base is
#               present, and the engine's base stays what was measured.
#               --research-engine-seasonal-input gives the earlier behaviour.
#            3. The aggregated multiplicative record says that its demand is an
#               aggregate and its sized figures are the final occurrence's.
#            On the event-window fixture, for the three seasonal models.
# USAGE    : python3 test/model_demand_test.py
# ==============================================================================
import glob
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import member_exposure_test as M
import event_window_test as E

MARKER = {'version': 2, 'forecast': 'profile-demand', 'sized': 'sized-result', 'peakCapacity': 'peak-capacity-estimate'}
PROFILE = 'G3-95W15'
LPARS = ('vmA', 'vmB', 'vmS')
DATES = '2026-08-01:2026-08-01, 2026-09-01:2026-09-01, 2026-09-12:2026-09-12'
MULTIPLICATIVE = {'days': 12, 'baseline': 8, 'prior': True, 'peak': 1.2}       # past occurrences 20% above their baseline


@unittest.skipUnless(M.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class ModelDemandTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.harness = E.EventWindowTests
        cls.harness.fixture = M.MemberExposureTests
        cls.harness.fixture.roots = []
        cls.scratch = tempfile.mkdtemp(prefix='model-demand-')
        cls.runs = {}
        for model, kw, when in (('predictive_peak', {}, '2026-09-01:2026-09-01'), ('recency_decay', {}, '2026-09-01:2026-09-01'),
                                ('multiplicative_seasonal', MULTIPLICATIVE, DATES)):
            for name, flags in (('protected', []), ('unprotected', ['--no-cut-protection']),
                                ('engineInput', ['--no-cut-protection', '--research-engine-seasonal-input'])):
                trace = os.path.join(cls.scratch, '%s-%s.json' % (model, name))
                run = cls.harness.seasonal(when, history=True, model=model, flags=flags + ['--research-runq-trace', trace], **kw)
                run['record'] = cls.record(cls.harness.fixture.roots[-1], model)
                with open(trace) as fh:
                    run['trace'] = json.load(fh)['members']
                cls.runs[(model, name)] = run

    @classmethod
    def tearDownClass(cls):
        cls.harness.fixture.tearDownClass()

    @staticmethod
    def record(root, model):
        """The model's newest stored record of the event."""
        found = None
        for path in sorted(glob.glob(os.path.join(root, 'history', M.SERIAL, 'nfit.hist.*.json'))):
            with open(path) as fh:
                for data in json.load(fh).values():
                    block = (((data.get('SeasonalEventSnapshots') or {}).get('ev') or {}).get('ModelForecasts') or {}).get(model)
                    found = block or found
        return found

    def terms(self, model, name, vm):
        return self.runs[(model, name)]['trace'][vm]['profiles'][PROFILE]

    # -- 1. the stored record -------------------------------------------------------------
    def test_every_model_marks_its_record_and_stores_both_figures(self):
        for (model, name), run in self.runs.items():
            record = run['record']
            self.assertIsNotNone(record, (model, name))
            semantics = record['_meta']['semantics']
            self.assertEqual({k: semantics[k] for k in MARKER}, MARKER, (model, name))
            for vm in LPARS:
                self.assertIn(PROFILE, record[vm]['forecast'], (model, name, vm))
                # The sized figure is the one the CSV publishes for the profile.
                self.assertAlmostEqual(record[vm]['sized'][PROFILE], float(run['rows'][vm][PROFILE]), delta=0.006, msg=(model, name, vm))

    def test_protection_changes_the_sized_figure_and_never_the_stored_demand(self):
        for model in ('predictive_peak', 'recency_decay', 'multiplicative_seasonal'):
            on, off = self.runs[(model, 'protected')]['record'], self.runs[(model, 'unprotected')]['record']
            for vm in LPARS:
                self.assertEqual(on[vm]['forecast'], off[vm]['forecast'], (model, vm))      # every profile, exactly
            moved = [vm for vm in LPARS if abs(on[vm]['sized'][PROFILE] - off[vm]['sized'][PROFILE]) > 0.005]
            if model != 'multiplicative_seasonal':                                           # its demand here lies above the protected level
                self.assertTrue(moved, model)                                                # the protection did act on the sizing
            for vm in moved:
                self.assertGreater(on[vm]['sized'][PROFILE], off[vm]['sized'][PROFILE], (model, vm))   # a floor: it only raises
                self.assertLess(on[vm]['forecast'][PROFILE], on[vm]['sized'][PROFILE], (model, vm))

    def test_the_two_figures_are_a_pair_only_when_they_come_from_one_sizing(self):
        for model in ('predictive_peak', 'recency_decay'):
            self.assertEqual(self.runs[(model, 'protected')]['record']['_meta']['semantics']['pair'], 'corresponding', model)
        # The aggregated multiplicative record: an aggregate demand, and the final occurrence's sized figures.
        semantics = self.runs[('multiplicative_seasonal', 'protected')]['record']['_meta']['semantics']
        self.assertEqual(semantics['pair'], 'not-corresponding')
        self.assertEqual(semantics['forecastScope'], 'median of 2 occurrences')
        self.assertEqual(semantics['sizedScope'], 'final occurrence ending 2026-09-12')

    # -- 2. the sizing input --------------------------------------------------------------
    def test_a_seasonal_multiplier_reaches_the_published_figure_although_the_engine_base_is_present(self):
        run = self.runs[('multiplicative_seasonal', 'unprotected')]
        for vm in LPARS:
            d = self.terms('multiplicative_seasonal', 'unprotected', vm)
            engine, demand = float(d['PreGrowthBasePhysC']), float(d['SizingInputModelDemand'])
            self.assertEqual(d['SizingInputSource'], 'seasonal model demand', vm)
            self.assertAlmostEqual(demand / engine, 1.2, delta=0.04, msg=vm)                 # the multiplier, on a base that is present
            self.assertAlmostEqual(float(d['BasePlanningDemand']), demand, places=3, msg=vm)  # no additive on this fixture
            self.assertGreaterEqual(float(run['rows'][vm][PROFILE]), demand - 0.006, vm)     # the chain's rules only add to it
        self.assertAlmostEqual(float(run['rows']['vmS'][PROFILE]), 0.60, places=2)           # 0.50 x 1.2: published, where 0.50 was before

    def test_the_engine_values_stay_what_was_measured(self):
        for model in ('multiplicative_seasonal', 'predictive_peak'):
            for vm in LPARS:
                now, before = self.terms(model, 'unprotected', vm), self.terms(model, 'engineInput', vm)
                for key in ('PreGrowthBasePhysC', 'GrowthAdjUsed', 'PostGrowthBasePhysC', 'rVCPU_RunQHiThreads', 'IsRunQPressure', 'IsWorkloadPressure'):
                    self.assertEqual(now.get(key), before.get(key), (model, vm, key))
                self.assertAlmostEqual(float(now['SizingInputEngineValue']), float(before['BasePlanningDemand']), places=3, msg=(model, vm))

    def test_the_research_switch_gives_the_earlier_sizing_and_recency_decay_is_not_touched(self):
        run = self.runs[('multiplicative_seasonal', 'engineInput')]
        self.assertEqual(self.terms('multiplicative_seasonal', 'engineInput', 'vmS')['SizingInputSource'], 'engine profile value')
        self.assertAlmostEqual(float(run['rows']['vmS'][PROFILE]), 0.50, places=2)           # the multiplier was lost
        # Recency decay assembles its own requirement: the same figures with and without the switch.
        a, b = self.runs[('recency_decay', 'unprotected')], self.runs[('recency_decay', 'engineInput')]
        for vm in LPARS:
            self.assertEqual(a['rows'][vm][PROFILE], b['rows'][vm][PROFILE], vm)
            self.assertEqual(a['rows'][vm]['NFIT - ENT'], b['rows'][vm]['NFIT - ENT'], vm)
            self.assertEqual(a['record'][vm]['forecast'], b['record'][vm]['forecast'], vm)

    def test_the_protected_target_reaches_the_published_result_and_the_pool_plan(self):
        run = self.runs[('predictive_peak', 'protected')]
        record, row = run['record']['vmB'], run['rows']['vmB']
        self.assertLess(record['forecast'][PROFILE], record['sized'][PROFILE])               # demand 0.90, protected at 0.97
        self.assertAlmostEqual(float(row[PROFILE]), record['sized'][PROFILE], places=2)
        self.assertRegex(row['NFIT - Action'], r'^Event window: Cut limited to 0\.97 ')
        self.assertRegex(run['log'], r'\n  7\s+2\s+4\.00\s+2\.97\s')                         # pool 7: 2.00 + 0.97 in the event plan


if __name__ == '__main__':
    unittest.main(verbosity=2)
