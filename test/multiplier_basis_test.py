#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/multiplier_basis_test.py
# PURPOSE  : The multiplicative seasonal model divides each past occurrence by
#            the quantity its ratio is then multiplied with (v10).
#            The model's baseline is the mean of the daily profile values of a
#            window of history. The baseline stored with an occurrence is
#            another quantity: one percentile over all days of its baseline
#            period. The two need not agree (here the stored one is set higher).
#            1. An event that reaches only that stored baseline (no uplift over
#               it) is forecast at the event's level: baseline x (event value /
#               history baseline as of the occurrence). Dividing by the stored
#               baseline gave the daily mean: below what every occurrence
#               measured.
#            2. An occurrence for which history holds no daily values is left
#               out and the run says so; it is not divided by another quantity.
#            On the fixture of test/profile_demand_test.py.
# USAGE    : python3 test/multiplier_basis_test.py
# ==============================================================================
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import member_exposure_test as M
from profile_demand_test import Case, PROFILE, OTHER, LPARS

MODEL = 'multiplicative_seasonal'
BASIS = 'history baseline as of each occurrence (mean of the daily profile values of its window)'


@unittest.skipUnless(M.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class MultiplierBasisTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        M.MemberExposureTests.roots = []
        # Every day of the earlier months at B; each occurrence's stored baseline and its event value both at 1.3 B.
        cls.level = Case('legacy', peak={PROFILE: 1.3, OTHER: 1.3}, stored_baseline=1.3, models=(MODEL,))
        # The same, without the month before the first occurrence.
        cls.short = Case('legacy', peak={PROFILE: 1.3, OTHER: 1.3}, stored_baseline=1.3, models=(MODEL,), month_before=False)

    @classmethod
    def tearDownClass(cls):
        M.MemberExposureTests.tearDownClass()

    def test_an_occurrence_is_divided_by_the_baseline_it_is_multiplied_with(self):
        case = self.level
        meta = case.records[MODEL]['_meta']
        self.assertEqual(meta['multiplierBasis']['denominator'], BASIS)
        self.assertGreater(meta['multiplierBasis']['occurrencePairs'], 0)
        self.assertNotIn('occurrencePairsWithoutIt', meta['multiplierBasis'])
        for vm in LPARS:
            for profile in (PROFILE, OTHER):
                # 1.3 B over a daily mean of B: the forecast is the event's level on the current baseline ...
                self.assertAlmostEqual(case.demand(MODEL, vm, profile) / case.baseline(MODEL, vm, profile), 1.3, delta=0.005, msg=(vm, profile))
        # ... where the stored baseline as the divisor (1.3 B / 1.3 B) gave the baseline itself.
        self.assertAlmostEqual(case.demand(MODEL, 'vmS'), 1.3 * case.values['vmS'][PROFILE], delta=0.005)

    def test_an_occurrence_without_daily_values_is_left_out_and_said(self):
        case = self.short
        meta = case.records[MODEL]['_meta']
        self.assertEqual(meta['multiplierBasis']['denominator'], BASIS)
        self.assertGreater(meta['multiplierBasis']['occurrencePairsWithoutIt'], 0)
        self.assertLess(meta['multiplierBasis']['occurrencePairsWithoutIt'], meta['multiplierBasis']['occurrencePairs'])
        self.assertRegex(case.stderr[MODEL], r'\[NOTE\] \d+ of \d+ LPAR-profile occurrence\(s\) left out of the seasonal multiplier: history holds no daily values')
        # The later occurrence has its month before it: the forecast stands on it alone.
        self.assertAlmostEqual(case.demand(MODEL, 'vmS') / case.baseline(MODEL, 'vmS'), 1.3, delta=0.005)
        self.assertEqual(case.capacity(MODEL, 'vmS')['observations'], {'legacy': 1})


if __name__ == '__main__':
    unittest.main(verbosity=2)
