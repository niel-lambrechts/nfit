"""The sizing that stands after the dispatch-width reference (defect of 27 November 2026),
through the real sizing path on synthetic caches of 30 days.

A profile whose VP range was widened on dispatch evidence is sized a second time without
the widening, as a reference for its entitlement. Whichever way that comparison goes, the
entitlement, the VP target, its witnesses, the diagnostics and the published pair must all
belong to the sizing that stands: the one with the widening.

  Reference rejected (pool observed, no competition: the reference entitlement is not higher)
    small   0.30 on 1 VP, run-queue width 2: the small-LPAR allowance admits 2 VPs.
    queued  0.40 on 2 VPs, run-queue width 3 under pressure: the existing pressure rule
            admits 3; its 2 installed VPs are not proposed for reduction.
    wide    0.30 on 1 VP, run-queue width 4 without pressure: the allowance gives 2 VPs,
            and the width beyond it is recorded as not admitted. No rule is widened.
  Reference selected (no pool observations: the tier's pool-borrowing rule applies)
    floor   1.00 on 1 VP with a daily half hour near its width: the entitlement is the
            reference's, on the 2 VPs of the widened sizing.
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

REJECTED = {'small': (0.30, 1, 0), 'queued': (0.40, 2, 0), 'wide': (0.30, 1, 0)}
SELECTED = {'floor': (1.0, 1, 0)}


def minute_of(vm, day, minute):
    if vm == 'small':
        return 0.15, 0.10, 12.0
    if vm == 'queued':
        return 0.18, 0.12, 14.0
    if vm == 'wide':
        return 0.15, 0.10, 20.0
    return (0.95, 0.90, 14.0) if 120 <= minute < 150 else (0.30, 0.20, 12.0)


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class WidthStandingTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="width-standing-")
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
            caches = {'rejected': we.build(cls.root, 'SYNWR', REJECTED), 'selected': we.build(cls.root, 'SYNWS', SELECTED, with_pool=False)}
        finally:
            we.minute_of = saved
        cls.members, cls.rows, cls.log = {}, {}, {}
        for case, cache in caches.items():
            proc = subprocess.run(['perl', os.path.join(cls.root, 'nfit-profile'), '--nmondir', cache, '--runq-perc-behavior=match'],
                                  cwd=cls.root, capture_output=True, text=True, timeout=280)
            assert proc.returncode == 0, proc.stderr[-1500:]
            plan, = glob.glob(os.path.join(cache, '.nfit.store', 'proposals', 'v1', 'state-based', '*.json'))
            with open(plan) as fh:
                cls.members[case] = json.load(fh)['members']
            serial = os.path.basename(cache)
            path, = [f for f in glob.glob(os.path.join(cls.root, 'output', '*.csv')) if 'state-based' in f and '.decisions.' not in f and serial in f]
            with open(path) as fh:
                cls.rows[case] = {r['VM']: r for r in csv.DictReader(fh)}
            log, = [f for f in glob.glob(os.path.join(cls.root, 'output', '*.log')) if 'state-based' in f and serial in f]
            with open(log, encoding='utf-8') as fh:
                cls.log[case] = re.sub(r'[ \t]+', ' ', fh.read())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def sized(self, case, vm):
        m = self.members[case][vm]
        return m['profiles'][m['planningProfile']]

    def test_a_rejected_reference_leaves_nothing_behind(self):
        # The signature of the defect: a small entitlement whose range maximum was left at 1 VP, which only the
        # sizing without the widening produces. No profile of any LPAR may carry it, in either branch.
        for case in self.members:
            for vm, m in self.members[case].items():
                for name, p in m['profiles'].items():
                    record = p.get('widthAboveRange')
                    if record and (record.get('workloadEntitlement') or 9) < 1.0:
                        self.assertGreaterEqual(record['rangeMaximum'], 2, '%s %s %s' % (case, vm, name))
                    if record:
                        self.assertEqual(record['target'], p['recommendedVPs'], '%s %s %s' % (case, vm, name))

    def test_the_small_lpar_allowance_admits_two_vps(self):
        p, row = self.sized('rejected', 'small'), self.rows['rejected']['small']
        self.assertEqual(p['recommendedVPs'], 2)
        self.assertNotIn('widthAboveRange', p)                                     # nothing was refused
        self.assertLess(p['entitlement'], 0.30)                                    # the workload's figure: not raised for the width
        self.assertEqual(p['currentCycle']['VPs'], 1)                              # an increase on queue evidence is a review, not applied
        self.assertRegex(row['NFIT - Action'], r'Review a VP increase to 2\b')
        self.assertRegex(self.log['rejected'], r'Envelope max expanded 1 -> 2 \(micro-partition floor; rENT_P=0\.15, RunQ dispatch evidence=2 VPs')

    def test_installed_vps_are_not_reduced_below_the_admitted_width(self):
        p, row = self.sized('rejected', 'queued'), self.rows['rejected']['queued']
        self.assertEqual(p['recommendedVPs'], 3)                                   # the existing pressure rule: small entitlement, dense queue
        self.assertEqual(p['currentCycle']['VPs'], 2)
        self.assertNotRegex(row['NFIT - Action'], r'VPs 2 to 1\b')

    def test_a_width_beyond_the_allowance_is_still_not_admitted(self):
        p = self.sized('rejected', 'wide')
        record = p['widthAboveRange']
        self.assertEqual((p['recommendedVPs'], record['state'], record['rangeMaximum']), (2, 'runQueueWidthNotAdmitted', 2))
        self.assertIn({'source': 'run-queue dispatch width', 'VPs': 4}, record['witnesses'])

    def test_a_selected_reference_gives_the_entitlement_and_the_widened_sizing_the_vps(self):
        p, row = self.sized('selected', 'floor'), self.rows['selected']['floor']
        self.assertRegex(self.log['selected'], r'Dispatch width reference : raised 0\.3000 -> 0\.\d+ cores')
        self.assertEqual(p['recommendedVPs'], 2)
        self.assertGreaterEqual(p['entitlement'], 0.70)                            # never below the sizing without the widening
        self.assertLessEqual(p['entitlement'], 1.0)
        self.assertTrue(0.05 * p['currentCycle']['VPs'] <= p['currentCycle']['entitlement'] <= p['currentCycle']['VPs'])
        self.assertRegex(row['NFIT - Action'], r'Review a VP increase to 2\b')


if __name__ == '__main__':
    unittest.main()
