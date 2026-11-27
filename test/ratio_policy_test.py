#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/ratio_policy_test.py
# PURPOSE  : The entitlement is never raised merely to fit a VP:entitlement
#            ratio (v10 B10), traced through the real sizing path.
#            Fixture: app1, installed 4.00 entitlement on 8 VPs, default pool;
#            a steady 0.60 cores with a daily half hour at 5.00 cores in the
#            small hours. On G3-95W15 the workload figure is below one core, and
#            the credible peak asks for 5 VPs: more than the normal VP range
#            of that entitlement admits (2).
#
#            What is asserted:
#              - the profile's entitlement stays the workload figure; it is
#                not raised until the VP range admits the width;
#              - the rationale says so, and names the width as the open point;
#              - the saved plan records the width witness above the range
#                (widthAboveRange), for review;
#              - on the planning profile, the entitlement of this cycle is not
#                above the largest of its three parts (workload, PowerVM
#                minimum, staging guard), and the parts are kept apart.
#            The VP target of such a profile is recorded here, not judged.
# USAGE    : python3 test/ratio_policy_test.py
# ==============================================================================
import csv
import glob
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import powerha_roles_test as H

PROFILE = 'G3-95W15'


def level(vm, t):
    if vm == 'app1':
        minute = (t % 86400) // 60
        return 5.0 if 120 <= minute < 150 else 0.6
    return 0.3


@unittest.skipUnless(H.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class RatioPolicyTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='ratio-policy-')
        saved = (H.level, dict(H.INSTALLED))
        H.level, H.INSTALLED = level, {'app1': 4.0}
        try:
            _, last = H.build(cls.root, False)
        finally:
            H.level, H.INSTALLED = saved
        states = os.path.join(cls.root, 'stage', 'SYSHA1', '.nfit.cache.states')
        with open(states) as fh:
            db = json.load(fh)
        for state in db['app1']:
            state['metadata'].update({'virtual_cpus': 8, 'max_cpu': 8})
        with open(states, 'w') as fh:
            json.dump(db, fh)
        run = subprocess.run(['perl', os.path.join(cls.root, 'nfit-profile'), '--runq-perc-behavior=match', '--enddate', last,
                              '--nmondir', os.path.join(cls.root, 'stage', 'SYSHA1')], capture_output=True, text=True, timeout=300, cwd=cls.root)
        assert run.returncode == 0, run.stderr[-800:]
        path, = [f for f in glob.glob(os.path.join(cls.root, 'output', '*.csv')) if '.decisions.' not in f]
        with open(path) as fh:
            cls.row = next(r for r in csv.DictReader(fh) if r['VM'] == 'app1')
        snaps = sorted(glob.glob(os.path.join(cls.root, 'stage', 'SYSHA1', '.nfit.store', 'proposals', 'v1', '*', '*.json*')), key=os.path.getmtime)
        with open(snaps[-1], 'rb') as fh:
            raw = fh.read()
        cls.member = json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)['members']['app1']
        log, = glob.glob(os.path.join(cls.root, 'output', '*.log'))
        with open(log) as fh:
            cls.rationale = fh.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_the_entitlement_is_the_workload_figure_not_one_that_admits_the_width(self):
        ent = float(self.row[PROFILE])
        record = self.member['profiles'][PROFILE]['widthAboveRange']
        witnesses = {w['source']: w['VPs'] for w in record['witnesses']}
        self.assertEqual(witnesses['peak-throughput protection'], 5)
        self.assertEqual((record['supportedVPs'], record['target']), (5, 2))                     # what the evidence supports; what the range left
        # What bears on the supported width, each stated apart: the PowerVM minimum is met, no pool or cap stands in the way.
        self.assertEqual((record['platformMinimum'], record['meetsPlatformMinimum'], record['capped'], record['poolBelowWidth']), (0.25, True, False, False))
        self.assertEqual(record['workloadEntitlement'], ent)
        self.assertEqual(record['reductionSafety'], 'BLOCK')
        self.assertIn('strategic guarantee invariant failed', record['reductionSafetyReasons'])
        # 5 VPs fit the normal range only from 2.01 entitlement: the profile was not raised towards it.
        self.assertLessEqual(ent, 1.0)
        self.assertEqual(record['rangeMaximum'], 2)
        self.assertEqual(self.member['profiles'][PROFILE]['entitlement'], ent)

    def test_the_rationale_says_the_entitlement_was_not_raised_for_the_width(self):
        self.assertRegex(self.rationale, r'INFEASIBLE WIDTH: applicable demand 4\.\d\d cores exceeds proposed reachable capacity 2\.00; entitlement was not '
                                         r'raised because the uncapped proposal requires more reachable width')
        self.assertIn('no entitlement raise applied; the uncapped proposal requires additional reachable width', self.rationale)

    def test_a_profile_without_such_a_witness_carries_no_record(self):
        clean = [name for name, p in self.member['profiles'].items() if 'widthAboveRange' not in p]
        self.assertTrue(clean, 'the record is kept only where the range maximum holds the target below a witness')

    def test_this_cycles_entitlement_is_within_its_three_parts(self):
        planning = self.member['planningProfile']
        cycle = self.member['profiles'][planning]['currentCycle']
        target = self.member['profiles'][planning]['entitlement']
        parts = cycle.get('entitlementParts')
        if parts is None:
            # Neither the PowerVM minimum nor the staging guard shaped it: the entitlement is the workload's (or the tier's floor).
            self.assertNotIn('PLATFORM_MINIMUM', cycle['binding'])
            self.assertNotIn('RATIO_GUARD', cycle['binding'])
            if 'TIER_FLOOR' not in cycle['binding']:
                self.assertAlmostEqual(cycle['entitlement'], target, places=2)
        else:
            self.assertLessEqual(cycle['entitlement'], max(v for v in parts.values() if v is not None) + 0.011)
            if parts['stagingGuard'] is not None:
                self.assertLess(parts['stagingGuard'], float(self.row['Current - ENT']))
        self.assertTrue(0.05 * cycle['VPs'] <= cycle['entitlement'] <= cycle['VPs'])


if __name__ == '__main__':
    unittest.main()
