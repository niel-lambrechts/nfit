#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/moved_ha_member_test.py
# PURPOSE  : A PowerHA member that moved to another managed system during the
#            period (v10 B15), end to end on the two-frame PowerHA fixture.
#            The primary hanodeB runs on SYSHA2 until day 16 and on SYSHA3 from
#            then on, where it is installed at 2.00 (1.50 before). The staged
#            states of SYSHA2 still show it to the end of that capture, as a
#            stager before the D-025 fix leaves them.
#            - moved: the standby on SYSHA1 is sized from the plan of the frame
#              that holds the primary now, the primary's installed figure is
#              that of that frame, and the frame it left gives it a historic row;
#            - on two frames at the end of the period (still sampled on both):
#              no role is inferred from the two together; the standby is sized
#              on its own workload and the rationale says why.
# USAGE    : python3 test/moved_ha_member_test.py
# ==============================================================================
import calendar
import csv
import glob
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import powerha_roles_test as H

MOVE = H.FIRST + 16 * 86400
SHARE = '[GLOBAL]\nha_sizing = standby_share\n'


def build(leaves_the_old_frame):
    """The fixture with hanodeB on SYSHA3 from MOVE; on SYSHA2 until then, or to the end when it does not leave."""
    root = tempfile.mkdtemp(prefix='moved-')
    _, last = H.build(root, True, SHARE)
    old, new = (os.path.join(root, 'stage', s) for s in ('SYSHA2', 'SYSHA3'))
    os.makedirs(new)
    with open(os.path.join(old, '.nfit.cache.data')) as fh:
        header, *rows = fh.read().splitlines()
    epoch = lambda r: calendar.timegm(time.strptime(r[:19], '%Y-%m-%d %H:%M:%S'))
    # An LPAR that stays on the old frame to the end, so that the frame's own data does not stop with the move.
    stays = [r.replace(',hanodeB,', ',app2,') for r in rows]
    with open(os.path.join(new, '.nfit.cache.data'), 'w') as fh:
        fh.write('\n'.join([header] + [r for r in rows if epoch(r) >= MOVE]) + '\n')
    kept = [r for r in rows if epoch(r) < MOVE] if leaves_the_old_frame else rows
    with open(os.path.join(old, '.nfit.cache.data'), 'w') as fh:
        fh.write('\n'.join([header] + sorted(kept + stays)) + '\n')
    for name in ('.nfit.cache.manifest', '.nfit_stage_id'):
        shutil.copy(os.path.join(old, name), os.path.join(new, name))
    with open(os.path.join(old, '.nfit.cache.states')) as fh:
        states = json.load(fh)
    moved = json.loads(json.dumps(states))
    moved['hanodeB'][0]['start_epoch'] = MOVE
    moved['hanodeB'][0]['metadata'].update(serial_number='SYSHA3', entitlement=2.0)
    with open(os.path.join(new, '.nfit.cache.states'), 'w') as fh:
        json.dump(moved, fh)
    states['app2'] = json.loads(json.dumps(states['hanodeB']))
    with open(os.path.join(old, '.nfit.cache.states'), 'w') as fh:
        json.dump(states, fh)
    runs = {}
    for serial in ('SYSHA3', 'SYSHA2', 'SYSHA1'):
        runs[serial] = subprocess.run(['perl', os.path.join(root, 'nfit-profile'), '--runq-perc-behavior=match', '--enddate', last,
                                       '--nmondir', os.path.join(root, 'stage', serial)], capture_output=True, text=True, timeout=300, cwd=root)
    return root, runs


def row_of(root, serial, vm):
    path, = [f for f in glob.glob(os.path.join(root, 'output', '*.csv')) if serial in f and '.decisions.' not in f]
    with open(path) as fh:
        return next(r for r in csv.DictReader(fh) if r['VM'] == vm)


def member_of(root, serial, vm):
    snaps = sorted(glob.glob(os.path.join(root, 'stage', serial, '.nfit.store', 'proposals', 'v1', '*', '*.json*')), key=os.path.getmtime)
    with open(snaps[-1], 'rb') as fh:
        raw = fh.read()
    return json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)['members'][vm]


def rationale(root, serial):
    path, = [f for f in glob.glob(os.path.join(root, 'output', '*.log')) if serial in f]
    with open(path, encoding='utf-8') as fh:
        return ' '.join(fh.read().split())


@unittest.skipUnless(H.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class MovedMemberTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.moved, cls.moved_runs = build(True)
        cls.both, cls.both_runs = build(False)

    @classmethod
    def tearDownClass(cls):
        for root in (cls.moved, cls.both):
            shutil.rmtree(root, ignore_errors=True)

    def test_every_frame_is_analysed(self):
        for runs in (self.moved_runs, self.both_runs):
            for serial, run in runs.items():
                self.assertEqual(run.returncode, 0, serial + ': ' + run.stderr[-600:])

    def test_the_standby_is_sized_from_the_plan_of_the_frame_that_holds_the_primary(self):
        target = member_of(self.moved, 'SYSHA3', 'hanodeB')
        target = target['profiles'][target['planningProfile']]['entitlement']
        row = row_of(self.moved, 'SYSHA1', 'hanodeA')
        self.assertTrue(row['NFIT - Action'].startswith(
            'PowerHA standby for hanodeB (inferred): 25%% of its %.2f target (its proposal for data ending ' % target), row['NFIT - Action'])
        role = member_of(self.moved, 'SYSHA1', 'hanodeA')['role']
        self.assertEqual((role['type'], role['primaryTarget']['source'], role['primaryTarget']['entitlement']), ('haStandby', 'proposal', target))
        self.assertNotIn('unresolved', role)
        # The plan read is one of the frame that holds the primary now, not of the frame it left.
        held = glob.glob(os.path.join(self.moved, 'stage', 'SYSHA3', '.nfit.store', 'proposals', 'v1', '*', role['primaryTarget']['snapshot']))
        self.assertEqual(len(held), 1)

    def test_the_primarys_installed_figure_is_that_of_the_frame_that_holds_it(self):
        evidence = member_of(self.moved, 'SYSHA1', 'hanodeA')['roleEvidence']
        self.assertEqual(evidence['configuration']['hanodeB'], {'entitlement': 2.0, 'VPs': 4})
        self.assertEqual((evidence['role']['effective'], evidence['role']['source']), ('standby', 'inferred'))
        self.assertEqual(row_of(self.moved, 'SYSHA3', 'hanodeB')['Current - ENT'], '2.00')

    def test_the_frame_it_left_keeps_it_as_a_record(self):
        row = row_of(self.moved, 'SYSHA2', 'hanodeB')
        self.assertIn('[HISTORIC]', row['VM_Comments'])
        self.assertTrue(row['NFIT - Action'].startswith('Historic:'), row['NFIT - Action'])
        self.assertNotIn('[HISTORIC]', row_of(self.moved, 'SYSHA2', 'app2')['VM_Comments'])

    def test_a_member_sampled_on_two_frames_to_the_end_gets_no_inferred_role(self):
        why = 'hanodeB is sampled on more than one managed system at the end of this period: roles are not inferred until its managed system is established'
        member = member_of(self.both, 'SYSHA1', 'hanodeA')
        self.assertNotIn('role', member)
        self.assertEqual((member['roleEvidence']['role']['effective'], member['roleEvidence']['reason']), ('unknown', why))
        row = row_of(self.both, 'SYSHA1', 'hanodeA')
        self.assertNotIn('PowerHA standby for', row['NFIT - Action'])
        text = rationale(self.both, 'SYSHA1')
        self.assertIn(why, text)
        self.assertIn('Sizing: no node is resolved as the standby of an active node; each node is sized on its own workload.', text)


if __name__ == '__main__':
    unittest.main()
