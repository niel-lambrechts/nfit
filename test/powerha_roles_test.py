#!/usr/bin/env python3
# ==============================================================================
# End-to-end test for the PowerHA role evidence of nfit-profile (v10 stage
# 3.1b step 6c, report only): two synthetic managed systems, a cluster whose
# nodes sit on different systems, and a failover from one to the other. The
# run on one system reads its peer's cache read-only, reports the handover
# and the inferred roles in the rationale, records them in the stored
# proposal, and changes nothing in the CSV.
# ==============================================================================
import calendar
import glob
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
SWITCH = FIRST + 12 * 86400 + 10 * 3600            # day 12, 10:00 UTC
SYSTEMS = {'SYSHA1': ('hanodeA', 'app1'), 'SYSHA2': ('hanodeB',)}


def perl_ready():
    return (shutil.which('perl') is not None and
            subprocess.run(['perl', '-MJSON', '-MList::MoreUtils', '-e1'], capture_output=True).returncode == 0)


def level(vm, t):
    if vm == 'app1':
        return 0.8
    active = (t < SWITCH) == (vm == 'hanodeA')
    return 1.2 if active else 0.08


def build(root, with_peer):
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
        fh.write('[hanodeA]\ntier = G2\n' + ('ha_peer = hanodeB\n' if with_peer else '') + '[hanodeB]\ntier = G2\n')
    last = time.strftime('%Y-%m-%d', time.gmtime(FIRST + DAYS * 86400 - 60))
    for serial, vms in SYSTEMS.items():
        cache = os.path.join(root, 'stage', serial)
        os.makedirs(cache)
        rows = ['Timestamp,VMName,PhysC,RunQ']
        for e in range(FIRST, FIRST + DAYS * 86400, 60):
            stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(e))
            for vm in vms:
                c = level(vm, e) * (1 + 0.04 * math.sin(e / 911.0 + len(vm)))
                rows.append(f'{stamp},{vm},{c:.4f},{c * 2:.4f}')
        with open(os.path.join(cache, '.nfit.cache.data'), 'w') as fh:
            fh.write('\n'.join(rows) + '\n')
        with open(os.path.join(cache, '.nfit.cache.manifest'), 'w') as fh:
            fh.write(f'cache_build_status: success\ndata_start: 2026-09-01\ndata_end: {last} 23:59:00\n')
        states = {vm: [{'start_epoch': FIRST, 'end_epoch': FIRST + DAYS * 86400 - 60, 'duration_days': DAYS,
                        'metadata': {'proc_clock': '3800MHz', 'serial_number': serial, 'proc_type': 'PowerPC_POWER10',
                                     'proc_version': '2.0', 'max_cpu': 4, 'pool_id': 0, 'smt': 8, 'pool_cpu': 32,
                                     'entitlement': 1.5, 'capped': 0, 'virtual_cpus': 4}}] for vm in vms}
        with open(os.path.join(cache, '.nfit.cache.states'), 'w') as fh:
            json.dump(states, fh)
        with open(os.path.join(cache, '.nfit_stage_id'), 'w') as fh:
            fh.write('synthetic\n')
    return os.path.join(root, 'stage', 'SYSHA1'), last


def run(with_peer):
    root = tempfile.mkdtemp(prefix='powerha-')
    cache, last = build(root, with_peer)
    proc = subprocess.run(['perl', os.path.join(root, 'nfit-profile'), '--runq-perc-behavior=match',
                           '--enddate', last, '--nmondir', cache], capture_output=True, text=True, timeout=300, cwd=root)
    out = os.path.join(root, 'output')
    csvs = [f for f in glob.glob(os.path.join(out, '*.csv')) if '.decisions.' not in f]
    logs = glob.glob(os.path.join(out, '*.log'))
    result = {'root': root, 'cache': cache, 'proc': proc, 'csv': '', 'log': ''}
    if csvs:
        with open(csvs[0]) as fh:
            result['csv'] = fh.read()
    if logs:
        with open(logs[0], encoding='utf-8') as fh:
            result['log'] = fh.read()
    return result


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class PowerHARoleTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.with_peer = run(True)
        cls.without = run(False)

    @classmethod
    def tearDownClass(cls):
        for r in (cls.with_peer, cls.without):
            shutil.rmtree(r['root'], ignore_errors=True)

    def section(self):
        log = self.with_peer['log']
        start = log.index('\nPowerHA Roles\n')
        return ' '.join(log[start:log.index('\nEntitlement Totals\n')].split())

    def test_runs_succeed(self):
        for r in (self.with_peer, self.without):
            self.assertEqual(r['proc'].returncode, 0, r['proc'].stderr[-1500:])

    def test_handover_across_systems_is_reported(self):
        s = self.section()
        self.assertIn('hanodeA, hanodeB (compared on PhysC)', s)
        self.assertIn('Possible workload handover from hanodeA to hanodeB: 13 Sep 2026', s)
        self.assertIn('the new levels hold to the end of the period. PowerHA ownership not confirmed.', s)
        self.assertIn('hanodeB active-like, its peer standby-like', s)
        self.assertIn('Role of hanodeA: standby (inferred)', s)
        self.assertIn('Role of hanodeB: active (inferred)', s)

    def test_csv_is_unchanged(self):
        self.assertTrue(self.with_peer['csv'])
        self.assertEqual(self.with_peer['csv'], self.without['csv'])

    def test_no_section_without_a_cluster(self):
        self.assertNotIn('\nPowerHA Roles\n', self.without['log'])

    def test_role_evidence_is_stored(self):
        paths = glob.glob(os.path.join(self.with_peer['cache'], '.nfit.store', 'proposals', 'v1', '*', '*.json'))
        self.assertTrue(paths, 'no stored proposal')
        with open(paths[0]) as fh:
            doc = json.load(fh)
        self.assertIn('roleEvidence', doc['members']['hanodeA'])
        self.assertNotIn('roleEvidence', doc['members']['app1'])
        evidence = doc['members']['hanodeA']['roleEvidence']
        self.assertEqual(evidence['detector'], 'ha-role-1')
        self.assertEqual(set(evidence['caches']), {'SYSHA1', 'SYSHA2'})
        self.assertEqual(evidence['role']['effective'], 'standby')
        self.assertTrue(any(t['sustained'] for t in evidence['transitions']))


if __name__ == '__main__':
    unittest.main(verbosity=1)
