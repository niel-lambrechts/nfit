#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/zone2_decoder_test.py
# PURPOSE  : The Zone 2 record decoder of nfit-forecast (zone2-record-decoder-1):
#            one reading of the records nfit-profile stores under
#            SeasonalEventSnapshots.<event>.ModelForecasts.<model>.
#            1. A record is a candidate only when it states the profile
#               requirement (semantics version 2 or later). Records without a
#               marker and version 1 records are not read, and say why.
#            2. The forecast is read from <LPAR>.forecast.<profile> only: the
#               sized result, the baseline, the components, the observed value
#               and the peak-capacity estimate are never read as forecasts.
#            3. Every Zone 2 consumer in nfit-forecast goes through the decoder
#               (live candidates, bootstrap median, strategic extraction,
#               scoring, staleness gate); the earlier ad hoc readings are gone.
#               Python candidates (AdaptiveForecast) are not judged by it.
#            4. The records nfit-profile writes today are read, value for
#               value.
#            5. End to end, where nfit-forecast's own modules are installed: a
#               current record becomes a live candidate; a record from before
#               stays in history, is not read, is named once with its reason,
#               and the Zone 2 gate asks for the month to be re-run. The run
#               leaves the stored observations (results) as they were.
#            The block is extracted from ../nfit-forecast/nfit-forecast (or
#            $NFIT_FORECAST) and run in isolation: no third-party modules.
# USAGE    : python3 test/zone2_decoder_test.py
# ==============================================================================
import copy
import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
FORECAST = os.environ.get('NFIT_FORECAST') or os.path.abspath(os.path.join(REPO, '..', 'nfit-forecast', 'nfit-forecast'))
import member_exposure_test as M
import event_window_test as E

SOURCE = ''
if os.path.exists(FORECAST):
    with open(FORECAST, encoding='utf-8') as fh:
        SOURCE = fh.read()


def load_decoder():
    """The decoder block and the bootstrap median that uses it, run in isolation (None when absent)."""
    if '# Zone 2 record decoder (zone2-record-decoder-1)' not in SOURCE:
        return None
    start = SOURCE.rindex('# ====', 0, SOURCE.index('# Zone 2 record decoder (zone2-record-decoder-1)'))
    end = SOURCE.index('# --- End of Zone 2 record decoder ---')
    ns = {}
    exec(compile(SOURCE[start:end], FORECAST, 'exec'), ns)
    median = re.search(r'^def compute_ensemble_median\(.*?(?=^(?:def|class) )', SOURCE, re.S | re.M)
    exec(compile(median.group(0), FORECAST, 'exec'), ns)
    return ns


D = load_decoder()
V2 = {'version': 2, 'forecast': 'profile-demand', 'sized': 'sized-result', 'peakCapacity': 'peak-capacity-estimate'}


def record(semantics=V2, **lpars):
    out = {'_meta': {'generated_on': '2026-11-24T10:00:00'}}
    if semantics is not None:
        out['_meta']['semantics'] = dict(semantics)
    out.update(lpars)
    return out


LPAR = {'forecast': {'G3-95W15': 1.5, 'G2-98W10': '1.75', 'O1-99W5': None, 'bad': 'n/a'},
        'sized': {'G3-95W15': 2.0, 'P-99W1': 2.4},
        'baseline': {'G3-95W15': 1.2},
        'components': {'G3-95W15': {'seasonal_multiplier': 1.25}},
        'observed': {'G3-95W15': 1.4},
        'peakCapacity': {'G3-95W15': {'value': 2.9, 'source': 'occurrence_peaks', 'observations': {'raw': 3}},
                         'G2-98W10': {'unavailable': 'mixed occurrence and legacy observations', 'observations': {'raw': 1, 'legacy': 1}}}}


@unittest.skipUnless(D, 'nfit-forecast source with the Zone 2 record decoder not found')
class Zone2DecoderTests(unittest.TestCase):

    # -- 1. eligibility ------------------------------------------------------------------
    def test_only_a_record_that_states_the_profile_requirement_is_a_candidate(self):
        ok = D['zone2_record_eligibility']
        self.assertEqual(ok(record(vmA=LPAR)), (True, 'profile-demand, semantics version 2'))
        self.assertEqual(ok(record(dict(V2, version=3), vmA=LPAR))[0], True)                     # a later version of the same quantity
        cases = {
            'no semantics marker': record(None, vmA={'G3-95W15': 2.0}),                          # flat, before the separation
            'no semantics marker ': record(None, vmA={'forecast': {'G3-95W15': 2.0}}),           # nested, before the marker
            'semantics version 1 (model-demand)': record({'version': 1, 'forecast': 'model-demand', 'sized': 'sized-result'}, vmA=LPAR),
            'semantics marker without a version': record({'forecast': 'profile-demand'}, vmA=LPAR),
            "forecast quantity 'peak-capacity-estimate' is not the profile requirement": record(dict(V2, forecast='peak-capacity-estimate'), vmA=LPAR),
            'not a record': 3.5,
        }
        for expected, rec in cases.items():
            eligible, reason = ok(rec)
            self.assertFalse(eligible, expected)
            self.assertIn(expected.strip(), reason)
            if isinstance(rec, dict):
                self.assertEqual(D['zone2_record_forecasts'](rec), {}, expected)                 # nothing is read from it
                self.assertIsNone(D['zone2_forecast_value'](rec, 'vmA', 'G3-95W15'), expected)

    # -- 2. what is read ------------------------------------------------------------------
    def test_the_forecast_block_is_read_and_no_other_quantity(self):
        rec = record(vmA=LPAR, vmB={'sized': {'G3-95W15': 9.0}}, vmC='text')
        self.assertEqual(D['zone2_record_forecasts'](rec), {'vmA': {'G3-95W15': 1.5, 'G2-98W10': 1.75}})
        self.assertEqual(D['zone2_forecast_value'](rec, 'vmA', 'G3-95W15'), 1.5)
        self.assertIsNone(D['zone2_forecast_value'](rec, 'vmA', 'P-99W1'))                       # only sized: no forecast
        self.assertIsNone(D['zone2_forecast_value'](rec, 'vmB', 'G3-95W15'))
        self.assertIsNone(D['zone2_forecast_value'](rec, '_meta', 'semantics'))
        for name in D['ZONE2_NOT_PROFILES']:                                                     # a block name is never a profile
            self.assertIsNone(D['zone2_forecast_value'](rec, 'vmA', name), name)
        # The peak-capacity estimate has its own accessor and its own shape.
        self.assertEqual(D['zone2_peak_capacity'](rec, 'vmA', 'G3-95W15')['value'], 2.9)
        self.assertNotIn('value', D['zone2_peak_capacity'](rec, 'vmA', 'G2-98W10'))
        self.assertIsNone(D['zone2_peak_capacity'](rec, 'vmA', 'O1-99W5'))

    def test_a_block_is_decoded_record_by_record_and_tallied(self):
        block = {'predictive_peak': record(vmA=LPAR),
                 'recency_decay': record({'version': 1, 'forecast': 'model-demand'}, vmA=LPAR),
                 'multiplicative_seasonal': record(None, vmA={'G3-95W15': 2.2}),
                 '_meta': {'note': 'not a model'}}
        tally = {}
        decoded = D['zone2_eligible_forecasts'](block, tally)
        self.assertEqual(sorted(decoded), ['predictive_peak'])
        self.assertEqual(decoded['predictive_peak']['vmA']['G3-95W15'], 1.5)
        self.assertEqual(tally['eligible'], 1)
        self.assertEqual(sum(tally['notEligible'].values()), 2)
        self.assertRegex(D['zone2_block_stale_reason'](block), r'^multiplicative_seasonal: no semantics marker')
        self.assertIsNone(D['zone2_block_stale_reason']({'predictive_peak': record(vmA=LPAR)}))
        self.assertEqual(D['zone2_eligible_forecasts'](None), {})

    def test_the_bootstrap_median_takes_eligible_records_and_python_candidates_as_they_are(self):
        candidates = {'ets': {'vmA': {'G3-95W15': {'forecast': 1.0}}}, 'theta': {'vmA': {'G3-95W15': {'forecast': 3.0}}}}
        legacy = {'recency_decay': record(None, vmA={'G3-95W15': 50.0}),                        # a sized figure from before: not read
                  'multiplicative_seasonal': record({'version': 1, 'forecast': 'model-demand'}, vmA={'forecast': {'G3-95W15': 60.0}})}
        got = D['compute_ensemble_median'](candidates, legacy, 'vmA', 'G3-95W15')
        self.assertEqual((got['value'], sorted(got['contributing_models'])), (2.0, ['ets', 'theta']))
        current = dict(legacy, predictive_peak=record(vmA=LPAR))
        got = D['compute_ensemble_median'](candidates, current, 'vmA', 'G3-95W15')
        self.assertEqual((got['value'], sorted(got['contributing_models'])), (1.5, ['ets', 'predictive_peak', 'theta']))

    # -- 3. one decoder for every consumer --------------------------------------------------
    def test_every_zone2_consumer_reads_through_the_decoder(self):
        body = SOURCE[:SOURCE.index('# Zone 2 record decoder (zone2-record-decoder-1)')]
        self.assertGreaterEqual(body.count('zone2_eligible_forecasts('), 3)       # bootstrap median, live candidates, scoring
        self.assertEqual(body.count('zone2_forecast_value('), 1)                   # strategic extraction
        self.assertEqual(body.count('zone2_block_stale_reason('), 1)               # the Zone 2 staleness gate
        # The earlier readings of a Zone 2 record are gone ...
        for gone in ("profile_data.get('forecast', profile_data.get('value'))", "val_block.get('forecast', val_block.get('value'))",
                     "val.get('forecast', val.get('value'))", 'Path A (scalar)', "vm_block['forecast'].get(profile_name)"):
            self.assertNotIn(gone, SOURCE, gone)
        # ... and the candidates nfit-forecast writes itself are still read as they are stored.
        self.assertIn("candidates_block = adaptive_block.get('candidates', {})", SOURCE)
        self.assertIn("y_forecast = vm_map.get(vm_name, {}).get(profile_name, {}).get('forecast')", SOURCE)
        compile(SOURCE, FORECAST, 'exec')


@unittest.skipUnless(D and M.perl_ready(), 'nfit-forecast source or perl with JSON unavailable')
class WrittenRecordTests(unittest.TestCase):

    # -- 4. the records nfit-profile writes --------------------------------------------------
    def test_the_records_nfit_profile_writes_are_read_value_for_value(self):
        harness = E.EventWindowTests
        harness.fixture = M.MemberExposureTests
        harness.fixture.roots = []
        self.addCleanup(harness.fixture.tearDownClass)
        for model in ('predictive_peak', 'recency_decay'):
            harness.seasonal('2026-09-01:2026-09-01', history=True, model=model)
            found = None
            for path in sorted(glob.glob(os.path.join(harness.fixture.roots[-1], 'history', M.SERIAL, 'nfit.hist.*.json'))):
                with open(path) as fh:
                    for data in json.load(fh).values():
                        found = (((data.get('SeasonalEventSnapshots') or {}).get('ev') or {}).get('ModelForecasts') or {}) or found
            self.assertIn(model, found)
            self.assertEqual(D['zone2_record_eligibility'](found[model]), (True, 'profile-demand, semantics version 2'), model)
            decoded = D['zone2_eligible_forecasts'](found)[model]
            lpars = [vm for vm in found[model] if not vm.startswith('_')]
            self.assertGreaterEqual(len(lpars), 3)
            for vm in lpars:
                written = found[model][vm]['forecast']
                self.assertEqual(decoded[vm], {k: float(v) for k, v in written.items()}, (model, vm))
                self.assertNotIn('sized', decoded[vm])
            self.assertIsNone(D['zone2_block_stale_reason'](found))


RUNTIME = all(importlib.util.find_spec(m) for m in ('numpy', 'pandas', 'scipy', 'statsmodels', 'sklearn', 'ruptures', 'pytdigest'))


@unittest.skipUnless(D and M.perl_ready() and RUNTIME, "nfit-forecast, perl with JSON or nfit-forecast's Python modules unavailable")
class LiveCandidateTests(unittest.TestCase):

    # -- 5. end to end --------------------------------------------------------------------
    def test_a_current_record_is_a_live_candidate_and_an_earlier_one_is_kept_and_not_read(self):
        import profile_demand_test as P
        M.MemberExposureTests.roots = []
        self.addCleanup(M.MemberExposureTests.tearDownClass)
        case = P.Case('legacy', peak={P.PROFILE: 1.2}, models=('predictive_peak',))          # September holds a current record
        earlier = {'recency_decay': {'_meta': {'generated_on': '2026-08-02T00:00:00', 'model_version': 1}, 'vmA': {P.PROFILE: 9.0}}}
        path = os.path.join(case.folder, 'nfit.hist.2026-08.json')
        with open(path) as fh:
            august = json.load(fh)
        august['2026-08']['SeasonalEventSnapshots']['ev']['ModelForecasts'] = copy.deepcopy(earlier)     # August: one from before
        with open(path, 'w') as fh:
            json.dump(august, fh)
        with open(os.path.join(case.root, 'etc', 'nfit.seasonality.cfg'), 'a') as fh:
            fh.write('\n[ad]\nmodel = adaptive_peak\nsource_event = ev\npriority = 10\ndates = %s\nbaseline_period_days = 8\n'
                     'min_historical_years = 0\nmin_historical_peaks = 1\nmin_historical_periods = 1\n' % P.DATES)
        shutil.copy(FORECAST, os.path.join(case.root, 'nfit-forecast'))
        before = P.snapshots(case.folder)
        run = subprocess.run([sys.executable, '-W', 'ignore', os.path.join(case.root, 'nfit-forecast'), '--nmondir',
                              os.path.join(case.root, 'stage', M.SERIAL), '--seasonal-event', 'ad'],
                             capture_output=True, text=True, timeout=150, env=dict(os.environ, PYTHONWARNINGS='ignore'))
        text = run.stdout + run.stderr
        self.assertEqual(run.returncode, 0, text[-1500:])
        self.assertRegex(text, r'Zone 2 records not read as candidates \(zone2-record-decoder-1\): 1 x no semantics marker .*\. Read: 1\.')
        self.assertRegex(text, r'2026-08 / ev\s+: \[Z-Gate\] Zone 2 record is no candidate \(recency_decay: no semantics marker')
        pool = re.search(r'Live Candidate Pool: (\d+) forecasts loaded', text)
        self.assertTrue(pool and int(pool.group(1)) > 0, text[-800:])
        after = P.snapshots(case.folder)
        self.assertEqual(after['2026-08']['ModelForecasts'], earlier)                         # kept as it was
        for month, event in before.items():
            self.assertEqual(after[month].get('results'), event.get('results'), month)       # observations untouched
            self.assertNotIn('observationsUnverified', after[month])


if __name__ == '__main__':
    unittest.main(verbosity=2)
