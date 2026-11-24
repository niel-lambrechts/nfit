#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/seasonal_demand_anatomy_test.py
# PURPOSE  : 1. A demonstration on constant history, with no growth and no event
#               uplift: every past occurrence of the event has the profile value
#               B, the same as the baseline. The same observations are stored
#               once in the legacy form (the profile value as the peak) and once
#               with OccurrencePeaks (the raw maximum, 1.5 x B, as the peak), both
#               with a peak residual of 0.25 x B (a residual profile at 1.25 x B).
#               The multiplicative model then forecasts 1.25 x B from the legacy
#               form and 1.75 x B from the occurrence form: more than the highest
#               value in the history, because the excursion above the profile
#               value is in the raw peak and is added again as the residual.
#               This test records the behaviour as it is; it is the reference
#               for the correction of the forecast quantity.
#            2. dev/seasonal-demand-anatomy on a kept work tree: it reports
#               aggregates and pseudonyms only and leaves the history untouched.
# USAGE    : python3 test/seasonal_demand_anatomy_test.py
# ==============================================================================
import glob
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
import event_window_test as E

PROFILE = 'G3-95W15'
DATES = '2026-08-01:2026-08-01, 2026-09-01:2026-09-01, 2026-09-12:2026-09-12'
RAW_OVER_PROFILE, RESIDUAL_SHARE = 1.5, 0.25


def forecast_from(form):
    """Sizes the event on a history whose only past occurrence is constant: returns {LPAR: (demand, model baseline, B)}."""
    fixture = M.MemberExposureTests
    M.DAYS = 12
    try:
        root = fixture.make_root()
    finally:
        M.DAYS = 2
    with open(os.path.join(root, 'etc', 'nfit.seasonality.cfg'), 'w') as fh:
        fh.write(E.EVENT % ('multiplicative_seasonal', DATES, 8, 1, 1))
    fixture.run_profile(root, '--update-history', '--min-history-days', '1', '--pool-history', '--force')
    folder = os.path.join(root, 'history', M.SERIAL)
    with open(os.path.join(folder, 'nfit.hist.2026-09.json')) as fh:
        analysis = json.load(fh)['2026-09']['MonthlyWorkloadAnalysis']
    values = {vm: {k: float(v) for k, v in (block.get('ProfileValues') or {}).items()} for vm, block in analysis.items()}
    results = {'HistoricBaseline': values, 'HistoricPeak': values,                      # the event's profile value equals the baseline
               'PeakResidual': {vm: {k: round(v * RESIDUAL_SHARE, 4) for k, v in pv.items()} for vm, pv in values.items()}}
    if form == 'occurrence':
        results['OccurrencePeaks'] = [{'occurrence_id': '2026-08-01_to_2026-08-01', 'occurrence_start': '2026-08-01', 'occurrence_end': '2026-08-01',
                                       'vm': vm, 'profile': k, 'raw_peak_value': round(v * RAW_OVER_PROFILE, 4), 'peak_source': 'raw',
                                       'clipped': False, 'history_schema_version': 'v1'} for vm, pv in values.items() for k, v in pv.items()]
    august = {'2026-08': {'Metadata': {'ProcessedStartDate': '2026-08-01', 'ProcessedEndDate': '2026-08-31', 'ProcessedDays': 31},
                          'MonthlyWorkloadAnalysis': analysis,
                          'SeasonalEventSnapshots': {'ev': {'eventName': 'ev', 'periodEndDate': '2026-08-01', 'results': results}}}}
    with open(os.path.join(folder, 'nfit.hist.2026-08.json'), 'w') as fh:
        json.dump(august, fh)
    fixture.run_profile(root, '--apply-seasonality', 'ev', '--no-cut-protection')
    record = None
    for path in sorted(glob.glob(os.path.join(folder, 'nfit.hist.*.json'))):
        with open(path) as fh:
            for data in json.load(fh).values():
                record = (((data.get('SeasonalEventSnapshots') or {}).get('ev') or {}).get('ModelForecasts') or {}).get('multiplicative_seasonal') or record
    return {vm: (record[vm]['forecast'][PROFILE], record[vm]['baseline'][PROFILE], values[vm][PROFILE]) for vm in ('vmA', 'vmB', 'vmS')}


@unittest.skipUnless(M.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class SeasonalDemandAnatomyTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        M.MemberExposureTests.roots = []
        cls.legacy = forecast_from('legacy')
        cls.occurrence = forecast_from('occurrence')

    @classmethod
    def tearDownClass(cls):
        M.MemberExposureTests.tearDownClass()

    def test_constant_history_is_forecast_above_its_own_maximum_when_occurrence_peaks_are_stored(self):
        for vm in ('vmA', 'vmB', 'vmS'):
            legacy, baseline, b = self.legacy[vm]
            occurrence, baseline_too, _ = self.occurrence[vm]
            self.assertAlmostEqual(baseline, baseline_too, places=3, msg=vm)             # the same data, the same model baseline
            # Legacy form: baseline x (profile value / baseline) + residual = the residual profile's value.
            self.assertAlmostEqual(legacy, baseline + RESIDUAL_SHARE * b, delta=0.01, msg=vm)
            # Occurrence form: baseline x (raw maximum / baseline) + residual.
            self.assertAlmostEqual(occurrence, RAW_OVER_PROFILE * baseline + RESIDUAL_SHARE * b, delta=0.015, msg=vm)
            # Neither is the profile requirement B the history repeats; the second exceeds the raw maximum itself.
            self.assertGreater(legacy, b * 1.2, vm)
            self.assertGreater(occurrence, RAW_OVER_PROFILE * b * 1.1, vm)

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
        self.assertEqual(sum(s['lpars'] for s in result['bySelectedSource'].values()), result['lpars'])
        self.assertTrue(all(re_.startswith(('vm_', 'lpar-')) for re_ in (c['lpar'] for c in result['largestContributors'])))
        for name in ('vmA', 'vmB', 'vmS', 'vmX', M.SERIAL):
            self.assertNotIn(name, text + run.stdout)
        self.assertIn('total hits: 0', run.stdout)
        with open(history, 'rb') as fh:
            self.assertEqual(fh.read(), kept)


if __name__ == '__main__':
    unittest.main(verbosity=2)
