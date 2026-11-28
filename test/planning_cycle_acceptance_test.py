#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/planning_cycle_acceptance_test.py
# PURPOSE  : dev/planning-cycle-acceptance on the two-frame PowerHA fixture
#            (standby hanodeA on SYSHA1, primary hanodeB on SYSHA2), with a
#            redaction secret of its own.
#            - the two frames are found by pseudonym, analysed into one cycle
#              and finalised; with --both-orders both orders agree;
#            - the report names no frame and no LPAR, only pseudonyms;
#            - the capture the frames were cloned from is not written to;
#            - --read gives the role and pool checks again from a kept work tree,
#              running nothing and changing nothing;
#            - an unknown pseudonym stops the tool before anything is run, and so
#              does a run that does not say where the captures are.
# USAGE    : python3 test/planning_cycle_acceptance_test.py
# ==============================================================================
import hashlib
import os
import re
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

TOOL = 'planning-cycle-acceptance'


def tree_hash(root):
    digest = hashlib.sha256()
    for folder, _, files in sorted(os.walk(root)):
        for name in sorted(files):
            path = os.path.join(folder, name)
            digest.update(os.path.relpath(path, root).encode())
            with open(path, 'rb') as fh:
                digest.update(fh.read())
    return digest.hexdigest()


@unittest.skipUnless(H.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class PlanningCycleAcceptanceTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # A stand-in repository (programs, etc/, dev tools) and a capture beside it.
        cls.root = tempfile.mkdtemp(prefix='cycle-accept-')
        cls.repo = os.path.join(cls.root, 'repo')
        _, cls.last = H.build(cls.repo, True, P.SHARE)
        cls.capture = os.path.join(cls.root, 'captures', 'one')
        os.makedirs(cls.capture)
        shutil.move(os.path.join(cls.repo, 'stage'), os.path.join(cls.capture, 'stage'))
        os.makedirs(os.path.join(cls.repo, 'dev'))
        for tool in (TOOL, 'nfit-redact'):
            shutil.copy(os.path.join(H.REPO, 'dev', tool), os.path.join(cls.repo, 'dev', tool))
        secret = os.path.join(cls.root, 'secret')
        with open(secret, 'wb') as fh:
            fh.write(b'acceptance-test-secret-0123456789abcdef')
        os.chmod(secret, 0o600)
        cls.env = dict(os.environ, NFIT_REDACT_SECRET_FILE=secret)
        sys.path.insert(0, os.path.join(cls.repo, 'dev'))
        os.environ['NFIT_REDACT_SECRET_FILE'] = secret
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader('acceptance_tool', os.path.join(cls.repo, 'dev', TOOL))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        name_of = module.namer()
        cls.standby, cls.primary = name_of('SYSHA1'), name_of('SYSHA2')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def tool(self, *args):
        return subprocess.run([sys.executable, os.path.join(self.repo, 'dev', TOOL), '--captures', os.path.join(self.root, 'captures')] + list(args),
                              capture_output=True, text=True, timeout=400, env=self.env)

    def test_two_frames_both_orders(self):
        before = tree_hash(self.capture)
        run = self.tool(self.standby, self.primary, '--both-orders', '--enddate', self.last)
        self.assertEqual(run.returncode, 0, run.stdout[-2500:] + run.stderr[-800:])
        report = run.stdout
        self.assertIn('model hybrid-state-decay', report)
        self.assertIn('Result: all checks passed', report)
        self.assertNotIn('FAIL', report)
        self.assertRegex(report, r'\[ok\] standbys whose primary is on the other frame: 1')
        self.assertRegex(report, r'\[ok\] vm_[0-9a-f]{6} on %s, primary vm_[0-9a-f]{6}: \S+ / \d+ provisional, now \S+ / \d+; set from the primary\'s analysis'
                         % self.standby)
        self.assertIn('[ok] second finalisation: no output changed (6 files compared)', report)
        self.assertIn('[ok] both orders give the same final entitlement, VPs and action for 1 standby', report)
        self.assertRegex(report, r'Planning cycle \S+: finalised')
        self.assertRegex(report, r'Cycle on disk: \d+ KB in \d+ files')
        for name in ('SYSHA1', 'SYSHA2', 'hanodeA', 'hanodeB'):
            self.assertNotIn(name, report)
            self.assertNotIn(name, run.stderr)
        self.assertEqual(tree_hash(self.capture), before)                              # the capture is not written to

    def test_a_fixed_standby_entitlement_for_the_run_is_checked_in_both_orders(self):
        # The standby's frame is given a fixed standby entitlement in the work tree's copy of the configuration only.
        with open(os.path.join(self.repo, 'etc', 'nfit.vms.cfg')) as fh:
            config = fh.read()
        run = self.tool(self.standby, self.primary, '--both-orders', '--enddate', self.last, '--standby-fixed', self.standby + '=0.15')
        self.assertEqual(run.returncode, 0, run.stdout[-3000:] + run.stderr[-800:])
        report = run.stdout
        self.assertIn('Result: all checks passed', report)
        self.assertNotIn('FAIL', report)
        self.assertIn('Fixed standby entitlement 0.15 for frame %s, in the work tree copy of nfit.vms.cfg only.' % self.standby, report)
        self.assertRegex(report, r'\[ok\] frame %s: standbys under the fixed standby entitlement 0\.15 asked for this run: 1 \(applied to 1, not applied to 0\)' % self.standby)
        self.assertRegex(report, r'Fixed standby policy, vm_[0-9a-f]{6} on %s \(primary vm_[0-9a-f]{6}, role inferred\): 0\.15 from the managed-system stanza; installed 1\.50 / 4, own workload needs \S+ on 2 VPs' % self.standby)
        self.assertRegex(report, r'VP requirements: strategic 2 \(run-queue width \S+, peak-throughput floor \S+, configured-burst floor \S+\); VPs that preserve the installed entitlement 2 ')
        self.assertRegex(report, r'reduction safety CLEAR \(.*\); 0\.15 carries at most 3 VPs')
        self.assertRegex(report, r'Guarantee check of the strategic pair: Yes \(.+\)')
        self.assertRegex(report, r'Sustained workload of vm_[0-9a-f]{6}: OBSERVED: PhysC at 0\.20 cores or more .* on \d+ day\(s\), 2 needed')      # the active node
        self.assertRegex(report, r'Comparison of the two nodes \(\S+ hours\): ACTIVE:vm_[0-9a-f]{6}; \d+ of \d+ blocks separated')
        self.assertRegex(report, r'Sustained workload: not observed: PhysC at 0\.20 cores or more \(median of the minute values of a 15-minute block\) for 2 hours or longer '
                                 r'on 0 day\(s\), 2 needed; longest run 0 hours; all hours, the current role period from \d\d \w+ 2026; PhysB not collected')
        self.assertIn('Sizing decision: fixed standby entitlement applied', report)
        self.assertRegex(report, r'\[ok\] vm_[0-9a-f]{6}: set to the fixed 0\.15 on 2 VPs \(installed 1\.50 / 4\)')
        self.assertRegex(report, r'\[ok\] vm_[0-9a-f]{6}: the VPs are no fewer than its workload needs \(2\) and no more than the fixed figure carries')
        self.assertRegex(report, r'\[ok\] vm_[0-9a-f]{6}: final at analysis, not pending in the cycle')
        # The active node of the pair is on the other frame; a PowerHA node the policy did not reach is listed with its role.
        self.assertRegex(report, r'PowerHA nodes of sys_[0-9a-f]{6} outside the fixed standby policy \(1\):\n\s+vm_[0-9a-f]{6} \(cluster with vm_[0-9a-f]{6}\): role active \(inferred; configured auto, inferred active\)')
        self.assertRegex(report, r'\[ok\] vm_[0-9a-f]{6}: finalisation left the fixed standby\'s entitlement, VPs and action unchanged')
        self.assertIn('[ok] both orders give the same final entitlement, VPs and action for 1 standby', report)
        for name in ('SYSHA1', 'SYSHA2', 'hanodeA', 'hanodeB'):
            self.assertNotIn(name, report)
        with open(os.path.join(self.repo, 'etc', 'nfit.vms.cfg')) as fh:
            self.assertEqual(fh.read(), config)                                         # the repository's configuration is not changed

    def test_a_fixed_figure_goes_with_the_vps_the_workload_needs(self):
        # 0.10 carries two VPs and the standby's workload needs two: set at once, past the step of 4 to 3.
        run = self.tool(self.standby, self.primary, '--enddate', self.last, '--standby-fixed', self.standby + '=0.10', '--keep', '--work', self.root)
        self.assertEqual(run.returncode, 0, run.stdout[-3000:] + run.stderr[-800:])
        self.assertRegex(run.stdout, r'\[ok\] vm_[0-9a-f]{6}: set to the fixed 0\.10 on 2 VPs \(installed 1\.50 / 4\)')
        # The kept results give the same lines again without a run, and the PowerHA nodes the policy did not reach.
        work = re.search(r'Work tree kept \(customer data\): (\S+)', run.stderr).group(1)
        again = subprocess.run([sys.executable, os.path.join(self.repo, 'dev', TOOL), '--read', work], capture_output=True, text=True, env=self.env)
        self.assertEqual(again.returncode, 0, again.stderr[-600:])
        self.assertIn('  Fixed standby entitlement (saved plans of both frames):', again.stdout)
        self.assertRegex(again.stdout, r'\[ok\] vm_[0-9a-f]{6}: set to the fixed 0\.10 on 2 VPs \(installed 1\.50 / 4\)')
        self.assertRegex(again.stdout, r'PowerHA nodes of %s outside the fixed standby policy \(1\):\n\s+vm_[0-9a-f]{6} \(cluster with vm_[0-9a-f]{6}\): role active \(inferred; configured auto, inferred active\)' % self.primary)
        self.assertIn('Result: all checks passed', again.stdout)
        self.assertIn('The workload supports fewer VPs; the previous limit came from preserving installed entitlement.', run.stdout)
        self.assertIn('Result: all checks passed', run.stdout)

    def test_a_fixed_figure_that_cannot_carry_the_workloads_vps_is_reported_as_not_applied(self):
        # 0.05 carries one VP and the workload needs two: not applied, and the node keeps its ordinary recommendation.
        # The same run asks for independent VIOS sizing on both frames (one stanza carries both settings on the standby's frame).
        run = self.tool(self.standby, self.primary, '--enddate', self.last, '--standby-fixed', self.standby + '=0.05',
                        '--vios-sizing', self.standby + '=independent', '--vios-sizing', self.primary + '=independent')
        self.assertEqual(run.returncode, 0, run.stdout[-3000:] + run.stderr[-800:])
        self.assertRegex(run.stdout, r'vios_sizing = independent for frame %s, in the work tree copy of nfit\.vms\.cfg only\.' % self.primary)
        self.assertRegex(run.stdout, r'VIOS sizing \(saved plans, final; CSV\):\s+Frame sys_[0-9a-f]{6}: no VIOS in the saved plan\.')
        self.assertRegex(run.stdout, r'\[ok\] frame %s: standbys under the fixed standby entitlement 0\.05 asked for this run: 1 \(applied to 0, not applied to 1\)' % self.standby)
        self.assertIn("Sizing decision: fixed standby entitlement not applied (width); the recommendation follows this node's workload", run.stdout)
        self.assertRegex(run.stdout, r'\[ok\] vm_[0-9a-f]{6}: fixed figure not applied \(width: its own workload needs 2 VPs and 0\.05 carries at most 1 '
                                     r'\(0\.05 processors per VP\)\); the recommendation \S+ on \d VPs? follows its workload \(installed 1\.50 / 4\)')
        self.assertIn('Result: all checks passed', run.stdout)

    def test_a_frame_outside_the_run_is_refused_for_a_fixed_entitlement(self):
        run = self.tool(self.standby, self.primary, '--standby-fixed', 'sys_000000=0.10')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('--standby-fixed takes one of the two frames', run.stderr)
        run = self.tool(self.standby, self.primary, '--vios-sizing', self.primary + '=largest')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('--vios-sizing takes one of the two frames and a policy', run.stderr)

    def test_the_saved_results_are_read_again_without_a_run(self):
        run = self.tool(self.standby, self.primary, '--enddate', self.last, '--model', 'state-based', '--keep', '--work', self.root)
        self.assertEqual(run.returncode, 0, run.stdout[-2500:] + run.stderr[-800:])
        report = run.stdout
        self.assertRegex(report, r"\[ok\] its floor is 25%% of 1\.5: the held target of vm_[0-9a-f]{6} on frame %s" % self.primary)
        self.assertRegex(report, r'\[ok\] both frames read the cluster alike: vm_[0-9a-f]{6} standby, vm_[0-9a-f]{6} active')
        self.assertIn('handovers seen: 1 sustained, 0 short', report)
        self.assertRegex(report, r'Latest role period: from \d\d \w{3} 2026 to the end of the evidence \(\d+ days\)')
        self.assertRegex(report, r'Sizing of vm_[0-9a-f]{6}: workload of 2026-09-01 to %s \(state-based\); \d+ days of it before the latest handover' % self.last)
        self.assertRegex(report, r'Handover treatment: vm_[0-9a-f]{6} reductions held since')
        self.assertIn('[ok] action and comments of every CSV row are those of the saved plan (2 of 2 rows)', report)
        self.assertRegex(report, r'Frame %s: 2 LPARs with a configuration state on %s, installed entitlement 3\.00; 0 whose last state ended earlier, installed entitlement 0\.00' % (self.standby, self.last))
        self.assertIn('Shared capacity reported by the default-pool LPARs still there: 32 by 2', report)
        self.assertRegex(report, r'Days with samples: (2 LPARs sampled on %s, installed entitlement 3\.00; 0 last sampled earlier, installed entitlement 0\.00|no sample store in the work tree; not checked\.)' % self.last)
        self.assertRegex(report, r'\[ok\] frame totals: block installed 3\.00, current cycle (\S+), target \S+; CSV rows counted \(2\) installed 3\.00, current cycle \1')
        work = re.search(r'Work tree kept \(customer data\): (\S+)', run.stderr).group(1)
        before = tree_hash(work)
        again = subprocess.run([sys.executable, os.path.join(self.repo, 'dev', TOOL), '--read', work], capture_output=True, text=True, env=self.env)
        self.assertEqual(again.returncode, 0, again.stderr[-600:])
        self.assertIn('read from the kept results (model state-based; nothing run)', again.stdout)
        self.assertIn('No standby under a fixed standby entitlement in these results.', again.stdout)
        self.assertIn(report[report.index('  Where each floor'):report.index('  [ok] second finalisation')], again.stdout)
        self.assertEqual(tree_hash(work), before)
        for name in ('SYSHA1', 'SYSHA2', 'hanodeA', 'hanodeB'):
            self.assertNotIn(name, again.stdout)

    def test_an_unknown_frame_stops_the_tool(self):
        run = self.tool('sys_000000', self.primary)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('sys_000000: no staged capture found', run.stderr)
        run = self.tool('SYSHA1', self.primary)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('Expected two sys_ pseudonyms', run.stderr)

    def test_no_capture_location_is_built_in(self):
        run = subprocess.run([sys.executable, os.path.join(self.repo, 'dev', TOOL), self.standby, self.primary],
                             capture_output=True, text=True, env={k: v for k, v in self.env.items() if k != 'NFIT_CAPTURES'})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('Say where the staged captures are', run.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=1)
