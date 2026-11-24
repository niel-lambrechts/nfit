#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/model_demand_test.py
# PURPOSE  : Demand and sizing are kept apart in the stored seasonal forecasts
#            (SeasonalEventSnapshots.<event>.ModelForecasts.<model>): 'forecast'
#            is the model's demand before any allocation rule, 'sized' is the
#            figure after the sizing chain, and the record carries a marker
#            saying so. Shown for the three seasonal models on the event-window
#            fixture: switching cut protection off changes the sized figure and
#            the CSV, never the stored demand.
# USAGE    : python3 test/model_demand_test.py
# ==============================================================================
import glob
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import member_exposure_test as M
import event_window_test as E

MARKER = {'version': 1, 'forecast': 'model-demand', 'sized': 'sized-result'}
PROFILE = 'G3-95W15'


@unittest.skipUnless(M.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class ModelDemandTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.harness = E.EventWindowTests
        cls.harness.fixture = M.MemberExposureTests
        cls.harness.fixture.roots = []
        dates = '2026-08-01:2026-08-01, 2026-09-01:2026-09-01, 2026-09-12:2026-09-12'
        setups = {'predictive_peak': {}, 'recency_decay': {},
                  'multiplicative_seasonal': {'days': 12, 'baseline': 8, 'prior': True, 'dates': dates}}
        cls.runs = {}
        for model, kw in setups.items():
            kw = dict(kw)
            when = kw.pop('dates', '2026-09-01:2026-09-01')
            for name, flags in (('protected', []), ('unprotected', ['--no-cut-protection'])):
                run = cls.harness.seasonal(when, history=True, model=model, flags=flags, **kw)
                run['record'] = cls.record(cls.harness.fixture.roots[-1], model)
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

    def test_every_model_marks_its_record_and_stores_both_figures(self):
        for (model, name), run in self.runs.items():
            record = run['record']
            self.assertIsNotNone(record, (model, name))
            self.assertEqual(record['_meta']['semantics'], MARKER, (model, name))
            for vm in ('vmA', 'vmB', 'vmS'):
                self.assertIn(PROFILE, record[vm]['forecast'], (model, name, vm))
                self.assertIn(PROFILE, record[vm]['sized'], (model, name, vm))
                # The sized figure is the one the CSV publishes for the profile.
                self.assertAlmostEqual(record[vm]['sized'][PROFILE], float(run['rows'][vm][PROFILE]), delta=0.006, msg=(model, name, vm))

    def test_protection_changes_the_sized_figure_and_never_the_stored_demand(self):
        for model in ('predictive_peak', 'recency_decay', 'multiplicative_seasonal'):
            on, off = self.runs[(model, 'protected')]['record'], self.runs[(model, 'unprotected')]['record']
            for vm in ('vmA', 'vmB', 'vmS'):
                self.assertEqual(on[vm]['forecast'], off[vm]['forecast'], (model, vm))      # every profile, exactly
            moved = [vm for vm in ('vmA', 'vmB', 'vmS') if abs(on[vm]['sized'][PROFILE] - off[vm]['sized'][PROFILE]) > 0.005]
            self.assertTrue(moved, model)                                                    # the protection did act on the sizing
            for vm in moved:
                self.assertGreater(on[vm]['sized'][PROFILE], off[vm]['sized'][PROFILE], (model, vm))   # a floor: it only raises

    def test_the_demand_of_each_model_is_its_own_figure(self):
        # Predictive peak on this fixture forecasts its baseline; the chain adds to it without protection too.
        record = self.runs[('predictive_peak', 'unprotected')]['record']
        self.assertAlmostEqual(record['vmB']['forecast'][PROFILE], record['vmB']['baseline'][PROFILE], places=4)
        self.assertGreater(record['vmA']['sized'][PROFILE], record['vmA']['forecast'][PROFILE] + 0.5)   # 2.00 sized on a demand of 1.24
        # Recency decay: decayed base plus growth; run-queue potential and protection belong to the sizing.
        record = self.runs[('recency_decay', 'protected')]['record']
        self.assertLessEqual(record['vmB']['forecast'][PROFILE], record['vmB']['sized'][PROFILE])


if __name__ == '__main__':
    unittest.main(verbosity=2)
