#!/usr/bin/env python3
# ==============================================================================
# Unit tests for dev/golden: the --classify listing (a changed cell outside
# the intended changes is listed even when more than 20 expected current-cycle
# cells changed before it), fresh timestamps on scratch copies, and
# capture --resume adding only the combinations a baseline lacks.
# ==============================================================================
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.abspath(os.path.join(HERE, '..', 'dev', 'golden'))


def _load_golden():
    loader = importlib.machinery.SourceFileLoader('golden_harness', GOLDEN)
    spec = importlib.util.spec_from_loader('golden_harness', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ClassifyCsvListing(unittest.TestCase):
    def test_unintended_cell_survives_the_listing_cap(self):
        golden = _load_golden()
        # The v10 layout: a column moved to the rationale and one added, so that
        # rVCPU_Tactical cells count as completed by the current-cycle pair.
        base = 'VM,TIER,NETT,P-99W1_rVCPU_Tactical\n' + ''.join(f'vm{i:02d},G3,0,1\n' for i in range(25)) + 'vmzz,O3,0,1\n'
        cur = ('VM,TIER,Planning Profile,P-99W1_rVCPU_Tactical\n' + ''.join(f'vm{i:02d},G3,G3-95W15,2\n' for i in range(25))
               + 'vmzz,O4,O4-90W15,1\n')
        c = golden.classify_csv(base, cur)
        self.assertEqual(c['cells'], 1)
        self.assertEqual(c['completed'], 25)
        self.assertEqual(c['cell_lines'], ['vmzz TIER: O3 -> O4'])
        self.assertEqual(len(c['completed_lines']), 20)


class _Out:
    def __init__(self):
        self.lines = []

    def say(self, text):
        self.lines.append(text)


class ScratchCopies(unittest.TestCase):
    def test_configuration_copies_get_fresh_timestamps(self):
        golden = _load_golden()
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp, 'src'), Path(tmp, 'dst')
            src.mkdir()
            (src / 'nfit.profiles.cfg').write_text('x')
            (src / 'influxdb.conf').write_text('secret')
            old = time.time() - 30 * 86400
            os.utime(src / 'nfit.profiles.cfg', (old, old))
            golden.copy_nfit_config(src, dst)
            self.assertGreater((dst / 'nfit.profiles.cfg').stat().st_mtime, time.time() - 3600)
            self.assertFalse((dst / 'influxdb.conf').exists())


class CaptureResume(unittest.TestCase):
    def test_only_the_missing_combination_is_captured(self):
        golden = _load_golden()
        with tempfile.TemporaryDirectory() as tmp:
            root, stage = Path(tmp, 'golden'), Path(tmp, 'stage')
            (stage / 'sysA').mkdir(parents=True)
            dest = root / 'base'
            (dest / 'frozen' / 'etc').mkdir(parents=True)
            (dest / 'results' / 'sysA' / 'decay-states').mkdir(parents=True)
            (dest / 'results' / 'sysA' / 'decay-states' / 'ERROR.txt').write_text('cached run failed')
            meta = {'name': 'base', 'stage_dir': str(stage), 'enddate': '2026-10-31',
                    'runs': ['forensic', 'decay-states'],
                    'systems': {'sysA': {'data_identity': 'ID', 'runs': {'forensic': {'csv': 'f'}}}}}
            (dest / 'meta.json').write_text(json.dumps(meta))
            calls = []
            golden.GOLDEN_ROOT = root
            golden.data_identity = lambda sysdir: 'ID'
            golden.perl_env = lambda: {}

            def fake_execute(system, sysdir, run, enddate, exe_src, etc_src, env, **kw):
                calls.append((system, run, enddate, exe_src))
                return {'outputs': {'csv': 'c\n', 'log': 'l\n'}}
            golden.execute = fake_execute
            out = _Out()
            self.assertEqual(golden.resume_capture('base', out), 0)
            self.assertEqual(calls, [('sysA', 'decay-states', '2026-10-31', dest / 'frozen')])
            stored = json.loads((dest / 'meta.json').read_text())
            self.assertEqual(sorted(stored['systems']['sysA']['runs']), ['decay-states', 'forensic'])
            self.assertEqual(stored['systems']['sysA']['runs']['forensic'], {'csv': 'f'})
            rdir = dest / 'results' / 'sysA' / 'decay-states'
            self.assertFalse((rdir / 'ERROR.txt').exists())
            self.assertEqual((rdir / 'decay-states.csv').read_text(), 'c\n')
            # A second resume finds nothing to do.
            calls.clear()
            self.assertEqual(golden.resume_capture('base', _Out()), 0)
            self.assertEqual(calls, [])

    def test_changed_data_is_not_resumed(self):
        golden = _load_golden()
        with tempfile.TemporaryDirectory() as tmp:
            root, stage = Path(tmp, 'golden'), Path(tmp, 'stage')
            (stage / 'sysA').mkdir(parents=True)
            dest = root / 'base'
            (dest / 'frozen').mkdir(parents=True)
            meta = {'name': 'base', 'stage_dir': str(stage), 'enddate': '2026-10-31', 'runs': ['forensic'],
                    'systems': {'sysA': {'data_identity': 'OLD', 'runs': {}}}}
            (dest / 'meta.json').write_text(json.dumps(meta))
            golden.GOLDEN_ROOT = root
            golden.data_identity = lambda sysdir: 'NEW'
            golden.perl_env = lambda: {}
            golden.execute = lambda *a, **k: self.fail('must not run')
            out = _Out()
            self.assertEqual(golden.resume_capture('base', out), 1)
            self.assertTrue(any('not resumed' in line for line in out.lines))


class ProfileArgs(unittest.TestCase):
    def test_extra_options_and_reserved_ones(self):
        golden = _load_golden()
        self.assertEqual(golden.parse_profile_args('--no-cut-protection --pool-exposure'),
                         ['--no-cut-protection', '--pool-exposure'])
        self.assertEqual(golden.parse_profile_args(''), [])
        with self.assertRaises(ValueError):
            golden.parse_profile_args('--enddate=2026-10-31')
        with self.assertRaises(ValueError):
            golden.parse_profile_args('--nmondir /x')


class PoolEvidenceStaging(unittest.TestCase):
    def test_pool_observations_are_linked_and_identified(self):
        golden = _load_golden()
        self.assertIn('.nfit.cache.pool.observations.csv.gz', golden.STAGE_FILES)
        with tempfile.TemporaryDirectory() as tmp:
            sysdir = Path(tmp, 'stage', 'SYS1')
            sysdir.mkdir(parents=True)
            for name in ('.nfit.cache.data', '.nfit.cache.states'):
                (sysdir / name).write_text('x')
            old = golden.data_identity(sysdir)                      # a baseline taken before pool files
            self.assertNotIn('.nfit.cache.pool.observations.csv.gz', old)
            (sysdir / '.nfit.cache.pool.observations.csv.gz').write_bytes(b'pool')
            self.assertFalse(golden.data_changed(sysdir, old), 'a file the baseline did not record is not compared')
            new = golden.data_identity(sysdir)
            self.assertIn('.nfit.cache.pool.observations.csv.gz', new)
            (sysdir / '.nfit.cache.pool.observations.csv.gz').write_bytes(b'pool, changed')
            self.assertTrue(golden.data_changed(sysdir, new))

    def test_protection_summary(self):
        golden = _load_golden()
        evaluated = ('Cut Protection\n' + '=' * 70 + '\n  ...\n'
                     '  Summary: 3 planning-profile cuts, 2 limited by the protected level; 0.11 cores of entitlement kept.\n')
        self.assertEqual(golden.protection_summary(evaluated), 'cut protection: 3 cut(s), 2 limited')
        missing = ('Cut Protection\n' + '=' * 70 + '\n  Recorded, not applied.\n\n'
                   '  Not evaluated: the cache holds no pool observations.\n')
        self.assertEqual(golden.protection_summary(missing), 'cut protection: not evaluated (the cache holds no pool observations)')
        self.assertEqual(golden.protection_summary('no such section'), 'cut protection: section absent')
        unavailable = '  Pool competition for entitlement cuts could not be checked: the CPU cache declares no PhysB column.\n'
        self.assertEqual(golden.protection_summary(unavailable), 'cut protection: not run (the CPU cache declares no PhysB column)')


if __name__ == '__main__':
    unittest.main()
