#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/lpar_presence_test.py
# PURPOSE  : An LPAR that is no longer sampled on a frame at the analysis end
#            (v10 B15; workaround for D-025, where its configuration state still
#            runs to the end of the capture). On the two-frame PowerHA fixture,
#            the samples of app1 on SYSHA1 stop eleven days before the end.
#            - its CSV row is kept with its figures, the action begins
#              "Historic:" and VM_Comments carries [HISTORIC];
#            - the Entitlement Totals leave it out and state it apart;
#            - the other rows of the frame are as in a run where it is present;
#            - the saved plan states when it was sampled;
#            - a planning cycle's footprint counts the LPARs still there;
#            - an LPAR sampled to within two days of the end is still present;
#            - when its samples continue on the other frame of the cycle, the
#              finalisation names that frame on the historic row, the frame it is
#              on sizes it and says how many days of data it has, and each frame's
#              footprint counts it once.
# USAGE    : python3 test/lpar_presence_test.py
# ==============================================================================
import csv
import json
import os
import re
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
import planning_cycle_test as P
import cycle_finalise_test as F


def stop_samples(root, serial, lpar, days_before_end):
    """Drops the samples of one LPAR in the last days of the capture; its configuration state is left as it is."""
    cutoff = time.strftime('%Y-%m-%d', time.gmtime(H.FIRST + (H.DAYS - days_before_end) * 86400))
    path = os.path.join(root, 'stage', serial, '.nfit.cache.data')
    with open(path) as fh:
        lines = fh.read().splitlines()
    kept = [l for l in lines if not (l.split(',')[1:2] == [lpar] and l[:10] >= cutoff)]
    with open(path, 'w') as fh:
        fh.write('\n'.join(kept) + '\n')
    return time.strftime('%Y-%m-%d', time.gmtime(H.FIRST + (H.DAYS - days_before_end - 1) * 86400))


def move_samples(root, lpar, source, target, days_before_end):
    """The last days of one LPAR's samples go to another frame, with a configuration state there from that day."""
    cutoff = time.strftime('%Y-%m-%d', time.gmtime(H.FIRST + (H.DAYS - days_before_end) * 86400))
    src, dst = (os.path.join(root, 'stage', f, '.nfit.cache.data') for f in (source, target))
    with open(src) as fh:
        lines = fh.read().splitlines()
    moved = [l for l in lines[1:] if l.split(',')[1:2] == [lpar] and l[:10] >= cutoff]
    with open(src, 'w') as fh:
        fh.write('\n'.join([lines[0]] + [l for l in lines[1:] if l not in set(moved)]) + '\n')
    with open(dst) as fh:
        there = fh.read().splitlines()
    with open(dst, 'w') as fh:
        fh.write('\n'.join([there[0]] + sorted(there[1:] + moved)) + '\n')
    states_src, states_dst = (os.path.join(root, 'stage', f, '.nfit.cache.states') for f in (source, target))
    with open(states_src) as fh:
        state = json.load(fh)[lpar][-1]
    with open(states_dst) as fh:
        states = json.load(fh)
    start = int(time.mktime(time.strptime(cutoff + ' UTC', '%Y-%m-%d %Z'))) - time.timezone
    states[lpar] = [dict(state, start_epoch=start, metadata=dict(state['metadata'], serial_number=target))]
    with open(states_dst, 'w') as fh:
        json.dump(states, fh)
    return cutoff


def rows_of(root, serial):
    path, = [os.path.join(root, 'output', f) for f in os.listdir(os.path.join(root, 'output'))
             if serial in f and f.endswith('.csv') and '.decisions.' not in f]
    with open(path) as fh:
        return {r['VM']: r for r in csv.DictReader(fh)}


def rationale_of(root, serial):
    path, = [os.path.join(root, 'output', f) for f in os.listdir(os.path.join(root, 'output')) if serial in f and f.endswith('.log')]
    return P.text(path)


@unittest.skipUnless(H.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class LparPresenceTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.roots = {}
        for name, days in (('gone', 11), ('there', 0), ('gap', 2)):
            root = tempfile.mkdtemp(prefix='presence-')
            _, cls.last = H.build(root, True, P.SHARE)
            cls.last_day = stop_samples(root, 'SYSHA1', 'app1', days) if days else cls.last
            if name == 'gone':
                cls.gone_last = cls.last_day
            run = P.analyse(root, 'SYSHA1', cls.last)
            assert run.returncode == 0, run.stderr[-800:]
            cls.roots[name] = root

    @classmethod
    def tearDownClass(cls):
        for root in cls.roots.values():
            shutil.rmtree(root, ignore_errors=True)

    def test_the_row_is_kept_and_marked_historic(self):
        gone, there = rows_of(self.roots['gone'], 'SYSHA1'), rows_of(self.roots['there'], 'SYSHA1')
        row = gone['app1']
        day = time.strftime('%-d %b %Y', time.strptime(self.gone_last, '%Y-%m-%d'))
        self.assertTrue(row['NFIT - Action'].startswith('Historic: not sampled on this frame since %s (11 days before the analysis end); '
                                                        "not counted in this frame's totals. As analysed: " % day), row['NFIT - Action'])
        self.assertTrue(row['VM_Comments'].startswith('[HISTORIC]'), row['VM_Comments'])
        self.assertRegex(row['NFIT - ENT'], r'^\d+\.\d\d$')                              # its figures stay, as a record
        self.assertEqual(row['Current - ENT'], there['app1']['Current - ENT'])
        self.assertNotIn('Historic', there['app1']['NFIT - Action'])
        self.assertNotIn('[HISTORIC]', there['app1']['VM_Comments'])
        self.assertEqual(gone['hanodeA'], there['hanodeA'])                             # the LPAR still there: unchanged

    def test_the_totals_leave_it_out_and_say_so(self):
        text = rationale_of(self.roots['gone'], 'SYSHA1')
        totals = text[text.index('Entitlement Totals'):]
        self.assertRegex(totals, r'All LPARs\s+1\s+1\.50\s')
        self.assertIn('Not counted: 1 LPAR no longer sampled on this frame at the analysis end, installed entitlement 1.50 (marked [HISTORIC] in the CSV).',
                      re.sub(r'\s+', ' ', totals))
        present = rationale_of(self.roots['there'], 'SYSHA1')
        self.assertRegex(present[present.index('Entitlement Totals'):], r'All LPARs\s+2\s+3\.00\s')
        self.assertNotIn('no longer sampled on this frame', present)

    def test_two_days_without_samples_is_not_a_departure(self):
        row = rows_of(self.roots['gap'], 'SYSHA1')['app1']
        self.assertNotIn('Historic', row['NFIT - Action'])
        self.assertNotIn('[HISTORIC]', row['VM_Comments'])

    def test_the_saved_plan_and_the_footprint(self):
        root = self.roots['gone']
        self.assertEqual(P.analyse(root, 'SYSHA2', self.last).returncode, 0)
        done = F.finalise(root)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        plan = P.load(F.outputs(root, 'SYSHA1')['plan'])
        member = plan['members']['app1']
        self.assertEqual(member['lastSampled'], self.gone_last)
        self.assertLess(member['sampled']['first'], member['sampled']['last'])
        self.assertNotIn('poolMember', member)                                          # not in the pool check
        self.assertIn('[HISTORIC]', member['published']['alerts'])
        with open(os.path.join(root, 'cycle', 'cycle-footprint.csv')) as fh:
            row = next(r for r in csv.DictReader(fh) if r['Frame'] == 'SYSHA1')
        self.assertEqual((row['LPARs'], row['Installed_ENT'], row['LPARs_Left'], row['Left_Installed_ENT']), ('1', '1.50', '1', '1.50'))
        self.assertEqual(row['Shared_Capacity_Check'], 'agreed by 1 LPAR')              # the LPAR that left is no reporter
        text = P.text(os.path.join(root, 'cycle', 'cycle-footprint.txt'))
        self.assertRegex(re.sub(r'\s+', ' ', text), r'Not counted: 1 LPAR last sampled before the analysis end \(11 days earlier\), installed '
                                                    r'entitlement 1\.50\. It is marked \[HISTORIC\] in the CSV')
        # The historic row survives the finalisation as it was written.
        self.assertTrue(rows_of(root, 'SYSHA1')['app1']['NFIT - Action'].startswith('Historic: not sampled on this frame since'))
        self.assertTrue(rows_of(root, 'SYSHA1')['app1']['VM_Comments'].startswith('[HISTORIC]'))


@unittest.skipUnless(H.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class LparMoveTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='presence-')
        _, cls.last = H.build(cls.root, True, P.SHARE)
        cls.arrived = move_samples(cls.root, 'app1', 'SYSHA1', 'SYSHA2', 11)
        for serial in ('SYSHA1', 'SYSHA2'):
            run = P.analyse(cls.root, serial, cls.last)
            assert run.returncode == 0, run.stderr[-800:]
        cls.before = rows_of(cls.root, 'SYSHA1')['app1']
        cls.done = F.finalise(cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_the_move_is_named_once_the_other_frame_shows_it(self):
        self.assertEqual(self.done.returncode, 0, self.done.stderr[-800:])
        self.assertTrue(self.before['NFIT - Action'].startswith('Historic: not sampled on this frame since'))     # neutral at analysis
        row = rows_of(self.root, 'SYSHA1')['app1']
        day = time.strftime('%-d %b %Y', time.strptime(self.arrived, '%Y-%m-%d'))
        self.assertRegex(row['NFIT - Action'], r"^Historic: moved to frame SYSHA2 \(last sampled on this frame \d+ \w{3} 2026, sampled there from %s\); "
                                               r"its plan is the row on that frame\. Not counted in this frame's totals\. As analysed: \S" % day)
        self.assertNotIn('As analysed: Historic', row['NFIT - Action'])
        self.assertTrue(row['VM_Comments'].startswith('[HISTORIC]'))
        self.assertEqual(row['NFIT - ENT'], self.before['NFIT - ENT'])
        rationale = rationale_of(self.root, 'SYSHA1')
        self.assertRegex(re.sub(r'\s+', ' ', rationale[rationale.index('Cycle Finalisation (planning cycle'):]),
                         r'app1: moved to frame SYSHA2 \(last sampled on this frame \d+ \w{3} 2026, sampled there from %s\)' % day)

    def test_the_frame_it_is_on_sizes_it_and_states_its_history(self):
        row = rows_of(self.root, 'SYSHA2')['app1']
        self.assertNotIn('Historic', row['NFIT - Action'])
        day = time.strftime('%-d %b %Y', time.strptime(self.arrived, '%Y-%m-%d'))
        self.assertTrue(row['NFIT - Action'].endswith('On this frame since %s: sized on 11 days of data.' % day), row['NFIT - Action'])
        self.assertRegex(row['NFIT - ENT'], r'^\d+\.\d\d$')

    def test_it_is_counted_once(self):
        with open(os.path.join(self.root, 'cycle', 'cycle-footprint.csv')) as fh:
            frames = {r['Frame']: r for r in csv.DictReader(fh)}
        self.assertEqual((frames['SYSHA1']['LPARs'], frames['SYSHA1']['LPARs_Left'], frames['SYSHA1']['LPARs_Left_Moved']), ('1', '1', '1'))
        self.assertEqual((frames['SYSHA2']['LPARs'], frames['SYSHA2']['LPARs_Left'], frames['SYSHA2']['LPARs_On_Two_Frames']), ('2', '0', '0'))
        self.assertAlmostEqual(float(frames['SYSHA1']['Installed_ENT']) + float(frames['SYSHA2']['Installed_ENT']), 4.5, places=2)
        text = re.sub(r'\s+', ' ', P.text(os.path.join(self.root, 'cycle', 'cycle-footprint.txt')))
        self.assertIn('Of these, 1 moved to another frame analysed and 0 are seen on no other frame analysed', text)
        self.assertRegex(self.done.stderr, r'Planning cycle \S+: finalised')
        before = {s: F.hashes(self.root, s) for s in ('SYSHA1', 'SYSHA2')}
        self.assertEqual(F.finalise(self.root).returncode, 0)
        self.assertEqual({s: F.hashes(self.root, s) for s in ('SYSHA1', 'SYSHA2')}, before)       # a repeat changes nothing


if __name__ == '__main__':
    unittest.main(verbosity=1)
