#!/usr/bin/env python3
# ==============================================================================
# End-to-end test for the MaxCPU shadow record (plan 4.4, decision F14): an
# LPAR at its one-VP ceiling with a deep run queue has its target held by the
# MaxCPU sanity limit. The stored proposal records, for that profile, the
# target and current-cycle pair without the limit; the CSV keeps the limited
# figure. Synthetic data only.
# ==============================================================================
import calendar
import csv
import glob
import io
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
TRACKED_ETC = ('nfit.profiles.cfg', 'nfit.profiles.cfg.default', 'nfit.mgsys.cfg.default', 'nfit.scenarios.cfg.default')
FIRST = calendar.timegm((2026, 9, 1, 0, 0, 0))
DAYS = 14


def perl_ready():
    return (shutil.which('perl') is not None and
            subprocess.run(['perl', '-MJSON', '-MList::MoreUtils', '-e1'], capture_output=True).returncode == 0)


def run():
    root = tempfile.mkdtemp(prefix='maxcpu-shadow-')
    os.makedirs(os.path.join(root, 'etc'))
    os.makedirs(os.path.join(root, 'output'))
    for prog in ('nfit', 'nfit-profile'):
        shutil.copy(os.path.join(REPO, prog), root)
    for name in TRACKED_ETC:
        src = os.path.join(REPO, 'etc', name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(root, 'etc', name))
    open(os.path.join(root, 'etc', 'nfit.seasonality.cfg'), 'w').close()
    with open(os.path.join(root, 'etc', 'nfit.vms.cfg'), 'w') as fh:
        fh.write('[busy1]\ntier = G1\n')
    last = time.strftime('%Y-%m-%d', time.gmtime(FIRST + DAYS * 86400 - 60))
    cache = os.path.join(root, 'stage', 'SYSMAX1')
    os.makedirs(cache)
    rows = ['Timestamp,VMName,PhysC,RunQ']
    for e in range(FIRST, FIRST + DAYS * 86400, 60):
        stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(e))
        c = 0.98 + 0.01 * math.sin(e / 777.0)
        rows.append(f'{stamp},busy1,{c:.4f},{24 + 2 * math.sin(e / 333.0):.4f}')
    with open(os.path.join(cache, '.nfit.cache.data'), 'w') as fh:
        fh.write('\n'.join(rows) + '\n')
    with open(os.path.join(cache, '.nfit.cache.manifest'), 'w') as fh:
        fh.write(f'cache_build_status: success\ndata_start: 2026-09-01\ndata_end: {last} 23:59:00\n')
    meta = {'proc_clock': '3800MHz', 'serial_number': 'SYSMAX1', 'proc_type': 'PowerPC_POWER10', 'proc_version': '2.0',
            'max_cpu': 1, 'pool_id': 0, 'smt': 8, 'pool_cpu': 32, 'entitlement': 1.0, 'capped': 0, 'virtual_cpus': 1}
    with open(os.path.join(cache, '.nfit.cache.states'), 'w') as fh:
        json.dump({'busy1': [{'start_epoch': FIRST, 'end_epoch': FIRST + DAYS * 86400 - 60, 'duration_days': DAYS,
                              'metadata': meta}]}, fh)
    with open(os.path.join(cache, '.nfit_stage_id'), 'w') as fh:
        fh.write('synthetic\n')
    proc = subprocess.run(['perl', os.path.join(root, 'nfit-profile'), '--runq-perc-behavior=match',
                           '--enddate', last, '--nmondir', cache], capture_output=True, text=True, timeout=600, cwd=root)
    csvs = [f for f in glob.glob(os.path.join(root, 'output', '*.csv')) if '.decisions.' not in f]
    row = {}
    if csvs:
        with open(csvs[0]) as fh:
            text = fh.read().split('\n\n')[0]
        row = next(r for r in csv.DictReader(io.StringIO(text)) if r.get('VM') == 'busy1')
    snaps = glob.glob(os.path.join(cache, '.nfit.store', 'proposals', 'v1', '*', '*.json'))
    snap = {}
    if len(snaps) == 1:
        with open(snaps[0]) as fh:
            snap = json.load(fh)
    shutil.rmtree(root, ignore_errors=True)
    return proc, row, snap


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class MaxCpuShadowTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.proc, cls.row, cls.snap = run()

    def test_run_succeeds(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-1500:])

    def test_limited_profiles_are_recorded_and_the_csv_keeps_the_limit(self):
        profiles = self.snap['members']['busy1']['profiles']
        held = {p: v['maxCpuLimit'] for p, v in profiles.items() if 'maxCpuLimit' in v}
        self.assertTrue(held, 'the deep queue at the one-VP ceiling must reach the MaxCPU limit on some profile')
        for name, rec in held.items():
            self.assertEqual(float(rec['limit']), 1.75)            # MaxCPU 1 x 1.75 for an entitlement of 1.0
            self.assertGreater(rec['uncappedTarget'], rec['target'])
            self.assertEqual(float(self.row[name]), rec['target'])  # the CSV keeps the limited figure
            self.assertEqual(rec['pair']['entitlement'], profiles[name]['currentCycle']['entitlement'])
            self.assertGreaterEqual(rec['uncappedPair']['entitlement'], rec['pair']['entitlement'])
            self.assertIn('RawAdditive', rec['chain'])
        unheld = [p for p, v in profiles.items() if 'maxCpuLimit' not in v]
        for name in unheld:
            self.assertNotIn('maxCpuLimit', profiles[name])


if __name__ == '__main__':
    unittest.main()
