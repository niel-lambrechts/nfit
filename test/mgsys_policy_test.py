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
#              managed system is set together with the VPs the node's own
#              workload needs, in one change: the VP floor that preserves the
#              installed entitlement and the per-cycle step cap are not needs
#              for VPs; the own-workload requirement stays on the row; no
#              primary plan is awaited and finalisation leaves the row as it
#              is, in either order of analysis; the plan keeps the trace;
#            - an ordinary LPAR of the same frame is sized as without it;
#            - a fixed policy with no entitlement for the standby stops the run.
# USAGE    : python3 test/mgsys_policy_test.py
# ==============================================================================
import csv
import re
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

    def test_a_fixed_standby_entitlement_is_set_with_the_workloads_width_and_is_final_at_analysis(self):
        # The standby is installed at 1.50 on 4 VPs; its own workload needs about 0.80 on 1 VP; reduction safety is CLEAR.
        # Ordinary sizing would step the VPs 4 to 3 (the tier's cap, towards the VPs that preserve 1.50). Neither is a
        # need for VPs: the fixed 0.10 is set on the 1 VP the workload needs, in one coordinated change.
        roots = [self.run_cycle(FIXED % '0.10', order) for order in (('SYSHA2', 'SYSHA1'), ('SYSHA1', 'SYSHA2'))]
        rows = [P.standby_row(root) for root in roots]
        self.assertEqual(rows[0], rows[1])
        row = rows[0]
        self.assertEqual((row['Current - ENT'], row['Current - VCPU'], row['NFIT - ENT'], row['NFIT - VCPU']), ('1.50', '4', '0.10', '1'))
        # The own-workload figure lies on a rounding boundary (0.795): 0.80 on some platforms, 0.79 on others. It is
        # taken from the row and checked against the node's planning-profile column and the saved plan, not fixed here.
        said = re.match(r"PowerHA standby for hanodeB \(inferred\): fixed standby entitlement 0\.10 \(its managed system's default\)\. "
                        r'Its own workload needs (0\.\d\d), above the standby allocation\. '
                        r'The workload supports fewer VPs; the previous limit came from preserving installed entitlement\. '
                        r'Entitlement and VPs are assessed together\.', row['NFIT - Action'])
        self.assertTrue(said, row['NFIT - Action'])
        own = said.group(1)
        self.assertIn(own, ('0.79', '0.80'))
        self.assertEqual(row['G2-98W10'], own)                                       # the planning profile of a G2 node
        self.assertIn('entitlement 1.50 to 0.10', row['NFIT - Action'])
        self.assertIn('VPs 4 to 1', row['NFIT - Action'])
        self.assertIn('Capacity at takeover is arranged outside nFit and is not verified.', row['NFIT - Action'])
        self.assertNotIn('Provisional', row['NFIT - Action'])
        self.assertIn('[STANDBY_FIXED]', row['VM_Comments'])
        for root in roots:
            member = P.load(F.outputs(root, 'SYSHA1')['plan'])['members']['hanodeA']
            self.assertNotIn('pendingRole', member)
            role, policy = member['role'], member['standbyPolicy']
            self.assertEqual((role['type'], role['fixedEntitlement'], role['source'], role['managedSystem'], role['entitlement'], role['VPs']),
                             ('haStandbyFixed', 0.10, 'mgsys', 'SYSHA1', 0.10, 1))
            self.assertEqual((policy['applied'], policy['transition'], policy['fixedEntitlement']), (True, True, 0.10))
            self.assertEqual(policy['ownRequirement'], {'entitlement': float(own), 'VPs': 1})
            trace = policy['trace']
            # What held the VPs in ordinary sizing was the installed entitlement and the step cap, not the workload.
            self.assertEqual((trace['strategicVPs'], trace['widthVPs'], trace['entitlementPreservingVPs'], trace['vpStep'], trace['safetyState'], trace['carriesVPs']),
                             (1, 1, 2, 3, 'CLEAR', 2))
            self.assertIn('current entitlement floor', trace['entitlementPreservingGovernedBy'])
            self.assertEqual((member['published']['entitlement'], str(member['published']['VPs'])), ('0.10', '1'))
            # The frame's totals count the fixed figure.
            with open(F.outputs(root, 'SYSHA1')['csv']) as fh:
                total = sum(float(r['NFIT - ENT']) for r in csv.DictReader(fh) if r['NFIT - ENT'])
            self.assertAlmostEqual(float(F.summary_row(root, 'SYSHA1')['CurrentCycle_ENT']), total, places=2)
            # The full figure of the frame is the fixed figure itself, not one raised to a VP minimum.
            self.assertAlmostEqual(float(F.summary_row(root, 'SYSHA1')['Target_ENT']), total, places=2)
        text = ' '.join(rationale(roots[0], 'SYSHA1').split())
        self.assertIn("Sizing: fixed standby entitlement 0.10 (its managed system's default)", text)
        self.assertIn('own workload requirement %s on 1 VP, above' % own, text)
        self.assertIn('the workload supports fewer VPs than the step that preserved the installed entitlement', text)
        self.assertIn('does not report whether', text)

    def test_the_other_lpar_of_the_frame_is_sized_as_without_the_setting(self):
        # app1 is an ordinary LPAR on the standby's frame: the managed-system stanza does not touch it.
        with_fixed = self.run_cycle(FIXED % '0.10', ('SYSHA2', 'SYSHA1'))
        without = self.run_cycle(P.SHARE, ('SYSHA2', 'SYSHA1'))
        rows = []
        for root in (with_fixed, without):
            with open(F.outputs(root, 'SYSHA1')['csv']) as fh:
                rows.append(next(r for r in csv.DictReader(fh) if r['VM'] == 'app1'))
        self.assertEqual(rows[0], rows[1])
        self.assertNotIn('standbyPolicy', P.load(F.outputs(with_fixed, 'SYSHA1')['plan'])['members']['app1'])

    def test_an_inferred_standby_that_carries_sustained_workload_keeps_its_ordinary_sizing(self):
        # An unequal active-active pair: the larger node leads throughout, so the role evidence reads the smaller one
        # as standby; it runs at 1.3 cores all day. The fixed allocation is withheld and the node is sized as without it.
        def working(vm, t):
            if vm == 'app1':
                return 0.8
            return 3.0 if ((t < H.SWITCH) == (vm == 'hanodeA')) else 1.3
        saved = H.level
        H.level = working
        try:
            fixed = [self.run_cycle(FIXED % '0.10', order) for order in (('SYSHA2', 'SYSHA1'), ('SYSHA1', 'SYSHA2'))]
            plain = self.run_cycle('[GLOBAL]\nha_sizing = independent\n', ('SYSHA2', 'SYSHA1'))
        finally:
            H.level = saved
        rows = [P.standby_row(root) for root in fixed]
        self.assertEqual(rows[0], rows[1])
        row, ordinary = rows[0], P.standby_row(plain)
        self.assertEqual((row['NFIT - ENT'], row['NFIT - VCPU']), (ordinary['NFIT - ENT'], ordinary['NFIT - VCPU']))
        self.assertGreater(float(row['NFIT - ENT']), 1.0)
        self.assertTrue(row['NFIT - Action'].startswith(ordinary['NFIT - Action']), row['NFIT - Action'])
        said = row['NFIT - Action'][len(ordinary['NFIT - Action']):].strip()
        self.assertTrue(said.startswith("Sustained workload observed: idle-standby allocation not applied. Recommendation follows this node's workload. "
                                        "Not applied: fixed standby entitlement 0.10 (its managed system's default). "
                                        'Evidence: PhysC at 0.20 cores or more (15-minute medians) for 2 hours or longer without a break on '), said)
        self.assertRegex(said, r'on \d+ days; 2 needed; longest run [\d.]+ hours; all hours, its current role period from 13 Sep 2026; PhysB not collected\. '
                               r'Sustained workload is observed on both nodes of this cluster in this period: neither is sized as an idle standby\. '
                               r'Which node owns the resource groups is not verified\.$')
        self.assertIn('[STANDBY_FIXED_NOT_APPLIED]', row['VM_Comments'])
        self.assertNotIn('[STANDBY_FIXED]', row['VM_Comments'])
        member = P.load(F.outputs(fixed[0], 'SYSHA1')['plan'])['members']['hanodeA']
        self.assertNotEqual((member.get('role') or {}).get('type'), 'haStandbyFixed')
        policy = member['standbyPolicy']
        self.assertEqual((policy['applied'], policy['notApplied']['kind'], policy['roleSource']), (False, 'workload', 'inferred'))
        work = policy['sustainedWorkload']
        self.assertEqual((work['metric'], work['cores'], work['hours'], work['daysNeeded'], work['observed'], work['assessed'], work['bothNodes'],
                          work['physB'], work['profileHours'], work['period']), ('PhysC', 0.2, 2, 2, True, True, True, None, 'none', 'role'))
        self.assertGreaterEqual(work['days'], 7)
        self.assertIn('sustained workload observed', ' '.join(rationale(fixed[0], 'SYSHA1').split()))
        # Every node's record carries its own finding; the inferred role is left as it is (sizing eligibility only).
        proof = member['roleEvidence']
        self.assertEqual((proof['role']['inferred'], proof['role']['effective'], proof['ownWorkload']['observed'], proof['ownWorkload']['bothNodes']),
                         ('standby', 'standby', True, True))
        self.assertNotIn('busyShare', proof['ownWorkload'])
        self.assertNotIn('candidate', proof['latestPeriod'])
        partner = P.load(F.outputs(fixed[0], 'SYSHA2')['plan'])['members']['hanodeB']['roleEvidence']
        self.assertEqual((partner['role']['effective'], partner['ownWorkload']['observed']), ('active', True))

    def test_a_small_node_beside_a_large_one_is_judged_on_its_own_workload(self):
        # 0.30 cores all day beside 3.0: a tenth of the pair's consumption, and sustained workload all the same.
        def working(vm, t):
            if vm == 'app1':
                return 0.8
            return 3.0 if ((t < H.SWITCH) == (vm == 'hanodeA')) else 0.30
        saved = H.level
        H.level = working
        try:
            root = self.run_cycle(FIXED % '0.10', ('SYSHA2', 'SYSHA1'))
        finally:
            H.level = saved
        row = P.standby_row(root)
        self.assertIn('Sustained workload observed: idle-standby allocation not applied.', row['NFIT - Action'])
        self.assertIn('[STANDBY_FIXED_NOT_APPLIED]', row['VM_Comments'])
        self.assertGreater(float(row['NFIT - ENT']), 0.25)                           # its own workload, not 0.10

    def test_the_finding_does_not_need_the_partners_data(self):
        # The partner's frame is not staged here: no role can be inferred, and the node's own finding is still made.
        def working(vm, t):
            return 0.8 if vm == 'app1' else 1.3
        saved = H.level
        H.level = working
        try:
            root = tempfile.mkdtemp(prefix='nfit-mgsys-')
            self.addCleanup(shutil.rmtree, root, True)
            _, last = H.build(root, True, FIXED % '0.10')
        finally:
            H.level = saved
        shutil.rmtree(os.path.join(root, 'stage', 'SYSHA2'))
        run = P.analyse(root, 'SYSHA1', last)
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        member = P.load(F.outputs(root, 'SYSHA1')['plan'])['members']['hanodeA']
        proof = member['roleEvidence']
        self.assertEqual((proof['role']['effective'], proof['reason']), ('unknown', 'no samples for hanodeB in this period'))
        self.assertEqual((proof['ownWorkload']['observed'], proof['ownWorkload']['period'], proof['ownWorkload']['bothNodes']), (True, 'whole', False))
        self.assertNotIn('standbyPolicy', member)                                     # no standby role: no standby allocation
        self.assertNotIn('STANDBY_FIXED', P.standby_row(root)['VM_Comments'])

    def test_a_configured_standby_keeps_the_customers_policy_and_the_mismatch_is_reported(self):
        # ha_role = standby is the customer's statement: the fixed figure is applied where it can be set, and the
        # sustained workload on the node is reported as a mismatch. The settings of the test are set for the node.
        def working(vm, t):
            if vm == 'app1':
                return 0.8
            return 0.30 if vm == 'hanodeA' else 3.0                                 # no handover: the roles hold throughout
        saved = H.level
        H.level = working
        try:
            root = self.run_cycle('[hanodeA]\nha_role = standby\nstandby_work_cores = 0.25\nstandby_work_hours = 3\nstandby_work_days = 5\n' + FIXED % '0.10',
                                  ('SYSHA2', 'SYSHA1'))
        finally:
            H.level = saved
        row = P.standby_row(root)
        member = P.load(F.outputs(root, 'SYSHA1')['plan'])['members']['hanodeA']
        policy = member['standbyPolicy']
        self.assertEqual((policy['roleSource'], policy['applied'], policy.get('workloadMismatch')), ('configured', True, True))
        self.assertEqual((policy['sustainedWorkload']['cores'], policy['sustainedWorkload']['hours'], policy['sustainedWorkload']['daysNeeded'],
                          policy['sustainedWorkload']['source']), (0.25, 3, 5, {'cores': 'lpar', 'hours': 'lpar', 'days': 'lpar'}))
        self.assertEqual(row['NFIT - ENT'], '0.10')
        self.assertIn('[STANDBY_FIXED]', row['VM_Comments'])
        self.assertIn('[STANDBY_WORKLOAD_MISMATCH]', row['VM_Comments'])
        self.assertIn('This node is configured as the standby and sustained workload is observed on it (PhysC at 0.25 cores or more (15-minute medians) '
                      'for 3 hours or longer without a break on ', row['NFIT - Action'])
        self.assertIn('the fixed standby entitlement is applied as configured; check the role.', row['NFIT - Action'])

    def test_a_configured_active_node_that_works_is_not_called_quiet(self):
        # Both nodes configured active; the comparison still reads the smaller one as the lesser of the two. The note
        # says what the evidence shows (it works) and does not call it a quiet node.
        def working(vm, t):
            if vm == 'app1':
                return 0.8
            return 3.0 if ((t < H.SWITCH) == (vm == 'hanodeA')) else 1.3
        saved = H.level
        H.level = working
        try:
            root = self.run_cycle('ha_role = active\n[hanodeA]\nha_role = active\n' + FIXED % '0.10', ('SYSHA2', 'SYSHA1'))
        finally:
            H.level = saved
        member = P.load(F.outputs(root, 'SYSHA1')['plan'])['members']['hanodeA']
        role = member['roleEvidence']['role']
        self.assertEqual((role['configured'], role['inferred'], role['effective'], role['source']), ('active', 'standby', 'active', 'configured'))
        self.assertTrue(role['note'].startswith('its partner leads in the comparison of the two nodes, and sustained workload is observed on this node '
                                                '(PhysC at 0.20 cores or more (15-minute medians) for 2 hours or longer without a break on '), role['note'])
        self.assertTrue(role['note'].endswith('consistent with both nodes active'), role['note'])
        self.assertNotIn('quiet', role['note'])
        self.assertNotIn('standbyPolicy', member)                                     # an active node is not a standby: no fixed figure
        self.assertNotIn('[STANDBY_FIXED', P.standby_row(root)['VM_Comments'])

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
