#!/usr/bin/env python3
# ==============================================================================
# End-to-end test for the order of the PowerHA sizing (v10 stage 3.1b step
# 6d): on one system, the primary that took over has its reductions held, and
# its standby must take that held pair as the reference for its floor, whatever
# the sort order of the two names. Two synthetic runs with the names swapped
# must give the standby the same figures. With standby_share = 1.0 the floor
# equals the reference, so a diluted reference would be visible at once.
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
DAYS = 21
SWITCH = FIRST + 6 * 86400 + 10 * 3600            # day 6, 10:00 UTC
INSTALLED = 2.0


def perl_ready():
    return (shutil.which('perl') is not None and
            subprocess.run(['perl', '-MJSON', '-MList::MoreUtils', '-e1'], capture_output=True).returncode == 0)


def run(falls, takes_over):
    """One system, two nodes: 'falls' is active until SWITCH, 'takes_over' after it."""
    root = tempfile.mkdtemp(prefix='powerha-order-')
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
        fh.write('[GLOBAL]\nha_sizing = standby_share\nstandby_share = 1.0\n'
                 f'[{falls}]\ntier = G2\nha_peer = {takes_over}\n[{takes_over}]\ntier = G2\n')
    last = time.strftime('%Y-%m-%d', time.gmtime(FIRST + DAYS * 86400 - 60))
    cache = os.path.join(root, 'stage', 'SYSORD1')
    os.makedirs(cache)
    rows = ['Timestamp,VMName,PhysC,RunQ']
    for e in range(FIRST, FIRST + DAYS * 86400, 60):
        stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(e))
        for vm in (falls, takes_over):
            active = (e < SWITCH) == (vm == falls)
            c = (1.2 if active else 0.08) * (1 + 0.04 * math.sin(e / 911.0))
            rows.append(f'{stamp},{vm},{c:.4f},{c * 2:.4f}')
    with open(os.path.join(cache, '.nfit.cache.data'), 'w') as fh:
        fh.write('\n'.join(rows) + '\n')
    with open(os.path.join(cache, '.nfit.cache.manifest'), 'w') as fh:
        fh.write(f'cache_build_status: success\ndata_start: 2026-09-01\ndata_end: {last} 23:59:00\n')
    meta = {'proc_clock': '3800MHz', 'serial_number': 'SYSORD1', 'proc_type': 'PowerPC_POWER10', 'proc_version': '2.0',
            'max_cpu': 4, 'pool_id': 0, 'smt': 8, 'pool_cpu': 32, 'entitlement': INSTALLED, 'capped': 0, 'virtual_cpus': 4}
    states = {vm: [{'start_epoch': FIRST, 'end_epoch': FIRST + DAYS * 86400 - 60, 'duration_days': DAYS, 'metadata': meta}]
              for vm in (falls, takes_over)}
    with open(os.path.join(cache, '.nfit.cache.states'), 'w') as fh:
        json.dump(states, fh)
    with open(os.path.join(cache, '.nfit_stage_id'), 'w') as fh:
        fh.write('synthetic\n')
    proc = subprocess.run(['perl', os.path.join(root, 'nfit-profile'), '--runq-perc-behavior=match',
                           '--enddate', last, '--nmondir', cache], capture_output=True, text=True, timeout=600, cwd=root)
    csvs = [f for f in glob.glob(os.path.join(root, 'output', '*.csv')) if '.decisions.' not in f]
    rows = {}
    if csvs:
        with open(csvs[0]) as fh:
            text = fh.read().split('\n\n')[0]
        rows = {r['VM']: r for r in csv.DictReader(io.StringIO(text)) if r.get('VM')}
    shutil.rmtree(root, ignore_errors=True)
    return proc, rows


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class PowerHAOrderTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Run 1: the standby ('alpha', the node that fell) sorts before the primary.
        cls.first = run('alpha', 'omega')
        # Run 2: the same roles with the names swapped.
        cls.second = run('omega', 'alpha')

    def test_runs_succeed(self):
        for proc, rows in (self.first, self.second):
            self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
            self.assertEqual(len(rows), 2)

    def test_standby_uses_the_held_pair_in_either_order(self):
        cols = ('NFIT - ENT', 'NFIT - VCPU')
        standby_first = [self.first[1]['alpha'][c] for c in cols]
        standby_second = [self.second[1]['omega'][c] for c in cols]
        self.assertEqual(standby_first, standby_second)
        # The primary's held pair is the installed one, and the share is 1.0.
        self.assertEqual(float(standby_first[0]), INSTALLED)

    def test_primary_is_held_in_either_order(self):
        self.assertEqual(float(self.first[1]['omega']['NFIT - ENT']), INSTALLED)
        self.assertEqual(float(self.second[1]['alpha']['NFIT - ENT']), INSTALLED)
        self.assertIn('Reductions held', self.first[1]['omega']['NFIT - Action'])


if __name__ == '__main__':
    unittest.main()
