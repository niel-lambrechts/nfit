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
        self.assertRegex(report, r'\[ok\] frame totals: block installed 3\.00, current cycle (\S+), target \S+; CSV installed 3\.00, current cycle \1')
        work = re.search(r'Work tree kept \(customer data\): (\S+)', run.stderr).group(1)
        before = tree_hash(work)
        again = subprocess.run([sys.executable, os.path.join(self.repo, 'dev', TOOL), '--read', work], capture_output=True, text=True, env=self.env)
        self.assertEqual(again.returncode, 0, again.stderr[-600:])
        self.assertIn('read from the kept results (model state-based; nothing run)', again.stdout)
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
