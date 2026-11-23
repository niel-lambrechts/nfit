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
        self.assertEqual((result['format']['writtenCompact'], result['format']['parsedContentIdentical']), (True, True))
        self.assertLess(result['format']['writtenBytes'], result['format']['indentedBytes'])
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

    def test_the_seasonal_compare_reuses_the_kept_history_and_reports_aggregates_only(self):
        fixture = M.MemberExposureTests
        fixture.roots = []
        root = fixture.make_root()
        self.addCleanup(fixture.tearDownClass)
        scratch = tempfile.mkdtemp(prefix='hist-check-')
        self.addCleanup(lambda: __import__('shutil').rmtree(scratch, ignore_errors=True))
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
        tool = [sys.executable, os.path.join(REPO, 'dev', 'seasonal-protection-compare'), '--work', work, '--out', out, '--seasonality', cfg]
        listing = subprocess.run(tool, capture_output=True, text=True, timeout=60)
        self.assertEqual(listing.returncode, 2)
        self.assertRegex(listing.stdout, r'ev\s+predictive_peak')
        run = subprocess.run(tool + ['--event', 'ev'], capture_output=True, text=True, timeout=160)
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-1500:])
        with open(os.path.join(out, 'seasonal-protection-ev.json')) as fh:
            text = fh.read()
        result = json.loads(text)
        self.assertEqual(result['evidence']['pastWindows'], 1)
        self.assertEqual(result['evidence']['lpars'], {'contested': 2, 'noCompetition': 1, 'insufficient': 0, 'unavailable': 0})
        self.assertEqual(result['lpars']['both'], 4)
        self.assertGreaterEqual(result['planningEntitlement']['raised'], 1)             # a cut limited by the event's evidence
        self.assertGreaterEqual(result['actions']['cutLimitedByPastEventWindows'], 1)
        # Every moved figure is explained from the stored proposal of the run, and no profile value came
        # down without supported no-competition evidence.
        self.assertTrue(any(k.startswith('raised | evidence contested | own profile values moved') for k in result['movedPlanningFigures']),
                        result['movedPlanningFigures'])
        self.assertEqual(result['exemptionCheck']['withoutIt'], 0)
        self.assertEqual((result['eventWindowPlan']['pools'], result['eventWindowPlan']['poolsWithinMaximum']), (2, 2))
        for name in ('vmA', 'vmB', 'vmS', 'vmX', M.SERIAL):
            self.assertNotIn(name, text)
        with open(history, 'rb') as fh:
            self.assertEqual(fh.read(), kept)                                           # the kept history is left as it was found


if __name__ == '__main__':
    unittest.main(verbosity=2)
