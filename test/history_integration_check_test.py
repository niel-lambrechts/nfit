#!/usr/bin/env python3
# ==============================================================================
# Test for dev/history-integration-check on the synthetic system of
# member_exposure_test: history written twice, the month sized from the
# samples, both readers run; the check must pass and name no LPAR.
# ==============================================================================
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import member_exposure_test as M


@unittest.skipUnless(M.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class HistoryIntegrationCheckTests(unittest.TestCase):

    def test_the_check_passes_on_the_synthetic_system_and_reports_aggregates_only(self):
        fixture = M.MemberExposureTests
        fixture.roots = []
        root = fixture.make_root()
        self.addCleanup(fixture.tearDownClass)
        scratch = tempfile.mkdtemp(prefix='hist-check-')
        self.addCleanup(lambda: __import__('shutil').rmtree(scratch, ignore_errors=True))
        run = subprocess.run([sys.executable, os.path.join(REPO, 'dev', 'history-integration-check'),
                              '--stage', os.path.join(root, 'stage', M.SERIAL), '--out', os.path.join(scratch, 'out'),
                              '--work', os.path.join(scratch, 'work')], capture_output=True, text=True, timeout=160)
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-1500:])
        with open(os.path.join(scratch, 'out', 'integration-stage.json')) as fh:
            text = fh.read()
        result = json.loads(text)
        self.assertEqual((result['verdict'], result['failures']), ('pass', []))
        self.assertEqual(result['secondRun'], {'monthFiles': 1, 'unchanged': 1, 'changed': 0, 'added': 0})
        raw = result['rawAgainstHistory']
        self.assertGreater(raw['comparisons'], 0)
        self.assertTrue(all(v['different'] == 0 and v['equal'] > 0 for v in raw['supportCounts'].values()))
        self.assertGreater(raw['cutProtectionBound']['compared'], 0)
        self.assertGreater(raw['contestedLevel']['compared'], 0)
        self.assertEqual(result['readerParity']['different'] if 'different' in result['readerParity'] else 0, 0)
        self.assertEqual(result['readerView']['byStatus'], {'complete': 4, 'partial': 5})   # minutes without a pool verdict or PhysB stay visible
        for name in ('vmA', 'vmB', 'vmS', 'vmX', M.SERIAL):
            self.assertNotIn(name, text)
        self.assertFalse(os.path.exists(os.path.join(scratch, 'work')))                     # the work tree is removed


if __name__ == '__main__':
    unittest.main(verbosity=2)
