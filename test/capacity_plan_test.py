#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/capacity_plan_test.py
# PURPOSE  : The Seasonal Capacity Plan (v10 C6) end to end, on the
#            profile-demand fixture: a predictive event with its next
#            occurrence configured, and an adaptive event sizing through it
#            with a selection that raises two LPARs.
#            - one row per LPAR, its figures those the seasonal CSV published;
#            - nothing is cut, and the planned pair is valid;
#            - the full event requirement is planned; this cycle's pair, held
#              at the pool's size, is shown beside it;
#            - pools on the planned entitlements, the maximum in whole units;
#            - an overlap from the calendar stays unresolved beside an
#              increase, with a compatible stored plan as a reference; events
#              on the same days are alternatives;
#            - PowerHA stated as not assessed;
#            - the normal plan compared only, and left as it was;
#            - UTC only for InfluxDB-staged data;
#            - the proposal record; a standard run writes no plan.
# USAGE    : python3 test/capacity_plan_test.py
# ==============================================================================
import csv
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import member_exposure_test as M
import event_window_test as E
import profile_demand_test as P

CONFIG = (E.EVENT % ('predictive_peak', P.DATES + ', ' + P.NEXT_DATES, 8, 2, 1)
          + '[ad]\nmodel = adaptive_peak\nsource_event = ev\npriority = 20\nmin_historical_peaks = 2\n'
          # Another business event over the next occurrence, and another forecast of the same day.
          + '[bf]\nmodel = multiplicative_seasonal\npriority = 5\ndates = 2026-09-28:2026-10-03\n'
          + '[alt]\nmodel = predictive_peak\npriority = 1\ndates = 2026-10-01:2026-10-01\n')
EPS = 0.005


def num(text):
    return float(text) if text not in ('', None) else None


@unittest.skipUnless(M.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class CapacityPlanTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fixture = M.MemberExposureTests
        cls.fixture.roots = []
        case = P.Case('occurrence', models=('predictive_peak',),
                      target_event={'eventName': 'ev', 'periodEndDate': '2026-09-12', 'generatedOn': '2026-09-13T00:00:00'})
        cls.root, cls.folder = case.root, case.folder
        cls.cache = os.path.join(cls.root, 'stage', M.SERIAL)
        cls.store = os.path.join(cls.cache, '.nfit.store', 'proposals', 'v1')
        with open(os.path.join(cls.root, 'etc', 'nfit.seasonality.cfg'), 'w') as fh:
            fh.write(CONFIG)
        with open(os.path.join(cls.root, 'etc', 'nfit.vms.cfg'), 'w') as fh:
            fh.write('[vmS]\nha_peer = vmX\n')

        # A: the predictive event alone; no normal plan, no stored plan of the overlapping event, NMON-like manifest.
        cls.a = cls.run_event('ev', '--force')
        cls.record = cls.stored_record()

        # The normal plan: a standard hybrid-state-decay run; one LPAR's current-cycle figure then lowered in it.
        cls.standard = cls.invoke(['--decay-over-states'])
        normal_dir = os.path.join(cls.store, 'hybrid-state-decay')
        path, = glob.glob(os.path.join(normal_dir, '*.json'))
        with open(path) as fh:
            doc = json.load(fh)
        member = doc['members']['vmB']
        member['profiles'][member['planningProfile']]['currentCycle']['entitlement'] = 0.10
        payload = json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()
        shutil.move(path, os.path.join(cls.root, 'normal-original.json'))
        with open(os.path.join(normal_dir, hashlib.sha256(payload).hexdigest() + '.json'), 'wb') as fh:
            fh.write(payload)
        cls.normal_before = cls.digest(normal_dir)

        # The overlapping event's stored plan on the same data: the predictive event's own, filed under it.
        ev_plan = max(glob.glob(os.path.join(cls.store, 'ev', '*.json')), key=os.path.getmtime)     # run A's
        os.makedirs(os.path.join(cls.store, 'bf'))
        shutil.copy(ev_plan, os.path.join(cls.store, 'bf'))
        with open(ev_plan) as fh:
            cls.bf_plan = json.load(fh)

        # Staged from InfluxDB: the time basis is UTC.
        with open(os.path.join(cls.cache, '.nfit.cache.manifest'), 'a') as fh:
            fh.write('staged_by: nfit-stage-influxdb.py v1.0.test\n')

        # B: the adaptive event; its selection raises vmA and vmS (above pool 9's size of 2).
        planning = {vm: m['planningProfile'] for vm, m in cls.a['snapshot']['members'].items()}
        sel = {vm: {p: cls.pair(v) for p, v in cls.record[vm]['forecast'].items()} for vm in cls.lpars()}
        for vm, value in (('vmA', 3.4), ('vmS', 2.6)):
            factor = value / cls.record[vm]['forecast'][planning[vm]]
            sel[vm] = {p: cls.pair(round(v * factor, 4)) for p, v in cls.record[vm]['forecast'].items()}
        cls.write_selection(sel)
        cls.b = cls.run_event('ad')
        cls.normal_after = cls.digest(normal_dir)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    # -- helpers ---------------------------------------------------------------------------
    @classmethod
    def invoke(cls, flags):
        for old in glob.glob(os.path.join(cls.root, 'output', '*')):
            os.remove(old)
        proc = cls.fixture.run_profile(cls.root, *flags)
        logs = glob.glob(os.path.join(cls.root, 'output', '*.log'))
        text = ''
        if logs:
            with open(logs[0]) as fh:
                text = fh.read()
        return {'stderr': proc.stderr, 'log': text,
                'files': sorted(os.path.basename(p) for p in glob.glob(os.path.join(cls.root, 'output', '*')))}

    @classmethod
    def run_event(cls, event, *flags):
        out = cls.invoke(['--apply-seasonality', event] + list(flags))
        found = lambda pattern: glob.glob(os.path.join(cls.root, 'output', pattern))
        plan_csv, = found('*.%s.capacity-plan.*.csv' % event)
        seasonal, = [p for p in found('*.%s.*.csv' % event) if '.capacity-plan.' not in p]
        log, = found('*.log')
        snapshot, = found('*.%s.proposal.*.json' % event)
        with open(plan_csv) as fh:
            out['plan'] = {row['VM']: row for row in csv.DictReader(fh)}
        with open(seasonal) as fh:
            out['rows'] = {row['VM']: row for row in csv.DictReader(fh)}
        with open(log) as fh:
            out['log'] = fh.read()
        with open(snapshot) as fh:
            out['snapshot'] = json.load(fh)
        start = out['log'].index('Seasonal Capacity Plan (event')
        out['section'] = out['log'][start:]
        out['flat'] = ' '.join(out['section'].split())
        return out

    @classmethod
    def stored_record(cls):
        found = None
        for path in sorted(glob.glob(os.path.join(cls.folder, 'nfit.hist.*.json'))):
            with open(path) as fh:
                for data in json.load(fh).values():
                    found = ((((data.get('SeasonalEventSnapshots') or {}).get('ev') or {}).get('ModelForecasts') or {})
                             .get('predictive_peak')) or found
        return found

    @classmethod
    def lpars(cls):
        return sorted(vm for vm in cls.record if not vm.startswith('_'))

    @classmethod
    def target(cls):
        meta = cls.record['_meta']
        return {'occurrenceStart': meta['forecast_target_start'], 'occurrenceEnd': meta['forecast_target_date'], 'observed': False}

    @classmethod
    def pair(cls, value):
        return {'chosen_model': 'holt_event', 'chosen_source': 'Zone 3', 'forecast': value, 'pi_lower': value * 0.9, 'pi_upper': value * 1.1,
                'confidence_score': 55, 'selection_mode': 'tournament', 'observations': 6, 'rationale': 'test',
                'quantity': 'profile-demand', 'candidates_stored_in': '2026-09', 'target_occurrence': cls.target()}

    @classmethod
    def write_selection(cls, selection):
        meta = cls.record['_meta']
        path = os.path.join(cls.folder, 'nfit.hist.2026-09.json')
        with open(path) as fh:
            data = json.load(fh)
        data['2026-09'].setdefault('SeasonalEventSnapshots', {})['ad'] = {'eventName': 'ad', 'AdaptiveForecast': {
            'candidates': {}, 'selection': selection,
            'selection_meta': {'semantics': 'selected-demand-1', 'quantity': 'profile-demand', 'event': 'ad', 'source_event': 'ev',
                               'written_to': '2026-09', 'candidates_stored_in': '2026-09', 'target_occurrence': cls.target(),
                               'data_ends': meta['AnalysisContext']['analysis_end'][:10],
                               'profile_definitions': meta['profile_definitions'], 'exclusion_definitions': meta['exclusion_definitions'],
                               'record_definitions': {'profile_definitions': meta['profile_definitions'],
                                                      'exclusion_definitions': meta['exclusion_definitions']},
                               'live_candidates_added': 0, 'generated_on': '2026-09-30T00:00:00'}}}
        with open(path, 'w') as fh:
            json.dump(data, fh)

    @staticmethod
    def digest(folder):
        h = hashlib.sha256()
        for path in sorted(glob.glob(os.path.join(folder, '*'))):
            with open(path, 'rb') as fh:
                h.update(os.path.basename(path).encode() + fh.read())
        return h.hexdigest()

    # -- figures ---------------------------------------------------------------------------
    def test_one_row_per_lpar_with_the_published_figures(self):
        for run in (self.a, self.b):
            self.assertEqual(set(run['plan']), set(run['rows']))
            for vm, row in run['plan'].items():
                published = run['rows'][vm]
                self.assertEqual(num(row['Installed_ENT']), num(published['Current - ENT']), vm)
                self.assertEqual(int(row['Installed_VP']), int(published['Current - VCPU']), vm)
                profile = published['Planning Profile']
                self.assertAlmostEqual(num(row['Event_ENT']), num(published[profile]), places=2, msg=vm)
                # The VP figure of the requirement is the profile's recommended VPs, as the proposal records it.
                self.assertEqual(int(row['Event_VP']), int(run['snapshot']['members'][vm]['profiles'][profile]['recommendedVPs']), vm)

    def test_nothing_is_cut_and_the_planned_pair_is_valid(self):
        for run in (self.a, self.b):
            for vm, row in run['plan'].items():
                ient, ivp, eent, evp = num(row['Installed_ENT']), int(row['Installed_VP']), num(row['Event_ENT']), int(row['Event_VP'])
                pent, pvp = num(row['Plan_ENT']), int(row['Plan_VP'])
                self.assertGreaterEqual(pent, ient - EPS, vm)
                self.assertGreaterEqual(pvp, ivp, vm)
                self.assertLessEqual(pent, pvp + EPS, vm)                       # at most 1.00 per VP
                self.assertGreaterEqual(pent, 0.05 * pvp - EPS, vm)             # at least 0.05 per VP
                self.assertAlmostEqual(pent, max(ient, eent) + num(row['PlatformMinimum_ENT']), places=2, msg=vm)
                self.assertGreaterEqual(pvp, max(ivp, evp), vm)
                self.assertAlmostEqual(num(row['Additional_ENT']), pent - ient, places=2, msg=vm)
                self.assertEqual(int(row['Additional_VP']), pvp - ivp, vm)
                self.assertEqual(row['Action'], 'increase' if (pent > ient + EPS or pvp > ivp) else 'retain', vm)

    def test_a_confirmation_never_replaces_the_figure(self):
        for run in (self.a, self.b):
            for vm, row in run['plan'].items():
                marked = 'confirm before the event' in row['Unresolved'].split('; ')
                self.assertEqual(marked, row['Confirmation'].startswith('confirm before the event: '), vm)
                if marked:
                    self.assertEqual(row['Action'], 'increase', vm)
                    self.assertAlmostEqual(num(row['Plan_ENT']), max(num(row['Installed_ENT']), num(row['Event_ENT'])) + num(row['PlatformMinimum_ENT']), places=2)
                if row['ThisCycleReason'] and 'pool' not in row['ThisCycleReason']:
                    self.assertIn('it adds VPs only to carry the entitlement', row['ThisCycleReason'], vm)

    def test_the_full_requirement_is_planned_with_the_held_step_beside_it(self):
        row, published = self.b['plan']['vmS'], self.b['rows']['vmS']
        self.assertGreater(num(row['Event_ENT']), 2.0 + EPS)                   # above pool 9's size
        self.assertEqual(num(row['Plan_ENT']), num(row['Event_ENT']))          # the full requirement, not the step
        self.assertEqual(num(row['ThisCycle_ENT']), num(published['NFIT - ENT']))   # this cycle's recommendation, as published
        self.assertLessEqual(num(row['ThisCycle_ENT']), 2.0 + EPS)
        self.assertIn("limited by pool 9's maximum", row['ThisCycleReason'])
        self.assertEqual(row['Action'], 'increase')
        self.assertRegex(self.b['flat'], r"vmS 1\.50 / 2 -> %s / \d+ \(\+[0-9.]+ ENT;.* this cycle's recommendation is %s / \d+: limited by pool 9's maximum"
                         % (re.escape(row['Plan_ENT']), re.escape(row['ThisCycle_ENT'])))
        for vm, row in self.b['plan'].items():
            if row['ThisCycle_ENT'] == '':
                continue
            self.assertTrue(num(row['ThisCycle_ENT']) < num(row['Plan_ENT']) - EPS or int(row['ThisCycle_VP']) < int(row['Plan_VP']), vm)

    def test_pools_on_the_planned_entitlements(self):
        plan = self.b['plan']
        sizes = {'7': 4.0, '9': 2.0}
        for pool, size in sizes.items():
            members = [r for r in plan.values() if r['Pool'] == pool]
            total = round(sum(num(r['Plan_ENT']) for r in members), 2)
            needed = int(total - 1e-9) + 1
            if total > size + EPS:
                for r in members:
                    self.assertEqual(r['PoolPrerequisite'], "raise pool %s's maximum to at least %d first (calculated requirement %.2f)" % (pool, needed, total))
                    self.assertIn('pool change first', r['Unresolved'].split('; '))
                self.assertIn("Pool %s: calculated requirement %.2f processing units; raise the pool's maximum to at least %d (now %d)."
                              % (pool, total, needed, size), self.b['flat'])
            else:
                self.assertTrue(all(r['PoolPrerequisite'] == '' for r in members))
        self.assertIn('pool change first', plan['vmS']['Unresolved'])
        self.assertIn('Reserved pool capacity is not collected; check licensing.', self.b['flat'])

    def test_an_overlap_stays_unresolved_beside_an_increase(self):
        stored = self.bf_plan['members']
        for vm, row in self.b['plan'].items():
            reference = stored[vm]['profiles'][stored[vm]['planningProfile']]['entitlement']
            self.assertEqual(row['Overlap'], 'bf 2026-09-28 to 2026-10-03, overlapping 2026-10-01 to 2026-10-01: combined requirement not '
                                             'assessed (bf %.2f, its saved plan on the same data)' % reference, vm)
            self.assertIn('overlapping event', row['Unresolved'].split('; '), vm)
        self.assertEqual(self.b['plan']['vmA']['Action'], 'increase')         # the increase is kept, and so is the overlap
        self.assertIn('bf (28 Sep to 03 Oct 2026) overlaps this occurrence on 01 Oct 2026; combined requirement not assessed for all',
                      self.b['flat'])
        self.assertRegex(self.b['flat'], r"Each event's own requirement, ad and bf \(bf from its saved plan on the same data\): vmA [0-9.]+ and [0-9.]+")
        # Without a stored plan of the overlapping event, the overlap is still stated.
        self.assertIn('bf: no saved plan of that event on the same data', self.a['flat'])
        self.assertTrue(all('combined requirement not assessed (no saved plan of that event on the same data)' in r['Overlap'] for r in self.a['plan'].values()))

    def test_events_on_the_same_days_are_alternatives(self):
        self.assertIn('Other forecasts of the same event days (for reference; not overlapping events): alt, ev.', self.b['flat'])
        self.assertIn('Other forecasts of the same event days (for reference; not overlapping events): alt.', self.a['flat'])
        for run in (self.a, self.b):
            self.assertTrue(all('alt ' not in r['Overlap'] and not r['Overlap'].startswith('ev ') for r in run['plan'].values()))

    def test_powerha_is_stated_as_not_assessed(self):
        for vm, peer in (('vmS', 'vmX'), ('vmX', 'vmS')):
            row = self.b['plan'][vm]
            self.assertRegex(row['RoleLimitation'], r'^PowerHA roles are not assessed for events: confirm this node can take over the event '
                                                    r'allocation of %s \([0-9.]+ / \d+\)$' % peer)
            self.assertIn('PowerHA role', row['Unresolved'].split('; '))
        self.assertEqual(self.b['plan']['vmA']['RoleLimitation'], '')

    def test_the_normal_plan_is_compared_only(self):
        row = self.b['plan']['vmB']
        self.assertRegex(row['NormalPlanConflict'], r'^normal recommendation \(hybrid-state-decay\) of 0\.10 / \d+ is below the event requirement$')
        self.assertRegex(self.b['flat'], r"vmB: the normal recommendation of 0\.10 / \d+ is below the event requirement [0-9.]+ / \d+ for 01 Oct 2026\.")
        self.assertEqual(self.normal_before, self.normal_after)              # the normal plan is not changed
        self.assertEqual(self.b['snapshot']['context']['capacityPlan']['normalPlan']['status'], 'matching')
        self.assertIn('Normal recommendation (hybrid-state-decay run): none saved for this system; not compared.', self.a['flat'])

    def test_time_basis(self):
        self.assertTrue(all(r['TimeBasis'] == 'not confirmed' and r['RequiredBy'] == '2026-10-01 00:00' for r in self.a['plan'].values()))
        self.assertIn('01 Oct 2026 00:00 in the time zone of the collected data. nFit confirms that time zone (UTC) only for data collected through InfluxDB.', self.a['flat'])
        self.assertTrue(all(r['TimeBasis'] == 'UTC' for r in self.b['plan'].values()))
        self.assertIn('Capacity available before the event starts: 01 Oct 2026 00:00 UTC (the time zone of the collected data).', self.b['flat'])

    def test_the_proposal_record(self):
        context = self.b['snapshot']['context']['capacityPlan']
        self.assertEqual((context['produced'], context['occurrence'], context['requiredBy'], context['timeBasis']),
                         (True, {'start': '2026-10-01', 'end': '2026-10-01'}, '2026-10-01 00:00', 'UTC'))
        self.assertEqual([o['event'] for o in context['overlaps']], ['bf'])
        self.assertEqual(sorted(context['alternatives']), ['alt', 'ev'])
        self.assertIn('selection', self.b['snapshot']['context'])             # the adaptive context is kept beside it
        for vm, row in self.b['plan'].items():
            record = self.b['snapshot']['members'][vm]['capacityPlan']
            self.assertEqual(record['action'], row['Action'])
            self.assertEqual('; '.join(record['unresolved']), row['Unresolved'])
            self.assertEqual(record['plan'], {'entitlement': num(row['Plan_ENT']), 'VPs': int(row['Plan_VP'])})
            self.assertEqual(record['combinedEvents'], [])

    def test_the_section_is_a_plan_only(self):
        for run in (self.a, self.b):
            section = run['section']
            self.assertIn('A planning artefact: it does not authorise or make any HMC change.', section)
            self.assertEqual(run['log'].count('Post-event allocation is determined by the normal capacity review.'), 1)
            self.assertNotRegex(section.split('\n\n\n')[0], r'(?i)partition profile|return to|rerun|re-run')

    def test_a_standard_run_writes_no_capacity_plan(self):
        self.assertFalse([f for f in self.standard['files'] if '.capacity-plan.' in f])
        self.assertTrue(self.standard['log'])
        self.assertNotIn('Seasonal Capacity Plan', self.standard['log'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
