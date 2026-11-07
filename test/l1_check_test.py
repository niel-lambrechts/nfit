#!/usr/bin/env python3
# ==============================================================================
# Tests for dev/l1-check: caches built by the stager (schema 1 and 2, with the
# pool sidecar) pass; damaged copies fail on the right check; hand-made caches
# cover ordering, negative values and leftovers. Output must not name VMs,
# hosts or systems. Synthetic data only.
# ==============================================================================
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
CHECK = os.path.join(HERE, '..', 'dev', 'l1-check')

import stager_pool_test as pool_fixture  # noqa: E402  (dataset, fake server, Phase 2 stand-in)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)


@unittest.skipUnless(pool_fixture.HAVE_CLIENT, 'influxdb client package not installed')
class L1CheckTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.harness = pool_fixture.PoolCaptureTests
        cls.harness.setUpClass()
        cls.tmp = tempfile.mkdtemp(prefix='l1check-')
        cls.secret = os.path.join(cls.tmp, 'secret')
        write(cls.secret, 'e' * 64 + '\n')
        runner = cls.harness('test_off_by_default_writes_no_pool_file_or_keys')
        runner.fake.faults = []
        cls.stage2 = os.path.join(cls.tmp, 'stage2')
        cls.stage1 = os.path.join(cls.tmp, 'stage1')
        rc2 = runner.run_main('--mgsys', 'S1,S2', '--startd', '2026-10-01', '--endd', '2026-10-01',
                              '--l1-schema', '2', '--pool-capture', 'required', stage=cls.stage2)[0]
        rc1 = runner.run_main('--mgsys', 'S1,S2', '--startd', '2026-10-01', '--endd', '2026-10-01',
                              stage=cls.stage1)[0]
        assert (rc1, rc2) == (0, 0), (rc1, rc2)

    @classmethod
    def tearDownClass(cls):
        cls.harness.tearDownClass()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def check(self, *dirs):
        r = subprocess.run([sys.executable, CHECK, '--pseudonym-secret', self.secret] + list(dirs),
                           capture_output=True, text=True)
        for name in ('vmA', 'vmB', 'vmC', 'vmD', 'S1', 'S2'):
            self.assertNotIn(name, r.stdout + r.stderr, f'{name} appears in l1-check output')
        return r.returncode, r.stdout

    def copy(self, stage, serial='S1'):
        dst = tempfile.mkdtemp(prefix='l1copy-', dir=self.tmp)
        shutil.copytree(os.path.join(stage, serial), os.path.join(dst, serial))
        return os.path.join(dst, serial)

    # -- stager-built caches pass -------------------------------------------------
    def test_schema_two_with_pool_passes(self):
        rc, out = self.check(self.stage2)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count(': OK'), 2)
        self.assertIn('schema 2; 360 rows; 3 VM(s)', out)
        self.assertIn('pool capture complete; 360 observations, 3 reporter(s), 2 pool(s), 3 gzip member(s)', out)

    def test_schema_one_passes(self):
        rc, out = self.check(self.stage1)
        self.assertEqual(rc, 0, out)
        self.assertIn('schema 1; 360 rows', out)

    # -- damage is detected ----------------------------------------------------------
    def test_changed_cell_breaks_data_revision(self):
        d = self.copy(self.stage2)
        p = os.path.join(d, '.nfit.cache.data')
        with open(p) as fh:
            lines = fh.read().splitlines()
        cells = lines[5].split(',')
        cells[2] = '9.9'
        lines[5] = ','.join(cells)
        write(p, '\n'.join(lines) + '\n')
        rc, out = self.check(d)
        self.assertEqual(rc, 1)
        self.assertIn('FAIL data_revision', out)

    def test_wrong_cell_count_and_bad_number(self):
        d = self.copy(self.stage1)
        p = os.path.join(d, '.nfit.cache.data')
        with open(p) as fh:
            lines = fh.read().splitlines()
        lines[3] += ',1.0'
        c = lines[4].split(',')
        c[3] = 'abc'
        lines[4] = ','.join(c)
        write(p, '\n'.join(lines) + '\n')
        rc, out = self.check(d)
        self.assertEqual(rc, 1)
        self.assertIn('FAIL cell count: 1 occurrence(s); e.g. row 4 has 5 of 4', out)
        self.assertIn('FAIL numeric: 1 occurrence(s); e.g. row 5 column RunQ', out)

    def test_vm_without_states_fails(self):
        d = self.copy(self.stage1)
        p = os.path.join(d, '.nfit.cache.states')
        with open(p) as fh:
            states = json.load(fh)
        states.pop('vmB')
        write(p, json.dumps(states))
        rc, out = self.check(d)
        self.assertEqual(rc, 1)
        self.assertIn('FAIL states: 1 VM(s) in the data have no states', out)

    def test_truncated_pool_file_fails(self):
        d = self.copy(self.stage2)
        p = os.path.join(d, '.nfit.cache.pool.observations.csv.gz')
        with open(p, 'rb') as fh:
            data = fh.read()
        with open(p, 'wb') as fh:
            fh.write(data[:-40])
        rc, out = self.check(d)
        self.assertEqual(rc, 1)
        self.assertIn('FAIL pool_revision', out)
        self.assertIn('FAIL pool gzip: stream damaged', out)

    def test_pool_row_with_unknown_series_fails(self):
        d = self.copy(self.stage2)
        p = os.path.join(d, '.nfit.cache.pool.observations.csv.gz')
        with gzip.open(p, 'rt') as fh:
            lines = fh.read().splitlines()
        c = lines[1].split(',')
        c[2] = 'ffffffffffff'
        lines[1] = ','.join(c)
        with gzip.open(p, 'wt') as fh:
            fh.write('\n'.join(lines) + '\n')
        rc, out = self.check(d)
        self.assertEqual(rc, 1)
        self.assertIn('FAIL pool series: 1 occurrence(s); e.g. row 2 series_id not in the series file', out)

    def test_leftover_work_file_warns(self):
        d = self.copy(self.stage2)
        write(os.path.join(d, '.nfit.cache.pool.observations.csv.gz.work'), '')
        rc, out = self.check(d)
        self.assertEqual(rc, 0, out)
        self.assertIn('WARN leftovers', out)

    # -- hand-made caches -------------------------------------------------------------
    def handmade(self, rows, states=None):
        d = os.path.join(tempfile.mkdtemp(prefix='l1hand-', dir=self.tmp), 'SYNX')
        write(os.path.join(d, '.nfit.cache.manifest'),
              'cache_build_status: success\ndata_start: 2026-10-01\ndata_end: 2026-10-01 00:01:00\n')
        write(os.path.join(d, '.nfit.cache.data'), 'Timestamp,VMName,PhysC,RunQ\n' + '\n'.join(rows) + '\n')
        write(os.path.join(d, '.nfit.cache.states'), json.dumps(states or {'web01': [], 'Web02': []}))
        return d

    def test_collation_order_warns_and_time_order_fails(self):
        d = self.handmade(['2026-10-01 00:00:00,web01,1.0,1', '2026-10-01 00:00:00,Web02,1.0,1',
                           '2026-10-01 00:01:00,web01,1.0,1'])
        rc, out = self.check(d)
        self.assertEqual(rc, 0, out)
        self.assertIn('WARN VM order', out)
        d = self.handmade(['2026-10-01 00:01:00,web01,1.0,1', '2026-10-01 00:00:00,Web02,1.0,1'])
        rc, out = self.check(d)
        self.assertEqual(rc, 1)
        self.assertIn('FAIL timestamp order', out)

    def test_negative_value_fails_and_data_end_warns(self):
        d = self.handmade(['2026-10-01 00:00:00,web01,-1.0,1', '2026-10-01 00:02:30,Web02,1.0,1'])
        rc, out = self.check(d)
        self.assertEqual(rc, 1)
        self.assertIn('FAIL negative value: 1 occurrence(s); e.g. row 2 column PhysC', out)
        self.assertIn('WARN data_end', out)


if __name__ == '__main__':
    unittest.main(verbosity=1)
