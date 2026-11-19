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


if __name__ == '__main__':
    unittest.main()
