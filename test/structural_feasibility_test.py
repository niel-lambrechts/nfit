#!/usr/bin/env python3
# ==============================================================================
# End-to-end test for the structural feasibility checks in nfit-profile: a
# standard run on a small synthetic system with four LPARs in a user-defined
# pool and one in the default pool. Checks that the rationale section agrees
# with the figures in the CSV of the same run (planning profile by configured
# TIER, else tier 3 on the hinted pattern), that the shortfall and the
# entitlement/VP findings follow from those figures, and that the CSV carries
# no trace of the checks.
# ==============================================================================
import calendar
import csv
import glob
import json
import math
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
SERIAL = 'SYNSF'
FIRST = calendar.timegm((2026, 9, 1, 0, 0, 0))
DAYS = 21
POOL_ID, POOL_SIZE = 25, 7
MIN_ENT_PER_VP = 0.05
# name: (entitlement, VPs, pool id, pool size, day level, night level, RunQ per core)
VMS = {
    'vmA': (2.5, 4, POOL_ID, POOL_SIZE, 2.70, 1.20, 3.0),
    'vmB': (2.0, 4, POOL_ID, POOL_SIZE, 2.90, 1.50, 4.0),
    'vmC': (1.5, 3, POOL_ID, POOL_SIZE, 1.30, 0.60, 2.0),
    'vmD': (0.5, 1, POOL_ID, POOL_SIZE, 0.97, 0.40, 14.0),
    'vmE': (4.0, 16, 0, 64, 0.45, 0.30, 1.0),
}
TIERS = {'vmC': 'G1'}


def perl_ready():
    return (shutil.which('perl') is not None and
            subprocess.run(['perl', '-MJSON', '-MList::MoreUtils', '-e1'], capture_output=True).returncode == 0)


def build_root(root):
    os.makedirs(os.path.join(root, 'etc'))
    os.makedirs(os.path.join(root, 'output'))
    for prog in ('nfit', 'nfit-profile'):
        shutil.copy(os.path.join(REPO, prog), root)
    for name in TRACKED_ETC:
        src = os.path.join(REPO, 'etc', name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(root, 'etc', name))
    open(os.path.join(root, 'etc', 'nfit.seasonality.cfg'), 'w').close()
    with open(os.path.join(root, 'etc', 'nfit.vms.cfg'), 'w') as fh:           # synthetic tiers only
        fh.write(''.join(f'[{vm}]\ntier = {tier}\n' for vm, tier in TIERS.items()))
    cache = os.path.join(root, 'stage', SERIAL)
    os.makedirs(cache)
    rows = ['Timestamp,VMName,PhysC,RunQ']
    for e in range(FIRST, FIRST + DAYS * 86400, 60):
        tm = time.gmtime(e)
        stamp = time.strftime('%Y-%m-%d %H:%M:%S', tm)
        for vm, (_, vp, _, _, day_level, night_level, runq) in sorted(VMS.items()):
            base = day_level if 8 <= tm.tm_hour < 17 else night_level
            c = min(vp, base * (1 + 0.06 * math.sin(e / 977.0 + len(vm) + ord(vm[-1]))))
            rows.append(f'{stamp},{vm},{c:.4f},{c * runq:.4f}')
    with open(os.path.join(cache, '.nfit.cache.data'), 'w') as fh:
        fh.write('\n'.join(rows) + '\n')
    last = time.strftime('%Y-%m-%d', time.gmtime(FIRST + DAYS * 86400 - 60))
    with open(os.path.join(cache, '.nfit.cache.manifest'), 'w') as fh:
        fh.write(f'cache_build_status: success\ndata_start: 2026-09-01\ndata_end: {last} 23:59:00\n')
    states = {vm: [{'start_epoch': FIRST, 'end_epoch': FIRST + DAYS * 86400 - 60, 'duration_days': DAYS,
                    'metadata': {'proc_clock': '3800MHz', 'serial_number': SERIAL, 'proc_type': 'PowerPC_POWER10',
                                 'proc_version': '2.0', 'max_cpu': vp, 'pool_id': pool, 'smt': 8, 'pool_cpu': size,
                                 'entitlement': ent, 'capped': 0, 'virtual_cpus': vp}}]
              for vm, (ent, vp, pool, size, *_) in VMS.items()}
    with open(os.path.join(cache, '.nfit.cache.states'), 'w') as fh:
        json.dump(states, fh)
    with open(os.path.join(cache, '.nfit_stage_id'), 'w') as fh:
        fh.write('synthetic\n')
    return cache, last


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class StructuralFeasibilityTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='structural-')
        cache, last = build_root(cls.root)
        cls.proc = subprocess.run(['perl', os.path.join(cls.root, 'nfit-profile'), '--runq-perc-behavior=match',
                                   '--enddate', last, '--nmondir', cache],
                                  capture_output=True, text=True, timeout=300, cwd=cls.root)
        out = os.path.join(cls.root, 'output')
        csvs, logs = glob.glob(os.path.join(out, '*.csv')), glob.glob(os.path.join(out, '*.log'))
        cls.rows, cls.header, cls.csv_text, cls.log = {}, [], '', ''
        if csvs:
            with open(csvs[0], newline='') as fh:
                cls.csv_text = fh.read()
            reader = csv.DictReader(cls.csv_text.splitlines())
            cls.header = reader.fieldnames
            cls.rows = {r['VM']: r for r in reader}
        if logs:
            with open(logs[0], encoding='utf-8') as fh:
                cls.log = fh.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def planning_profile(self, vm):
        """As nfit-profile resolves it: the configured TIER, else tier 3 on the
        hinted pattern letter (O or B), else G."""
        label = TIERS.get(vm)
        if label is None:
            hint = self.rows[vm]['Hint']
            label = (hint[0] if hint[:1] in ('O', 'B') else 'G') + '3'
        return next(h for h in self.header if h.startswith(label + '-') and '_rVCPU' not in h)

    def section(self):
        return self.log[self.log.index('Structural Feasibility Checks'):]

    def test_run_succeeds_and_csv_carries_no_trace(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-1500:])
        self.assertEqual(set(self.rows), set(VMS))
        for word in ('SHORTFALL', 'MISMATCH', 'Structural', 'ORDER OF CHANGE'):
            self.assertNotIn(word, self.csv_text)

    def test_pool_sums_agree_with_the_csv(self):
        members = [vm for vm, v in VMS.items() if v[2] == POOL_ID]
        proposed = sum(float(self.rows[vm][self.planning_profile(vm)]) for vm in members)
        installed = sum(VMS[vm][0] for vm in members)
        s = self.section()
        self.assertRegex(s, rf'Pool ceiling \(Pool Size\)\s+: {POOL_SIZE:.2f} cores')
        self.assertRegex(s, r'LPARs analysed in the pool\s+: 4 \(1 with a configured TIER, 3 on default tier 3\)')
        self.assertRegex(s, rf'Installed entitlement \(sum\)\s+: {installed:.2f} cores')
        self.assertRegex(s, rf'Proposed entitlement \(sum\)\s+: {proposed:.2f} cores')
        if proposed > POOL_SIZE + 0.005:
            self.assertIn(f'Pool {POOL_ID} - SHORTFALL', s)
            self.assertRegex(s, rf'Shortfall\s+: {proposed - POOL_SIZE:.2f} cores')
            self.assertRegex(self.proc.stderr, r'\[WARN\] Structural checks: 1 pool\(s\) with an entitlement shortfall')
            for vm in members:                                 # every member row shows the CSV figures
                value = float(self.rows[vm][self.planning_profile(vm)])
                change = re.escape(f'{value - VMS[vm][0]:+.2f}')
                self.assertRegex(s, rf'{vm}\s+{re.escape(self.planning_profile(vm))}( \(default\))?\s+'
                                    rf'{VMS[vm][0]:.2f}\s+{value:.2f}\s+{change}')
        else:
            self.assertIn(f'Pool {POOL_ID} - FITS', s)
        self.assertNotIn('Pool 0 -', s)                        # the default pool is not assessed

    def test_ent_vp_findings_follow_from_the_csv(self):
        s = self.section()
        for vm in VMS:
            profile = self.planning_profile(vm)
            ent = float(self.rows[vm][profile])
            tactical = int(self.rows[vm][profile + '_rVCPU_Tactical'])
            current = VMS[vm][1]
            min_vps = math.ceil(round(ent, 2) - 1e-9)
            max_vps = int(round(ent, 2) / MIN_ENT_PER_VP + 1e-6)
            block = re.search(rf'^    {vm} \(planning profile {re.escape(profile)}\)\n(      .*\n)+', s, re.M)
            text = block.group(0) if block else ''
            if tactical < min_vps or tactical > max_vps:
                self.assertIn('ENTITLEMENT/WIDTH MISMATCH', text, vm)
                self.assertIn(f'proposed ENT {ent:.2f}', text)
                self.assertIn(f'the tactical VP recommendation is {tactical}', text)
            elif current < min_vps or current > max_vps:
                self.assertIn('ORDER OF CHANGE', text, vm)
                self.assertIn(f'the LPAR has {current}', text)
            else:
                self.assertNotIn('MISMATCH', text.split('Other profiles')[0], vm)
                self.assertNotIn('ORDER OF CHANGE', text.split('Other profiles')[0], vm)

    def test_terms_used_are_defined_in_the_terminology_reference(self):
        reference = self.log[self.log.index('Key Terminology Reference'):self.log.index('Structural Feasibility Checks\n')]
        for term in ('Planning Profile', 'Pool Entitlement', 'Entitlement/Width', 'Order of Change', 'Fits alone',
                     'Lowest sum across all profiles'):
            self.assertIn(term, reference)


if __name__ == '__main__':
    unittest.main(verbosity=1)
