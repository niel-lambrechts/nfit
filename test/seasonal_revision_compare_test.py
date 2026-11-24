#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/seasonal_revision_compare_test.py
# PURPOSE  : dev/seasonal-revision-compare on a kept work tree: the same event
#            is sized with nfit and nfit-profile of an earlier revision and with
#            the current files, on inputs it restores and fingerprints before
#            each run. It reports aggregates and pseudonyms only, says that the
#            inputs were identical, and leaves the kept history and the stage's
#            proposal store as it found them.
# USAGE    : python3 test/seasonal_revision_compare_test.py
# ==============================================================================
import json
import os
import shutil
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
class SeasonalRevisionCompareTests(unittest.TestCase):

    def test_two_revisions_are_compared_on_identical_inputs_and_the_kept_tree_is_untouched(self):
        fixture = M.MemberExposureTests
        fixture.roots = []
        root = fixture.make_root()
        self.addCleanup(fixture.tearDownClass)
        scratch = tempfile.mkdtemp(prefix='revision-compare-')
        self.addCleanup(lambda: shutil.rmtree(scratch, ignore_errors=True))
        work, out = os.path.join(scratch, 'work'), os.path.join(scratch, 'out')
        keep = subprocess.run([sys.executable, os.path.join(REPO, 'dev', 'history-integration-check'), '--stage',
                               os.path.join(root, 'stage', M.SERIAL), '--out', out, '--work', work, '--keep'],
                              capture_output=True, text=True, timeout=160)
        self.assertEqual(keep.returncode, 0, (keep.stdout + keep.stderr)[-1500:])
        history = os.path.join(work, 'case', 'history', M.SERIAL, 'nfit.hist.%s.json' % M.MONTH)
        with open(history, 'rb') as fh:
            kept = fh.read()
        cfg = os.path.join(scratch, 'seasonality.cfg')
        with open(cfg, 'w') as fh:
            fh.write('[Global]\nmin_history_days = 1\nseasonal_lookback_days = 1\n'
                     '[ev]\nmodel = predictive_peak\npriority = 10\ndates = 2026-09-01:2026-09-01\nbaseline_period_days = 1\nmin_historical_peaks = 1\n')
        stored = os.path.join(work, 'case', 'stage', M.SERIAL, '.nfit.store', 'proposals')
        listing = lambda: sorted(os.path.relpath(os.path.join(d, f), stored) for d, _s, files in os.walk(stored) for f in files)
        proposals = listing()
        tool = [sys.executable, os.path.join(REPO, 'dev', 'seasonal-revision-compare'), '--work', work, '--out', out, '--seasonality', cfg]
        self.assertEqual(subprocess.run(tool + ['--event', 'ev'], capture_output=True, text=True, timeout=60).returncode, 2)   # no --base
        run = subprocess.run(tool + ['--event', 'ev', '--base', 'HEAD', '--flag=--research-engine-seasonal-input'],
                             capture_output=True, text=True, timeout=160)
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-1500:])
        with open(os.path.join(out, 'seasonal-revision-ev-research_engine_seasonal_input.json')) as fh:
            text = fh.read()
        result = json.loads(text)
        self.assertTrue(result['allInputsIdentical'], result['inputsIdentical'])
        self.assertEqual(sorted(result['inputsIdentical']), ['configuration', 'engineCaches', 'history', 'proposalStore', 'settings', 'stagedData'])
        self.assertGreaterEqual(result['lpars'], 3)
        self.assertEqual(sum(c['lpars'] for c in result['byFirstDifferingGroup'].values()), result['lpars'])
        self.assertAlmostEqual(result['totals']['publishedHead'] - result['totals']['publishedBase'], result['totals']['publishedChange'], places=2)
        # The engine's measurements are the same in both runs: no LPAR is filed under them.
        self.assertNotIn('engine measurements', result['byFirstDifferingGroup'])
        self.assertIn('Inputs identical for both runs: stagedData yes, proposalStore yes, engineCaches yes, history yes, configuration yes, settings yes', run.stdout)
        for name in ('vmA', 'vmB', 'vmS', 'vmX', M.SERIAL):
            self.assertNotIn(name, text + run.stdout)
        self.assertIn('total hits: 0', run.stdout)
        with open(history, 'rb') as fh:
            self.assertEqual(fh.read(), kept)                                           # the kept history is left as it was found
        self.assertEqual(listing(), proposals)                                          # and so is the proposal store


if __name__ == '__main__':
    unittest.main(verbosity=2)
