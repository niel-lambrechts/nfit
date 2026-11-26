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
#              any analysis, with every such setting listed;
#            - a fixed standby entitlement (v10 B13) inherited from the standby's
#              managed system is its entitlement in one step, on the VP count the
#              VP rules give this cycle; the own-workload requirement stays on
#              the row; no primary plan is awaited and finalisation leaves the
#              row as it is, in either order of analysis;
#            - a fixed entitlement that cannot carry this cycle's VPs changes
#              nothing and says why;
#            - a fixed policy with no entitlement for the standby stops the run.
# USAGE    : python3 test/mgsys_policy_test.py
# ==============================================================================
import csv
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
FIXED = '[GLOBAL]\nha_sizing = standby_share\n[MGSYS:SYSHA1]\nha_sizing = standby_fixed\nstandby_entitlement = %s\n'


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

    def test_a_fixed_standby_entitlement_is_set_in_one_step_and_is_final_at_analysis(self):
        # The standby is installed at 1.50 on 4 VPs; its own workload needs 0.80; the VP rules step it to 3 VPs.
        roots = [self.run_cycle(FIXED % '0.15', order) for order in (('SYSHA2', 'SYSHA1'), ('SYSHA1', 'SYSHA2'))]
        rows = [P.standby_row(root) for root in roots]
        self.assertEqual(rows[0], rows[1])
        row = rows[0]
        self.assertEqual((row['Current - ENT'], row['Current - VCPU'], row['NFIT - ENT'], row['NFIT - VCPU']), ('1.50', '4', '0.15', '3'))
        self.assertTrue(row['NFIT - Action'].startswith(
            "PowerHA standby for hanodeB (inferred): fixed standby entitlement 0.15 (its managed system's default). "
            'Its own workload needs 0.80, above the standby allocation.'), row['NFIT - Action'])
        self.assertIn('entitlement 1.50 to 0.15', row['NFIT - Action'])
        self.assertIn('VPs 4 to 3', row['NFIT - Action'])
        self.assertIn('Capacity at takeover is arranged outside nFit and is not verified.', row['NFIT - Action'])
        self.assertNotIn('Provisional', row['NFIT - Action'])
        self.assertIn('[STANDBY_FIXED]', row['VM_Comments'])
        for root in roots:
            member = P.load(F.outputs(root, 'SYSHA1')['plan'])['members']['hanodeA']
            self.assertNotIn('pendingRole', member)
            role = member['role']
            self.assertEqual((role['type'], role['fixedEntitlement'], role['source'], role['managedSystem'], role['entitlement'], role['VPs']),
                             ('haStandbyFixed', 0.15, 'mgsys', 'SYSHA1', 0.15, 3))
            self.assertEqual(role['ownRequirement'], {'entitlement': 0.8, 'VPs': 1})
            self.assertEqual((member['published']['entitlement'], str(member['published']['VPs'])), ('0.15', '3'))
            # The frame's totals count the fixed figure.
            with open(F.outputs(root, 'SYSHA1')['csv']) as fh:
                total = sum(float(r['NFIT - ENT']) for r in csv.DictReader(fh) if r['NFIT - ENT'])
            self.assertAlmostEqual(float(F.summary_row(root, 'SYSHA1')['CurrentCycle_ENT']), total, places=2)
        text = rationale(roots[0], 'SYSHA1')
        self.assertIn("Sizing: fixed standby entitlement 0.15 (its managed system's default)", text)
        self.assertIn('own workload requirement 0.80 on 1 VP, above', text)
        self.assertIn('does not report whether', text)

    def test_a_fixed_entitlement_that_cannot_carry_the_vps_changes_nothing(self):
        root = self.run_cycle(FIXED % '0.10', ('SYSHA2', 'SYSHA1'))
        row = P.standby_row(root)
        self.assertEqual((row['NFIT - ENT'], row['NFIT - VCPU']), (row['Current - ENT'], row['Current - VCPU']))
        self.assertIn('fixed standby entitlement 0.10 (its managed system\'s default) cannot be set this cycle: 0.10 carries at most 2 VPs '
                      '(0.05 processors per VP) and the VP count of this cycle is 3, which needs at least 0.15. '
                      'The installed 1.50 on 4 VPs is kept; no change.', row['NFIT - Action'])
        self.assertIn('Its own workload needs 0.80', row['NFIT - Action'])
        self.assertIn('[STANDBY_FIXED_CONFLICT]', row['VM_Comments'])
        role = P.load(F.outputs(root, 'SYSHA1')['plan'])['members']['hanodeA']['role']
        self.assertEqual((role['fixedEntitlement'], role['entitlement'], role['VPs'], role['status']), (0.10, 1.5, 4, 'HOLD'))
        self.assertIn('conflict', role)

    def test_a_fixed_policy_without_an_entitlement_for_the_standby_stops_the_run(self):
        # The file check accepts another managed system's entitlement for an LPAR stanza; the standby's own frame has none.
        root = tempfile.mkdtemp(prefix='mgsys-')
        self.roots.append(root)
        _, last = H.build(root, True, '[MGSYS:ELSEWHERE]\nstandby_entitlement = 0.20\n[hanodeA]\nha_sizing = standby_fixed\n')
        run = subprocess.run(['perl', os.path.join(root, 'nfit-profile'), '--runq-perc-behavior=match', '--enddate', last,
                              '--nmondir', os.path.join(root, 'stage', 'SYSHA1')], capture_output=True, text=True, timeout=300, cwd=root)
        self.assertEqual(run.returncode, 2, run.stderr[-600:])
        self.assertIn('ha_sizing = standby_fixed applies to the standby hanodeA on managed system SYSHA1 and no standby_entitlement is set for it', run.stderr)

    def test_an_unreadable_policy_value_stops_the_run(self):
        root = tempfile.mkdtemp(prefix='mgsys-')
        self.roots.append(root)
        _, last = H.build(root, True, '[GLOBAL]\nha_sizing = sometimes\n[MGSYS:SYSHA1]\nha_peer = hanodeB\n')
        run = subprocess.run(['perl', os.path.join(root, 'nfit-profile'), '--enddate', last, '--nmondir', os.path.join(root, 'stage', 'SYSHA1')],
                             capture_output=True, text=True, timeout=120, cwd=root)
        self.assertEqual(run.returncode, 2, run.stderr[-600:])
        self.assertIn("[GLOBAL]: ha_sizing must be one of independent, standby_share, standby_fixed; 'sometimes' is not", run.stderr)
        self.assertIn("[MGSYS:SYSHA1]: 'ha_peer' is an LPAR setting", run.stderr)
        self.assertIn('no analysis was made', run.stderr)
        self.assertFalse([f for f in os.listdir(os.path.join(root, 'output')) if f.endswith('.csv')] if os.path.isdir(os.path.join(root, 'output')) else [])


if __name__ == '__main__':
    unittest.main()
