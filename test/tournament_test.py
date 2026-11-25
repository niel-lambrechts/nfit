#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/tournament_test.py
# PURPOSE  : The sizing tournament of nfit-forecast scores one quantity, the
#            profile requirement of an event occurrence (profile-requirement-1).
#            1. Target: the next stored occurrence of the event after the
#               candidates' data ends, matched by its dates and not by the
#               calendar month; the event's value on the profile basis, with
#               the hidden-demand increment added once where the occurrence was
#               clipped; observed value, increment and class kept apart; only
#               from results whose provenance is verified.
#            2. Participation: a candidate takes part when it states that its
#               output is the profile requirement and is frozen before its
#               target. Training on denser data does not disqualify; an output
#               that is another quantity, or not stated, is withheld and never
#               relabelled. Each model's reason is given.
#            3. Ledger: its own file per scoring semantics, the earlier ledger
#               left as it was; a month's contributions are replaced as a
#               whole, so a withdrawn candidate leaves no loss and a rebuild
#               repeats exactly.
#            4. End to end, where nfit-forecast's modules are installed: the
#               backfill states each candidate's output; event_multiplier
#               forecasts the profile requirement; the pool for selection and
#               the bootstrap holds profile-demand candidates only; the
#               read-only participation query writes nothing.
#            Blocks are extracted from ../nfit-forecast/nfit-forecast (or
#            $NFIT_FORECAST) and run in isolation.
# USAGE    : python3 test/tournament_test.py
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
import tempfile
import csv
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
FORECAST = os.environ.get('NFIT_FORECAST') or os.path.abspath(os.path.join(REPO, '..', 'nfit-forecast', 'nfit-forecast'))
import member_exposure_test as M

SOURCE = ''
if os.path.exists(FORECAST):
    with open(FORECAST, encoding='utf-8') as fh:
        SOURCE = fh.read()


def load():
    """The decoder/tournament block and the score ledger, run in isolation (None when absent)."""
    if 'Tournament quantity (profile-requirement-1)' not in SOURCE:
        return None
    start = SOURCE.rindex('# ====', 0, SOURCE.index('# Zone 2 record decoder (zone2-record-decoder-1)'))
    end = SOURCE.index('# --- End of Zone 2 record decoder ---')
    ns = {'os': os, 'json': json, 'sys': sys, 'datetime': datetime}
    exec(compile(SOURCE[start:end], FORECAST, 'exec'), ns)
    ledger = re.search(r'^SCORING_LOGIC_VERSION = .*?$', SOURCE, re.M).group(0) + '\n' + \
        re.search(r'^MODEL_COMPLEXITY_ORDER = \{.*?^\}', SOURCE, re.S | re.M).group(0) + '\n' + \
        re.search(r'^def calculate_pinball_loss\(.*?(?=^class NFitForecast)', SOURCE, re.S | re.M).group(0)
    exec(compile(ledger, FORECAST, 'exec'), ns)
    return ns


T = load()
V2 = {'version': 2, 'forecast': 'profile-demand'}
P1, P2 = 'G3-95W15', 'G2-98W10'


AHEAD = 'next occurrence after the last occurrence trained on'
# What an nfit-forecast candidate states of the occurrence it is for.
NAMED = {'data_end': '2026-09-12', 'target_start': '2026-10-01', 'target_date': '2026-10-02', 'target_basis': 'next occurrence after the data ends'}


def perl(origin, semantics=V2, names=None, basis=None, profiles='flags-1:p1', exclusions='NONE', **lpars):
    """A stored nfit-profile record: where its data ends, the occurrence it names, how that target was
    established, and the definitions it was made on."""
    meta = {'AnalysisContext': {'analysis_end': origin + 'T00:00:00'}}
    if profiles:
        meta['profile_definitions'] = profiles
    if exclusions:
        meta['exclusion_definitions'] = exclusions
    if names:
        meta['forecast_target_date'] = names
    if basis:
        meta['forecast_target_basis'] = basis
    if semantics:
        meta['semantics'] = dict(semantics)
    return dict({'_meta': meta}, **{vm: {'forecast': dict(f)} for vm, f in lpars.items()})


def occurrence(start, end, values, clipped=None, stamped=True, form='occurrence'):
    """A stored occurrence snapshot of the event: profile values, and an estimate of hidden demand where clipped."""
    results = {'HistoricPeak': copy.deepcopy(values), 'HistoricBaseline': copy.deepcopy(values)}
    if form == 'occurrence':
        results['OccurrencePeaks'] = [{'vm': vm, 'profile': p, 'occurrence_start': start, 'occurrence_end': end, 'raw_peak_value': v * 2}
                                      for vm, pv in values.items() for p, v in pv.items()]
    if clipped:
        results['ClippingInfo'] = {vm: {p: {'isClipped': 1, 'clippingConfidence': 0.9, 'unmetDemandEstimate': u} for p, u in pu.items()}
                                   for vm, pu in clipped.items()}
    snapshot = {'eventName': 'ev', 'periodEndDate': end, 'results': results}
    if stamped:
        snapshot['generatedOn'] = end + 'T06:00:00'
    return snapshot


def history(**months):
    return {month[1:].replace('_', '-'): {'SeasonalEventSnapshots': dict(events)} for month, events in months.items()}


@unittest.skipUnless(T, 'nfit-forecast source with the tournament block not found')
class TargetTests(unittest.TestCase):

    def test_the_target_is_the_next_occurrence_after_the_origin_whatever_the_month(self):
        values = {'vmA': {P1: 2.0}}
        raw = history(m2025_11={'bf': occurrence('2025-11-28', '2025-11-30', values)},
                      m2026_03={'ev': occurrence('2026-03-30', '2026-03-31', values)},
                      m2026_11={'bf': occurrence('2026-11-27', '2026-11-29', values), 'ev': occurrence('2026-11-28', '2026-11-30', values)},
                      m2026_12={'ev': {'eventName': 'ev', 'periodEndDate': '2026-12-31', 'ModelForecasts': {}}})     # no results: no target
        find = T['target_occurrence']
        # An annual event: the target of a forecast frozen in December 2025 is the occurrence a year on, not January.
        got = find(raw, 'bf', '2025-12-05')
        self.assertEqual((got['month'], got['start'], got['end']), ('2026-11', '2026-11-27', '2026-11-29'))
        # The occurrence that ended on the origin day is behind it; the next one is the target.
        self.assertEqual(find(raw, 'ev', '2026-03-31')['month'], '2026-11')
        self.assertEqual(find(raw, 'ev', '2026-03-30')['month'], '2026-03')
        self.assertIsNone(find(raw, 'ev', '2026-11-30'))                 # December holds no results
        self.assertIsNone(find(raw, 'ev', None))
        self.assertIsNone(find(raw, 'other', '2025-01-01'))

    def test_the_target_value_keeps_observed_increment_and_class_apart(self):
        snap = occurrence('2026-10-01', '2026-10-02', {'vmA': {P1: 2.0, P2: 3.0}}, clipped={'vmA': {P2: 0.4}})
        value = T['event_profile_target']
        self.assertEqual(value(snap, 'vmA', P1), {'target': 2.0, 'observed': 2.0, 'hiddenIncrement': 0.0, 'targetClass': 'observed'})
        got = value(snap, 'vmA', P2)
        self.assertEqual((got['target'], got['observed'], got['hiddenIncrement']), (3.4, 3.0, 0.4))      # added once, whole
        self.assertEqual(got['targetClass'], 'estimated (hidden demand added)')
        self.assertIsNone(value(snap, 'vmA', 'O1-99W5'))
        self.assertIsNone(value(snap, 'vmZ', P1))
        # The raw occurrence peak (twice the profile value here) is never the target.
        self.assertNotEqual(value(snap, 'vmA', P1)['target'], 4.0)
        # Results whose provenance is not verified give no target.
        self.assertIsNone(value(occurrence('2026-10-01', '2026-10-02', {'vmA': {P1: 2.0}}, stamped=False), 'vmA', P1))
        T['ZONE1_NOT_READ'].clear()


@unittest.skipUnless(T, 'nfit-forecast source with the tournament block not found')
class ParticipationTests(unittest.TestCase):

    def setUp(self):
        values = {'vmA': {P1: 2.0, P2: 3.0}, 'vmB': {P1: 1.0}}
        zone3 = {'periodEndDate': '2026-09-12', 'AdaptiveForecast': {
            'analysis_windows': {'vmA': {P1: {'start': '2026-06-01', 'end': '2026-09-12'}}},
            'candidates': {
                # Trained on dense weekly data, output stated as the profile requirement: takes part.
                'event_multiplier': {'vmA': {P1: dict(NAMED, forecast=2.2, pi_lower=1.9, pi_upper=2.5, output_quantity='profile-demand',
                                                      training_source='daily baseline x ratio')}},
                # The next value of a weekly series: another quantity.
                'holt': {'vmA': {P1: {'forecast': 5.0, 'output_quantity': 'weekly-raw-peak', 'training_source': 'DailyPeakSeries_weekly_max'}}},
                # Fitted to the event values themselves.
                'arima': {'vmA': {P1: dict(NAMED, forecast=2.1, output_quantity='profile-demand', training_source='MonthlyPeakValue')}},
                # States the profile requirement, but not which occurrence its projection is for.
                'svr': {'vmA': {P1: {'forecast': 2.0, 'output_quantity': 'profile-demand', 'training_source': 'MonthlyPeakValue'}}},
                # Written before outputs were stated.
                'huber': {'vmA': {P1: {'forecast': 2.4}}},
            }}}
        self.raw = history(
            m2026_09={'ev': {'eventName': 'ev', 'periodEndDate': '2026-09-12', 'generatedOn': '2026-09-13T00:00:00',
                             'results': {'HistoricPeak': values},
                             'ModelForecasts': {
                                 # States which occurrence its projection is for, and how that follows from the model.
                                 'predictive_peak': perl('2026-09-12', names='2026-10-02', basis=AHEAD, vmA={P1: 1.9, P2: 2.8}, vmB={P1: 1.1}),
                                 'multiplicative_seasonal': perl('2026-09-12', {'version': 1, 'forecast': 'model-demand'}, vmA={P1: 9.0}),
                                 # Its data runs into the occurrence it is for.
                                 'late_run': perl('2026-10-01', names='2026-10-02', basis=AHEAD, vmA={P1: 2.0}),
                                 # Names an occurrence its own data already covers (recency decay over a completed
                                 # period; any record written when the label was the period the analysis ended on).
                                 'recency_decay': perl('2026-09-12', names='2026-09-12', vmA={P1: 2.0}),
                                 # Names a later occurrence without saying how the projection comes to be for it.
                                 'unexplained': perl('2026-09-12', names='2026-10-02', vmA={P1: 2.0}),
                                 # Names nothing.
                                 'unnamed': perl('2026-09-12', vmA={P1: 2.0}),
                                 # Is for an occurrence other than the one stored next.
                                 'another': perl('2026-09-12', names='2026-11-02', basis=AHEAD, vmA={P1: 2.0}),
                             }},
                      'ad': zone3},
            m2026_10={'ev': occurrence('2026-10-01', '2026-10-02', values, clipped={'vmA': {P2: 0.4}})})

    def table(self):
        return T['tournament_candidates'](self.raw, '2026-09', 'ad', 'ev')

    def test_each_model_takes_part_or_is_withheld_for_a_stated_reason(self):
        t = self.table()
        self.assertEqual((t['target']['month'], t['target']['occurrenceStart'], t['target']['observationsUsable']), ('2026-10', '2026-10-01', True))
        status = {name: (row['status'], row['reason']) for name, row in t['models'].items()}
        self.assertEqual(status['predictive_peak'][0], 'participates')
        self.assertEqual(status['event_multiplier'][0], 'participates')           # dense training, profile-demand output
        self.assertEqual(status['arima'][0], 'participates')
        self.assertEqual(status['holt'], ('withheld', 'output is another quantity (weekly-raw-peak: 1)'))
        self.assertEqual(status['svr'], ('withheld', 'target not established: the candidates do not state which occurrence their projection is for'))
        self.assertEqual(status['huber'], ('withheld', 'output quantity not stated (written before outputs were stated)'))
        self.assertRegex(status['multiplicative_seasonal'][1], r'^semantics version 1 \(model-demand\)')
        self.assertEqual(status['late_run'], ('withheld', 'not frozen before its target (data ends 2026-10-01, target occurrence starts 2026-10-01)'))
        self.assertEqual(status['recency_decay'], ('withheld', 'names an occurrence its own data already covers (ends 2026-09-12; data ends 2026-09-12): not a forecast of a later occurrence'))
        # No target is assigned on a record's behalf.
        for name in ('unexplained', 'unnamed'):
            self.assertEqual(status[name], ('withheld', 'target not established: the record does not state which occurrence its projection is for'))
        self.assertEqual(status['another'], ('withheld', 'its target (occurrence ending 2026-11-02; %s) is not the occurrence in question (ends 2026-10-02)' % AHEAD))
        self.assertEqual(status['predictive_peak'][1], 'states profile-demand; frozen 2026-09-12, before the target occurrence it names (%s)' % AHEAD)
        self.assertEqual(sorted(t['admitted']), ['arima', 'event_multiplier', 'predictive_peak'])
        self.assertEqual(t['admitted']['predictive_peak'], {'vmA': {P1: 1.9, P2: 2.8}, 'vmB': {P1: 1.1}})
        self.assertEqual(t['models']['predictive_peak']['pairs'], 3)
        self.assertEqual(t['targets'][('vmA', P2)]['target'], 3.4)
        self.assertEqual(t['models']['holt']['trainingSources'], {'DailyPeakSeries_weekly_max': 1})
        text = T['format_tournament_participation']([t])
        self.assertIn('Target occurrence: 2026-10-01 to 2026-10-02, stored in 2026-10; observations verified', text)
        self.assertRegex(text, r'holt\s+Zone 3 \(nfit-forecast\)\s+ad\s+withheld\s+0  output is another quantity')
        for name in ('vmA', 'vmB'):
            self.assertNotIn(name, text)                                           # counts only: no LPAR is named

    def test_records_of_a_second_event_compete_only_on_the_same_definitions(self):
        snaps = self.raw['2026-09']['SeasonalEventSnapshots']
        same = perl('2026-09-12', names='2026-10-02', basis='next occurrence after the data ends', vmA={P1: 2.3})
        snaps['strategic'] = {'eventName': 'strategic', 'ModelForecasts': {
            'multiplicative_seasonal': same,
            # The same model as the source event stores: told apart by the event it is stored under.
            'predictive_peak': perl('2026-09-12', names='2026-10-02', basis=AHEAD, vmA={P1: 7.0}),
            'other_profiles': perl('2026-09-12', names='2026-10-02', basis=AHEAD, profiles='flags-1:p2', vmA={P1: 2.0}),
            # Written before the fingerprint covered the flags: a hash of the names alone is no statement.
            'names_only': perl('2026-09-12', names='2026-10-02', basis=AHEAD, profiles='p1', vmA={P1: 2.0}),
            'other_exclusions': perl('2026-09-12', names='2026-10-02', basis=AHEAD, exclusions='x9', vmA={P1: 2.0}),
            'unstated': perl('2026-09-12', names='2026-10-02', basis=AHEAD, profiles=None, vmA={P1: 2.0}),
            'other_occurrence': perl('2026-09-12', names='2026-10-09', basis=AHEAD, vmA={P1: 2.0})}}
        # By default the source event alone supplies records.
        self.assertNotIn('other_profiles', self.table()['models'])
        t = T['tournament_candidates'](self.raw, '2026-09', 'ad', 'ev', ['ev', 'strategic'])
        rows = t['models']
        # The source event's own version-1 record and the second event's current one are two candidates.
        self.assertEqual((rows['multiplicative_seasonal']['sourceEvent'], rows['multiplicative_seasonal']['status']), ('ev', 'withheld'))
        self.assertEqual((rows['multiplicative_seasonal@strategic']['sourceEvent'], rows['multiplicative_seasonal@strategic']['status']),
                         ('strategic', 'participates'))
        self.assertEqual(t['admitted']['multiplicative_seasonal@strategic'], {'vmA': {P1: 2.3}})
        self.assertEqual(rows['predictive_peak@strategic']['status'], 'participates')
        self.assertEqual(rows['other_profiles']['reason'], "profile definitions differ from those of the source event 'ev'")
        self.assertEqual(rows['other_exclusions']['reason'], "exclusions differ from those of the source event 'ev'")
        self.assertEqual(rows['unstated']['reason'], "profile definitions not stated by the record or by the source event's record")
        self.assertEqual(rows['names_only']['reason'], "profile definitions not stated by the record or by the source event's record")
        self.assertIsNone(T['zone2_record_contract'](perl('2026-09-12', profiles='0123abcd'))['profileDefinitions'])
        self.assertEqual(T['zone2_record_contract'](perl('2026-09-12'))['profileDefinitions'], 'flags-1:p1')
        self.assertRegex(rows['other_occurrence']['reason'], r'^its target \(occurrence ending 2026-10-09; .*\) is not the occurrence in question')
        self.assertIn('Records of: ev, strategic (observed occurrences: ev)', T['format_tournament_participation']([t]))
        # Without a usable record of the source event there is nothing to match the definitions against.
        snaps['ev']['ModelForecasts'] = {}
        t = T['tournament_candidates'](self.raw, '2026-09', 'ad', 'ev', ['ev', 'strategic'])
        self.assertEqual(t['models']['multiplicative_seasonal']['reason'], "no usable record of the source event 'ev' to match its definitions against")
        self.assertEqual(T['candidate_source_events']({'source_event': 'ev', 'candidate_sources': 'strategic, ev ,x'}, 'ad'), ('ev', ['ev', 'strategic', 'x']))
        self.assertEqual(T['candidate_source_events']({'source_event': 'ev'}, 'ad'), ('ev', ['ev']))
        self.assertEqual(T['candidate_source_events']({}, 'ev'), ('ev', ['ev']))

    def test_the_pool_for_selection_is_one_month_and_one_occurrence(self):
        # An earlier month holds a model the latest month does not: it is not carried forward.
        self.raw['2026-08'] = {'SeasonalEventSnapshots': {'ev': {'eventName': 'ev', 'ModelForecasts': {
            'old_model': perl('2026-08-12', names='2026-09-02', basis=AHEAD, vmA={P1: 9.9})}}}}
        pool = T['tournament_live_pool'](self.raw, 'ad', 'ev')
        self.assertEqual(pool['forecastMonth'], '2026-09')
        self.assertNotIn('old_model', pool['models'])
        # The occurrence ahead is the one the source event's record names; here history already holds it.
        self.assertEqual(pool['target'], {'occurrenceStart': '2026-10-01', 'occurrenceEnd': '2026-10-02', 'observed': True})
        chosen_from = pool['pool']['vmA'][P1]
        self.assertEqual(sorted(chosen_from), ['arima', 'event_multiplier', 'predictive_peak'])
        self.assertEqual(chosen_from['predictive_peak'], 1.9)
        # An nfit-forecast candidate is handed over whole: its own interval and what it states travel with it.
        self.assertEqual((chosen_from['event_multiplier']['pi_lower'], chosen_from['event_multiplier']['pi_upper'],
                          chosen_from['event_multiplier']['target_date']), (1.9, 2.5, '2026-10-02'))
        self.assertEqual(pool['models']['holt']['status'], 'withheld')
        self.assertEqual(pool['models']['recency_decay']['status'], 'withheld')
        del self.raw['2026-10']
        self.assertFalse(T['tournament_live_pool'](self.raw, 'ad', 'ev')['target']['observed'])
        self.assertIn('Occurrence ahead: 2026-10-01 to 2026-10-02\n', T['format_tournament_live_pool'](T['tournament_live_pool'](self.raw, 'ad', 'ev')))
        self.assertEqual(T['tournament_live_pool']({}, 'ad', 'ev')['pool'], {})

    def test_the_pool_states_its_data_end_definitions_and_an_observed_target_only_from_observations(self):
        pool = T['tournament_live_pool'](self.raw, 'ad', 'ev')
        self.assertEqual(pool['dataEnds'], '2026-09-12')
        self.assertEqual(pool['definitions'], {'profileDefinitions': 'flags-1:p1', 'exclusions': 'NONE'})
        self.assertTrue(pool['target']['observed'])
        # October's results without the generator's stamp are no observation: the occurrence is not observed.
        self.raw['2026-10']['SeasonalEventSnapshots']['ev'].pop('generatedOn')
        self.assertFalse(T['tournament_live_pool'](self.raw, 'ad', 'ev')['target']['observed'])
        T['ZONE1_NOT_READ'].clear()

    def test_the_pool_is_completed_with_the_live_multiplier_before_selection(self):
        complete, resolve_all = T['complete_live_pool'], T['resolve_pool_selections']
        pool = T['tournament_live_pool'](self.raw, 'ad', 'ev')
        live = {'vmA': {P1: {'forecast': 9.0, 'target_start': '2026-10-01', 'target_date': '2026-10-02', 'data_end': '2026-09-20'}},
                'vmB': {P1: {'forecast': 1.4, 'pi_lower': 1.2, 'pi_upper': 1.6, 'target_start': '2026-10-01', 'target_date': '2026-10-02',
                             'data_end': '2026-09-20', 'evidence_penalty': 0.25}},
                'vmC': {P1: {'forecast': 2.2, 'target_start': '2026-10-08', 'target_date': '2026-10-09'}},       # another occurrence
                'vmD': {P1: {'forecast': None, 'target_start': '2026-10-01', 'target_date': '2026-10-02'}}}
        added = complete(pool, live)
        # vmA already holds a stored event_multiplier: it is not replaced. vmB lacks one: this run's joins, whole.
        self.assertNotEqual(pool['pool']['vmA'][P1]['event_multiplier'], 9.0)
        self.assertEqual(added, {('vmB', P1): ['event_multiplier']})
        self.assertEqual(pool['pool']['vmB'][P1]['event_multiplier']['pi_upper'], 1.6)
        self.assertNotIn('vmC', pool['pool'])
        self.assertNotIn('vmD', pool['pool'])
        self.assertEqual(pool['dataEnds'], '2026-09-20')
        # One resolution per pair, for the pool's pairs and any others asked for; the ledger is called once per pair.
        calls = []

        def select(vm, profile, names, penalty):
            calls.append((vm, profile, tuple(sorted(names)), penalty))
            return {'selection_mode': 'tournament', 'chosen_model': 'event_multiplier', 'rationale': 'r'}
        got = resolve_all(pool, select, pairs=[('vmZ', P1)])
        self.assertEqual(got['vmB'][P1]['resolved'], {'model': 'event_multiplier', 'mode': 'tournament', 'forecast': 1.4,
                                                     'pi_lower': 1.2, 'pi_upper': 1.6, 'rationale': 'r'})
        self.assertIn(('vmB', P1, tuple(sorted(pool['pool']['vmB'][P1])), 0.25), calls)
        self.assertEqual(got['vmZ'][P1]['resolved']['mode'], 'no eligible forecast')
        self.assertEqual(len(calls), len({(c[0], c[1]) for c in calls}))
        self.assertNotIn(('vmZ', P1), [(c[0], c[1]) for c in calls])
        # A pool that states no occurrence takes the live forecasts only when they all state the same one.
        empty = {'pool': {}, 'target': None, 'dataEnds': None}
        self.assertEqual(complete(empty, {'vmA': live['vmA'], 'vmC': live['vmC']}), {})
        self.assertIsNone(empty['target'])
        self.assertEqual(complete(empty, {'vmA': live['vmA']}), {('vmA', P1): ['event_multiplier']})
        self.assertEqual(empty['target'], {'occurrenceStart': '2026-10-01', 'occurrenceEnd': '2026-10-02', 'observed': False})

    def test_the_next_occurrence_follows_from_the_event_definition(self):
        nxt = T['next_occurrence_after']
        monthly = {'period': 'monthly', 'day_of_period': '-1', 'duration_days': '7'}
        self.assertEqual(nxt(monthly, '2026-10-31'), ('2026-11-24', '2026-11-30'))
        self.assertEqual(nxt(monthly, '2026-10-24'), ('2026-10-25', '2026-10-31'))        # begins after the day
        self.assertEqual(nxt(monthly, '2026-10-25'), ('2026-11-24', '2026-11-30'))        # one under way is not ahead
        self.assertEqual(nxt(monthly, '2026-12-31'), ('2027-01-25', '2027-01-31'))
        self.assertEqual(nxt({'period': 'monthly', 'day_of_period': '31', 'duration_days': '3'}, '2027-01-31'), ('2027-02-26', '2027-02-28'))
        dates = {'dates': '2025-11-21:2025-11-28, 2026-11-20:2026-11-27'}
        self.assertEqual(nxt(dates, '2025-11-28'), ('2026-11-20', '2026-11-27'))
        self.assertEqual(nxt(dates, '2026-11-20'), (None, None))
        self.assertEqual(nxt({}, '2026-01-01'), (None, None))
        self.assertEqual(nxt(monthly, None), (None, None))

    def test_nothing_is_admitted_without_a_verified_target(self):
        del self.raw['2026-10']['SeasonalEventSnapshots']['ev']['generatedOn']
        t = self.table()
        self.assertEqual(t['admitted'], {})
        self.assertRegex(t['models']['predictive_peak']['reason'], r'^target results are not verified observations')
        T['ZONE1_NOT_READ'].clear()
        # Nor when the day the target occurrence began is not stored: no candidate can be shown to precede it.
        self.raw['2026-10']['SeasonalEventSnapshots']['ev'] = occurrence('2026-10-01', '2026-10-02', {'vmA': {P1: 2.0}}, form='plain')
        t = self.table()
        self.assertEqual(t['admitted'], {})
        self.assertRegex(t['models']['predictive_peak']['reason'], r'^the start of the target occurrence is not stored')
        del self.raw['2026-10']
        t = self.table()
        self.assertIsNone(t['target'])
        self.assertEqual(t['models']['predictive_peak']['reason'], 'no later occurrence of the event with results in history')
        self.assertEqual(t['admitted'], {})

    def test_a_series_states_what_its_next_value_is(self):
        quantity = T['output_quantity_of_training_source']
        self.assertEqual(quantity('MonthlyPeakValue'), 'profile-demand')
        self.assertEqual(quantity('DailyPeakSeries_weekly_max'), 'weekly-raw-peak')
        self.assertEqual(quantity('DailyProfileSeries_weekly_max'), 'weekly-profile-maximum')
        self.assertIsNone(quantity('something else'))


@unittest.skipUnless(T, 'nfit-forecast source with the tournament block not found')
class LedgerTests(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='ledger-')
        self.addCleanup(lambda: shutil.rmtree(self.folder, ignore_errors=True))
        self.old = os.path.join(self.folder, 'nfit.scores.json')
        with open(self.old, 'w') as fh:
            fh.write('{"meta": {"schema_version": "2.0"}, "scores": {"vmA": {"earlier": "ledger"}}}')

    def score(self, ledger, month, forecasts, target=2.0):
        """One month scored as the scoring task does it: the month is taken out first, then recorded afresh."""
        ledger.prune_month_contributions(month, 'ev')
        for model, value in forecasts.items():
            ledger.record_observation('vmA', P1, 'ev', model, target, value, 0.95, month,
                                      target_detail={'target': target, 'observed': target - 0.5, 'hiddenIncrement': 0.5,
                                                     'targetClass': 'estimated (hidden demand added)'})

    def content(self, ledger):
        data = copy.deepcopy(ledger.load())
        for key in ('created_on', 'last_updated'):
            data['meta'].pop(key, None)
        return data

    def test_a_selection_reports_the_chosen_candidates_own_figures_or_nothing(self):
        resolve = T['resolve_selection']
        median = {'selection_mode': 'bootstrap', 'chosen_model': 'ensemble_median', 'rationale': 'r'}
        # No candidate in the pool: nothing is reported, whatever the selection says. (The forecast of the
        # synthesised weekly ensemble used to stay in the row under the name ensemble_median.)
        for pool in ({}, None, {'holt': None}):
            got = resolve(median, pool, {})
            self.assertEqual((got['model'], got['mode'], got['forecast'], got['pi_lower'], got['pi_upper']), ('', 'no eligible forecast', None, None, None))
            self.assertRegex(got['rationale'], r'^No eligible forecast')
        # The median of the pool: no interval, even where candidates have their own.
        got = resolve(median, {'a': 1.0, 'b': 3.0, 'c': 10.0}, {'a': (0.5, 1.5)})
        self.assertEqual((got['model'], got['mode'], got['forecast'], got['pi_lower'], got['pi_upper']), ('ensemble_median', 'bootstrap', 3.0, None, None))
        self.assertEqual(resolve(median, {'a': 1.0, 'b': 3.0}, {})['forecast'], 2.0)
        # A chosen model: its own forecast and its own interval, or no interval.
        for mode in ('tournament', 'unopposed'):
            chosen = {'selection_mode': mode, 'chosen_model': 'a', 'rationale': 'r'}
            got = resolve(chosen, {'a': 1.0, 'b': 3.0}, {'a': (0.5, 1.5), 'b': (2.0, 4.0)})
            self.assertEqual((got['model'], got['mode'], got['forecast'], got['pi_lower'], got['pi_upper']), ('a', mode, 1.0, 0.5, 1.5))
            got = resolve(chosen, {'a': 1.0, 'b': 3.0}, {'b': (2.0, 4.0)})
            self.assertEqual((got['forecast'], got['pi_lower'], got['pi_upper']), (1.0, None, None))
            self.assertEqual(resolve(chosen, {'b': 3.0}, {})['mode'], 'no eligible forecast')

    def test_a_single_qualified_model_is_chosen_and_said_to_be_unopposed(self):
        ledger = T['ScoreLedger'](self.folder)
        for index in range(5):
            month = '2026-%02d' % (index + 1)
            ledger.record_observation('vmA', P1, 'ev', 'event_multiplier', 2.0, 1.9, 0.95, month)
            if index < 2:
                ledger.record_observation('vmA', P1, 'ev', 'predictive_peak', 2.0, 2.0, 0.95, month)
        got = ledger.select_best_model('vmA', P1, 'ev', ['event_multiplier', 'predictive_peak'])
        self.assertEqual((got['selection_mode'], got['chosen_model'], got['observations']), ('unopposed', 'event_multiplier', 5))
        self.assertRegex(got['rationale'], r'^Single qualified model: event_multiplier \(avg pinball loss \d+\.\d{4} over 5 observations\)\. '
                                           r'Comparative superiority not established\.$')
        # Nothing was measured against it: no credit for a margin or for a field.
        self.assertEqual((got['confidence_components']['margin_score'], got['confidence_components']['field_score']), (0.0, 0.0))
        # With a second qualified model it is a tournament.
        for index in range(2, 5):
            ledger.record_observation('vmA', P1, 'ev', 'predictive_peak', 2.0, 2.0, 0.95, '2026-%02d' % (index + 1))
        got = ledger.select_best_model('vmA', P1, 'ev', ['event_multiplier', 'predictive_peak'])
        self.assertEqual((got['selection_mode'], got['chosen_model']), ('tournament', 'predictive_peak'))
        self.assertEqual(got['confidence_components']['field_score'], 10.0)
        # None qualified: the median of the pool.
        self.assertEqual(ledger.select_best_model('vmA', P1, 'ev', ['holt'])['selection_mode'], 'bootstrap')

    def test_the_new_ledger_is_its_own_file_and_the_earlier_one_is_left_alone(self):
        with open(self.old, 'rb') as fh:
            before = fh.read()
        ledger = T['ScoreLedger'](self.folder)
        self.assertEqual(os.path.basename(ledger.scores_file), 'nfit.scores.profile-requirement-1.json')
        self.score(ledger, '2026-08', {'predictive_peak': 1.8})
        ledger.persist()
        with open(self.old, 'rb') as fh:
            self.assertEqual(fh.read(), before)
        with open(ledger.scores_file) as fh:
            stored = json.load(fh)
        self.assertEqual((stored['meta']['semantics'], stored['meta']['quantity']), ('profile-requirement-1', 'profile-demand'))
        entry = stored['scores']['vmA'][P1]['ev']['models']['predictive_peak']['monthly_scores']['2026-08']
        self.assertEqual((entry['target'], entry['observed'], entry['hidden_increment'], entry['target_class']),
                         (2.0, 1.5, 0.5, 'estimated (hidden demand added)'))

    def test_a_withdrawn_candidate_leaves_no_loss_and_a_rebuild_repeats(self):
        ledger = T['ScoreLedger'](self.folder)
        months = {'2026-07': {'predictive_peak': 1.8, 'holt': 5.0}, '2026-08': {'predictive_peak': 2.1, 'holt': 5.5}}
        for month, forecasts in months.items():
            self.score(ledger, month, forecasts)
        models = ledger.load()['scores']['vmA'][P1]['ev']['models']
        self.assertEqual((models['holt']['observations'], models['predictive_peak']['observations']), (2, 2))
        first = self.content(ledger)
        # The same history scored again: the same ledger.
        for month, forecasts in months.items():
            self.score(ledger, month, forecasts)
        self.assertEqual(self.content(ledger), first)
        # holt is no longer admitted for August: its August loss goes, July's stays, the totals follow.
        self.score(ledger, '2026-08', {'predictive_peak': 2.1})
        models = ledger.load()['scores']['vmA'][P1]['ev']['models']
        self.assertEqual(sorted(models['holt']['monthly_scores']), ['2026-07'])
        self.assertEqual((models['holt']['observations'], models['holt']['last_scored_month']), (1, '2026-07'))
        self.assertAlmostEqual(models['holt']['pinball_loss_sum'], models['holt']['monthly_scores']['2026-07']['pinball_loss'])
        # Withdrawn for July as well: the model leaves the ledger; the other is untouched.
        self.score(ledger, '2026-07', {'predictive_peak': 1.8})
        models = ledger.load()['scores']['vmA'][P1]['ev']['models']
        self.assertEqual(sorted(models), ['predictive_peak'])
        self.assertEqual(models['predictive_peak'], first['scores']['vmA'][P1]['ev']['models']['predictive_peak'])
        # A month scored with nobody admitted leaves nothing of that month.
        self.score(ledger, '2026-07', {})
        self.score(ledger, '2026-08', {})
        self.assertEqual(ledger.load()['scores'], {})
        # Another event's contributions for the same month are not touched by an event's prune.
        ledger.record_observation('vmA', P1, 'bf', 'holt', 2.0, 1.0, 0.95, '2026-08')
        self.assertEqual(ledger.prune_month_contributions('2026-08', 'ev'), 0)
        self.assertEqual(ledger.prune_month_contributions('2026-08', 'bf'), 1)


RUNTIME = all(importlib.util.find_spec(m) for m in ('numpy', 'pandas', 'scipy', 'statsmodels', 'sklearn', 'ruptures', 'pytdigest'))


@unittest.skipUnless(T and M.perl_ready() and RUNTIME, "nfit-forecast, perl with JSON or nfit-forecast's Python modules unavailable")
class EndToEndTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import profile_demand_test as P
        cls.P = P
        M.MemberExposureTests.roots = []
        # History: level months, occurrences in July, August and September at 1.2 B (raw peaks at 1.5 B), a
        # current record of two nfit-profile models in September, and the October occurrence at 1.3 B as the target.
        # The September occurrence is in history before the models run (as after a history update), so the trend is
        # fitted through it and its projection is for October. Without it the projection would be for September itself.
        cls.case = P.Case('occurrence', peak={P.PROFILE: 1.2}, models=('predictive_peak', 'multiplicative_seasonal'), later_peak=1.3,
                          target_event={'eventName': 'ev', 'periodEndDate': '2026-09-12', 'generatedOn': '2026-09-13T00:00:00'})
        # For nfit-forecast the event has one more occurrence, in November: the one ahead of all stored data.
        path = os.path.join(cls.case.root, 'etc', 'nfit.seasonality.cfg')
        with open(path) as fh:
            text = fh.read()
        assert text.count(P.NEXT_DATES) == 1
        with open(path, 'w') as fh:
            # (and September one occurrence, the one stored: a backfill takes the first occurrence of a month)
            fh.write(text.replace(P.NEXT_DATES, P.NEXT_DATES + ', 2026-11-01:2026-11-01').replace('2026-09-01:2026-09-01, ', ''))
        with open(os.path.join(cls.case.root, 'etc', 'nfit.seasonality.cfg'), 'a') as fh:
            fh.write('\n[ad]\nmodel = adaptive_peak\nsource_event = ev\npriority = 10\ndates = %s, 2026-10-01:2026-10-01\n'
                     'baseline_period_days = 8\nmin_historical_years = 0\nmin_historical_peaks = 1\nmin_historical_periods = 1\n' % P.DATES)
        shutil.copy(FORECAST, os.path.join(cls.case.root, 'nfit-forecast'))
        cls.old = os.path.join(cls.case.folder, 'nfit.scores.json')
        with open(cls.old, 'w') as fh:
            fh.write('{"meta": {"schema_version": "2.0"}, "scores": {}}')
        cls.ledger = os.path.join(cls.case.folder, 'nfit.scores.profile-requirement-1.json')

    @classmethod
    def tearDownClass(cls):
        M.MemberExposureTests.tearDownClass()

    def run_forecast(self, *args):
        run = subprocess.run([sys.executable, '-W', 'ignore', os.path.join(self.case.root, 'nfit-forecast'), '--nmondir',
                              os.path.join(self.case.root, 'stage', M.SERIAL)] + list(args),
                             capture_output=True, text=True, timeout=160, env=dict(os.environ, PYTHONWARNINGS='ignore'))
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-1500:])
        return run

    def ledger_content(self):
        with open(self.ledger) as fh:
            data = json.load(fh)
        for key in ('created_on', 'last_updated'):
            data['meta'].pop(key, None)
        return data

    def files(self):
        found = {}
        for path in glob.glob(os.path.join(self.case.folder, '*')):
            with open(path, 'rb') as fh:
                found[os.path.basename(path)] = fh.read()
        return found

    def test_the_tournament_end_to_end(self):
        P, case = self.P, self.case
        # -- the read-only query before anything is scored: nothing is written
        before = self.files()
        query = self.run_forecast('--participation-query', '--seasonal-event', 'ad')
        self.assertEqual(self.files(), before)
        self.assertIn('candidates stored in 2026-09 (data ends 2026-09-12); quantity scored: profile-demand (profile-requirement-1)', query.stdout)
        self.assertIn('Target occurrence: 2026-10-01 to 2026-10-01, stored in 2026-10; observations verified', query.stdout)
        self.assertRegex(query.stdout, r'predictive_peak\s+Zone 2 \(nfit-profile\)\s+ev\s+participates\s+\d+  states profile-demand; frozen 2026-09-12')
        for name in P.LPARS + (M.SERIAL,):
            self.assertNotIn(name, query.stdout)

        # The records say which occurrence they are for: the trend was fitted through September's occurrence.
        meta = case.records['predictive_peak']['_meta']
        self.assertEqual((meta['last_trained_occurrence_end'], meta['forecast_target_date']), ('2026-09-12', '2026-10-01'))
        self.assertEqual(case.records['multiplicative_seasonal']['_meta']['forecast_target_date'], '2026-10-01')

        # -- backfill, scoring and selection
        run = self.run_forecast('--seasonal-event', 'ad', '--tournament')
        text = run.stdout + run.stderr
        with open(self.old) as fh:
            self.assertEqual(fh.read(), '{"meta": {"schema_version": "2.0"}, "scores": {}}')      # the earlier ledger: untouched
        snaps = {}
        for path in sorted(glob.glob(os.path.join(case.folder, 'nfit.hist.*.json'))):
            with open(path) as fh:
                for month, data in json.load(fh).items():
                    snaps[month] = data.get('SeasonalEventSnapshots') or {}
        candidates = snaps['2026-09']['ad']['AdaptiveForecast']['candidates']
        # Every candidate states its output and what it was fitted to.
        for model, by_vm in candidates.items():
            for by_profile in by_vm.values():
                for cand in by_profile.values():
                    self.assertIn('output_quantity', cand, model)
                    self.assertIn('training_source', cand, model)
        # event_multiplier forecasts the profile requirement: baseline x 1.2, not the raw-peak ratio of 1.5.
        em = candidates['event_multiplier']['vmS'][P.PROFILE]
        self.assertEqual(em['output_quantity'], 'profile-demand')
        self.assertAlmostEqual(em['forecast'], 1.2 * case.values['vmS'][P.PROFILE], delta=0.01)
        # A direct model fitted to the weekly series states that series' quantity and is not relabelled.
        direct = candidates['holt']['vmS'][P.PROFILE]
        self.assertIn(direct['output_quantity'], ('weekly-profile-maximum', 'weekly-raw-peak'))
        self.assertNotIn('ahead', direct)
        # Beside it, under its own name, the event-profile candidate derived from it: the model's forecast of the
        # week before October's occurrence (three weeks on from the last week of data) times the ratio of past
        # event values to the same series in the week before each of them. Here that ratio is 1.2.
        derived = candidates['holt_event']['vmS'][P.PROFILE]
        self.assertEqual((derived['derived_from'], derived['output_quantity'], derived['steps_ahead'], derived['background_week_end']),
                         ('holt', 'profile-demand', 3, '2026-09-27'))
        self.assertEqual((derived['data_end'], derived['target_start'], derived['target_date']), ('2026-09-12', '2026-10-01', '2026-10-01'))
        self.assertAlmostEqual(derived['forecast'], derived['background_forecast'] * derived['multiplier'], places=6)
        self.assertAlmostEqual(derived['multiplier'], 1.2, delta=0.02)
        self.assertGreaterEqual(derived['multiplier_n'], 1)
        # The pool for selection is that of the latest month, for the occurrence its candidates name; the weekly outputs stay out.
        # Here that is October, whose candidates are for November's occurrence, which no history holds yet.
        self.assertIn('ad, candidates for selection: those stored in 2026-10; quantity: profile-demand (profile-requirement-1)', text)
        self.assertIn('Occurrence ahead: 2026-11-01 to 2026-11-01\n', text)
        self.assertRegex(text, r'event_multiplier\s+Zone 3 \(nfit-forecast\)\s+ad\s+participates')
        self.assertRegex(text, r'holt\s+Zone 3 \(nfit-forecast\)\s+ad\s+withheld')
        october = snaps['2026-10']['ad']['AdaptiveForecast']['candidates']['event_multiplier']['vmS'][P.PROFILE]
        self.assertEqual((october['data_end'], october['target_start'], october['target_date']), ('2026-10-01', '2026-11-01', '2026-11-01'))
        self.assertEqual((em['data_end'], em['target_start'], em['target_date']), ('2026-09-12', '2026-10-01', '2026-10-01'))
        self.assertEqual(em['horizon_days'], 19)                                   # the baseline is projected to that occurrence
        # -- one selection, stored once and read alike by the CSV
        # (The backfill leaves an empty selection placeholder in every month: the block is found by its meta.)
        holders = [m for m in sorted(snaps) if 'selection_meta' in ((snaps[m].get('ad') or {}).get('AdaptiveForecast') or {})]
        self.assertEqual(len(holders), 1, holders)
        adaptive = snaps[holders[0]]['ad']['AdaptiveForecast']
        meta = adaptive['selection_meta']
        self.assertEqual((meta['semantics'], meta['quantity'], meta['event'], meta['source_event'], meta['written_to'], meta['candidates_stored_in']),
                         ('selected-demand-1', 'profile-demand', 'ad', 'ev', holders[0], '2026-10'))
        self.assertEqual(meta['target_occurrence'], {'occurrenceStart': '2026-11-01', 'occurrenceEnd': '2026-11-01', 'observed': False})
        self.assertEqual(meta['data_ends'], '2026-10-01')
        selection = adaptive['selection']
        for by_profile in selection.values():
            for entry in by_profile.values():
                self.assertEqual((entry['candidates_stored_in'], entry['target_occurrence']), ('2026-10', meta['target_occurrence']))
        csvs = sorted(glob.glob(os.path.join(case.root, 'output', 'nfit-forecast.*.ad.*.csv')), key=os.path.getmtime)
        self.assertTrue(csvs)
        with open(csvs[-1], newline='') as fh:
            rows = list(csv.DictReader(fh))
        vm_key = next(k for k in rows[0] if k in ('VM', 'VMName', 'VM_Name', 'vm_name'))
        compared = 0
        for row in rows:
            for profile, entry in (selection.get(row[vm_key]) or {}).items():
                if '%s_Mode' % profile not in row:
                    continue
                self.assertEqual((row['%s_Mode' % profile], row['%s_Model' % profile]), (entry['selection_mode'], entry['chosen_model']))
                if entry['forecast'] is None:
                    self.assertIn(row['%s_Forecast' % profile], ('', 'None', 'nan'))
                else:
                    self.assertAlmostEqual(float(row['%s_Forecast' % profile]), entry['forecast'], places=3)
                compared += 1
        self.assertGreater(compared, 0)
        # It states the definitions as nfit-profile states them: those of the source event's own record.
        record_meta = case.records['predictive_peak']['_meta']
        self.assertEqual((meta['profile_definitions'], meta['exclusion_definitions']),
                         (record_meta['profile_definitions'], record_meta['exclusion_definitions']))
        # nfit-profile reads the block nfit-forecast wrote. The selection is for November (the candidates of
        # October); the source model of an adaptive run on this data (ending in September) forecasts an earlier
        # occurrence: every check before that one passes, and the selection is withheld on it, whole, with
        # nothing stored.
        config = os.path.join(case.root, 'etc', 'nfit.seasonality.cfg')
        with open(config) as fh:
            text = fh.read()
        # The source event was last configured for the multiplicative model: an adaptive run refuses it.
        refused = subprocess.run(['perl', os.path.join(case.root, 'nfit-profile'), '--nmondir', os.path.join(case.root, 'stage', M.SERIAL),
                                  '--apply-seasonality', 'ad'], capture_output=True, text=True, cwd=case.root, timeout=120)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("The source event 'ev' of 'ad' uses model 'multiplicative_seasonal'; an adaptive run sizes through a predictive_peak source event only.",
                      refused.stderr)
        head, tail = text.split('[ad]', 1)
        with open(config, 'w') as fh:
            fh.write(head.replace('model = multiplicative_seasonal', 'model = predictive_peak') + '[ad]' + tail)
        before = self.files()
        sized = M.MemberExposureTests.run_profile(case.root, '--apply-seasonality', 'ad')
        self.assertRegex(sized.stderr, r'Selected forecast not used: it is for the occurrence 2026-11-01 to 2026-11-01; this run sizes 2026-\d\d-\d\d to ')
        self.assertEqual({k: v for k, v in self.files().items() if k.startswith('nfit.hist.')},
                         {k: v for k, v in before.items() if k.startswith('nfit.hist.')})

        # The stored scoring block says who took part against which occurrence.
        scoring = snaps['2026-09']['ad']['AdaptiveForecast']['scoring']
        self.assertEqual((scoring['_meta']['scoring_semantics'], scoring['_meta']['scored_against_month']), ('profile-requirement-1', '2026-10'))
        part = scoring['participation']
        self.assertEqual(part['holt_event']['status'], 'participates')
        self.assertEqual({m: part[m]['status'] for m in ('predictive_peak', 'multiplicative_seasonal', 'event_multiplier', 'holt')},
                         {'predictive_peak': 'participates', 'multiplicative_seasonal': 'participates',
                          'event_multiplier': 'participates', 'holt': 'withheld'})
        # The ledger: the target is the October value on the profile basis, 1.3 B, for the admitted models only.
        first = self.ledger_content()
        models = first['scores']['vmS'][P.PROFILE]['ad']['models']
        self.assertEqual(sorted(m for m in models if not m.endswith('_event')), ['event_multiplier', 'multiplicative_seasonal', 'predictive_peak'])
        self.assertIn('holt_event', models)
        entry = models['predictive_peak']['monthly_scores']['2026-09']
        self.assertAlmostEqual(entry['target'], 1.3 * case.values['vmS'][P.PROFILE], places=3)
        self.assertEqual(entry['target_class'], 'observed')

        # -- a rebuild repeats
        self.run_forecast('--seasonal-event', 'ad', '--tournament', '--force-rescore')
        self.assertEqual(self.ledger_content(), first)

        # -- a withdrawn record leaves no loss behind
        path = os.path.join(case.folder, 'nfit.hist.2026-09.json')
        with open(path) as fh:
            september = json.load(fh)
        del september['2026-09']['SeasonalEventSnapshots']['ev']['ModelForecasts']['predictive_peak']
        with open(path, 'w') as fh:
            json.dump(september, fh)
        self.run_forecast('--seasonal-event', 'ad', '--tournament', '--force-rescore')
        after = self.ledger_content()
        models = after['scores']['vmS'][P.PROFILE]['ad']['models']
        self.assertEqual(sorted(m for m in models if not m.endswith('_event')), ['event_multiplier', 'multiplicative_seasonal'])
        self.assertEqual(models['multiplicative_seasonal'], first['scores']['vmS'][P.PROFILE]['ad']['models']['multiplicative_seasonal'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
