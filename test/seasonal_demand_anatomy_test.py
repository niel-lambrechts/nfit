#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/seasonal_demand_anatomy_test.py
# PURPOSE  : dev/seasonal-demand-anatomy on a kept work tree: it reports
#            aggregates and pseudonyms only, reads the corrected record (the
#            profile requirement, with the peak-capacity estimate beside it) and
#            leaves the history untouched.
#            (The demand quantity itself: test/profile_demand_test.py.)
# USAGE    : python3 test/seasonal_demand_anatomy_test.py
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
class SeasonalDemandAnatomyTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        M.MemberExposureTests.roots = []

    @classmethod
    def tearDownClass(cls):
        M.MemberExposureTests.tearDownClass()

    def test_the_tool_reports_aggregates_and_pseudonyms_and_leaves_the_history_untouched(self):
        fixture = M.MemberExposureTests
        root = fixture.make_root()
        scratch = tempfile.mkdtemp(prefix='demand-anatomy-')
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
        tool = [sys.executable, os.path.join(REPO, 'dev', 'seasonal-demand-anatomy'), '--work', work, '--out', out, '--seasonality', cfg]
        self.assertEqual(subprocess.run(tool, capture_output=True, text=True, timeout=60).returncode, 2)       # lists the events
        run = subprocess.run(tool + ['--event', 'ev'], capture_output=True, text=True, timeout=160)
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-1500:])
        with open(os.path.join(out, 'seasonal-demand-ev.json')) as fh:
            text = fh.read()
        result = json.loads(text)
        self.assertGreaterEqual(result['lpars'], 3)
        self.assertGreater(result['totals']['demand'], 0)
        self.assertEqual((result['recordSemantics']['version'], result['recordSemantics']['forecast']), (2, 'profile-demand'))
        self.assertFalse(result['residualIsAdded'])
        self.assertIn('residual reported with the components, not added', run.stdout)
        self.assertEqual(sum(s['lpars'] for s in result['bySelectedSource'].values()), result['lpars'])
        self.assertTrue(all(re_.startswith(('vm_', 'lpar-')) for re_ in (c['lpar'] for c in result['largestContributors'])))
        for name in ('vmA', 'vmB', 'vmS', 'vmX', M.SERIAL):
            self.assertNotIn(name, text + run.stdout)
        self.assertIn('total hits: 0', run.stdout)
        with open(history, 'rb') as fh:
            self.assertEqual(fh.read(), kept)


if __name__ == '__main__':
    unittest.main(verbosity=2)
