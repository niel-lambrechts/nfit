#!/usr/bin/env python3
# ==============================================================================
# Test for dev/thread-density-explore (research; no sizing figure) on a
# designed system: LPARs whose busy share lies on the Power10 yield curve at
# one, four and eight threads per consumed core, one LPAR holding cores its
# threads do not use, one above its entitlement and one idle.
#
# Checked: the estate curve reproduces the designed points; the LPAR below the
# curve is found and the single-threaded one is not mistaken for it; nFit's
# default curve misplaces the single-threaded LPAR; the run-queue load on
# entitlement is classed against the tier of the planning profile; the result
# names no LPAR and carries the method it used.
# ==============================================================================
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
START = 1788220800                     # 2026-09-01T00:00:00Z, a Tuesday
DAYS = 3
#        name: (entitlement, VPs, PhysC, run queue, PhysB, planning profile)
LPARS = {'single': (2.0, 4, 1.0, 1.0, 0.294, 'G3-95W15'),      # one thread per core: on the curve at 0.29
         'dense': (4.0, 8, 2.0, 16.0, 2.0, 'G3-95W15'),        # eight threads per core: fully busy
         'mid': (2.0, 4, 1.5, 6.0, 1.173, 'G1-99W5'),          # four threads per core: 0.78
         'wide': (3.0, 6, 2.0, 8.0, 0.8, 'G3-95W15'),          # four threads per core, busy share 0.40: below the curve
         'hot': (1.0, 4, 2.5, 20.0, 2.5, 'G3-95W15'),          # above its entitlement: not in the guaranteed set
         'idle': (1.0, 2, 0.1, 0.2, 0.05, 'G3-95W15')}         # below the activity floor


class ThreadDensityTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.mkdtemp(prefix='thread-density-')
        stage = os.path.join(cls.scratch, 'work', 'case', 'stage', 'SYNTD')
        os.makedirs(stage)
        with open(os.path.join(stage, '.nfit.cache.data'), 'w') as fh:
            fh.write('Timestamp,VMName,PhysC,RunQ,PhysB\n')
            for t in range(START, START + DAYS * 86400, 60):
                stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t))
                wobble = 0.01 if (t // 60) % 2 else -0.01
                for vm, (ent, vps, c, q, b, profile) in LPARS.items():
                    fh.write(f'{stamp},{vm},{c:.4f},{q:.4f},{b + wobble * c:.4f}\n')
        md = lambda ent, vps: {'proc_type': 'PowerPC_POWER10', 'proc_version': '2.0', 'smt': 8, 'entitlement': ent,
                               'virtual_cpus': vps, 'capped': 0, 'pool_id': 0}
        with open(os.path.join(stage, '.nfit.cache.states'), 'w') as fh:
            json.dump({vm: [{'start_epoch': START, 'end_epoch': START + DAYS * 86400 - 60, 'metadata': md(v[0], v[1])}]
                       for vm, v in LPARS.items()}, fh)
        store = os.path.join(stage, '.nfit.store', 'proposals', 'v1', 'state-based')
        os.makedirs(store)
        with open(os.path.join(store, 'snapshot.json'), 'w') as fh:
            json.dump({'model': 'state-based', 'members': {vm: {'planningProfile': v[5], 'profiles': {v[5]: {'entitlement': v[0]}}}
                                                         for vm, v in LPARS.items()}}, fh)
        cls.run_ = subprocess.run([sys.executable, os.path.join(REPO, 'dev', 'thread-density-explore'), '--work',
                                   os.path.join(cls.scratch, 'work'), '--out', os.path.join(cls.scratch, 'out'), '--label', 'designed'],
                                  capture_output=True, text=True, timeout=120)
        cls.text = ''
        path = os.path.join(cls.scratch, 'out', 'thread-density-designed.json')
        if os.path.exists(path):
            with open(path) as fh:
                cls.text = fh.read()
        cls.result = json.loads(cls.text) if cls.text else {}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def test_the_run_completes_and_reports_its_method(self):
        self.assertEqual(self.run_.returncode, 0, (self.run_.stdout + self.run_.stderr)[-1500:])
        method = self.result['method']
        self.assertEqual((method['activityFloorCores'], method['residualBand'], method['tierDensity']['1']), (0.25, 0.10, 0.45))
        self.assertEqual(method['curves']['Power10'], {'1': 1.0, '2': 1.95, '4': 2.66, '8': 3.4})
        self.assertIn('mean depth while the queue was occupied', method['runQueue'])
        samples = DAYS * 1440
        self.assertEqual(self.result['samples']['rows'], samples * len(LPARS))
        self.assertEqual(self.result['samples']['belowFloor'], samples)                 # the idle LPAR
        self.assertEqual(self.result['samples']['guaranteed'], samples * 4)             # not the one above its entitlement
        for name in LPARS:
            self.assertNotIn('"%s"' % name, self.text)
        self.assertNotIn('SYNTD', self.text)

    def test_the_estate_curve_reproduces_the_designed_points(self):
        rows = {r['threadsPerCore']: r for r in self.result['H1_estateCurve']['guaranteedSet']['Power10 SMT8']}
        one, four, eight = rows['1 to 1.5'], rows['3.5 to 5'], rows['7 to 9']
        self.assertEqual((one['lpars'], one['predictedGeneration'], one['predictedDefault']), (1, 0.294, 0.426))
        self.assertAlmostEqual(one['busyShare']['median'], 0.294, delta=0.011)
        self.assertEqual((four['lpars'], four['predictedGeneration']), (2, 0.782))      # on the curve, and well below it
        self.assertEqual((eight['lpars'], eight['predictedGeneration']), (1, 1.0))
        self.assertAlmostEqual(eight['busyShare']['median'], 1.0, delta=0.011)
        # All valid samples add the LPAR above its entitlement, at eight threads per core.
        self.assertEqual({r['threadsPerCore']: r['lpars'] for r in self.result['H1_estateCurve']['allValidSamples']['Power10 SMT8']}['7 to 9'], 2)

    def test_the_lpar_below_the_curve_is_found_and_single_threaded_work_is_not_mistaken_for_it(self):
        fit = self.result['H1_H2_fitPerLpar']['guaranteedSet']['Power10 SMT8']
        generation = fit['generation']
        self.assertEqual((generation['fits'], generation['below'], generation['above']), (3, 1, 0))
        # nFit's default curve expects 0.43 at one thread: it places the single-threaded LPAR below the curve too.
        self.assertEqual((fit['default']['fits'], fit['default']['below']), (2, 2))
        self.assertLess(generation['medianAbsoluteResidual'], fit['default']['medianAbsoluteResidual'] + 1e-9)

    def test_run_queue_load_is_classed_against_the_tier_of_the_planning_profile(self):
        h3 = self.result['H3_loadOnEntitlement']
        tier3, tier1 = h3['byTier']['3'], h3['byTier']['1']
        self.assertEqual((tier3['farBelow']['lpars'], tier3['within']['lpars'], tier3['above']['lpars']), (3, 1, 1))
        self.assertEqual(tier1['within']['lpars'], 1)                                   # 0.375 of the guaranteed threads against 0.45
        self.assertEqual(tier3['above']['consumingAtOrAboveEntitlement'], 1)
        self.assertEqual(tier3['farBelow']['consumingAtOrAboveEntitlement'], 0)
        self.assertEqual(tier3['farBelow']['entitlement'], 6.0)                         # 2 + 3 + 1
        self.assertEqual(h3['againstCurveVerdict']['farBelow'], {'fits': 1, 'below': 1, 'notClassified': 1})
        self.assertEqual(h3['againstCurveVerdict']['above'], {'notClassified': 1})
        self.assertEqual(h3['withProposedFactor'], {'lpars': 6, 'farBelow': 5, 'within': 0, 'above': 1})


if __name__ == '__main__':
    unittest.main(verbosity=2)
