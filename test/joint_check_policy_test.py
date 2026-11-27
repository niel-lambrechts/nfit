"""Checks on the resolved plan (v10 B7): a policy exception and missing evidence, through the real
analysis and the finalisation of a planning cycle, on a synthetic cache of 30 days. One pool of 6
processing units; on three nights it is at its maximum for half an hour while all are busy.

  quiet   PowerHA standby of hot on a fixed standby entitlement of 0.10 (installed 1.00 on 2 VPs).
          Quiet but for those half hours, when its busy work is 0.40: its protected level is above
          0.10. The fixed figure is an explicit exception: kept, marked, named in the rationale, its
          exposure reported; finalisation neither restores the level nor lays a share floor under it.
  hot     the active node.
  nopb    2.00 on 3 VPs with no PhysB measurement: not assessed. Its missing evidence is neither zero
          exposure nor a reason to hide the exposure measured on the others; its sizing is unchanged.
  other   3.00 on 4 VPs, an ordinary LPAR.
"""
import calendar
import csv
import glob
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import member_exposure_test as mx

FIRST = calendar.timegm((2026, 9, 1, 0, 0, 0))
DAYS = 30
POOL = 6
LAST = '2026-09-30'
LPARS = {'hot': (2.0, 3), 'quiet': (1.0, 2), 'nopb': (2.0, 3), 'other': (3.0, 4)}
VMS_CFG = ('[hot]\ntier = G3\nha_peer = quiet\nha_role = active\n'
           '[quiet]\ntier = G3\nha_peer = hot\nha_role = standby\nha_sizing = standby_fixed\nstandby_entitlement = 0.10\n'
           '[nopb]\ntier = G3\n[other]\ntier = G3\n')


def minute_of(vm, day, minute):
    contested = day in (5, 12, 19) and 120 <= minute < 150
    figures = {'hot': ((1.60, 1.50), (0.40, 0.30)), 'quiet': ((0.45, 0.40), (0.04, 0.02)),
               'nopb': ((1.50, None), (0.40, None)), 'other': ((2.40, 2.30), (0.50, 0.40))}[vm]
    c, b = figures[0] if contested else figures[1]
    return c, b, (4.0 if contested else 1.0)


def build(root, serial):
    cache = os.path.join(root, 'stage', serial)
    os.makedirs(cache)
    last = FIRST + DAYS * 86400 - 60
    header = 'Timestamp,VMName,PhysC,RunQ,PhysB'
    with open(os.path.join(cache, '.nfit.cache.data'), 'w') as fh, \
         gzip.open(os.path.join(cache, '.nfit.cache.pool.observations.csv.gz'), 'wt') as pool:
        fh.write(header + '\n')
        pool.write(','.join(mx.HEADER) + '\n')
        for day in range(DAYS):
            lines, rows = [], []
            for minute in range(1440):
                t = FIRST + day * 86400 + minute * 60
                stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t))
                total = 0.0
                for vm in LPARS:
                    c, b, q = minute_of(vm, day, minute)
                    total += c
                    lines.append(f'{stamp},{vm},{c:.4f},{q:.4f},' + ('' if b is None else f'{b:.4f}'))
                rows.append(mx.obs(t + 15, 'hot', 20, POOL, round(total, 4), 20.0))
            fh.write('\n'.join(lines) + '\n')
            pool.write('\n'.join(rows) + '\n')
    with open(os.path.join(cache, '.nfit.cache.manifest'), 'w') as fh:
        fh.write(f'cache_build_status: success\ndata_start: 2026-09-01\ndata_end: {LAST} 23:59:00\n'
                 f'data_schema_version: 2\ndata_columns: {header}\ndata_revision: synthetic-1\n')
    states = {vm: [{'start_epoch': FIRST, 'end_epoch': last, 'duration_days': DAYS, 'metadata': {
        'proc_clock': '3800MHz', 'serial_number': serial, 'proc_type': 'PowerPC_POWER10', 'proc_version': '2.0', 'max_cpu': 8,
        'smt': 8, 'entitlement': ent, 'capped': 0, 'virtual_cpus': vp, 'pool_id': 20, 'pool_cpu': POOL}}]
        for vm, (ent, vp) in LPARS.items()}
    with open(os.path.join(cache, '.nfit.cache.states'), 'w') as fh:
        json.dump(states, fh)
    with open(os.path.join(cache, '.nfit_stage_id'), 'w') as fh:
        fh.write('synthetic\n')
    return cache


def load(path):
    with open(path) as fh:
        return json.load(fh)


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class JointCheckPolicyTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='joint-policy-')
        os.makedirs(os.path.join(cls.root, 'etc'))
        os.makedirs(os.path.join(cls.root, 'output'))
        for prog in ('nfit', 'nfit-profile'):
            shutil.copy(os.path.join(mx.REPO, prog), cls.root)
        for name in mx.TRACKED_ETC:
            src = os.path.join(mx.REPO, 'etc', name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(cls.root, 'etc', name))
        open(os.path.join(cls.root, 'etc', 'nfit.seasonality.cfg'), 'w').close()
        with open(os.path.join(cls.root, 'etc', 'nfit.vms.cfg'), 'w') as fh:
            fh.write(VMS_CFG)
        cache = build(cls.root, 'SYNJP')
        cycle = os.path.join(cls.root, 'cycle')
        run = subprocess.run(['perl', os.path.join(cls.root, 'nfit-profile'), '--runq-perc-behavior=match', '--enddate', LAST,
                              '--nmondir', cache, '--cycle', cycle], cwd=cls.root, capture_output=True, text=True, timeout=280)
        assert run.returncode == 0, run.stderr[-1500:]
        reg = load(os.path.join(cycle, 'runs', 'SYNJP.state-based.json'))
        cls.outputs = {o['kind']: o['path'] for o in reg['outputs']}
        with gzip.open(os.path.join(cycle, 'plans', reg['plan'] + '.json.gz'), 'rt') as fh:
            cls.provisional = json.load(fh)
        cls.provisional_csv = cls.rows_of(cls.outputs['csv'])
        fin = subprocess.run(['perl', os.path.join(cls.root, 'nfit-profile'), '--finalise-cycle', cycle], cwd=cls.root,
                             capture_output=True, text=True, timeout=120)
        assert fin.returncode == 0, fin.stderr[-1500:]
        cls.done = load(os.path.join(cycle, 'finalised.json'))
        cls.rows = cls.rows_of(cls.outputs['csv'])
        with open(cls.outputs['rationale'], encoding='utf-8') as fh:
            cls.log = re.sub(r'[ \t]+', ' ', fh.read())
        with open(os.path.join(cycle, 'cycle-pools.csv'), newline='') as fh:
            cls.pools = [r for r in csv.DictReader(fh) if r['Pool'] == '20']

    @staticmethod
    def rows_of(path):
        with open(path, newline='') as fh:
            return {r['VM']: r for r in csv.DictReader(fh)}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def check(self, doc):
        return doc['jointCheck']

    def test_the_fixed_standby_is_kept_as_an_explicit_exception(self):
        row = self.rows['quiet']
        self.assertEqual(row['NFIT - ENT'], '0.10')
        vps = int(row['NFIT - VCPU'])
        self.assertTrue(0.05 * vps <= 0.10 + 1e-9 and 0.10 <= vps)                    # a pair PowerVM accepts
        self.assertIn('[STANDBY_FIXED]', row['VM_Comments'])
        self.assertIn('[BELOW_PROTECTED_LEVEL]', row['VM_Comments'])
        self.assertNotIn('[PROTECTED_LEVEL_HELD]', row['VM_Comments'])
        level = self.check(self.provisional)['protectedLevel']['quiet']['target']
        self.assertEqual((level['outcome'], level['policy']), ('belowByPolicy', 'fixed standby entitlement'))
        self.assertGreaterEqual(level['required'], 0.40)
        self.assertRegex(self.log, r'quiet, the full target: \d\.\d\d below its protected level of \d\.\d\d, by policy \(fixed standby entitlement\)\. '
                                   r'Reported; the policy is not reversed\.')

    def test_its_exposure_is_still_reported(self):
        r = self.check(self.provisional)['results']['20']
        quiet = r['target']['profileHours']['members']['quiet']
        self.assertEqual(quiet['minutes'], 90)                                        # three half hours
        self.assertAlmostEqual(quiet['shortfall'], 90 * (0.40 - 0.10), places=2)

    def test_missing_physb_is_not_zero_and_hides_nothing(self):
        r = self.check(self.provisional)['results']['20']
        c = r['completeness']
        self.assertEqual(c['state'], 'incomplete')
        self.assertEqual(c['notAssessed'], {'nopb': 'no PhysB measurement in the period'})
        self.assertEqual(c['assessed'], c['members'] - 1)
        self.assertNotIn('nopb', r['target']['profileHours']['members'])               # not counted as zero exposure either
        self.assertGreater(r['target']['profileHours']['poolMinutes'], 0)               # the others' exposure stands
        self.assertNotIn('nopb', self.check(self.provisional)['evidence']['20']['members'])
        self.assertRegex(self.log, r'Evidence incomplete: \d of \d assessed\. What follows is for the LPARs assessed; for the others it is not known\.')
        self.assertTrue('Not assessed: nopb (no PhysB measurement in the period).' in self.log)
        pool, = self.pools
        self.assertEqual(pool['Joint_Check'], 'exposed')
        self.assertRegex(pool['Joint_Evidence'], r'^incomplete: \d of \d LPARs$')

    def test_sizing_is_unchanged_by_the_check(self):
        for vm in ('nopb', 'other', 'hot'):
            self.assertNotIn('PROTECTED_LEVEL', self.rows[vm]['VM_Comments'])
        m = self.provisional['members']['nopb']
        p = m['profiles'][m['planningProfile']]
        self.assertEqual(self.rows['nopb']['NFIT - ENT'], '%.2f' % p['currentCycle']['entitlement'])
        self.assertLess(p['entitlement'], 2.0)                                         # its reduction stands

    def test_finalisation_keeps_the_exception_and_the_distinction(self):
        self.assertEqual({vm: (r['NFIT - ENT'], r['NFIT - VCPU']) for vm, r in self.rows.items()},
                         {vm: (r['NFIT - ENT'], r['NFIT - VCPU']) for vm, r in self.provisional_csv.items()})
        self.assertEqual(self.rows['quiet']['NFIT - ENT'], '0.10')                    # no protected level restored, no share floor
        self.assertNotIn('pendingRole', self.provisional['members']['quiet'])         # a fixed standby is final at analysis
        # Nothing on this frame waits for the cycle (a fixed standby does not depend on its primary's plan): the
        # frame is final as analysed, and its outputs and plan, with the exception and the missing evidence, stand.
        self.assertEqual(self.done['written'], {})
        self.assertNotIn('Cycle Finalisation (planning cycle', self.log)

if __name__ == '__main__':
    unittest.main()
