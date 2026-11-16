#!/usr/bin/env python3
# ==============================================================================
# Pool evidence store (nfit-profile, v10 milestone 1.1d): the reduced pool
# minutes kept in <cache>/.nfit.store/pool must give exactly what reducing the
# pool observations gives. On the member exposure fixture (two days, pools with
# a physical layer, PhysB), three copies of one system are compared:
#   - the store disabled (NFIT_POOL_STORE=0): the observations are reduced;
#   - the store cold: the days are reduced and written;
#   - the store warm: every day is read from the store.
# Compared: the history month (PoolHistory, MemberExposure and their markers,
# without their run timestamps) and the Entitlement Cut Exposure section of a
# --pool-exposure run. Also checked: a warm run reads no observation, and new
# pool observations (a new revision) empty and rebuild the store.
# ==============================================================================
import glob
import gzip
import importlib.util
import json
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('member_exposure_test', os.path.join(HERE, 'member_exposure_test.py'))
mx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mx)
SERIAL, MONTH = mx.SERIAL, mx.MONTH
STORE = os.path.join('.nfit.store', 'pool')
TIMESTAMP_KEYS = {'availableAt', 'LastUpdated', 'lastUpdated', 'generatedAt', 'GeneratedAt', 'createdAt'}


def strip_times(x):
    if isinstance(x, dict):
        return {k: strip_times(v) for k, v in x.items() if k not in TIMESTAMP_KEYS}
    if isinstance(x, list):
        return [strip_times(v) for v in x]
    return x


@unittest.skipUnless(mx.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class PoolStoreTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        mx.MemberExposureTests.roots = []
        cls.off, cls.on = mx.MemberExposureTests.make_root(), mx.MemberExposureTests.make_root()
        # The history signature includes the compressed source bytes (gzip timestamp included).
        pool_file = os.path.join('stage', SERIAL, '.nfit.cache.pool.observations.csv.gz')
        shutil.copyfile(os.path.join(cls.off, pool_file), os.path.join(cls.on, pool_file))
        cls.roots = list(mx.MemberExposureTests.roots)
        history = ('--update-history', '--min-history-days', '1', '--pool-history', '--force')
        cls.off_history = cls.nfp(cls.off, history, store=False)
        cls.cold_history = cls.nfp(cls.on, history)
        cls.cold_month = cls.month(cls.on)
        cls.warm_history = cls.nfp(cls.on, history)
        cls.warm_month = cls.month(cls.on)
        cls.off_cut = cls.cut_section(cls.off, store=False)
        cls.on_cut = cls.cut_section(cls.on)

    @classmethod
    def tearDownClass(cls):
        for r in cls.roots:
            shutil.rmtree(r, ignore_errors=True)

    @classmethod
    def nfp(cls, root, flags, store=True):
        env = dict(os.environ)
        env.pop('NFIT_POOL_STORE', None)
        if not store:
            env['NFIT_POOL_STORE'] = '0'
            env['NFIT_SAMPLE_STORE'] = '0'
        p = subprocess.run(['perl', os.path.join(root, 'nfit-profile'), '--nmondir', os.path.join(root, 'stage', SERIAL)]
                           + list(flags), capture_output=True, text=True, timeout=280, cwd=root, env=env)
        assert p.returncode == 0, p.stderr[-1500:]
        return p

    @staticmethod
    def month(root):
        with open(os.path.join(root, 'history', SERIAL, f'nfit.hist.{MONTH}.json')) as fh:
            return json.load(fh)[MONTH]

    @classmethod
    def cut_section(cls, root, store=True):
        for old in glob.glob(os.path.join(root, 'output', '*')):
            os.remove(old)
        cls.nfp(root, ('--pool-exposure', '--runq-perc-behavior=match'), store)
        logs = [f for f in glob.glob(os.path.join(root, 'output', '*.log')) if 'state-based' in f]
        assert logs, os.listdir(os.path.join(root, 'output'))
        with open(logs[0], encoding='utf-8') as fh:
            text = fh.read()
        start = text.index('\nEntitlement Cut Exposure')
        end = text.index('\nRecommended Actions', start)
        return text[start:end]

    def pool_parts(self, month):
        keep = ('PoolHistory', 'MemberExposure')
        meta = {k: v for k, v in month['Metadata'].items() if k in ('PoolEvidence', 'MemberExposureEvidence')}
        return strip_times({**{k: month[k] for k in keep}, 'Metadata': meta})

    def test_history_month_is_identical_with_and_without_the_store(self):
        off = self.pool_parts(self.month(self.off))
        self.assertTrue(off['PoolHistory'] and off['MemberExposure'])
        self.assertIn('Member CPU samples from the sample store', self.cold_history.stderr)
        self.assertNotIn('Member CPU samples from the sample store', self.off_history.stderr)
        self.assertEqual(off, self.pool_parts(self.cold_month), 'store written on the first run')
        self.assertEqual(off, self.pool_parts(self.warm_month), 'store read on the second run')

    def test_cut_exposure_is_identical_with_and_without_the_store(self):
        self.assertIn('vm', self.off_cut)
        self.assertEqual(self.off_cut, self.on_cut)

    def test_store_files_and_a_warm_run_reads_no_observation(self):
        d = os.path.join(self.on, 'stage', SERIAL, STORE)
        with open(os.path.join(d, 'store.json')) as fh:
            store = json.load(fh)
        self.assertEqual(sorted(store['days']), sorted(f[:-4] for f in os.listdir(d) if f.endswith('.tsv')))
        self.assertEqual(len(store['days']), 2)
        self.assertEqual(store['physicalColumns'], 1)
        self.assertRegex(self.cold_history.stderr, r'0 day\(s\) from the pool evidence store, 2 reduced')
        self.assertRegex(self.warm_history.stderr, r' 0 row\(s\) read, 0 in the window\(s\); 2 day\(s\) from the pool evidence store, 0 reduced')
        off = os.path.join(self.off, 'stage', SERIAL, STORE)
        self.assertFalse(os.path.exists(off), 'NFIT_POOL_STORE=0 must not write a store')

    def test_store_missing_an_invalid_runq_row_uses_original_exposure_population(self):
        root = mx.MemberExposureTests.make_root()
        self.roots.append(root)
        data = os.path.join(root, 'stage', SERIAL, '.nfit.cache.data')
        with open(data) as fh:
            rows = fh.readlines()
        fields = rows[1].split(',')
        fields[3] = 'missing'
        rows[1] = ','.join(fields)
        with open(data, 'w') as fh:
            fh.writelines(rows)
        flags = ('--update-history', '--min-history-days', '1', '--pool-history', '--force')
        enabled = self.nfp(root, flags)
        self.assertNotIn('Member CPU samples from the sample store', enabled.stderr)
        # PhysC and PhysB did not change. The missing RunQ must not discard
        # a minute of observed exposure, even though the engine skips the row.
        self.nfp(root, flags, store=False)
        expected = self.month(root)['MemberExposure']
        self.nfp(root, flags)
        self.assertEqual(self.month(root)['MemberExposure'], expected)

    def test_missing_populated_day_is_rebuilt(self):
        root = mx.MemberExposureTests.make_root()
        self.roots.append(root)
        flags = ('--update-history', '--min-history-days', '1', '--pool-history', '--force')
        self.nfp(root, flags, store=False)
        expected = self.pool_parts(self.month(root))
        self.nfp(root, flags)
        store_dir = os.path.join(root, 'stage', SERIAL, STORE)
        day_file = sorted(glob.glob(os.path.join(store_dir, '*.tsv')))[0]
        os.remove(day_file)
        repaired = self.nfp(root, flags)
        self.assertRegex(repaired.stderr, r'1 day\(s\) from the pool evidence store, 1 reduced')
        self.assertTrue(os.path.isfile(day_file))
        self.assertEqual(self.pool_parts(self.month(root)), expected)

    def test_new_pool_observations_rebuild_the_store(self):
        root = mx.MemberExposureTests.make_root()
        self.roots.append(root)
        flags = ('--update-history', '--min-history-days', '1', '--pool-history', '--force')
        self.nfp(root, flags)
        d = os.path.join(root, 'stage', SERIAL)
        path = os.path.join(d, '.nfit.cache.pool.observations.csv.gz')
        with gzip.open(path, 'rt') as fh:
            lines = fh.read().splitlines()
        with gzip.open(path, 'wt') as fh:                     # the header and the first half of the rows
            fh.write('\n'.join(lines[: 1 + (len(lines) - 1) // 2]) + '\n')
        p = self.nfp(root, flags)
        self.assertRegex(p.stderr, r'0 day\(s\) from the pool evidence store, 2 reduced')
        with open(os.path.join(d, STORE, 'store.json')) as fh:
            days = json.load(fh)['days']
        self.assertCountEqual(days.values(), ['stored', 'empty'])
        warm = self.nfp(root, flags)
        self.assertRegex(warm.stderr, r' 0 row\(s\) read, 0 in the window\(s\); 2 day\(s\) from the pool evidence store, 0 reduced')
        rebuilt = self.month(root)
        control = mx.MemberExposureTests.make_root()
        self.roots.append(control)
        shutil.copy(path, os.path.join(control, 'stage', SERIAL, '.nfit.cache.pool.observations.csv.gz'))
        self.nfp(control, flags, store=False)
        self.assertEqual(self.pool_parts(rebuilt), self.pool_parts(self.month(control)))


if __name__ == '__main__':
    unittest.main()
