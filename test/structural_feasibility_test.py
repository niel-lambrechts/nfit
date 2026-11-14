#!/usr/bin/env python3
# ==============================================================================
# End-to-end test for the structural feasibility checks in nfit-profile: a
# standard run on a small synthetic system with four LPARs in a user-defined
# pool and one in the default pool. Checks that the rationale section agrees
# with the figures in the CSV of the same run (planning profile by configured
# TIER, else tier 3 on the hinted pattern), that the shortfall and the
# entitlement/VP findings follow from those figures, and that the CSV carries
# only the two major alerts, leading VM_Comments, on the rows they concern.
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
        return self.log[self.log.index('\nPool and VP Checks\n'):]

    @staticmethod
    def flat(text):
        return re.sub(r'\s+', ' ', text)

    def test_run_succeeds(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-1500:])
        self.assertEqual(set(self.rows), set(VMS))

    def test_csv_carries_only_the_major_alerts_in_vm_comments(self):
        members = [vm for vm, v in VMS.items() if v[2] == POOL_ID]
        proposed = sum(float(self.rows[vm][self.planning_profile(vm)]) for vm in members)
        short = proposed > POOL_SIZE + 0.005
        for vm in VMS:
            profile = self.planning_profile(vm)
            ent = round(float(self.rows[vm][profile]), 2)
            tactical = int(self.rows[vm][profile + '_rVCPU_Tactical'])
            mismatch = tactical < math.ceil(ent - 1e-9) or tactical > int(ent / MIN_ENT_PER_VP + 1e-6)
            expected = (['[POOL_SHORTFALL]'] if short and vm in members else []) + (['[VP_MISMATCH]'] if mismatch else [])
            comments = self.rows[vm]['VM_Comments']
            found = re.findall(r'\[(?:POOL_SHORTFALL|VP_MISMATCH)\]', comments)
            self.assertEqual(found, expected, vm)
            if expected:                                       # alerts lead; other comments follow
                self.assertTrue(comments.startswith('; '.join(expected)), comments)
            for column, value in self.rows[vm].items():        # nowhere else in the row
                if column != 'VM_Comments':
                    self.assertNotIn('SHORTFALL', value or '')
                    self.assertNotIn('MISMATCH', value or '')
        for text in ('Raise VPs', 'Reduce VPs', 'Pool and VP', 'Recommended Actions'):
            self.assertNotIn(text, self.csv_text)              # actions stay in the rationale

    def test_pool_sums_agree_with_the_csv(self):
        members = [vm for vm, v in VMS.items() if v[2] == POOL_ID]
        proposed = sum(float(self.rows[vm][self.planning_profile(vm)]) for vm in members)
        installed = sum(VMS[vm][0] for vm in members)
        raw = self.section()
        s = self.flat(raw)
        units = '%d' % POOL_SIZE if float(POOL_SIZE).is_integer() else '%.2f' % POOL_SIZE
        if proposed > POOL_SIZE + 0.005:
            self.assertIn(f'Pool {POOL_ID} - the proposed entitlements do not fit', s)
            self.assertIn(f'{proposed:.2f} cores proposed against a pool maximum of {units} '
                          f'(installed today {installed:.2f}; 4 LPARs analysed).', s)
            self.assertIn(f'Action: defer at least {proposed - POOL_SIZE:.2f} cores of the proposed increases, or raise the '
                          f'pool maximum from {units} to at least {math.floor(proposed - 1e-9) + 1} processing units.', s)
            self.assertRegex(self.proc.stderr, r'\[WARN\] Pool and VP checks: 1 pool\(s\) cannot hold the proposed entitlements')
            for vm in members:                                 # every member row shows the CSV figures
                value = float(self.rows[vm][self.planning_profile(vm)])
                change = re.escape(f'{value - VMS[vm][0]:+.2f}')
                self.assertRegex(raw, rf'{vm}\s+{re.escape(self.planning_profile(vm))}( \(default\))?\s+'
                                      rf'{VMS[vm][0]:.2f}\s+{value:.2f}\s+{change}')
            actions = self.flat(self.log[self.log.index('Recommended Actions'):])
            self.assertIn(f'Pool {POOL_ID}: Defer at least {proposed - POOL_SIZE:.2f} cores', actions)
        else:
            self.assertIn(f'Pool {POOL_ID} - fits: {proposed:.2f} cores proposed against a maximum of {units}', s)
        self.assertNotIn('Pool 0 -', s)                        # the default pool is not assessed

    def test_ent_vp_findings_follow_from_the_csv(self):
        s = self.section()
        actions = self.flat(self.log[self.log.index('Recommended Actions'):])
        for vm in VMS:
            profile = self.planning_profile(vm)
            ent = round(float(self.rows[vm][profile]), 2)
            tactical = int(self.rows[vm][profile + '_rVCPU_Tactical'])
            current = VMS[vm][1]
            min_vps = math.ceil(ent - 1e-9)
            max_vps = int(ent / MIN_ENT_PER_VP + 1e-6)
            block = re.search(rf'^    {vm} \({re.escape(profile)}\): .*\n(      .*\n)+', s, re.M)
            text = self.flat(block.group(0)) if block else ''
            if tactical < min_vps:
                self.assertIn(f'The proposed {ent:.2f} cores needs at least', text, vm)
                self.assertIn(f'Action: Limit the entitlement to {tactical:.2f}', text, vm)
            elif tactical > max_vps:
                want = ('Minimum configurable entitlement: 0.05.' if tactical == 1
                        else f'minimum entitlement {tactical * MIN_ENT_PER_VP:.2f}')
                self.assertIn(want, text, vm)
            elif current < min_vps:
                self.assertIn(f'Action: Raise VPs from {current} to', text, vm)
            elif current > max_vps:
                self.assertIn(f'Action: Reduce VPs from {current} to', text, vm)
            else:
                self.assertEqual(text, '', vm)                 # nothing to do: not listed
                continue
            self.assertIn(f'{vm}: ', actions, vm)              # and the action is in the summary

    def test_terms_used_are_defined_in_the_terminology_reference(self):
        reference = self.log[self.log.index('Key Terminology Reference'):self.log.index('\nPool and VP Checks\n')]
        for term in ('Planning profile', 'Pool entitlement', 'Entitlement and', 'Recommended', 'Raise/Reduce VPs first',
                     '[POOL_SHORTFALL]', '[VP_MISMATCH]'):
            self.assertIn(term, reference)


if __name__ == '__main__':
    unittest.main(verbosity=1)
