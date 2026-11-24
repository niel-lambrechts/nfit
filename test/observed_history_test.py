#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/observed_history_test.py
# PURPOSE  : Observed history is never written from a model's output (v10).
#            1. Repeated forecast generation leaves the stored observations
#               (the results of every event snapshot) exactly as they were and
#               gives the same forecast again: multiplicative seasonal,
#               predictive peak and recency decay.
#            2. A model's record never brings results with it: where no
#               observation is stored, none appears.
#            3. A snapshot holding a multiplicative seasonal or recency decay
#               record from before the demand-quantity correction, or results no
#               history generator stamped, is not read as an observation; the
#               run says so and records it with its forecast.
#            On the fixture of test/profile_demand_test.py.
# USAGE    : python3 test/observed_history_test.py
# ==============================================================================
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import member_exposure_test as M
from profile_demand_test import Case, PROFILE, LPARS


@unittest.skipUnless(M.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class ObservedHistoryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        M.MemberExposureTests.roots = []
        cls.occurrence = Case('occurrence', repeats=2)
        cls.recency = Case('legacy', models=('recency_decay',), repeats=2)
        legacy_record = {'multiplicative_seasonal': {'_meta': {'generated_on': '2026-08-02T00:00:00'}, 'vmA': {PROFILE: 9.0}}}
        # July at B; August 'peaks' at one and a half times B and holds a record from before the correction.
        cls.unverified = Case('legacy', latest_peak={PROFILE: 1.5}, earlier_record=legacy_record, models=('multiplicative_seasonal',))
        # Neither earlier occurrence was stamped by the history generator.
        cls.unstamped = Case('legacy', peak={PROFILE: 1.5}, generated=False, models=('multiplicative_seasonal',))
        # The month being sized already holds generated results and a record from before the correction.
        cls.replaced = Case('legacy', models=('multiplicative_seasonal',), repeats=2,
                            target_event={'eventName': 'ev', 'periodEndDate': '2026-09-12', 'generatedOn': '2026-09-13T00:00:00',
                                          'ModelForecasts': legacy_record})

    @classmethod
    def tearDownClass(cls):
        M.MemberExposureTests.tearDownClass()

    def test_repeated_generation_leaves_observed_history_alone_and_does_not_inflate(self):
        for case in (self.occurrence, self.recency, self.unverified, self.unstamped, self.replaced):
            self.assertTrue(case.before)
            for month, event in case.before.items():
                self.assertEqual(case.after[month].get('results'), event.get('results'), month)      # exactly as they were
            for month, event in case.after.items():
                if month not in case.before:
                    self.assertNotIn('results', event, month)                    # a model's record never brings results with it
        for case in (self.occurrence, self.recency):
            self.assertNotIn('results', case.after['2026-09'])                   # the sized month had no observation: it still has none
            for event in case.after.values():
                self.assertNotIn('observationsUnverified', event)
        by_model = {}
        for model, record in self.occurrence.passes + self.recency.passes + self.replaced.passes:
            by_model.setdefault(model, []).append({vm: record[vm]['forecast'] for vm in LPARS})
        self.assertEqual(sorted(by_model), ['multiplicative_seasonal', 'predictive_peak', 'recency_decay'])
        for model, passes in by_model.items():
            self.assertGreaterEqual(len(passes), 2, model)
            for later in passes[1:]:
                self.assertEqual(later, passes[0], model)

    def test_a_snapshot_a_model_store_may_have_written_into_is_not_read_as_an_observation(self):
        m = 'multiplicative_seasonal'
        # August's 'peak', one and a half times B, would raise the forecast if it were read: July alone is, at B.
        case = self.unverified
        for vm in LPARS:
            self.assertAlmostEqual(case.demand(m, vm), case.baseline(m, vm), places=3, msg=vm)
        reason = 'holds a multiplicative_seasonal record from before the correction'
        self.assertRegex(case.stderr[m], r'1 stored occurrence record\(s\) not read as observations \(provenance not verified\): ev 2026-08 \(%s\)' % reason)
        self.assertEqual(case.records[m]['_meta']['observationsNotUsed'], {'ev 2026-08': reason})
        # Results no generator stamped are not observations either.
        case = self.unstamped
        for vm in LPARS:
            self.assertAlmostEqual(case.demand(m, vm), case.baseline(m, vm), places=3, msg=vm)
        self.assertEqual(case.records[m]['_meta']['observationsNotUsed'],
                         {'ev 2026-07': 'not written by the history generator', 'ev 2026-08': 'not written by the history generator'})

    def test_results_beside_a_replaced_record_from_before_the_correction_stay_marked(self):
        case = self.replaced
        event = case.after['2026-09']
        self.assertEqual(event['ModelForecasts']['multiplicative_seasonal']['_meta']['semantics']['version'], 2)     # the record was replaced
        self.assertIn('multiplicative_seasonal record from before the demand-quantity correction', event['observationsUnverified'])
        self.assertEqual(event['results'], case.before['2026-09']['results'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
