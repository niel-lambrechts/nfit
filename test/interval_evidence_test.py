"""Interval-aware evidence (v10 stage 3.4) on the member-exposure fixture reduced to
15-minute samples: every fifteenth LPAR sample and pool observation is kept, as a
collector with a 15-minute interval would record them.

  - An observation is one sample. Nothing is spread into minute buckets, and coverage
    is measured against what the cadence can give.
  - One credible contested observation limits a cut, never above installed, and the
    action says the overlap inside the interval is unknown.
  - The absence of competition at this resolution does not enable the pool-borrowing
    exemption.
  - LPAR and pool at different cadences: the evidence is misaligned and the no-evidence
    path is taken.
  - A pool at its maximum is counted in intervals, with no longest run.
"""
import csv
import glob
import gzip
import json
import os
import shutil
import time
import unittest
from pathlib import Path
import member_exposure_test as mx
from one_entry_point_test import run

STEP = 15


def coarse_root(pool_step=STEP, member_step=STEP, pool_shift=0, no_pool=False):
    """The fixture of member_exposure_test with its data file and pool observations thinned."""
    root = Path(mx.MemberExposureTests.make_root())
    cache = root / 'stage' / mx.SERIAL
    header, *rows = (cache / '.nfit.cache.data').read_text().splitlines()
    keep = [r for r in rows if (int(r[14:16]) % member_step) == 0]
    (cache / '.nfit.cache.data').write_text('\n'.join([header] + keep) + '\n')
    with gzip.open(cache / '.nfit.cache.pool.observations.csv.gz', 'rt') as fh:
        head, *obs = fh.read().splitlines()
    kept = [o for o in obs if (int(o.split(',')[0]) // mx.NS // 60) % pool_step == 0]
    if pool_shift:                                    # the pool is observed a few minutes after the LPARs
        kept = [','.join([str(int(o.split(',')[0]) + pool_shift * mx.NS)] + o.split(',')[1:]) for o in kept]
    with gzip.open(cache / '.nfit.cache.pool.observations.csv.gz', 'wt') as fh:
        fh.write('\n'.join([head] + kept) + '\n')
    if no_pool:                                       # as with NMON files: no pool observations at all
        os.remove(cache / '.nfit.cache.pool.observations.csv.gz')
    return root, cache, len(keep) // 4


class CoarseRun:
    def __init__(self, pool_step=STEP, enddate='2026-09-02', pool_shift=0, no_pool=False, member_step=STEP):
        mx.MemberExposureTests.roots = []
        self.root, self.cache, self.samples_per_lpar = coarse_root(pool_step, member_step, pool_shift=pool_shift, no_pool=no_pool)
        cfg = self.root / 'vms.csv'
        with cfg.open('w') as f:
            w = csv.writer(f)
            w.writerow('hostname serial systemtype procpool_name procpool_id entitledcpu maxcpu'.split())
            for vm, role in [('vmA', 'AIX'), ('vmB', 'VIO Server'), ('vmS', 'PowerHA Standby')]:
                ent, vp, pool, _ = mx.CONFIG[vm]
                w.writerow([vm, mx.SERIAL, role, 'pool', pool, ent, vp])
        run(str(self.root), str(self.cache), '--config', str(cfg), '--enddate', enddate)
        self.log = glob.glob(str(self.root / 'output' / '*.log'))[0]
        self.log = Path(self.log).read_text(encoding='utf-8')
        self.flat = ' '.join(self.log.split())
        text = [p for p in glob.glob(str(self.root / 'output' / '*.csv')) if '.decisions.' not in p][0]
        self.rows = {r['VM']: r for r in csv.DictReader(Path(text).read_text(encoding='utf-8').splitlines())}
        snaps = glob.glob(str(self.cache / '.nfit.store' / 'proposals' / 'v1' / '*' / '*.json'))
        self.members = json.loads(Path(snaps[0]).read_text())['members'] if snaps else {}

    def gates(self, vm):
        return {v['tierFloor']['gate'] for v in self.members[vm]['profiles'].values() if 'tierFloor' in v}

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class IntervalEvidenceTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.coarse = CoarseRun()
        cls.mixed = CoarseRun(pool_step=5)          # pool every 5 minutes, LPARs every 15
        cls.shifted = CoarseRun(pool_shift=180)     # pool observed three minutes after the LPAR samples
        cls.no_pool = CoarseRun(no_pool=True)       # coarse LPAR samples, no pool observations
        cls.fine_no_pool = CoarseRun(no_pool=True, member_step=1)

    @classmethod
    def tearDownClass(cls):
        cls.coarse.close()
        cls.mixed.close()
        cls.shifted.close()
        cls.no_pool.close()
        cls.fine_no_pool.close()

    def test_an_observation_is_one_sample_and_coverage_follows_the_cadence(self):
        support = [v['cutProtection']['support'] for v in self.coarse.members['vmA']['profiles'].values() if 'cutProtection' in v]
        self.assertTrue(support)
        for s in support:
            # Paired observations cannot exceed the samples recorded: nothing was repeated into minutes.
            self.assertLessEqual(s['pairedMinutes'], self.coarse.samples_per_lpar)
            self.assertLessEqual(s['contestedMinutes'], s['pairedMinutes'])
        all_hours = max(support, key=lambda s: s['expectedMinutes'])
        self.assertEqual(all_hours['expectedMinutes'], 2 * 1440)                 # the window, in minutes
        self.assertGreaterEqual(all_hours['pairedMinutes'], 0.95 * 2 * 96)       # 96 samples a day were there to be had
        # The standby's pool was never contested and every sample was classified: at one minute that
        # withholds the rule. Its coverage is still read as complete against the cadence.
        self.assertRegex(self.coarse.flat, r'applies as legacy policy: no pool competition was observed, but at 15-minute resolution short '
                                           r'competition is not seen')
        self.assertNotIn('pool evidence insufficient', self.coarse.flat)

    def test_absence_of_competition_does_not_enable_the_exemption(self):
        gates = self.coarse.gates('vmS')
        self.assertIn('applied (legacy: pool evidence at 15-minute resolution)', gates)
        self.assertFalse([g for g in gates if g.startswith('withheld')], gates)   # no profile has the rule withheld
        self.assertNotIn('not applied: no pool competition observed', self.coarse.flat)

    def test_one_contested_observation_limits_a_cut_as_a_precaution(self):
        records = [v['cutProtection'] for v in self.coarse.members['vmA']['profiles'].values() if 'cutProtection' in v]
        contested = [r for r in records if r['support']['contestedMinutes'] > 0]
        self.assertTrue(contested, 'a 15-minute sample falls inside a contested window of the fixture')
        for r in contested:
            self.assertLessEqual(r['bound']['ladder'], r['installed'])           # never above installed
        action = self.coarse.rows['vmA']['NFIT - Action']
        self.assertRegex(action, r'^Cut limited to 2\.00 \(profile \d\.\d\d\): busy demand and pressure in pool 7 observed in \d+ of the '
                                 r'15-minute intervals over \d days?; overlap within the interval is unknown\.')
        self.assertNotIn('minutes over', action)
        self.assertEqual(self.coarse.rows['vmA']['NFIT - ENT'], '2.00')
        self.assertRegex(self.coarse.flat, r'Cut protection : profile \d\.\d\d limited to 2\.00 \(share 0\.\d\d\); busy demand and pool pressure in '
                                           r'\d+ of the 15-minute intervals over \d days? in pool 7; overlap within an interval is unknown')

    def test_a_cut_is_held_and_its_target_kept(self):
        # The standby's own-workload target is well below its installed 1.50. With 15-minute pool evidence the
        # entitlement stays this cycle; the target is still the calculated one.
        row = self.coarse.rows['vmS']
        self.assertEqual(row['NFIT - ENT'], '1.50')
        self.assertLess(float(row[row['Planning Profile']]), 1.5)
        self.assertRegex(row['NFIT - Action'], r'^No change\. Hold the cut to \d\.\d\d: 15-minute data cannot assess brief pool competition\. '
                                               r'Obtain finer evidence or authorise a monitored trial\.$')
        self.assertNotIn('Hold the cut', self.coarse.rows['vmX']['NFIT - Action'])       # a dedicated LPAR has no pool evidence

    def test_the_hold_does_not_depend_on_pool_observations_being_present(self):
        # Coarse LPAR samples with no pool observations at all: the uncapped shared-pool LPAR's cut is held as well.
        row = self.no_pool.rows['vmS']
        self.assertEqual(row['NFIT - ENT'], '1.50')
        self.assertIn('Hold the cut', row['NFIT - Action'])
        self.assertIn('15-minute data cannot assess brief pool competition', row['NFIT - Action'])
        self.assertNotIn('Hold the cut', self.no_pool.rows['vmX']['NFIT - Action'])      # dedicated: not a shared-pool LPAR
        # One-minute samples without pool observations keep the established path: the cut is applied.
        fine = self.fine_no_pool.rows['vmS']
        self.assertLess(float(fine['NFIT - ENT']), 1.5)
        self.assertNotIn('Hold the cut', fine['NFIT - Action'])

    def test_pool_observations_offset_from_the_lpar_samples_are_still_matched(self):
        # The same data with the pool read three minutes later: exact-minute pairing would find nothing.
        for run in (self.coarse, self.shifted):
            support = [v['cutProtection']['support'] for v in run.members['vmA']['profiles'].values() if 'cutProtection' in v]
            self.assertTrue(all(s['classifiedMinutes'] >= 0.95 * s['pairedMinutes'] for s in support))
        both = lambda run: sorted(v['cutProtection']['support']['contestedMinutes'] for v in run.members['vmA']['profiles'].values() if 'cutProtection' in v)
        self.assertEqual(both(self.shifted), both(self.coarse))
        self.assertGreater(max(both(self.shifted)), 0)
        self.assertIn('busy demand and pressure in pool 7 observed in', self.shifted.rows['vmA']['NFIT - Action'])

    def test_a_full_pool_is_counted_in_intervals(self):
        self.assertRegex(self.coarse.log, r'(?m)^\s+7\s+4\s+[\d. ]+\s\d+ of the 15-minute intervals on \d days?$')
        self.assertNotIn('(longest', self.coarse.log[self.coarse.log.index('\nPool Use\n'):self.coarse.log.index('Recommended Actions')])
        self.assertIn('Resolution: pool observations arrive every 15 minutes.', self.coarse.flat)
        self.assertIn('Pool reduction not assessed: 15-minute data can hide short peaks. Collect one-minute data.', self.coarse.flat)

    def test_mixed_cadence_takes_the_no_evidence_path(self):
        # LPAR samples every 15 minutes, pool observations every 5: not compatible, so nothing is classified.
        for vm in ('vmA', 'vmS'):
            for v in self.mixed.members[vm]['profiles'].values():
                if 'cutProtection' in v:
                    self.assertEqual(v['cutProtection']['support']['classifiedMinutes'], 0, vm)
                    self.assertEqual(v['cutProtection']['support']['contestedMinutes'], 0, vm)
        self.assertNotIn('Cut limited', self.mixed.rows['vmA']['NFIT - Action'])
        self.assertTrue(all(g.startswith('applied (legacy') for g in self.mixed.gates('vmS')), self.mixed.gates('vmS'))


if __name__ == '__main__':
    unittest.main()
