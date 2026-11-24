#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/seasonal_input_compare_test.py
# PURPOSE  : dev/seasonal-input-compare on a kept synthetic work tree: the event
#            is sized with the engine-input variant and with the model's demand, every moved
#            profile figure is attributed to the different input or to a rule
#            further down the chain, only aggregates are reported and the kept
#            history is left as it was found.
# USAGE    : python3 test/seasonal_input_compare_test.py
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
class SeasonalInputCompareTests(unittest.TestCase):

    def test_moved_figures_are_attributed_and_the_kept_history_is_untouched(self):
        fixture = M.MemberExposureTests
        fixture.roots = []
        root = fixture.make_root()
        self.addCleanup(fixture.tearDownClass)
        scratch = tempfile.mkdtemp(prefix='input-compare-')
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
        tool = [sys.executable, os.path.join(REPO, 'dev', 'seasonal-input-compare'), '--work', work, '--out', out, '--seasonality', cfg]
        self.assertEqual(subprocess.run(tool, capture_output=True, text=True, timeout=60).returncode, 2)       # lists the events
        run = subprocess.run(tool + ['--event', 'ev'], capture_output=True, text=True, timeout=160)
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-1500:])
        with open(os.path.join(out, 'seasonal-input-ev.json')) as fh:
            text = fh.read()
        result = json.loads(text)
        planning = result['planningProfiles']
        self.assertGreaterEqual(planning['lpars'], 3)
        self.assertEqual(sum(c['lpars'] for c in planning['classes'].values()), planning['lpars'])
        # One LPAR follows the input (the model forecasts 0.50 where the engine's value was 0.52) ...
        corrected = planning['classes']['input']
        self.assertEqual((corrected['lpars'], corrected['lowered'], corrected['raised']), (1, 1, 0))
        self.assertAlmostEqual(corrected['targetCores'], corrected['inputCores'], places=2)
        # ... and where a floor sets the figure, the changed input leaves it as it was.
        self.assertGreaterEqual(planning['classes']['unchanged: a downstream rule set the same figure']['lpars'], 2)
        self.assertLess(planning['targetModelDemand'], planning['targetEngineInput'])
        self.assertLess(planning['modelDemand'], planning['engineValue'])
        self.assertEqual(result['otherSizedProfiles']['pairs'], sum(c['pairs'] for c in result['otherSizedProfiles']['classes'].values()))
        for name in ('vmA', 'vmB', 'vmS', 'vmX', M.SERIAL):
            self.assertNotIn(name, text + run.stdout)
        self.assertIn('total hits: 0', run.stdout)
        with open(history, 'rb') as fh:
            self.assertEqual(fh.read(), kept)                                           # the kept history is left as it was found


if __name__ == '__main__':
    unittest.main(verbosity=2)
