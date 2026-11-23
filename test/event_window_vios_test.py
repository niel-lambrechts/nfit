#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/event_window_vios_test.py
# PURPOSE  : A dual-VIOS pair in a seasonal run (v10 stage 3.6, with stage 3.1b):
#            each VIOS is first sized for the event on its own, the pair is then
#            given the combined size of those two event targets, and the
#            event-window plan carries the combined figures: the pool check and
#            the requirements by pool read them, and a pool whose event
#            guarantees exceed its maximum is said to be short.
#            The synthetic system of member_exposure_test, with its two LPARs of
#            pool 7 declared as a VIOS pair.
# USAGE    : python3 test/event_window_vios_test.py
# ==============================================================================
import csv
import glob
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import member_exposure_test as M
import event_window_test as E

PAIR = '[vmA]\nlpar_type = vios\nvios_peer = vmB\n[vmB]\nlpar_type = vios\n'


@unittest.skipUnless(M.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class EventWindowViosTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fixture = M.MemberExposureTests
        cls.fixture.roots = []
        cls.pair = cls.seasonal(PAIR)
        cls.plain = cls.seasonal('')

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    @classmethod
    def seasonal(cls, vms):
        root = cls.fixture.make_root()
        with open(os.path.join(root, 'etc', 'nfit.vms.cfg'), 'w') as fh:
            fh.write(vms)
        with open(os.path.join(root, 'etc', 'nfit.seasonality.cfg'), 'w') as fh:
            fh.write(E.EVENT % ('predictive_peak', '2026-09-01:2026-09-01', 1, 1, 1))
        cls.fixture.run_profile(root, '--update-history', '--min-history-days', '1', '--pool-history', '--force')
        cls.fixture.run_profile(root, '--apply-seasonality', 'ev')
        with open(glob.glob(os.path.join(root, 'output', '*.ev.*.csv'))[0]) as fh:
            rows = {row['VM']: row for row in csv.DictReader(fh)}
        with open(glob.glob(os.path.join(root, 'output', '*.ev.proposal.*.json'))[0]) as fh:
            members = json.load(fh)['members']
        with open(glob.glob(os.path.join(root, 'output', '*.ev.*.log'))[0]) as fh:
            return {'rows': rows, 'members': members, 'log': fh.read()}

    def test_the_pair_is_sized_from_both_event_targets(self):
        members, rows = self.pair['members'], self.pair['rows']
        roles = {vm: members[vm]['role'] for vm in ('vmA', 'vmB')}
        own = {vm: members[vm]['profiles'][roles[vm]['profile']]['entitlement'] for vm in roles}
        self.assertEqual(roles['vmA']['profile'], roles['vmB']['profile'])              # the pair is read on one profile
        for vm, role in roles.items():
            self.assertEqual((role['type'], role['mode'], role['margin']), ('viosPair', 'combined', 0))
            self.assertAlmostEqual(role['targetEntitlement'], own['vmA'] + own['vmB'], places=2)     # the sum of the two event targets
            self.assertAlmostEqual(float(rows[vm]['NFIT - ENT']), role['entitlement'], places=2)
            self.assertEqual(int(rows[vm]['NFIT - VCPU']), role['VPs'])
            self.assertEqual(members[vm]['roleSource'], 'VIOS_PAIR')
            self.assertRegex(rows[vm]['NFIT - Action'], r'^Event window: Combined VIOS sizing with vm[AB] \(planning scenario\): own targets '
                             r'%.2f \+ %.2f, margin 0%% = %.2f entitlement' % (own['vmA'], own['vmB'], role['targetEntitlement']))   # in the pair's order
        self.assertEqual(rows['vmA']['NFIT - ENT'], rows['vmB']['NFIT - ENT'])
        self.assertNotEqual(own['vmA'], own['vmB'])                                     # the own targets differ; the pair does not
        self.assertNotIn('role', members['vmS'])                                        # the LPAR outside the pair is sized as before
        self.assertEqual(self.pair['rows']['vmS']['NFIT - ENT'], self.plain['rows']['vmS']['NFIT - ENT'])

    def test_the_own_event_targets_of_the_pair_keep_their_event_evidence(self):
        for vm in ('vmA', 'vmB'):
            member = self.pair['members'][vm]
            self.assertEqual(member['eventEvidence']['none']['verdict'], 'contested')   # pool 7 was contested in the past window
            self.assertFalse(member['eventWindow']['allowanceWithheld'])

    def test_the_event_window_plan_carries_the_combined_figures(self):
        members, log = self.pair['members'], self.pair['log']
        combined = members['vmA']['role']['entitlement']
        for vm in ('vmA', 'vmB'):
            self.assertEqual((members[vm]['eventWindow']['pool'], members[vm]['eventWindow']['entitlement']), (7, combined))
        total = 2 * combined
        self.assertGreater(total, 4.0)                                                  # the pair no longer fits its pool of 4
        self.assertIn('Pool 7 - the proposed entitlements do not fit', log)
        self.assertRegex(log, r'\n  7\s+2\s+4\.00\s+%.2f\s+0\s+0\.00  the guarantees alone exceed the pool maximum' % total)
        self.assertIn('1 pool short at its maximum on the event guarantees alone: raise the pool maximum before the event', log)
        self.assertNotIn('No pool is short at its maximum', log)
        # Without the pair the same pool fits, and the plan says so.
        self.assertRegex(self.plain['log'], r'\n  7\s+2\s+4\.00\s+\d+\.\d\d\s+0\s+0\.00  guarantees and relied-on borrowing fit under the pool maximum')
        self.assertIn('No pool is short at its maximum on these figures.', self.plain['log'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
