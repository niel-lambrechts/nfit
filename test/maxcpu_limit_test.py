#!/usr/bin/env python3
# ==============================================================================
# End-to-end test for the removal of the MaxCPU sanity limit (v10 step 3b,
# decision F14): an LPAR at MaxCPU 1 with a deep run queue was held at
# MaxCPU x 1.75. Its targets now state the requirement; the rationale says
# MaxCPU must be raised, and NFIT - Action puts the MaxCPU raise first when the
# current cycle's entitlement is above it. Synthetic data only.
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
    root = tempfile.mkdtemp(prefix='maxcpu-limit-')
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
    logs = glob.glob(os.path.join(root, 'output', '*.log'))
    log = ''
    if logs:
        with open(logs[0], encoding='utf-8') as fh:
            log = fh.read()
    shutil.rmtree(root, ignore_errors=True)
    return proc, row, snap, log


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class MaxCpuLimitTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.proc, cls.row, cls.snap, cls.log = run()

    def test_run_succeeds(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-1500:])

    def test_targets_are_not_held_at_the_limit(self):
        profiles = self.snap['members']['busy1']['profiles']
        self.assertFalse([p for p, v in profiles.items() if 'maxCpuLimit' in v])
        # The deep queue at the one-VP ceiling used to hold some profile at 1 x 1.75.
        above = [p for p in profiles if float(self.row[p]) > 1.75]
        self.assertTrue(above, 'some profile must now state a requirement above the former limit')
        self.assertNotIn('Max Sanity Check', self.log)
        self.assertNotIn('Forecast Multiplier', self.log)
        self.assertIn('above MaxCPU 1.00: MaxCPU must be raised to at least', self.log)

    def test_pairs_stay_consistent_and_the_action_names_the_maxcpu_raise(self):
        profiles = self.snap['members']['busy1']['profiles']
        for name, v in profiles.items():
            pair = v['currentCycle']
            if pair.get('entitlement') is not None and pair.get('VPs') is not None:
                self.assertLessEqual(pair['entitlement'], pair['VPs'], name)      # an entitlement needs its VPs
        ent = float(self.row['NFIT - ENT'])
        action = self.row['NFIT - Action']
        if ent > 1.0 + 0.005:
            self.assertTrue(action.startswith('Raise MaxCPU 1.00 to at least %.2f first.' % ent), action)
        else:
            self.assertNotIn('Raise MaxCPU', action)


if __name__ == '__main__':
    unittest.main()
