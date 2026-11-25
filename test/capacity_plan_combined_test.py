#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/capacity_plan_combined_test.py
# PURPOSE  : The Seasonal Capacity Plan (v10 C6) of a multiplicative run with a
#            combined interaction, and of recency decay, on the profile-demand
#            fixture.
#            - An overlapping event named among the run's active events, whose
#              multiplier did not enter the forecast (no history of it), is not
#              taken as combined: the plan says "combined requirement not
#              assessed", and the record lists no combined event. A combined
#              requirement needs the other event's effect in the figure, not its
#              name in the run's metadata (the positive case is in
#              test/capacity_plan.t).
#            - Recency decay names no later occurrence: no plan, said once.
# USAGE    : python3 test/capacity_plan_combined_test.py
# ==============================================================================
import csv
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
import profile_demand_test as P

DATES = P.DATES + ', ' + P.NEXT_DATES
# bf overlaps the run's analysis window (so the run names it among its active events) and the next occurrence.
COMBINED = (E.EVENT % ('multiplicative_seasonal', DATES, 8, 1, 1) + 'interaction_policy = combined\n'
            + '[bf]\nmodel = multiplicative_seasonal\npriority = 5\ndates = 2026-09-02:2026-10-03\ninteraction_policy = combined\n'
            + 'min_historical_years = 0\n')


@unittest.skipUnless(M.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class CapacityPlanCombinedTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fixture = M.MemberExposureTests
        cls.fixture.roots = []
        case = P.Case('occurrence', models=('multiplicative_seasonal',))
        cls.root = case.root
        cls.combined = cls.run_event(COMBINED)
        cls.recency = cls.run_event(E.EVENT % ('recency_decay', DATES, 8, 1, 1))

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    @classmethod
    def run_event(cls, config):
        with open(os.path.join(cls.root, 'etc', 'nfit.seasonality.cfg'), 'w') as fh:
            fh.write(config)
        for old in glob.glob(os.path.join(cls.root, 'output', '*')):
            os.remove(old)
        proc = cls.fixture.run_profile(cls.root, '--apply-seasonality', 'ev', '--no-cut-protection', '--force')
        found = lambda pattern: glob.glob(os.path.join(cls.root, 'output', pattern))
        out = {'stderr': proc.stderr, 'plan': {}}
        plans = found('*.ev.capacity-plan.*.csv')
        out['plans'] = len(plans)
        if plans:
            with open(plans[0]) as fh:
                out['plan'] = {row['VM']: row for row in csv.DictReader(fh)}
        log, = found('*.log')
        with open(log) as fh:
            out['log'] = fh.read()
        snapshot, = found('*.ev.proposal.*.json')
        with open(snapshot) as fh:
            out['snapshot'] = json.load(fh)
        return out

    def test_a_named_event_without_effect_is_not_combined(self):
        run = self.combined
        self.assertIn('Active seasonal events: 2 (ev, bf)', run['stderr'])          # named in the run's metadata
        self.assertTrue(run['plan'])
        for vm, row in run['plan'].items():
            self.assertEqual(row['Overlap'], 'bf 2026-09-02 to 2026-10-03, overlapping 2026-10-01 to 2026-10-01: combined requirement '
                                             'not assessed (no stored plan on the same data)', vm)
            self.assertIn('overlap', row['Unresolved'].split('; '), vm)
            self.assertEqual(run['snapshot']['members'][vm]['capacityPlan']['combinedEvents'], [], vm)
        flat = ' '.join(run['log'].split())
        self.assertIn('bf (02 Sep to 03 Oct 2026) overlaps this occurrence on 01 Oct 2026; combined requirement not assessed for all', flat)
        self.assertNotIn('combined in this forecast', flat)

    def test_recency_decay_names_no_occurrence(self):
        run = self.recency
        self.assertEqual(run['plans'], 0)
        self.assertEqual(run['log'].count('Seasonal Capacity Plan'), 1)
        self.assertIn('Not produced: recency decay sizes the last completed occurrence and names no later one.', run['log'])
        self.assertEqual(run['snapshot']['context']['capacityPlan'],
                         {'produced': False, 'reason': 'recency decay sizes the last completed occurrence and names no later one'})


if __name__ == '__main__':
    unittest.main(verbosity=1)
