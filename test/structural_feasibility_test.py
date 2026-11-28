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
    'vio1': (1.0, 2, 0, 64, 0.45, 0.35, 1.0),
    'vio2': (1.0, 2, 0, 64, 1.30, 0.80, 1.0),
}
TIERS = {'vmC': 'G1'}
VIOS = ('vio1', 'vio2')                   # lpar_type = vios; vio1 names vio2 as its vios_peer


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
        fh.write('[GLOBAL]\nvios_sizing = combined\n[vio1]\nlpar_type = vios\nvios_peer = vio2\n[vio2]\nlpar_type = vios\n')
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
        cls.cache = cache
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
        label = TIERS.get(vm) or ('P' if vm in VIOS else None)
        if label is None:
            hint = self.rows[vm]['Hint']
            label = (hint[0] if hint[:1] in ('O', 'B') else 'G') + '3'
        return next(h for h in self.header if h.startswith(label + '-') and '_r' not in h)

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
            # The CSV shows the current-cycle pair (v10 stage 3.1), which is
            # always one PowerVM accepts, so [VP_MISMATCH] is never expected.
            profile = self.planning_profile(vm)
            ent = round(float(self.rows[vm][profile + '_rENT_Tactical']), 2)
            vps = int(self.rows[vm][profile + '_rVCPU_Tactical'])
            mismatch = vps < math.ceil(ent - 1e-9) or vps > int(ent / MIN_ENT_PER_VP + 1e-6)
            self.assertFalse(mismatch, vm)
            expected = ['[POOL_SHORTFALL]'] if short and vm in members else []
            comments = self.rows[vm]['VM_Comments']
            found = re.findall(r'\[(?:POOL_SHORTFALL|VP_MISMATCH)\]', comments)
            self.assertEqual(found, expected, vm)
            if expected:                                       # alerts lead; other comments follow
                self.assertTrue(comments.startswith('; '.join(expected)), comments)
            for column, value in self.rows[vm].items():        # nowhere else in the row
                if column != 'VM_Comments':
                    self.assertNotIn('SHORTFALL', value or '')
                    self.assertNotIn('MISMATCH', value or '')
        for text in ('Pool and VP', 'Recommended Actions'):
            self.assertNotIn(text, self.csv_text)              # the checks stay in the rationale
        for vm in VMS:                                         # the LPAR's own action is in NFIT - Action only
            for column, value in self.rows[vm].items():
                if column != 'NFIT - Action':
                    self.assertNotIn('VPs ', value or '', (vm, column))

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
            need = math.floor(proposed - 1e-9) + 1
            self.assertRegex(s, rf"Action: (Raise the pool maximum from {units} to at least \d+ processing units before this cycle's "
                                rf"entitlement increases( \({need} for the targets\))?\.|This cycle's changes fit within the maximum of "
                                rf"{units}\. The targets need at least {need} processing units: raise the pool maximum before the later "
                                rf"increases\.) Confirm the pool's reserved capacity and check licensing\.")
            self.assertNotIn('defer', s)
            self.assertRegex(self.proc.stderr, r'\[WARN\] Pool and VP checks: 1 pool\(s\) cannot hold the proposed entitlements')
            for vm in members:                                 # every member row shows the CSV figures
                value = float(self.rows[vm][self.planning_profile(vm)])
                change = re.escape(f'{value - VMS[vm][0]:+.2f}')
                self.assertRegex(raw, rf'{vm}\s+{re.escape(self.planning_profile(vm))}( \(default\))?\s+'
                                      rf'{VMS[vm][0]:.2f}\s+{value:.2f}\s+{change}')
            actions = self.flat(self.log[self.log.index('Recommended Actions'):])
            self.assertRegex(actions, rf"Pool {POOL_ID}: (Raise the pool maximum from {units} to at least|This cycle's changes fit within)")
            # The planning action of a member whose entitlement rises names the pool change it waits for.
            waiting = [vm for vm in members if 'the maximum of pool %s' % POOL_ID in self.rows[vm]['NFIT - Action']]
            rising = [vm for vm in members if float(self.rows[vm][self.planning_profile(vm)]) > VMS[vm][0] + 0.005]
            self.assertTrue(set(waiting) <= set(rising), (waiting, rising))
            self.assertTrue(waiting, 'some rising member carries the pool prerequisite')
            for vm in waiting:
                self.assertIn('(check licensing)', self.rows[vm]['NFIT - Action'])
        else:
            self.assertIn(f'Pool {POOL_ID} - fits: {proposed:.2f} cores proposed against a maximum of {units}', s)
        self.assertNotIn('Pool 0 -', s)                        # the default pool is not assessed

    def test_current_cycle_pair_and_nfit_columns(self):
        """The current-cycle pair of every profile is one PowerVM accepts; the
        NFIT columns carry the planning profile's pair and its action; the
        entitlement/VP check therefore lists no LPAR (v10 stage 3.1)."""
        s = self.section()
        for vm in VMS:
            row = self.rows[vm]
            current_ent, current_vp = VMS[vm][0], VMS[vm][1]
            for column in self.header:
                if not column.endswith('_rENT_Tactical'):
                    continue
                name = column[:-len('_rENT_Tactical')]
                if row[column] == '' or row[name] == '':           # no result: no pair is stated
                    self.assertEqual((row[column], row[name + '_rVCPU_Tactical']), ('', ''), (vm, name))
                    continue
                ent, vps = float(row[column]), int(row[name + '_rVCPU_Tactical'])
                self.assertLessEqual(ent, vps + 1e-9, (vm, name))
                self.assertGreaterEqual(ent, MIN_ENT_PER_VP * vps - 1e-9, (vm, name))
                # VPs rise only where the target entitlement needs them.
                if vps > current_vp:
                    self.assertEqual(vps, math.ceil(float(row[name]) - 1e-9), (vm, name))
            profile = self.planning_profile(vm)
            self.assertEqual(row['Planning Profile'], profile, vm)
            if vm in VIOS:                                     # the aligned pair: test_vios_pair_sized_to_the_larger
                continue
            self.assertEqual(row['NFIT - ENT'], row[profile + '_rENT_Tactical'], vm)
            self.assertEqual(row['NFIT - VCPU'], row[profile + '_rVCPU_Tactical'], vm)
            ent, vps, action = float(row['NFIT - ENT']), int(row['NFIT - VCPU']), row['NFIT - Action']
            if abs(ent - current_ent) >= 0.005:
                self.assertIn(f'entitlement {current_ent:.2f} to {ent:.2f}', action, vm)
            if vps != current_vp:
                self.assertIn(f'VPs {current_vp} to {vps}', action, vm)
                self.assertIn('re-measure', action, vm)
            if vps > current_vp and ent > current_vp:          # VPs must rise before the entitlement
                self.assertRegex(action, rf'^(First raise the maximum of pool \d+ from \S+ to at least \d+ processing units '
                                         rf'\(check licensing\)\. )?Raise VPs {current_vp} to {vps}, then raise entitlement', vm)
            if abs(ent - current_ent) < 0.005 and vps == current_vp:
                self.assertTrue(action.startswith('No change'), vm)
            self.assertIsNone(re.search(rf'^    {vm} \(', s, re.M), vm)   # nothing to list

    def test_at_a_glance_states_the_csv_pair(self):
        """The rationale's "At a glance" block of the planning profile states the
        same current-cycle pair and action as the CSV."""
        text = self.log
        for vm in VMS:
            if vm in VIOS:                                     # the aligned pair: test_vios_pair_sized_to_the_larger
                continue
            row = self.rows[vm]
            # The CSV adds the pool change an increase waits for, which is known only once
            # every member is sized; the LPAR's own action is the same in both places.
            own = re.sub(r'^First raise the maximum of pool \d+ from \S+ to at least \d+ processing units \(check licensing\)\. ', '',
                         row['NFIT - Action'])
            own = re.sub(r' The target needs the maximum of pool \d+ raised to at least \d+ processing units \(check licensing\)\.$', '', own)
            blocks = [m.start() for m in re.finditer(r'^  At a glance$', text, re.M)]
            found = False
            for b in blocks:
                block = text[b:text.index('\n\n', b)]
                if f"Current-cycle action           : {own}" in block:
                    vps = int(row['NFIT - VCPU'])
                    self.assertIn(f"Current cycle                  : {float(row['NFIT - ENT']):.2f} entitlement on "
                                  + ('1 VP' if vps == 1 else f'{vps} VPs'), block, vm)
                    found = True
                    break
            self.assertTrue(found, vm)

    def test_entitlement_totals_agree_with_the_csv(self):
        """The Entitlement Totals section adds up the CSV: installed, NFIT - ENT
        and the planning-profile target, an LPAR without a result at its
        installed entitlement (v10 stage 3.1)."""
        start = self.log.index('\nEntitlement Totals\n')
        self.assertLess(self.log.index('\nPool and VP Checks\n'), start)
        self.assertLess(start, self.log.index('\nRecommended Actions'))
        text = self.log[start:self.log.index('\nRecommended Actions')]
        cents = lambda v: int(round(float(v) * 100))
        installed = cycle = target = 0
        for vm in VMS:
            row, profile = self.rows[vm], self.planning_profile(vm)
            ent = cents(VMS[vm][0])
            installed += ent
            if row['NFIT - ENT'] == '' or row[profile] == '':
                cycle, target = cycle + ent, target + ent
            elif vm in VIOS:                                   # the pair's combined target
                cycle += cents(row['NFIT - ENT'])
                target += sum(cents(self.rows[v][profile]) for v in VIOS)
            else:
                cycle, target = cycle + cents(row['NFIT - ENT']), target + cents(row[profile])
        self.assertRegex(text, rf'(?m)^    All LPARs\s+{len(VMS)}\s+{installed / 100:.2f}\s+{cycle / 100:.2f}\s+{target / 100:.2f}$')
        self.assertRegex(text, rf'(?m)^    Excluding VIOS\s+{len(VMS) - len(VIOS)}\s')
        self.assertRegex(text, rf'(?m)^      VIOS\s+{len(VIOS)}\s')
        self.assertIn('Standbys are not recognised: the VM configuration file (--config) was not loaded.', self.flat(text))

    def test_vios_pair_sized_for_the_combined_workload(self):
        """Both VIOS of a pair are sized to carry the sum of their own targets
        (combined, the default; margin 0), on P when no TIER is configured; the
        profile columns keep each VIOS's own figures (v10 stage 3.1b, design
        revision 2)."""
        rows = {vm: self.rows[vm] for vm in VIOS}
        own = [round(float(rows[vm]['P-99W1']), 2) for vm in VIOS]
        combined = sum(int(round(v * 100)) for v in own) / 100
        section = self.log[self.log.index('\nVIOS Pairs\n'):self.log.index('\nEntitlement Totals\n')]
        own_vps = 0                                            # the strategic VPs are in the rationale only
        for vm in VIOS:
            m = re.search(rf'(?m)^      {vm}\s+P-99W1\s+own target {float(rows[vm]["P-99W1"]):.2f} on (\d+) VPs?;', section)
            self.assertIsNotNone(m, vm)
            own_vps += int(m.group(1))
        target_vps = max(own_vps, math.ceil(combined - 1e-9))
        for vm, row in rows.items():
            self.assertEqual(row['Planning Profile'], 'P-99W1', vm)
            ent, vps = float(row['NFIT - ENT']), int(row['NFIT - VCPU'])
            self.assertLessEqual(ent, vps + 1e-9, vm)
            self.assertGreaterEqual(ent, MIN_ENT_PER_VP * vps - 1e-9, vm)
            self.assertLessEqual(ent, combined + 1e-9, vm)
            partner = [v for v in VIOS if v != vm][0]
            vp_text = '1 VP' if target_vps == 1 else f'{target_vps} VPs'
            self.assertTrue(row['NFIT - Action'].startswith(
                f'Combined VIOS sizing with {partner} (planning scenario): own targets '
                f'{own[0]:.2f} + {own[1]:.2f}, margin 0% = {combined:.2f} entitlement on {vp_text}.'), row['NFIT - Action'])
            self.assertNotEqual(row['P-99W1'], '', vm)          # own figures kept in the profile column
        # Same installed pair and the same target: the same current-cycle pair.
        self.assertEqual(len({(r['NFIT - ENT'], r['NFIT - VCPU']) for r in rows.values()}), 1)
        self.assertIn('vio1, vio2 (named by vios_peer)', section)
        self.assertIn(f'combined on P-99W1: {own[0]:.2f} + {own[1]:.2f}, margin 0% = {combined:.2f} on {vp_text}', section)
        self.assertIn('planning scenario', self.flat(section))
        for vm in VIOS:
            self.assertRegex(self.log, rf'(?m)^    VIOS pair\s+: combined sizing \(planning scenario\) with {[v for v in VIOS if v != vm][0]};')
        self.assertIn('P, peak protected (default for a VIOS)', self.log)

    def test_retired_columns_and_option(self):
        for column in ('RunQ_Tactical', 'RunQ_Strategic', 'RunQ_Potential', 'RunQ_Source', 'VCPU_Constraints',
                       'NETT', 'NETT%'):
            self.assertNotIn(column, self.header)
        self.assertFalse([c for c in self.header if c.endswith('_rVCPU') or c.endswith('_rVCPU_EntBounded')])
        self.assertEqual(len(self.header), 21 + 3 * 13)
        self.assertEqual(self.header[:21], [
            'VM', 'TIER', 'Hint', 'Planning Profile', 'Pattern', 'SMT', 'Serial', 'SystemType', 'Pool Name',
            'Pool ID', 'Pool Size', 'Current - ENT', 'Current - VCPU', 'NFIT - ENT', 'NFIT - VCPU', 'NFIT - Action',
            'VM_Comments', 'Pressure', 'PressureDetail', 'Peak', 'Peak_P-99W1'])
        p = subprocess.run(['perl', os.path.join(self.root, 'nfit-profile'), '--nmondir', self.cache,
                            '--excel-formulas', 'true'], capture_output=True, text=True, cwd=self.root)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('--excel-formulas was retired', p.stderr)

    def test_terms_used_are_defined_in_the_terminology_reference(self):
        reference = self.log[self.log.index('Key Terminology Reference'):self.log.index('\nPool and VP Checks\n')]
        for term in ('Planning profile', 'Pool entitlement', 'Entitlement and', 'Recommended', 'rENT_Tactical',
                     'rVCPU_Tactical', 'VP reduction step', 'MaxCPU', 'Planning Profile', 'NFIT - ENT /', 'NFIT - Action',
                     '[POOL_SHORTFALL]', '[VP_MISMATCH]', 'Totals'):
            self.assertIn(term, reference)


if __name__ == '__main__':
    unittest.main(verbosity=1)
