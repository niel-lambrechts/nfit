#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/profile_demand_test.py
# PURPOSE  : The seasonal demand quantity (v10). A seasonal model's forecast is
#            the profile requirement: the demand on the profile's own
#            percentile, smoothing window and hours. The peak-capacity estimate
#            (raw or latent occurrence peaks) is kept beside it under its own
#            name and is no part of the demand.
#            1. Equivalent observations in the legacy and the occurrence form
#               give the same profile demand.
#            2. Constant history returns B: no growth, no clipping uplift, no
#               amplification.
#            3. Profiles sharing the same hours, and so the same raw occurrence
#               peak, keep their own percentile and window behaviour.
#            4. Changing the raw peak or the residual alone leaves the profile
#               demand as it was; the estimated hidden demand of a clipped
#               occurrence is added once, on the profile's basis.
#            (Observed history and repeated generation: test/observed_history_test.py.)
#            5. The peak-capacity estimate states its basis over every
#               observation it used: occurrence peaks, or legacy profile values
#               each with its own residual. A history holding both gives no
#               estimate, and the same profile demand.
#            6. Peak_P-99W1 is a measurement: neither model forecasts it, and the
#               rules that read the measured peak get the measurement although
#               history holds a higher event value for it.
#            Multiplicative seasonal and predictive peak, on the member-exposure
#            fixture with the same synthetic earlier occurrence in two months.
# USAGE    : python3 test/profile_demand_test.py
# ==============================================================================
import copy
import glob
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import member_exposure_test as M
import event_window_test as E

PEAK_PROFILE = 'Peak_P-99W1'                          # the measurement profile
PROFILE, OTHER = 'G3-95W15', 'G2-98W10'              # the same hours; their own percentile and window
LPARS = ('vmA', 'vmB', 'vmS')
EARLIER = ('2026-07', '2026-08')
DATES = '2026-07-01:2026-07-01, 2026-08-01:2026-08-01, 2026-09-01:2026-09-01, 2026-09-12:2026-09-12'
MODELS = ('multiplicative_seasonal', 'predictive_peak')


def snapshots(folder):
    """{month: the event's stored snapshot} of a history folder."""
    found = {}
    for path in sorted(glob.glob(os.path.join(folder, 'nfit.hist.*.json'))):
        with open(path) as fh:
            for month, data in json.load(fh).items():
                event = (data.get('SeasonalEventSnapshots') or {}).get('ev')
                if event is not None:
                    found[month] = event
    return found


def record_of(folder, model):
    found = None
    for event in snapshots(folder).values():
        found = (event.get('ModelForecasts') or {}).get(model) or found
    return found


class Case:
    """One history: the fixture's months plus the same synthetic earlier occurrence in July and August, sized by the models asked for."""
    template = None

    def __init__(self, form='legacy', raw=1.5, residual=0.25, peak=None, hidden=0.0, latent_extra=0.0, generated=True,
                 earlier_record=None, latest_peak=None, target_event=None, models=MODELS, repeats=1, trace=False):
        fixture = M.MemberExposureTests
        if Case.template is None:
            # The fixture's months and their history, generated once and copied for every case.
            M.DAYS = 12
            try:
                Case.template = fixture.make_root()
            finally:
                M.DAYS = 2
            self.root = Case.template
            self.configure(models[0])
            fixture.run_profile(Case.template, '--update-history', '--min-history-days', '1', '--pool-history', '--force')
        self.root = tempfile.mkdtemp(prefix='member-exp-')
        fixture.roots.append(self.root)
        shutil.copytree(Case.template, self.root, dirs_exist_ok=True)
        self.folder = os.path.join(self.root, 'history', M.SERIAL)
        with open(os.path.join(self.folder, 'nfit.hist.2026-09.json')) as fh:
            analysis = json.load(fh)['2026-09']['MonthlyWorkloadAnalysis']
        self.values = {vm: {k: float(v) for k, v in (block.get('ProfileValues') or {}).items()} for vm, block in analysis.items()}
        peak = peak or {}
        # The event's value on each profile's own basis: B, unless a factor is given for the profile.
        results = {'HistoricBaseline': self.values,
                   'HistoricPeak': {vm: {k: round(v * peak.get(k, 1.0), 4) for k, v in pv.items()} for vm, pv in self.values.items()},
                   'PeakResidual': {vm: {k: round(v * residual, 4) for k, v in pv.items()} for vm, pv in self.values.items()}}
        if hidden:
            results['ClippingInfo'] = {vm: {k: {'isClipped': True, 'clippingConfidence': 0.9, 'unmetDemandEstimate': round(v * hidden, 4),
                                                'unclippedPeakEstimate': round(v * (peak.get(k, 1.0) + hidden), 4)}
                                            for k, v in pv.items()} for vm, pv in self.values.items()}
        for index, month in enumerate(EARLIER):
            day = month + '-01'
            stored_form = form if isinstance(form, str) else form[index]      # one form, or one per earlier month
            mine = copy.deepcopy(results)
            if latest_peak and month == EARLIER[-1]:                     # the latest earlier occurrence on its own factors
                for pv in mine['HistoricPeak'].values():
                    for k in latest_peak:
                        pv[k] = round(pv[k] / peak.get(k, 1.0) * latest_peak[k], 4)
            if stored_form == 'occurrence':
                mine['OccurrencePeaks'] = []
                for vm, pv in self.values.items():
                    for k, v in pv.items():
                        row = {'occurrence_id': '%s_to_%s' % (day, day), 'occurrence_start': day, 'occurrence_end': day,
                               'vm': vm, 'profile': k, 'raw_peak_value': round(v * raw, 4), 'peak_source': 'raw', 'clipped': bool(hidden),
                               'history_schema_version': 'v1'}
                        if hidden:
                            row['latent_peak_value'] = round(v * (raw + hidden + latent_extra), 4)
                            row['peak_source'] = 'latent'
                        mine['OccurrencePeaks'].append(row)
            event = {'eventName': 'ev', 'periodEndDate': day, 'results': mine}
            if generated:
                event['generatedOn'] = month + '-02T00:00:00'
            if earlier_record and month == EARLIER[-1]:
                event['ModelForecasts'] = earlier_record
            stored = {month: {'Metadata': {'ProcessedStartDate': day, 'ProcessedEndDate': month + '-28', 'ProcessedDays': 28},
                              'MonthlyWorkloadAnalysis': analysis, 'SeasonalEventSnapshots': {'ev': event}}}
            with open(os.path.join(self.folder, 'nfit.hist.%s.json' % month), 'w') as fh:
                json.dump(stored, fh)
        if target_event:
            # A snapshot already stored for the month the run sizes.
            path = os.path.join(self.folder, 'nfit.hist.2026-09.json')
            with open(path) as fh:
                september = json.load(fh)
            event = dict(target_event)
            event.setdefault('results', copy.deepcopy(results))
            september['2026-09'].setdefault('SeasonalEventSnapshots', {})['ev'] = event
            with open(path, 'w') as fh:
                json.dump(september, fh)
        self.before = copy.deepcopy(snapshots(self.folder))
        self.records, self.stderr, self.passes, self.traces = {}, {}, [], {}
        for model in models:
            self.configure(model)
            for _ in range(repeats):
                flags = ['--research-runq-trace', os.path.join(self.root, 'trace-%s.json' % model)] if trace else []
                run = fixture.run_profile(self.root, '--apply-seasonality', 'ev', '--no-cut-protection', *flags)
                if trace:
                    with open(flags[1]) as fh:
                        self.traces[model] = json.load(fh)['members']
                self.stderr[model] = run.stderr
                self.records[model] = record_of(self.folder, model)
                self.passes.append((model, copy.deepcopy(self.records[model])))
        self.after = snapshots(self.folder)

    def configure(self, model):
        with open(os.path.join(self.root, 'etc', 'nfit.seasonality.cfg'), 'w') as fh:
            fh.write(E.EVENT % (model, DATES, 8, 2 if model == 'predictive_peak' else 1, 1))

    def demand(self, model, vm, profile=PROFILE):
        return self.records[model][vm]['forecast'][profile]

    def baseline(self, model, vm, profile=PROFILE):
        return self.records[model][vm]['baseline'][profile]

    def capacity(self, model, vm, profile=PROFILE):
        return (self.records[model][vm].get('peakCapacity') or {}).get(profile)


@unittest.skipUnless(M.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class ProfileDemandTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        M.MemberExposureTests.roots = []
        cls.legacy = Case('legacy')
        cls.occurrence = Case('occurrence')
        cls.moved = Case('occurrence', raw=3.0, residual=0.6)                       # only the raw peak and the residual differ
        cls.distinct = Case('occurrence', raw=2.0, peak={PROFILE: 1.1, OTHER: 1.3})
        # History holds the measurement profile half as high again in the event, or as it was: the measurement of the run is the same.
        cls.measured = {factor: Case('legacy', peak={PEAK_PROFILE: factor, PROFILE: 1.2}, models=('predictive_peak',), trace=True) for factor in (1.5, 1.0)}
        cls.mixed = Case(('legacy', 'occurrence'))                                  # July without, August with occurrence peaks
        cls.clipped = {form: Case(form, peak={PROFILE: 1.1, OTHER: 1.3}, hidden=0.2, latent_extra=extra)
                       for form, extra in (('legacy', 0.0), ('occurrence', 0.4))}

    @classmethod
    def tearDownClass(cls):
        M.MemberExposureTests.tearDownClass()

    # -- 1 and 2 ---------------------------------------------------------------------------
    def test_legacy_and_occurrence_forms_of_the_same_observations_give_the_same_profile_demand(self):
        for model in MODELS:
            for vm in LPARS:
                for profile in (PROFILE, OTHER):
                    self.assertAlmostEqual(self.legacy.demand(model, vm, profile), self.occurrence.demand(model, vm, profile), places=3,
                                           msg=(model, vm, profile))

    def test_constant_history_returns_b(self):
        for case in (self.legacy, self.occurrence):
            for vm in LPARS:
                # Multiplicative: the model's baseline times one. Nothing is added to it.
                self.assertAlmostEqual(case.demand('multiplicative_seasonal', vm), case.baseline('multiplicative_seasonal', vm), places=3, msg=vm)
                # Predictive peak: the larger of its baseline and the projected profile value B.
                expect = max(case.baseline('predictive_peak', vm), case.values[vm][PROFILE])
                self.assertAlmostEqual(case.demand('predictive_peak', vm), expect, places=3, msg=vm)
            for model in MODELS:                                                     # a level LPAR: exactly B
                self.assertAlmostEqual(case.demand(model, 'vmS'), case.values['vmS'][PROFILE], places=3, msg=model)

    # -- 3 -------------------------------------------------------------------------------
    def test_profiles_sharing_hours_keep_their_own_percentile_and_window(self):
        case = self.distinct
        for vm in LPARS:
            m = 'multiplicative_seasonal'
            self.assertAlmostEqual(case.demand(m, vm, PROFILE) / case.baseline(m, vm, PROFILE), 1.1, delta=0.005, msg=vm)
            self.assertAlmostEqual(case.demand(m, vm, OTHER) / case.baseline(m, vm, OTHER), 1.3, delta=0.005, msg=vm)
        b = case.values['vmS']
        self.assertAlmostEqual(case.demand('predictive_peak', 'vmS', PROFILE), 1.1 * b[PROFILE], delta=0.005)
        self.assertAlmostEqual(case.demand('predictive_peak', 'vmS', OTHER), 1.3 * b[OTHER], delta=0.005)
        # The raw occurrence peak, twice B for both profiles, is the peak-capacity estimate and nothing else.
        for model in MODELS:
            for profile in (PROFILE, OTHER):
                estimate = case.capacity(model, 'vmS', profile)
                self.assertAlmostEqual(estimate['value'], 2.0 * b[profile], delta=0.01, msg=(model, profile))
                self.assertEqual((estimate['source'], estimate['observations']), ('occurrence_peaks', {'raw': 2}), (model, profile))

    # -- 4 -------------------------------------------------------------------------------
    def test_the_raw_peak_and_the_residual_alone_do_not_move_the_profile_demand(self):
        for model in MODELS:
            for vm in LPARS:
                for profile in (PROFILE, OTHER):
                    self.assertEqual(self.moved.demand(model, vm, profile), self.occurrence.demand(model, vm, profile), (model, vm, profile))
            a, b = self.occurrence.capacity(model, 'vmS'), self.moved.capacity(model, 'vmS')
            self.assertAlmostEqual(b['value'] / a['value'], 2.0, delta=0.01, msg=model)      # the estimate did follow the raw peak

    def test_estimated_hidden_demand_is_added_once_on_the_profile_basis(self):
        legacy, occurrence = self.clipped['legacy'], self.clipped['occurrence']
        b = legacy.values['vmS']
        for model in MODELS:
            for profile, factor in ((PROFILE, 1.1), (OTHER, 1.3)):
                self.assertAlmostEqual(legacy.demand(model, 'vmS', profile), (factor + 0.2) * b[profile], delta=0.005, msg=(model, profile))
                # The stored latent peak is a different quantity: it does not reach the demand.
                self.assertAlmostEqual(occurrence.demand(model, 'vmS', profile), legacy.demand(model, 'vmS', profile), places=3, msg=(model, profile))
            self.assertEqual(occurrence.capacity(model, 'vmS')['observations'], {'latent': 2}, model)

    # -- 5 -------------------------------------------------------------------------------
    def test_the_peak_capacity_estimate_states_its_basis_over_every_observation(self):
        b = self.legacy.values['vmS'][PROFILE]
        for model in MODELS:
            # Legacy history: each observation is its profile value with its own residual, B + 0.25 B.
            estimate = self.legacy.capacity(model, 'vmS')
            self.assertEqual((estimate['source'], estimate['observations']), ('legacy_profile_values_with_residual', {'legacy': 2}), model)
            self.assertAlmostEqual(estimate['value'], 1.25 * b, delta=0.01, msg=model)
            # Occurrence history: the raw peaks, 1.5 B; no residual on top.
            estimate = self.occurrence.capacity(model, 'vmS')
            self.assertEqual((estimate['source'], estimate['observations']), ('occurrence_peaks', {'raw': 2}), model)
            self.assertAlmostEqual(estimate['value'], 1.5 * b, delta=0.01, msg=model)

    def test_a_history_of_both_forms_gives_no_peak_capacity_estimate_and_the_same_demand(self):
        for model in MODELS:
            for vm in LPARS:
                estimate = self.mixed.capacity(model, vm)
                self.assertNotIn('value', estimate, (model, vm))
                self.assertEqual(estimate['unavailable'], 'mixed occurrence and legacy observations', (model, vm))
                self.assertEqual(estimate['observations'], {'legacy': 1, 'raw': 1}, (model, vm))
                for profile in (PROFILE, OTHER):
                    self.assertAlmostEqual(self.mixed.demand(model, vm, profile), self.legacy.demand(model, vm, profile), places=3,
                                           msg=(model, vm, profile))

    # -- 6 -------------------------------------------------------------------------------
    def test_the_measurement_profile_is_not_forecast_and_stays_what_was_measured(self):
        for case in (self.legacy, self.occurrence, self.measured[1.5]):
            for model, record in case.records.items():
                for vm in LPARS:
                    self.assertNotIn(PEAK_PROFILE, record[vm]['forecast'], (model, vm))
                    self.assertIn(PROFILE, record[vm]['forecast'], (model, vm))
        high, level = self.measured[1.5], self.measured[1.0]
        for vm in LPARS:
            self.assertGreater(high.demand('predictive_peak', vm), 1.15 * high.values[vm][PROFILE], vm)       # the sized profile is forecast
            a, b = (c.traces['predictive_peak'][vm]['profiles'][PROFILE] for c in (high, level))
            # What the VP and guarantee rules read as the measured peak does not follow the stored event value.
            for key in ('rVCPU_P99W1RawPeak', 'rVCPU_P99W1Floor', 'rVCPU_YieldFloorCores'):
                self.assertEqual(a[key], b[key], (vm, key))
            self.assertNotEqual(a['rVCPU_P99W1RawPeak'], 'N/A', vm)

    def test_what_a_run_observed_stays_in_its_own_record(self):
        record = self.legacy.records['multiplicative_seasonal']
        self.assertEqual(record['_meta']['semantics']['version'], 2)
        self.assertEqual(record['_meta']['semantics']['forecast'], 'profile-demand')
        self.assertIn(PROFILE, record['vmS']['peakCapacity'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
