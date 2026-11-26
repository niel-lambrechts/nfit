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
#            - an LPAR sampled to within two days of the end is still present.
# USAGE    : python3 test/lpar_presence_test.py
# ==============================================================================
import csv
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


if __name__ == '__main__':
    unittest.main(verbosity=1)
