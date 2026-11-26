#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/stage1_reduction_test.py
# PURPOSE  : Coordinated entitlement and VP right-sizing of one ordinary
#            (non-PowerHA, non-VIOS) LPAR that is wider and larger than its
#            workload needs, traced through the real CSV and action path.
#            Fixture: app1, installed 1.50 entitlement on 4 VPs, a steady 0.80
#            cores of use, default pool, no TIER (planning profile G3-95W15).
#
#            The trace:
#              entitlement target (the profile's figure)   0.82
#              strategic VP target (rVCPU)                 1
#              entitlement-bounded VP target               2   the narrowest width that
#                (rVCPU_EntBounded)                            still carries the INSTALLED
#                                                              1.50: where a VP-only
#                                                              consolidation would stop
#              VP step of this cycle (rVCPU_Tactical)      2
#              published NFIT - ENT / NFIT - VCPU          0.82 / 2
#
#            Both figures come down in the same cycle. That is the intended
#            policy (coordinated right-sizing; v10 stage 3.1, decision 3.1-D3),
#            and it rests on evidence and safeguards, which the tests name:
#              - the entitlement is the selected workload profile's own figure
#                and covers the workload observed;
#              - peak-throughput protection gives the VP floor (1 VP here);
#              - the reduction-safety state is CLEAR (no clipping, RunQ pressure
#                or peak near the reachable capacity), so the tier's VP step
#                applies: 4 to 2, not straight to 1;
#              - the pair is one PowerVM accepts, and the action says to
#                re-measure before the next change, because PhysC moves with
#                the VP count.
#            The staging guard (RATIO_GUARD) is a separate safeguard for a very
#            wide LPAR and does not bind here. Holding the entitlement while the
#            VPs are reduced (to rVCPU_EntBounded) remains a planner's option
#            where the effect of narrower width should be observed first; it is
#            not a mandatory first step, and nothing here requires it.
# USAGE    : python3 test/stage1_reduction_test.py
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


@unittest.skipUnless(H.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class Stage1ReductionTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='stage1-')
        _, last = H.build(cls.root, False)
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
        cls.sized = cls.member['profiles'][PROFILE]
        log, = glob.glob(os.path.join(cls.root, 'output', '*.log'))
        with open(log) as fh:
            cls.rationale = fh.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_the_lpar_is_an_ordinary_one_on_its_default_planning_profile(self):
        self.assertEqual(self.row['Planning Profile'], PROFILE)
        self.assertNotIn('role', self.member)
        self.assertEqual((self.row['Current - ENT'], self.row['Current - VCPU']), ('1.50', '4'))

    def test_the_vp_targets_and_the_step_of_this_cycle(self):
        self.assertEqual(self.sized['recommendedVPs'], 1)             # strategic rVCPU
        self.assertEqual(self.sized['entitlementBoundedVPs'], 2)      # rVCPU_EntBounded: still carries the installed 1.50
        self.assertEqual(self.sized['tacticalVPs'], 2)                # the VP step of this cycle
        self.assertEqual(self.row[PROFILE + '_rVCPU_Tactical'], '2')
        # The step is between the strategic target and the installed count.
        self.assertLessEqual(self.sized['recommendedVPs'], self.sized['entitlementBoundedVPs'])
        self.assertLessEqual(self.sized['entitlementBoundedVPs'], self.sized['tacticalVPs'])
        self.assertLessEqual(self.sized['tacticalVPs'], int(self.row['Current - VCPU']))

    def test_the_entitlement_target_is_the_profiles_own_figure(self):
        self.assertEqual(self.row[PROFILE], '0.82')
        self.assertEqual(self.sized['entitlement'], 0.82)

    def test_the_published_pair_reduces_entitlement_and_vps_together(self):
        # Coordinated right-sizing: the entitlement goes to its target and the VPs take this cycle's step.
        self.assertEqual((self.row['NFIT - ENT'], self.row['NFIT - VCPU']), ('0.82', '2'))
        self.assertEqual(self.row[PROFILE + '_rENT_Tactical'], '0.82')
        self.assertEqual(self.row['NFIT - Action'],
                         'Reduce entitlement 1.50 to 0.82 and VPs 4 to 2. Target 0.82 entitlement on 1 VP over later cycles. '
                         'PhysC can change with the VP count: re-measure before the next change.')
        cycle = self.sized['currentCycle']
        self.assertEqual((cycle['entitlement'], cycle['VPs'], cycle['status']), (0.82, 2, 'STAGED'))
        # The staging guard did not shape this pair; it is a separate safeguard for a much wider LPAR.
        self.assertEqual(cycle['binding'], [])

    def test_the_evidence_and_safeguards_that_support_the_coordinated_change(self):
        ent, vps = float(self.row['NFIT - ENT']), int(self.row['NFIT - VCPU'])
        # The entitlement still covers the workload the profile selected (a steady 0.80 cores).
        self.assertGreaterEqual(ent, 0.80)
        # The pair is one PowerVM accepts, and the VPs are not below the strategic or peak-throughput floor.
        self.assertTrue(0.05 * vps <= ent <= vps)
        self.assertGreaterEqual(vps, self.sized['recommendedVPs'])
        # VPs are not kept merely because the entitlement came down: the step is taken in the same cycle,
        # at the tier's rate (half of 4), not all the way to the strategic target at once.
        self.assertLess(vps, int(self.row['Current - VCPU']))
        self.assertGreater(vps, self.sized['recommendedVPs'])
        # The rationale states the safeguards: reduction safety CLEAR, and the peak-throughput VP floor.
        self.assertIn('Reduction safety               : CLEAR', self.rationale)
        self.assertNotRegex(self.rationale, r'Reduction safety\s+: (?:BLOCK|CAUTION)')
        self.assertRegex(self.rationale, r'Peak-throughput protection: credible peak 0\.8\d cores; tier retains 92% of estimated peak throughput '
                                         r'-> 0\.7\d cores; headroom \+0% -> floor 1 VPs')
        self.assertIn('rVCPU_EntBounded (2) preserves current entitlement', self.rationale)
        # The planner is told that the new width changes what will be measured.
        self.assertIn('re-measure before the next change', self.row['NFIT - Action'])


if __name__ == '__main__':
    unittest.main()
