"""The pool ceiling replay (research; v10 stage 3.4, pool reduction policy) on a
synthetic cache of 35 days: 30 of training and 5 held out.

  pool 20  maximum 8, entitlements 3.5: busy peaks at 4.60 in training, so the
           candidate is 5 (the peak outside the near-ceiling tolerance); later
           one minute reaches 5.30 (an exceedance) and ten sit at 4.95 (newly
           near the ceiling).
  pool 21  maximum 4, at its maximum in training: no reduction.
  pool 22  maximum 6, entitlements 4.2, busy never above 1.0: the members'
           entitlements govern (5).
  pool 23  maximum 8, one training day unobserved: no reduction.
No sizing is run and no LPAR is named in the result.
"""
import calendar
import gzip
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import member_exposure_test as mx

FIRST = calendar.timegm((2026, 9, 1, 0, 0, 0))
TRAIN_DAYS, LATER_DAYS = 30, 5
MEMBERS = {   # LPAR: (entitlement, VPs, pool, pool maximum, capped)
    'a1': (2.0, 4, 20, 8, 0), 'a2': (1.5, 2, 20, 8, 1),
    'b1': (2.0, 4, 21, 4, 0),
    'c1': (2.2, 3, 22, 6, 0), 'c2': (2.0, 2, 22, 6, 0),
    'd1': (1.0, 2, 23, 8, 0),
}


def busy(pool, day, minute):
    later = day >= TRAIN_DAYS
    if pool == 20:
        if later and day == TRAIN_DAYS + 1 and minute == 600:
            return 5.30
        if later and day == TRAIN_DAYS + 2 and 700 <= minute < 710:
            return 4.95
        return 4.60 if (day == 12 and 540 <= minute < 548) else 3.00        # an eight-minute peak
    if pool == 21:
        return 4.00 if (day in (5, 6) and 600 <= minute < 630) else 2.00
    if pool == 22:
        return 1.00 if minute % 60 == 0 else 0.60
    return 2.50


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class PoolCeilingReplayTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='pool-ceiling-')
        os.makedirs(os.path.join(cls.root, 'etc'))
        os.makedirs(os.path.join(cls.root, 'output'))
        for prog in ('nfit', 'nfit-profile'):
            shutil.copy(os.path.join(mx.REPO, prog), cls.root)
        for name in mx.TRACKED_ETC:
            src = os.path.join(mx.REPO, 'etc', name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(cls.root, 'etc', name))
        open(os.path.join(cls.root, 'etc', 'nfit.seasonality.cfg'), 'w').close()
        cache = os.path.join(cls.root, 'stage', 'SYNPC')
        os.makedirs(cache)
        days = TRAIN_DAYS + LATER_DAYS
        last = FIRST + days * 86400 - 60
        header = 'Timestamp,VMName,PhysC,RunQ,PhysB'
        with open(os.path.join(cache, '.nfit.cache.data'), 'w') as fh:
            fh.write(header + '\n2026-09-01 00:00:00,a1,1.0000,1.0000,0.5\n')
        with open(os.path.join(cache, '.nfit.cache.manifest'), 'w') as fh:
            fh.write('cache_build_status: success\ndata_start: 2026-09-01\ndata_end: 2026-10-05 23:59:00\n'
                     f'data_schema_version: 2\ndata_columns: {header}\ndata_revision: synthetic-1\n')
        states = {}
        for vm, (ent, vp, pool, size, capped) in MEMBERS.items():
            states[vm] = [{'start_epoch': FIRST, 'end_epoch': last, 'duration_days': days, 'metadata': {
                'proc_clock': '3800MHz', 'serial_number': 'SYNPC', 'proc_type': 'PowerPC_POWER10', 'proc_version': '2.0',
                'max_cpu': vp, 'smt': 8, 'entitlement': ent, 'capped': capped, 'virtual_cpus': vp, 'pool_id': pool, 'pool_cpu': size}}]
        with open(os.path.join(cache, '.nfit.cache.states'), 'w') as fh:
            json.dump(states, fh)
        with open(os.path.join(cache, '.nfit_stage_id'), 'w') as fh:
            fh.write('synthetic\n')
        reporter = {20: 'a1', 21: 'b1', 22: 'c1', 23: 'd1'}
        size = {20: 8, 21: 4, 22: 6, 23: 8}
        with gzip.open(os.path.join(cache, '.nfit.cache.pool.observations.csv.gz'), 'wt') as fh:
            fh.write(','.join(mx.HEADER) + '\n')
            for day in range(days):
                rows = []
                for minute in range(1440):
                    t = FIRST + day * 86400 + minute * 60
                    for pool in (20, 21, 22, 23):
                        if pool == 23 and day == 9:                 # one training day without observations
                            continue
                        rows.append(mx.obs(t + 15, reporter[pool], pool, size[pool], busy(pool, day, minute), 20.0))
                fh.write('\n'.join(rows) + '\n')
        proc = subprocess.run(['perl', os.path.join(cls.root, 'nfit-profile'), '--nmondir', cache,
                               '--research-pool-ceiling-replay', '2026-09-01/2026-09-30/2026-10-05'],
                              cwd=cls.root, capture_output=True, text=True, timeout=280)
        cls.proc = proc
        path = os.path.join(cls.root, 'output', 'nfit-profile.pool-ceiling-replay.json')
        cls.text = ''
        if os.path.exists(path):
            with open(path) as fh:
                cls.text = fh.read()
        cls.data = json.loads(cls.text) if cls.text else {}
        cls.pools = {p['pool']: p for p in cls.data.get('pools', [])}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_runs_without_sizing_and_names_no_lpar(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-1500:])
        self.assertEqual(self.data['status'], 'evaluated')
        self.assertEqual(sorted(self.pools), [20, 21, 22, 23])
        for vm in MEMBERS:
            self.assertNotIn(f'"{vm}"', self.text)
        self.assertEqual([f for f in os.listdir(os.path.join(self.root, 'output')) if f.endswith('.csv')], [])

    def test_candidate_keeps_the_training_peak_outside_the_tolerance(self):
        p = self.pools[20]
        self.assertEqual((p['installedMaximum'], p['members'], p['cappedMembers']), (8, 2, 1))   # the capped member counts
        self.assertEqual(p['installedEntitlement'], 3.5)
        self.assertEqual((p['structuralMinimum'], p['workloadCandidate'], p['candidate'], p['unitsRemoved']), (4, 5, 5, 3))
        self.assertAlmostEqual(p['training']['peak'], 4.6, places=3)
        self.assertEqual(p['withheld'], [])
        self.assertEqual(p['reserve'], 'unknown')
        self.assertEqual(p['training']['days'], 30)
        self.assertEqual(p['training']['coverage'], 1)

    def test_later_replay_counts_exceedances_and_newly_near_minutes(self):
        at = self.pools[20]['later']['atCandidate']
        self.assertEqual((at['exceedMinutes'], at['exceedDays'], at['longestExceed']), (1, 1, 1))
        self.assertAlmostEqual(at['exceedCoreMinutes'], 0.30, places=3)
        self.assertEqual((at['nearMinutes'], at['newlyNearMinutes'], at['nearDays']), (10, 10, 1))
        self.assertEqual(sum(w['exceedMinutes'] for k, w in at['windows'].items() if k != 'closeDays'), 1)
        # The 99th percentile would have asked for less and cost more later.
        self.assertLess(self.pools[20]['later']['atP99']['ceiling'], 5)
        self.assertGreater(self.pools[20]['later']['atP99']['exceedMinutes'], at['exceedMinutes'])

    def test_a_pool_that_was_full_is_not_reduced(self):
        p = self.pools[21]
        self.assertIn('the pool was at its maximum', p['withheld'])
        self.assertEqual(p['training']['fullMinutes'], 60)
        self.assertEqual(p['unitsRemoved'], 0)

    def test_the_members_entitlements_govern_a_quiet_pool(self):
        p = self.pools[22]
        self.assertEqual((p['structuralMinimum'], p['workloadCandidate'], p['candidate'], p['unitsRemoved']), (5, 2, 5, 1))
        self.assertEqual(p['later']['atCandidate']['exceedMinutes'], 0)
        self.assertEqual(p['later']['atCandidate']['newlyNearMinutes'], 0)

    def test_a_missing_day_withholds_the_reduction(self):
        p = self.pools[23]
        self.assertIn('a day below 90% coverage', p['withheld'])
        self.assertIn('coverage below 99% of the minutes', p['withheld'])
        # The second coverage policy is recorded beside it: 29 well-covered days and 96.7% overall pass.
        self.assertEqual(p['training']['goodDays'], 29)
        self.assertEqual(p['training']['lowDays'], [{'date': '2026-09-10', 'coverage': 0, 'closeDay': False}])
        self.assertTrue(p['coverageAlt'])
        self.assertTrue(self.pools[20]['coverageAlt'])

    def test_history_length_changes_the_candidate(self):
        # The peak lies 18 days before the cutoff: the last 14 days do not contain it.
        lengths = self.pools[20]['experiment']['lengths']
        self.assertEqual((lengths['all']['ceiling'], lengths['30']['ceiling'], lengths['14']['ceiling']), (5, 5, 4))
        self.assertAlmostEqual(lengths['14']['peak'], 3.0, places=3)
        self.assertEqual(lengths['14']['exceedMinutes'], 11)             # judged on the later period at one minute
        self.assertEqual(lengths['all']['exceedMinutes'], 1)

    def test_coarse_data_hides_a_short_peak(self):
        coarse = self.pools[20]['experiment']['coarse']
        mean = {m['offset']: m for m in coarse['mean']}
        point = {m['offset']: m for m in coarse['point']}
        # Eight minutes at 4.60 inside a 15-minute bucket of 3.00: the mean reads 3.85, and the candidate is a unit lower.
        self.assertAlmostEqual(mean[0]['peak'], (8 * 4.6 + 7 * 3.0) / 15, places=3)
        self.assertEqual(mean[0]['ceiling'], 4)
        self.assertEqual(mean[0]['exceedMinutes'], 11)
        self.assertEqual(mean[0]['buckets'], 30 * 96)
        # A single reading every 15 minutes sees the peak or misses it, by offset.
        self.assertEqual((point[0]['ceiling'], point[5]['ceiling'], point[10]['ceiling']), (5, 5, 4))
        self.assertEqual(sorted(mean), [0, 5, 10])

    def test_margins_are_reported_not_applied(self):
        margins = self.pools[20]['experiment']['margins']
        self.assertEqual([margins['minuteAll'][k]['ceiling'] for k in ('0.00', '0.05', '0.10', '0.15', '0.25')], [5, 5, 6, 6, 6])
        self.assertEqual(margins['minuteAll']['0.10']['exceedMinutes'], 0)
        self.assertEqual([margins['coarseMean'][k]['ceiling'] for k in ('0.00', '0.25')], [4, 5])
        self.assertEqual(self.pools[20]['candidate'], 5)                 # the reference candidate carries no margin


if __name__ == '__main__':
    unittest.main()
