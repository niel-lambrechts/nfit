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
#            What is asserted (after the correction of 27 November):
#              - the VP target is the width the credible peak asks for; the
#                usual range of the entitlement does not hold it at 2;
#              - the workload figure of the profile is unchanged by that;
#              - this cache has no pool observations, so the tier's
#                pool-borrowing rule applies as it does to every profile: the
#                guarantee it asks for at 5 VPs is a raise named as the tier
#                rule, kept apart from the workload figure. (With the rule
#                withheld on pool evidence the entitlement stays the
#                workload's: test/vp_target_test.py.)
#              - on the planning profile, the entitlement of this cycle is not
#                above the largest of its three parts (workload, PowerVM
#                minimum, staging guard), and the parts are kept apart.
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

    def test_the_width_witness_is_the_vp_target(self):
        sized = self.member['profiles'][PROFILE]
        self.assertEqual(sized['recommendedVPs'], 5)                  # peak-throughput protection; formerly held at 2 (twice the entitlement)
        self.assertIn('Peak-throughput protection: credible peak 5.04 cores', self.rationale)

    def test_the_workload_figure_is_unchanged_and_the_tier_raise_is_named(self):
        sized = self.member['profiles'][PROFILE]
        floor = sized['tierFloor']
        self.assertLessEqual(floor['own'], 1.0)                       # the workload figure: the same as before the width stood
        self.assertTrue(floor['gate'].startswith('applied (legacy: no pool evidence'))
        self.assertFalse(floor['withheld'])
        # The guarantee the tier's pool-borrowing rule asks for at 5 VPs: the published entitlement is that figure, rounded up.
        self.assertGreater(floor['floor'], floor['own'])
        self.assertTrue(floor['floor'] <= sized['entitlement'] < floor['floor'] + 0.051)
        self.assertRegex(self.rationale, r"tier 'G3' budget 60%: applicable demand 4\.\d\d cores, reachable 5\.00\. Guarantee must be >= 3\.\d\d")
        self.assertIn('guarantee raised the planning entitlement', self.rationale)
        # 0.05 a VP is met with room: the raise is not the PowerVM minimum, and no ratio is named as its cause.
        self.assertGreaterEqual(floor['own'], 0.05 * 5)

    def test_no_target_is_left_below_a_completed_witness(self):
        held = [name for name, p in self.member['profiles'].items()
                if (p.get('widthAboveRange') or {}).get('state') not in (None, 'targetAboveUsualRange', 'runQueueWidthNotAdmitted')]
        self.assertEqual(held, [])
        for name, p in self.member['profiles'].items():
            record = p.get('widthAboveRange')
            if record and record['state'] == 'targetAboveUsualRange':
                self.assertGreater(record['target'], record['rangeMaximum'], name)
                self.assertTrue(record['meetsPlatformMinimum'], name)

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
