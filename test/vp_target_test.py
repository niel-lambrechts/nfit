"""The workload VP target and its entitlement, sized apart (v10 B10 correction),
through the real sizing path on a synthetic cache of 30 days with PhysB and an
uncontested pool. The usual VP:ENT range is advisory.

  peaky     installed 4.00 on 8 VPs; a steady 0.60 cores with half an hour at
            5.00 every day. The workload entitlement is under one core; the
            credible peak asks for 5 VPs. Target: that entitlement on 5 VPs.
            The VPs come down this cycle; nothing holds them at 8.
  nearfull  the same LPAR with its peak at 7.60 of 8 VPs: a genuine block (the
            credible peak near the reachable capacity) still holds the VPs.
  tiny      0.10 on 1 VP, nearly idle: a valid configuration, left alone.
  capped    capped at 2.00 on 4 VPs: its cap stays a restriction of its own.
"""
import csv
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import member_exposure_test as mx
import width_evidence_test as we

LPARS = {'peaky': (4.0, 8, 0), 'nearfull': (4.0, 8, 0), 'tiny': (0.10, 1, 0), 'capped': (2.0, 4, 1)}


def minute_of(vm, day, minute):
    peak = 120 <= minute < 150
    if vm == 'peaky':
        return (5.00, 3.00, 6.0) if peak else (0.60, 0.25, 1.0)
    if vm == 'nearfull':
        return (7.60, 6.00, 9.0) if peak else (0.60, 0.25, 1.0)
    if vm == 'tiny':
        return 0.04, 0.02, 0.2
    return (1.96, 1.70, 6.0) if peak else (0.80, 0.30, 1.0)


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class VpTargetTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='vp-target-')
        os.makedirs(os.path.join(cls.root, 'etc'))
        os.makedirs(os.path.join(cls.root, 'output'))
        for prog in ('nfit', 'nfit-profile'):
            shutil.copy(os.path.join(mx.REPO, prog), cls.root)
        for name in mx.TRACKED_ETC:
            src = os.path.join(mx.REPO, 'etc', name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(cls.root, 'etc', name))
        open(os.path.join(cls.root, 'etc', 'nfit.seasonality.cfg'), 'w').close()
        saved = we.minute_of
        we.minute_of = minute_of
        try:
            cache = we.build(cls.root, 'SYNVT', LPARS)
        finally:
            we.minute_of = saved
        proc = subprocess.run(['perl', os.path.join(cls.root, 'nfit-profile'), '--nmondir', cache, '--runq-perc-behavior=match'],
                              cwd=cls.root, capture_output=True, text=True, timeout=280)
        assert proc.returncode == 0, proc.stderr[-1500:]
        plan, = glob.glob(os.path.join(cache, '.nfit.store', 'proposals', 'v1', 'state-based', '*.json'))
        with open(plan) as fh:
            cls.members = json.load(fh)['members']
        path, = [f for f in glob.glob(os.path.join(cls.root, 'output', '*.csv')) if 'state-based' in f and '.decisions.' not in f]
        with open(path) as fh:
            cls.rows = {r['VM']: r for r in csv.DictReader(fh)}
        log, = [f for f in glob.glob(os.path.join(cls.root, 'output', '*.log')) if 'state-based' in f]
        with open(log, encoding='utf-8') as fh:
            cls.log = re.sub(r'[ \t]+', ' ', fh.read())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def sized(self, vm):
        m = self.members[vm]
        return m['profiles'][m['planningProfile']]

    def test_the_width_witness_stands_and_the_entitlement_is_the_workloads(self):
        # On the all-hours profile the workload figure is the steady 0.60 cores; the peak lies in the batch hours.
        p = self.members['peaky']['profiles']['G3-95W15']
        self.assertEqual(p['recommendedVPs'], 5)                                   # the credible peak's 5 VPs, not the 2 of the usual range
        self.assertLess(p['entitlement'], 1.0)                                     # the workload figure: not raised to admit the width
        self.assertTrue(0.05 * 5 <= p['entitlement'])                              # PowerVM accepts the pair
        record = p['widthAboveRange']
        self.assertEqual((record['state'], record['target'], record['rangeMaximum'], record['meetsPlatformMinimum']), ('targetAboveUsualRange', 5, 2, True))
        self.assertIn('above the usual VP range maximum of 2 VPs for this entitlement; the range is advisory', self.log)
        # The pool-borrowing rule was not applied (no competition for the pool was observed): reliance on the pool is
        # the tier's and the pool's question, stated by the rule that owns it, not by a ratio.
        self.assertTrue(p['tierFloor']['withheld'])
        self.assertTrue(p['tierFloor']['gate'].startswith('withheld (no contested minute)'))

    def test_the_vps_come_down_this_cycle(self):
        p, row = self.sized('peaky'), self.rows['peaky']
        cycle = p['currentCycle']
        self.assertLess(cycle['VPs'], 8)                                           # not held at the installed 8
        self.assertGreaterEqual(cycle['VPs'], 5)                                   # nor below what the peak needs
        self.assertRegex(row['NFIT - Action'], r'VPs 8 to %d\b' % cycle['VPs'])
        self.assertNotIn('BLOCK', self.members['peaky'].get('published', {}).get('comments') or '')
        self.assertTrue(0.05 * cycle['VPs'] <= cycle['entitlement'] <= cycle['VPs'])
        # What this cycle keeps above the workload figure is named: the staging guard, not workload demand.
        parts = cycle.get('entitlementParts')
        if cycle['entitlement'] > p['entitlement'] + 0.005:
            self.assertIn('RATIO_GUARD', cycle['binding'])
            self.assertEqual(parts['workload'], p['entitlement'])
            self.assertLess(parts['stagingGuard'], 4.0)
        # The target itself is the workload's: the planning profile's figure on the peak's 5 VPs.
        self.assertEqual(p['recommendedVPs'], 5)
        self.assertRegex(row['NFIT - Action'], r'Target %.2f entitlement on 5 VPs over later cycles' % p['entitlement'])

    def test_a_genuine_block_still_holds(self):
        p = self.sized('nearfull')
        self.assertEqual((p['recommendedVPs'], p['currentCycle']['VPs']), (7, 8))  # the peak needs 7; the 8 installed are kept this cycle
        record = self.members['nearfull']['profiles']['G3-95W15']['widthAboveRange']
        self.assertEqual((record['state'], record['target'], record['reductionSafety']), ('targetAboveUsualRange', 7, 'BLOCK'))
        # and the hold names its real cause: the peak is near what the LPAR can reach today
        self.assertRegex(record['reductionSafetyReasons'], r'credible peak is 9\d% of current reachable capacity \(BLOCK at 90%\)')
        self.assertNotIn('strategic guarantee invariant failed', record['reductionSafetyReasons'])

    def test_one_vp_on_a_small_entitlement_is_left_alone(self):
        p, row = self.sized('tiny'), self.rows['tiny']
        self.assertEqual((p['recommendedVPs'], p['currentCycle']['VPs']), (1, 1))
        self.assertGreaterEqual(p['currentCycle']['entitlement'], 0.05)
        self.assertNotIn('widthAboveRange', p)
        self.assertNotRegex((row['VM_Comments'] or '') + row['NFIT - Action'], r'(?i)ratio|VP:ENT')

    def test_the_cap_stays_a_restriction_of_its_own(self):
        p = self.sized('capped')
        self.assertGreaterEqual(p['recommendedVPs'], p['entitlement'])
        self.assertNotIn('widthAboveRange', p)                                     # a cap is not answered with VPs
        self.assertTrue(self.members['capped']['widthEvidence']['capped'])


if __name__ == '__main__':
    unittest.main()
