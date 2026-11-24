#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/seasonal_revision_compare_test.py
# PURPOSE  : dev/seasonal-revision-compare on a kept work tree: the same event
#            is sized with nfit and nfit-profile of an earlier revision and with
#            the current files, on inputs it restores and fingerprints before
#            each run. It reports aggregates and pseudonyms only, says that the
#            inputs were identical, and leaves the kept history and the stage's
#            proposal store as it found them.
#            Restoration: where the history found at invocation differs from the
#            fixed history both runs read (history.pristine), and the tree holds
#            its own executables and seasonality file, all of it is put back
#            exactly, after a comparison and after a run that fails; the fixed
#            history is not touched.
# USAGE    : python3 test/seasonal_revision_compare_test.py
# ==============================================================================
import hashlib
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


def state(*folders):
    """{relative name: digest} of every file below the folders."""
    found = {}
    for folder in folders:
        for d, _s, files in os.walk(folder):
            for f in files:
                path = os.path.join(d, f)
                with open(path, 'rb') as fh:
                    found[os.path.relpath(path, os.path.dirname(folder))] = hashlib.sha256(fh.read()).hexdigest()
    return found


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
        self.assertEqual(result['notCompared'], {})                                     # every LPAR sized by either run is compared
        self.assertIn('Every LPAR sized by either run is compared.', run.stdout)
        self.assertEqual((result['profilePairs']['onlyInBase'], result['profilePairs']['onlyInCurrent']), (0, 0))
        rc = result['reconciliationOfPublishedMoves']
        self.assertAlmostEqual(rc['total'], result['totals']['publishedChange'], places=2)
        self.assertFalse(os.path.exists(os.path.join(work, 'case', 'history.pristine')))    # none was there: none is left

        # -- restoration: the tree's own state differs from the fixed history, and one run fails --
        live = os.path.join(work, 'case', 'history', M.SERIAL)
        fixed = os.path.join(work, 'case', 'history.pristine', M.SERIAL)
        shutil.copytree(live, fixed)                                                    # the fixed history both runs read
        with open(os.path.join(live, 'nfit.hist.1999-01.json'), 'w') as fh:             # the live history has moved on since
            fh.write('{"1999-01": {"Metadata": {"note": "held at invocation only"}}}')
        with open(os.path.join(work, 'app', 'nfit-profile'), 'a') as fh:
            fh.write('\n# a local copy, not the repository file\n')
        with open(os.path.join(work, 'app', 'etc', 'nfit.seasonality.cfg'), 'w') as fh:
            fh.write('[Global]\nmin_history_days = 7\n')
        parts = (os.path.join(work, 'case', 'history'), os.path.join(work, 'case', 'history.pristine'), os.path.join(work, 'app', 'etc'),
                 os.path.join(work, 'case', 'stage', M.SERIAL, '.nfit.store'))
        files = lambda: dict(state(*parts), **{n: state(os.path.join(work, 'app'))[os.path.join('app', n)] for n in ('nfit', 'nfit-profile')},
                             **{n: os.path.exists(os.path.join(work, 'case', 'stage', M.SERIAL, n)) and
                                hashlib.sha256(open(os.path.join(work, 'case', 'stage', M.SERIAL, n), 'rb').read()).hexdigest()
                                for n in ('.nfit.cache.results', '.nfit.engine.last_args', '.nfit.engine.last_manifest')})
        found = files()
        self.assertIn(os.path.join('history', M.SERIAL, 'nfit.hist.1999-01.json'), found)
        again = subprocess.run(tool + ['--event', 'ev', '--base', 'HEAD'], capture_output=True, text=True, timeout=160)
        self.assertEqual(again.returncode, 0, (again.stdout + again.stderr)[-1500:])
        self.assertEqual(files(), found)                                                # after a comparison: exactly as found
        failed = subprocess.run(tool + ['--event', 'ev', '--base', 'HEAD', '--flag=--no-such-switch'], capture_output=True, text=True, timeout=160)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('The kept tree was put back as it was found.', failed.stderr)
        self.assertEqual(files(), found)                                                # after a failed run: exactly as found


if __name__ == '__main__':
    unittest.main(verbosity=2)
