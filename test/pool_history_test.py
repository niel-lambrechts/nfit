#!/usr/bin/env python3
# ==============================================================================
# Tests for the observed pool projection in monthly history:
# nfit-profile --update-history --pool-history on a small synthetic cache.
#
# Covered: one reading per reporter and minute (nearest the minute midpoint),
# the median across reporters (never a sum), collapsed duplicates, excluded
# conflicting series, a valid zero against a missing minute, invalid and
# incredible readings, daily statistics by linear interpolation, segments
# split on a member's configuration change, reported pool size with conflict
# and observation-only cases, reserved capacity and plans left unrecorded, no
# scarcity key anywhere, an unchanged second run, unrelated history left as it
# was, a stored block carried through a re-analysis, a changed source
# recomputed, and a cache without pool observations.
# ==============================================================================
import calendar
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
TRACKED_ETC = ('nfit.profiles.cfg', 'nfit.profiles.cfg.default', 'nfit.mgsys.cfg.default',
               'nfit.scenarios.cfg.default')
SERIAL = 'SYNPH'
MONTH = '2026-09'
DAY1 = calendar.timegm((2026, 9, 1, 0, 0, 0))
DAYS = 3
STEP = 900
NS = 1_000_000_000
HEADER = ['time_ns', 'host', 'series_id', 'pool_id', 'phys_cpus_pool', 'idle_cores', 'busy_cores',
          'scaled_busy_cores', 'max_cores', 'global_pool_busy_cores', 'global_pool_tot_cores',
          'max_capacity_cores', 'entitled_capacity_cores', 'max_capacity_raw', 'entitled_capacity_raw',
          'max_capacity', 'entitled_capacity']
SPLIT = DAY1 + 86400 + 12 * 3600          # vmB's entitlement changes at 12:00 on day 2


def perl_ready():
    return (shutil.which('perl') is not None and
            subprocess.run(['perl', '-MJSON', '-MList::MoreUtils', '-e1'], capture_output=True).returncode == 0)


def obs(epoch, host, series, pool, size, busy):
    row = dict.fromkeys(HEADER, '')
    row.update({'time_ns': str(int(round(epoch * NS))), 'host': host, 'series_id': series, 'pool_id': pool,
                'phys_cpus_pool': size, 'busy_cores': busy})
    return ','.join(str(row[h]) for h in HEADER)


def pool7_level(minute_index):
    """The pool's concurrent busy in an ordinary minute."""
    return round(1.0 + (minute_index % 97) * 0.02, 4)


def pool_rows():
    """Returns (rows, expected minute values of pool 7 on day 1)."""
    rows, expected = [], {}
    for m in range(DAYS * 1440):
        t = DAY1 + 60 * m
        v = pool7_level(m)
        special = m if m < 1440 else None
        if special == 5:        # two readings from one reporter: the one nearest the midpoint counts
            rows += [obs(t + 29, 'vmA', 'sA', 7, 4, v), obs(t + 59, 'vmA', 'sA', 7, 4, 3.9),
                     obs(t + 20, 'vmB', 'sB', 7, 4, v)]
            expected[m] = v
        elif special == 6:      # reporters differ: the median, never the sum
            rows += [obs(t + 10, 'vmA', 'sA', 7, 4, 2.0), obs(t + 20, 'vmB', 'sB', 7, 4, 3.0)]
            expected[m] = 2.5
        elif special == 7:      # an exact duplicate collapses
            rows += [obs(t + 10, 'vmA', 'sA', 7, 4, v), obs(t + 10, 'vmA', 'sA', 7, 4, v),
                     obs(t + 20, 'vmB', 'sB', 7, 4, v)]
            expected[m] = v
        elif special == 8:      # two series of one reporter disagree: that reporter is excluded
            rows += [obs(t + 10, 'vmA', 's1', 7, 4, 1.0), obs(t + 12, 'vmA', 's2', 7, 4, 3.0),
                     obs(t + 20, 'vmB', 'sB', 7, 4, 2.2)]
            expected[m] = 2.2
        elif special == 9:      # equally near the midpoint with different values: excluded
            rows += [obs(t + 20, 'vmA', 'sA', 7, 4, 1.0), obs(t + 40, 'vmA', 'sA', 7, 4, 3.0),
                     obs(t + 20, 'vmB', 'sB', 7, 4, 2.4)]
            expected[m] = 2.4
        elif special == 10:     # a valid zero is a measurement
            rows += [obs(t + 10, 'vmA', 'sA', 7, 4, 0), obs(t + 20, 'vmB', 'sB', 7, 4, 0)]
            expected[m] = 0.0
        elif special == 11:     # no reading at all
            pass
        elif special == 12:     # invalid readings only
            rows += [obs(t + 10, 'vmA', 'sA', 7, 4, 'nan'), obs(t + 20, 'vmB', 'sB', 7, 4, -1)]
        elif special == 13:     # a reading above the pool size is not credible
            rows += [obs(t + 10, 'vmA', 'sA', 7, 4, 50), obs(t + 20, 'vmB', 'sB', 7, 4, 1.0)]
            expected[m] = 1.0
        elif special == 14:     # a reading without a busy value
            rows += [obs(t + 10, 'vmA', 'sA', 7, 4, ''), obs(t + 20, 'vmB', 'sB', 7, 4, 1.5)]
            expected[m] = 1.5
        else:
            rows += [obs(t + 10, 'vmA', 'sA', 7, 4, v), obs(t + 20, 'vmB', 'sB', 7, 4, v)]
            if m < 1440:
                expected[m] = v
        if m % 5 == 0:          # sparser pools: default pool, conflicting sizes, a pool with no known member
            rows.append(obs(t + 15, 'vmE', 'sE', 0, 64, 20.5))
            rows += [obs(t + 15, 'vmC', 'sC', 9, 5, 1.0), obs(t + 16, 'vmD', 'sD', 9, 5, 1.0)]
            rows.append(obs(t + 15, 'other', 'sO', 12, 3, 0.5))
    return rows, expected


def quantile(values, p):
    s = sorted(values)
    rank = (len(s) - 1) * p
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (rank - lo)


def state(start, end, ent, vp, pool, size):
    return {'start_epoch': start, 'end_epoch': end, 'duration_days': (end - start) / 86400,
            'metadata': {'proc_clock': '3800MHz', 'serial_number': SERIAL, 'proc_type': 'PowerPC_POWER10',
                         'proc_version': '2.0', 'max_cpu': vp, 'pool_id': pool, 'smt': 8, 'pool_cpu': size,
                         'entitlement': ent, 'capped': 0, 'virtual_cpus': vp}}


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class PoolHistoryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.roots = []
        cls.rows, cls.expected = pool_rows()
        cls.root = cls.make_root()
        cls.first = cls.update(cls.root, '--pool-history', '--force')   # the fixture carries no tactical results
        cls.bytes_first = cls.raw(cls.root)
        cls.second = cls.update(cls.root, '--pool-history')
        cls.bytes_second = cls.raw(cls.root)

    @classmethod
    def tearDownClass(cls):
        for r in cls.roots:
            shutil.rmtree(r, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------------
    @classmethod
    def make_root(cls, pool=True):
        r = tempfile.mkdtemp(prefix='pool-hist-')
        cls.roots.append(r)
        os.makedirs(os.path.join(r, 'etc'))
        os.makedirs(os.path.join(r, 'output'))
        for prog in ('nfit', 'nfit-profile'):
            shutil.copy(os.path.join(REPO, prog), r)
        for name in TRACKED_ETC:
            src = os.path.join(REPO, 'etc', name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(r, 'etc', name))
        open(os.path.join(r, 'etc', 'nfit.seasonality.cfg'), 'w').close()
        d = os.path.join(r, 'stage', SERIAL)
        os.makedirs(d)
        end = DAY1 + DAYS * 86400
        lines = ['Timestamp,VMName,PhysC,RunQ']
        for e in range(DAY1, end, STEP):
            stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(e))
            for i, vm in enumerate(('vmA', 'vmB', 'vmC', 'vmD', 'vmE')):
                c = 0.5 + 0.1 * i + (0.8 if 9 <= time.gmtime(e).tm_hour < 17 else 0.0)
                lines.append(f'{stamp},{vm},{c:.4f},{c * 2:.4f}')
        with open(os.path.join(d, '.nfit.cache.data'), 'w') as fh:
            fh.write('\n'.join(lines) + '\n')
        with open(os.path.join(d, '.nfit.cache.manifest'), 'w') as fh:
            fh.write('cache_build_status: success\ndata_start: 2026-09-01\ndata_end: 2026-09-03 23:45:00\n')
        last = end - 60                      # the last sample of a one-minute capture
        states = {
            'vmA': [state(DAY1, last, 2.5, 4, 7, 4)],
            'vmB': [state(DAY1, SPLIT - 60, 1.0, 2, 7, 4), state(SPLIT, last, 2.0, 2, 7, 4)],
            'vmC': [state(DAY1, last, 1.0, 2, 9, 5)],
            'vmD': [state(DAY1, last, 1.0, 2, 9, 6)],
            'vmE': [state(DAY1, last, 4.0, 8, 0, 64)],
        }
        with open(os.path.join(d, '.nfit.cache.states'), 'w') as fh:
            json.dump(states, fh)
        with open(os.path.join(d, '.nfit_stage_id'), 'w') as fh:
            fh.write('synthetic\n')
        if pool:
            cls.write_pool(r, cls.rows)
        return r

    @staticmethod
    def write_pool(r, rows):
        path = os.path.join(r, 'stage', SERIAL, '.nfit.cache.pool.observations.csv.gz')
        half = len(rows) // 2
        with gzip.open(path, 'wt') as fh:                      # header and first slice
            fh.write(','.join(HEADER) + '\n' + '\n'.join(rows[:half]) + '\n')
        with gzip.open(path, 'at') as fh:                      # a second gzip member
            fh.write('\n'.join(rows[half:]) + '\n')

    @staticmethod
    def update(r, *flags):
        cmd = ['perl', os.path.join(r, 'nfit-profile'), '--update-history', '--min-history-days', '1',
               '--nmondir', os.path.join(r, 'stage', SERIAL)] + list(flags)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=200, cwd=r)
        assert p.returncode == 0, p.stderr[-1500:]
        return p

    @staticmethod
    def raw(r):
        with open(os.path.join(r, 'history', SERIAL, f'nfit.hist.{MONTH}.json'), 'rb') as fh:
            return fh.read()

    def month(self, r=None):
        return json.loads(self.raw(r or self.root))[MONTH]

    def pool(self, pool_id, r=None):
        return self.month(r)['PoolHistory'][f'{SERIAL}:{pool_id}']

    def day(self, pool_id, segment, date):
        return next(d for d in self.pool(pool_id)['segments'][segment]['daily'] if d['date'] == date)

    # -- the block and its marker ------------------------------------------------------
    def test_block_is_a_named_observed_projection(self):
        m = self.month()
        self.assertEqual(sorted(m['PoolHistory']), [f'{SERIAL}:0', f'{SERIAL}:12', f'{SERIAL}:7', f'{SERIAL}:9'])
        marker = m['Metadata']['PoolEvidence']
        self.assertEqual(marker['projection'], 'observed-pool-busy-1')
        self.assertEqual(marker['capabilities'], {'concurrentBusy': 'observed', 'observedConfiguration': 'observed',
                                                  'scarcity': 'notEvaluated', 'memberExposure': 'notEvaluated',
                                                  'plans': 'notRecorded'})
        self.assertEqual((marker['windowStart'], marker['windowEndExclusive'], marker['status']),
                         ('2026-09-01T00:00:00Z', '2026-09-04T00:00:00Z', 'complete'))
        self.assertEqual(marker['reduction']['acrossReporters'], 'median')
        self.assertTrue(marker['poolRevision'].startswith('sha256:'))
        self.assertIn('PoolBusy-observed-1', m['Metadata']['Features'])
        self.assertTrue(all(p['projection'] == 'observed-pool-busy-1' for p in m['PoolHistory'].values()))

    def test_no_scarcity_or_exposure_key_is_written(self):
        keys = set(re.findall(r'"([A-Za-z]+)":', json.dumps(self.month()['PoolHistory'])))
        for key in keys:
            self.assertNotRegex(key, r'(?i)scarc|idle|usable|physical|local|episode|exposure|shortfall|proposed')

    # -- reduction ----------------------------------------------------------------------
    def test_daily_statistics_come_from_the_minute_medians(self):
        d = self.day(7, 0, '2026-09-01')
        values = [self.expected[m] for m in sorted(self.expected)]
        self.assertEqual(len(values), 1438)                                   # minute 11 missing, minute 12 invalid
        self.assertEqual((d['expectedMinutes'], d['supportedMinutes']), (1440, 1438))
        self.assertAlmostEqual(d['busy']['sumCoreMinutes'], sum(values), places=3)
        for name, p in (('p50', 0.50), ('p95', 0.95), ('p99', 0.99)):
            self.assertAlmostEqual(d['busy'][name], quantile(values, p), places=4, msg=name)
        self.assertAlmostEqual(d['busy']['max'], max(values), places=4)
        self.assertLess(d['busy']['max'], 3.9)                                # 3.9 at +59 s was never selected

    def test_exclusions_and_reporter_support_are_counted(self):
        d = self.day(7, 0, '2026-09-01')
        self.assertEqual(d['excluded'], {'ambiguousReporterMinutes': 2, 'invalidReadings': 3,
                                         'duplicateObservations': 1, 'readingsWithoutBusy': 1,
                                         'sizeConflictMinutes': 0})
        self.assertEqual((d['reporters']['min'], d['reporters']['max'], d['reporters']['singleReporterMinutes']),
                         (1, 2, 4))

    def test_a_valid_zero_is_a_measurement_and_a_missing_minute_is_not(self):
        self.assertEqual(self.expected[10], 0.0)
        self.assertNotIn(11, self.expected)
        d = self.day(12, 0, '2026-09-01')                                     # one reading every five minutes
        self.assertEqual((d['expectedMinutes'], d['supportedMinutes']), (1440, 288))
        self.assertEqual(d['busy']['p50'], 0.5)

    # -- configuration ---------------------------------------------------------------------
    def test_segments_split_on_a_member_configuration_change(self):
        segments = self.pool(7)['segments']
        self.assertEqual([(s['fromUTC'], s['toUTCExclusive']) for s in segments],
                         [('2026-09-01T00:00:00Z', '2026-09-02T12:00:00Z'),
                          ('2026-09-02T12:00:00Z', '2026-09-04T00:00:00Z')])
        self.assertEqual([[(m['vm'], m['entitlement'], m['virtualProcessors']) for m in s['members']] for s in segments],
                         [[('vmA', 2.5, 4), ('vmB', 1.0, 2)], [('vmA', 2.5, 4), ('vmB', 2.0, 2)]])
        self.assertEqual([s['installedEntitlementSum'] for s in segments], [3.5, 4.5])
        self.assertEqual([s['id'] for s in segments], ['7@2026-09-01T00:00:00Z', '7@2026-09-02T12:00:00Z'])
        split_day = [next(d for d in s['daily'] if d['date'] == '2026-09-02') for s in segments]
        self.assertEqual([(d['expectedMinutes'], d['supportedMinutes']) for d in split_day], [(720, 720), (720, 720)])

    def test_capacity_is_the_reported_size_and_nothing_more(self):
        seg = self.pool(7)['segments'][0]
        self.assertEqual((seg['scope'], seg['ceiling'], seg['ceilingBasis'], seg['ceilingSource']),
                         ('user', 4, 'reportedPoolSize', 'configurationStates'))
        self.assertIsNone(seg['reserved'])
        self.assertEqual((seg['reservedBasis'], seg['membershipConfidence'], seg['plans']),
                         ('notCollected', 'unverified', []))
        conflict = self.pool(9)['segments'][0]                               # members report sizes 5 and 6
        self.assertEqual((conflict['ceiling'], conflict['ceilingBasis']), (None, 'conflict'))
        unknown = self.pool(12)['segments'][0]                               # reporters only, no member in the states
        self.assertEqual((unknown['members'], unknown['membership'], unknown['ceiling'], unknown['ceilingSource'],
                          unknown['installedEntitlementSum']), ([], 'noMemberInStates', 3, 'poolObservations', None))
        self.assertEqual(self.pool(0)['segments'][0]['scope'], 'default')

    # -- re-runs and unrelated history --------------------------------------------------------
    def test_an_identical_second_run_leaves_the_partition_unchanged(self):
        self.assertEqual(self.bytes_first, self.bytes_second)
        self.assertNotIn('Pool blocks absent', self.second.stderr)

    def test_unrelated_history_is_as_without_the_option(self):
        plain = self.make_root()
        self.update(plain, '--force', '--no-pool-history')              # pool history is automatic; switch it off
        without = json.loads(self.raw(plain))[MONTH]
        self.assertNotIn('PoolHistory', without)
        self.assertNotIn('PoolEvidence', without['Metadata'])
        self.assertEqual(without['MonthlyWorkloadAnalysis'], self.month()['MonthlyWorkloadAnalysis'])
        self.update(plain, '--pool-history')                                  # a stored month gains the block
        gained = json.loads(self.raw(plain))[MONTH]
        self.assertEqual(gained['MonthlyWorkloadAnalysis'], without['MonthlyWorkloadAnalysis'])
        self.assertEqual(gained['PoolHistory'], self.month()['PoolHistory'])
        self.assertEqual({k: v for k, v in gained['Metadata'].items()
                          if k not in ('PoolEvidence', 'Features', 'MemberExposureEvidence')},
                         {k: v for k, v in without['Metadata'].items() if k != 'Features'})
        # Without a PhysB column, member exposure is marked unavailable, never left silent.
        self.assertEqual((gained['Metadata']['MemberExposureEvidence']['status'],
                          gained['Metadata']['MemberExposureEvidence']['reason']),
                         ('unavailable', 'the CPU cache declares no PhysB column'))
        self.assertNotIn('MemberExposure', gained)

    def test_a_stored_block_survives_a_re_analysis_without_the_option(self):
        r = self.make_root()
        self.update(r, '--pool-history', '--force')
        before = json.loads(self.raw(r))[MONTH]
        self.update(r, '--force')
        after = json.loads(self.raw(r))[MONTH]
        self.assertEqual(after['PoolHistory'], before['PoolHistory'])
        self.assertEqual(after['Metadata']['PoolEvidence'], before['Metadata']['PoolEvidence'])
        self.assertIn('PoolBusy-observed-1', after['Metadata']['Features'])

    def test_a_changed_source_is_recomputed(self):
        r = self.make_root()
        self.update(r, '--pool-history', '--force')
        before = json.loads(self.raw(r))[MONTH]
        self.write_pool(r, self.rows + [obs(DAY1 + 60 * 11 + 30, 'vmA', 'sA', 7, 4, 1.7)])   # the missing minute arrives
        p = self.update(r, '--pool-history')
        self.assertIn('Pool blocks absent or out of date', p.stderr)
        after = json.loads(self.raw(r))[MONTH]
        self.assertNotEqual(after['Metadata']['PoolEvidence']['poolRevision'],
                            before['Metadata']['PoolEvidence']['poolRevision'])
        day = next(d for d in after['PoolHistory'][f'{SERIAL}:7']['segments'][0]['daily'] if d['date'] == '2026-09-01')
        self.assertEqual(day['supportedMinutes'], 1439)

    def test_a_cache_without_pool_observations_gets_no_block(self):
        r = self.make_root(pool=False)
        p = self.update(r, '--pool-history', '--force')
        self.assertIn('holds no pool observations', p.stderr)
        self.assertNotIn('PoolHistory', json.loads(self.raw(r))[MONTH])


if __name__ == '__main__':
    unittest.main(verbosity=1)
