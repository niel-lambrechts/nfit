#!/usr/bin/env python3
# ==============================================================================
# End-to-end tests for the v10 step 3b correction, on synthetic data:
#   - dispatch width reference: an uncapped micro-partition on 1 VP whose VP
#     range is widened on run-queue evidence keeps the entitlement of the same
#     sizing made without the widening (extra width is not a borrowing
#     allowance);
#   - a capped LPAR: today's physical CPU limit is its entitlement, and an
#     implausible run queue (XRQ) leads VM_Comments and is named in the action
#     of an increase.
# ==============================================================================
import calendar
import csv
import glob
import io
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
TRACKED_ETC = ('nfit.profiles.cfg', 'nfit.profiles.cfg.default', 'nfit.mgsys.cfg.default', 'nfit.scenarios.cfg.default')
FIRST = calendar.timegm((2026, 9, 1, 0, 0, 0))
DAYS = 14
PROFILE = re.compile(r'^[A-Z]\d?-\d+W\d+$')


def perl_ready():
    return (shutil.which('perl') is not None and
            subprocess.run(['perl', '-MJSON', '-MList::MoreUtils', '-e1'], capture_output=True).returncode == 0)


def run(ent, vps, capped, physc, runq, tier, *extra):
    """Size one synthetic LPAR (pool 32, SMT8); returns (process, CSV row, log)."""
    root = tempfile.mkdtemp(prefix='width-ref-')
    try:
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
            fh.write(f'[lp1]\ntier = {tier}\n')
        last = time.strftime('%Y-%m-%d', time.gmtime(FIRST + DAYS * 86400 - 60))
        cache = os.path.join(root, 'stage', 'SYSW1')
        os.makedirs(cache)
        rows = ['Timestamp,VMName,PhysC,RunQ']
        for e in range(FIRST, FIRST + DAYS * 86400, 60):
            h = (e // 3600) % 24
            rows.append(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(e))},lp1,{physc(e, h):.4f},{runq(e, h):.4f}")
        with open(os.path.join(cache, '.nfit.cache.data'), 'w') as fh:
            fh.write('\n'.join(rows) + '\n')
        with open(os.path.join(cache, '.nfit.cache.manifest'), 'w') as fh:
            fh.write(f'cache_build_status: success\ndata_start: 2026-09-01\ndata_end: {last} 23:59:00\n')
        # MaxCPU, today's physical CPU limit: the entitlement when capped, else min(VPs, pool).
        meta = {'proc_clock': '3800MHz', 'serial_number': 'SYSW1', 'proc_type': 'PowerPC_POWER10', 'proc_version': '2.0',
                'max_cpu': ent if capped else min(vps, 32), 'pool_id': 0, 'smt': 8, 'pool_cpu': 32,
                'entitlement': ent, 'capped': capped, 'virtual_cpus': vps}
        with open(os.path.join(cache, '.nfit.cache.states'), 'w') as fh:
            json.dump({'lp1': [{'start_epoch': FIRST, 'end_epoch': FIRST + DAYS * 86400 - 60, 'duration_days': DAYS,
                                'metadata': meta}]}, fh)
        with open(os.path.join(cache, '.nfit_stage_id'), 'w') as fh:
            fh.write('synthetic\n')
        proc = subprocess.run(['perl', os.path.join(root, 'nfit-profile'), '--runq-perc-behavior=match',
                               '--enddate', last, '--nmondir', cache, *extra], capture_output=True, text=True, timeout=600, cwd=root)
        row, log = {}, ''
        csvs = [f for f in glob.glob(os.path.join(root, 'output', '*.csv')) if '.decisions.' not in f]
        if csvs:
            with open(csvs[0]) as fh:
                row = next(r for r in csv.DictReader(io.StringIO(fh.read().split('\n\n')[0])) if r.get('VM') == 'lp1')
        logs = glob.glob(os.path.join(root, 'output', '*.log'))
        if logs:
            with open(logs[0], encoding='utf-8') as fh:
                log = fh.read()
        return proc, row, log
    finally:
        shutil.rmtree(root, ignore_errors=True)


def references(log):
    """{profile: (own, reference)} from the 'Dispatch width reference' rationale lines."""
    found, profile = {}, None
    for line in log.splitlines():
        m = re.match(r'Profile Processed\s*: (\S+)', line)
        if m:
            profile = m.group(1)
        m = re.match(r'\s+- Dispatch width reference\s*: raised ([0-9.]+) -> ([0-9.]+) cores', line)
        if m and profile:
            found[profile] = (float(m.group(1)), float(m.group(2)))
    return found


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class WidthReferenceTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Installed 0.70 on 1 VP; quiet but queue-heavy office hours (6 runnable threads at
        # about 0.15 cores) with two short daily bursts to 0.88: the run queue asks for two
        # VPs, which the 2:1 range of a sub-0.5 entitlement does not give.
        def physc(e, h):
            if h in (10, 14) and (e // 60) % 60 < 8:
                return 0.88
            return 0.15 + 0.02 * math.sin(e / 777.0) if 8 <= h < 18 else 0.05

        def runq(e, h):
            return 6.0 + 0.5 * math.sin(e / 333.0) if 8 <= h < 18 else 0.3
        cls.proc, cls.row, cls.log = run(0.70, 1, 0, physc, runq, 'O3')
        cls.research = run(0.70, 1, 0, physc, runq, 'O3', '--research-no-peak-uplift')

    def test_run_succeeds(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-1500:])

    def test_widened_profiles_keep_the_unwidened_entitlement(self):
        self.assertIn('Envelope max expanded 1 -> 2', self.log)
        refs = references(self.log)
        self.assertTrue(refs, 'some widened profile must have fallen below its unwidened sizing')
        for profile, (own, reference) in refs.items():
            self.assertGreater(reference, own, profile)
            self.assertAlmostEqual(float(self.row[profile]), reference, places=2, msg=profile)
        self.assertIn('not room to borrow. A policy limit, not a measured need.', self.log)

    def test_research_switch_withholds_the_peak_uplift_only(self):
        proc, row, log = self.research
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        self.assertIn('not applied (research switch)', log)
        self.assertNotIn('Dispatch width reference', log)             # nothing left for width to lower
        lower = 0
        for name in (k for k in self.row if PROFILE.match(k)):
            self.assertLessEqual(float(row[name]), float(self.row[name]) + 1e-9, name)
            lower += float(row[name]) < float(self.row[name]) - 0.005
            self.assertEqual(row[name + '_rVCPU'], self.row[name + '_rVCPU'], name) if name + '_rVCPU' in row else None
        self.assertGreater(lower, 0)

    def test_current_cycle_pairs_are_valid(self):
        for name in (k for k in self.row if PROFILE.match(k)):
            ent, vps = self.row.get(name + '_rENT_Tactical'), self.row.get(name + '_rVCPU_Tactical')
            if ent and vps:
                self.assertLessEqual(0.05 * int(vps), float(ent) + 1e-9, name)
                self.assertLessEqual(float(ent), int(vps) + 1e-9, name)


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class CappedXrqTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Capped at 0.40 on 2 VPs and pinned there in office hours with 9 runnable threads:
        # the queue implies far more than twice today's limit of 0.40 (XRQ).
        cls.proc, cls.row, cls.log = run(0.40, 2, 1, lambda e, h: 0.40 if 8 <= h < 18 else 0.10,
                                         lambda e, h: 9.0 if 8 <= h < 18 else 0.5, 'G2')

    def test_run_succeeds(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-1500:])

    def test_the_limit_of_a_capped_lpar_is_its_entitlement(self):
        self.assertIn("above today's physical CPU limit 0.40 (capped: the entitlement): raising the entitlement to", self.log)
        self.assertNotIn('Dispatch width reference', self.log)       # width gives a capped LPAR no reach
        self.assertNotIn('not applied: no pool competition observed', self.log)   # a capped LPAR keeps the rule

    def test_xrq_leads_the_comments_and_is_named_in_an_increase(self):
        self.assertTrue(self.row['VM_Comments'].startswith('[XRQ]'), self.row['VM_Comments'])
        ent = float(self.row['NFIT - ENT'])
        self.assertGreater(ent, 0.40)
        self.assertTrue(self.row['NFIT - Action'].startswith('Raise entitlement 0.40 to %.2f.' % ent), self.row['NFIT - Action'])
        self.assertTrue(self.row['NFIT - Action'].endswith(
            'The run queue is implausibly high (XRQ): find the cause before applying the increase.'), self.row['NFIT - Action'])
        self.assertNotIn('MaxCPU', self.row['NFIT - Action'])


if __name__ == '__main__':
    unittest.main()
