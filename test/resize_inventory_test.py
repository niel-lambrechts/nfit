#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/resize_inventory_test.py
# PURPOSE  : dev/resize-inventory on a designed staged system: each kind of
#            configuration change is classed, and a change counts as usable
#            only with enough one-minute data on both sides, on uncapped shared
#            LPARs. Counts only; nothing is scored.
# USAGE    : python3 test/resize_inventory_test.py
# ==============================================================================
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
START = 1788220800                     # 2026-09-01T00:00:00Z
DAYS = 32
HALF = START + 16 * 86400
LATE = START + 29 * 86400

MD = lambda ent, vps, **more: dict({'proc_type': 'PowerPC_POWER10', 'proc_version': '2.0', 'smt': 8, 'entitlement': ent,
                                    'virtual_cpus': vps, 'capped': 0, 'pool_id': 0}, **more)
#        name: (change at, state before, state after, PhysC before, PhysC after, sampling step in seconds)
LPARS = {'zzquietcut': (HALF, MD(2.0, 8), MD(2.0, 4), 1.0, 1.0, 60),          # VPs down, never near its width
         'zzwidened': (HALF, MD(1.0, 2), MD(1.0, 4), 1.9, 2.5, 60),           # VPs up, at its width before
         'zzguarantee': (HALF, MD(1.0, 4), MD(2.0, 4), 1.0, 1.0, 60),         # entitlement only
         'zzboth': (HALF, MD(1.0, 2), MD(2.0, 4), 1.0, 1.0, 60),              # entitlement and VPs
         'zzrecent': (LATE, MD(2.0, 4), MD(2.0, 2), 1.0, 1.0, 60),            # VPs down three days before the end
         'zzcoarse': (HALF, MD(2.0, 4), MD(2.0, 2), 1.0, 1.0, 300),           # five-minute samples
         'zzcapped': (HALF, MD(2.0, 4, capped=1), MD(2.0, 2, capped=1), 1.0, 1.0, 3600),
         'zzmoved': (HALF, MD(2.0, 4), MD(2.0, 4, pool_id=1), 1.0, 1.0, 3600),   # another pool
         'zzsteady': (None, MD(2.0, 4), None, 1.0, 1.0, 3600)}


class ResizeInventoryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.mkdtemp(prefix='resize-inventory-')
        stage = os.path.join(cls.scratch, 'work', 'case', 'stage', 'SYNRI')
        os.makedirs(stage)
        with open(os.path.join(stage, '.nfit.cache.data'), 'w') as fh:
            fh.write('Timestamp,VMName,PhysC,RunQ,PhysB\n')
            for t in range(START, START + DAYS * 86400, 60):
                stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t))
                for vm, (at, _before, _after, c0, c1, step) in LPARS.items():
                    if t % step:
                        continue
                    c = c0 if at is None or t < at else c1
                    fh.write(f'{stamp},{vm},{c:.4f},1.0000,{0.3 * c:.4f}\n')
                    if vm == 'zzquietcut' and t == START + 3600:
                        fh.write(f'{stamp},{vm},{c:.4f},1.0000,{0.3 * c:.4f}\n')       # a repeated second
        end = START + DAYS * 86400 - 60
        states = {}
        for vm, (at, before, after, *_rest) in LPARS.items():
            states[vm] = ([{'start_epoch': START, 'end_epoch': end, 'metadata': before}] if at is None else
                          [{'start_epoch': START, 'end_epoch': at - 60, 'metadata': before}, {'start_epoch': at, 'end_epoch': end, 'metadata': after}])
        with open(os.path.join(stage, '.nfit.cache.states'), 'w') as fh:
            json.dump(states, fh)
        cls.run_ = subprocess.run([sys.executable, os.path.join(REPO, 'dev', 'resize-inventory'), '--work', os.path.join(cls.scratch, 'work'),
                                   '--out', os.path.join(cls.scratch, 'out'), '--label', 'designed'], capture_output=True, text=True, timeout=160)
        cls.result = {}
        path = os.path.join(cls.scratch, 'out', 'resize-inventory-designed.json')
        if os.path.exists(path):
            with open(path) as fh:
                cls.result = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def test_the_run_completes_and_names_nothing(self):
        self.assertEqual(self.run_.returncode, 0, self.run_.stderr)
        self.assertIn('total hits: 0', self.run_.stdout)
        text = json.dumps(self.result) + self.run_.stdout
        for vm in LPARS:
            self.assertNotIn(vm, text)
        self.assertEqual(self.result['lpars'], {'withStates': 9, 'withMoreThanOneState': 8, 'withASizeChange': 7})
        self.assertEqual(self.result['boundaries'], 8)
        self.assertEqual(self.result['samples']['sameSecond'], 1)

    def test_each_kind_of_change_is_classed(self):
        c = self.result['changes']
        self.assertEqual({kind: {d: cell['events'] for d, cell in directions.items()} for kind, directions in c.items()},
                         {'vpOnly': {'down': 4, 'up': 1}, 'entitlementOnly': {'up': 1}, 'combined': {'bothUp': 1}, 'otherConfiguration': {'-': 1}})
        self.assertIsNone(c['otherConfiguration']['-']['usableByDaysEachSide'])

    def test_a_change_is_usable_only_with_enough_one_minute_data_on_both_sides(self):
        c = self.result['changes']
        down = c['vpOnly']['down']
        # Of the four reductions only the quiet one is usable: 16 days on each side, so at 7 and 14 days but not at 28.
        self.assertEqual(down['usableByDaysEachSide'], {'7': {'events': 1, 'lpars': 1}, '14': {'events': 1, 'lpars': 1}, '28': {'events': 0, 'lpars': 0}})
        self.assertEqual(down['usableAtWorkingDays'], {'beforeApproachedWidth': 0, 'beforeDidNotApproachWidth': 1, 'medianRelativeChange': 0.5})
        self.assertEqual(self.result['notUsableAtWorkingDays']['vpOnly'], {'tooFewDaysAfter': 1, 'coarseSampling': 1, 'notUncappedShared': 1})
        up = c['vpOnly']['up']
        self.assertEqual(up['usableAtWorkingDays'], {'beforeApproachedWidth': 1, 'beforeDidNotApproachWidth': 0, 'medianRelativeChange': 1.0})
        self.assertEqual(c['entitlementOnly']['up']['usableByDaysEachSide']['14'], {'events': 1, 'lpars': 1})
        self.assertEqual(c['combined']['bothUp']['usableByDaysEachSide']['14'], {'events': 1, 'lpars': 1})

    def test_a_staged_directory_needs_a_label(self):
        stage = os.path.join(self.scratch, 'work', 'case', 'stage', 'SYNRI')
        run = subprocess.run([sys.executable, os.path.join(REPO, 'dev', 'resize-inventory'), '--stage', stage, '--out', os.path.join(self.scratch, 'out2')],
                             capture_output=True, text=True, timeout=60)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('--label', run.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.scratch, 'out2')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
