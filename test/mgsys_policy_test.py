#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/mgsys_policy_test.py
# PURPOSE  : Managed-system defaults in nfit.vms.cfg (v10 B14), on the two-frame
#            PowerHA fixture (standby hanodeA on SYSHA1, primary hanodeB on
#            SYSHA2).
#            - a policy set for the standby's managed system gives the standby
#              the row that the same policy in [GLOBAL] gives, whichever frame
#              is analysed first: both frames read the same policy, so the
#              primary's plan is matched;
#            - a policy set for the active node's managed system does not size
#              the standby: the policy is that of the node that is standby;
#            - the rationale names the standby and where its policy came from;
#            - a PowerHA policy value that cannot be read stops the run before
#              any analysis, with every such setting listed.
# USAGE    : python3 test/mgsys_policy_test.py
# ==============================================================================
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import powerha_roles_test as H
import planning_cycle_test as P
import cycle_finalise_test as F

ON_STANDBY_FRAME = '[GLOBAL]\nha_sizing = independent\n[MGSYS:SYSHA1]\nha_sizing = standby_share\n'
ON_ACTIVE_FRAME = '[MGSYS:SYSHA2]\nha_sizing = standby_share\n'


def cycle(config, order):
    root = tempfile.mkdtemp(prefix='mgsys-')
    _, last = H.build(root, True, config)
    runs = [P.analyse(root, serial, last) for serial in order]
    return root, runs, F.finalise(root)


def rationale(root, serial):
    return P.text(F.outputs(root, serial)['rationale'])


@unittest.skipUnless(H.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class ManagedSystemPolicyTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.roots = []

    @classmethod
    def tearDownClass(cls):
        for root in cls.roots:
            shutil.rmtree(root, ignore_errors=True)

    def run_cycle(self, config, order):
        root, runs, done = cycle(config, order)
        self.roots.append(root)
        for run in runs + [done]:
            self.assertEqual(run.returncode, 0, run.stderr[-800:])
        return root

    def test_the_standbys_managed_system_sets_its_policy_in_either_order(self):
        roots = [self.run_cycle(ON_STANDBY_FRAME, order) for order in (('SYSHA2', 'SYSHA1'), ('SYSHA1', 'SYSHA2'))]
        rows = [P.standby_row(root) for root in roots]
        self.assertEqual(rows[0], rows[1])
        self.assertTrue(rows[0]['NFIT - Action'].startswith(F.RESOLVED), rows[0]['NFIT - Action'])
        # The same policy in [GLOBAL] gives the same row.
        self.assertEqual(rows[0], P.standby_row(self.run_cycle(P.SHARE, ('SYSHA2', 'SYSHA1'))))
        text = rationale(roots[0], 'SYSHA1')
        self.assertIn("policy of the standby hanodeA, from its managed system's defaults", text)

    def test_the_active_nodes_managed_system_does_not_size_the_standby(self):
        root = self.run_cycle(ON_ACTIVE_FRAME, ('SYSHA2', 'SYSHA1'))
        row = P.standby_row(root)
        self.assertNotIn('PowerHA standby for', row['NFIT - Action'])
        self.assertIn('Sizing: independent, each node on its own workload (policy of the standby hanodeA, from the built-in default)',
                      rationale(root, 'SYSHA1'))

    def test_an_unreadable_policy_value_stops_the_run(self):
        root = tempfile.mkdtemp(prefix='mgsys-')
        self.roots.append(root)
        _, last = H.build(root, True, '[GLOBAL]\nha_sizing = sometimes\n[MGSYS:SYSHA1]\nha_peer = hanodeB\n')
        run = subprocess.run(['perl', os.path.join(root, 'nfit-profile'), '--enddate', last, '--nmondir', os.path.join(root, 'stage', 'SYSHA1')],
                             capture_output=True, text=True, timeout=120, cwd=root)
        self.assertEqual(run.returncode, 2, run.stderr[-600:])
        self.assertIn("[GLOBAL]: ha_sizing must be one of independent, standby_share; 'sometimes' is not", run.stderr)
        self.assertIn("[MGSYS:SYSHA1]: 'ha_peer' is an LPAR setting", run.stderr)
        self.assertIn('no analysis was made', run.stderr)
        self.assertFalse([f for f in os.listdir(os.path.join(root, 'output')) if f.endswith('.csv')] if os.path.isdir(os.path.join(root, 'output')) else [])


if __name__ == '__main__':
    unittest.main()
