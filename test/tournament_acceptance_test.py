#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/tournament_acceptance_test.py
# PURPOSE  : dev/tournament-acceptance runs the sizing tournament of one event
#            on a disposable copy of a kept work tree and reports in counts.
#            1. Its parts: the four participation groups, the candidate-source
#               setting written into the copy's seasonality file, and the check
#               that a selection carries the chosen candidate's own forecast and
#               interval.
#            2. End to end on a small kept tree (where nfit-forecast's modules
#               are installed): the kept tree is left as found, the working copy
#               is removed, the ledger rebuilt from nothing is identical, and no
#               LPAR or system is named.
# USAGE    : python3 test/tournament_acceptance_test.py
# ==============================================================================
import csv
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
TOOL = os.path.join(REPO, 'dev', 'tournament-acceptance')
import member_exposure_test as M
import tournament_test as TT


def tool():
    loader = importlib.machinery.SourceFileLoader('tournament_acceptance', TOOL)
    spec = importlib.util.spec_from_loader('tournament_acceptance', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


A = tool()


class PartsTests(unittest.TestCase):

    def test_each_reason_falls_in_one_of_four_groups(self):
        group = lambda status, reason: A.group_of({'status': status, 'reason': reason})
        self.assertEqual(group('participates', 'states profile-demand; frozen ...'), 'eligible now')
        for reason in ('no semantics marker (written before the correction)', 'semantics version 1 (model-demand)',
                       'output quantity not stated (written before outputs were stated)',
                       'target not established: the record does not state which occurrence its projection is for',
                       "profile definitions not stated by the record or by the source event's record"):
            self.assertEqual(group('withheld', reason), 'awaiting regeneration', reason)
        for reason in ('output is another quantity (weekly-raw-peak: 64)',
                       'names an occurrence its own data already covers (ends 2026-09-30; data ends 2026-09-30): not a forecast of a later occurrence'):
            self.assertEqual(group('withheld', reason), 'another quantity or no later target', reason)
        for reason in ('its target (occurrence ending 2026-11-30; x) is not the occurrence in question (ends 2026-10-31)',
                       'not frozen before its target (data ends 2026-10-01, target occurrence starts 2026-10-01)',
                       'no later occurrence of the event with results in history', "exclusions differ from those of the source event 'ev'",
                       'target results are not verified observations (no generatedOn)'):
            self.assertEqual(group('withheld', reason), 'target or provenance missing or ambiguous', reason)

    def test_candidate_sources_are_written_into_the_event_of_the_copy_only(self):
        folder = tempfile.mkdtemp(prefix='acc-')
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        path = Path(folder) / 'nfit.seasonality.cfg'
        path.write_text('[a]\nmodel = predictive_peak\n\n[ad]\nmodel = adaptive_peak\ncandidate_sources = old\nsource_event = a\n\n[b]\nmodel = x\n')
        A.set_candidate_sources(path, 'ad', ['b', 'c'])
        self.assertEqual(path.read_text(), '[a]\nmodel = predictive_peak\n\n[ad]\ncandidate_sources = b, c\nmodel = adaptive_peak\nsource_event = a\n\n[b]\nmodel = x\n')

    def test_a_selection_is_checked_against_the_chosen_candidates_own_figures(self):
        folder = tempfile.mkdtemp(prefix='acc-')
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        pool = {'pool': {
            'v1': {'P': {'predictive_peak': 2.0, 'event_multiplier': {'forecast': 2.4, 'pi_lower': 2.0, 'pi_upper': 2.8}}},
            'v2': {'P': {'predictive_peak': 3.0, 'arima': {'forecast': 5.0, 'pi_lower': 4.0, 'pi_upper': 6.0}}}}}
        columns = ['VM', 'P_Model', 'P_Mode', 'P_Forecast', 'P_PI_Lower', 'P_PI_Upper', 'P_MultiplierForecast', 'P_Multiplier_PI_Lower', 'P_Multiplier_PI_Upper']
        rows = [
            ['v1', 'event_multiplier', 'tournament', '2.4', '2.0', '2.8', '', '', ''],          # own forecast and own interval
            ['v1', 'predictive_peak', 'tournament', '2.0', '', '', '', '', ''],                # no interval of its own, none reported
            ['v1', 'predictive_peak', 'tournament', '2.0', '1.0', '9.0', '', '', ''],          # another forecast's interval kept
            ['v2', 'arima', 'tournament', '5.5', '4.0', '6.0', '', '', ''],                    # not its own forecast
            ['v2', 'arima', 'tournament', '5.0', '4.0', '7.0', '', '', ''],                    # not its own interval
            ['v2', 'event_multiplier', 'tournament', '4.2', '3.9', '4.5', '4.2', '3.9', '4.5'],   # this run's own multiplier forecast
            ['v2', 'holt', 'tournament', '1.0', '', '', '', '', ''],                           # chosen from outside the pool
            ['v2', 'ensemble_median', 'bootstrap', '4.0', '', '', '', '', ''],                 # the median of 3.0 and 5.0
            ['v2', 'ensemble_median', 'bootstrap', '4.2', '', '', '4.2', '', ''],              # the median with this run's multiplier
            ['v2', 'ensemble_median', 'bootstrap', '4.0', '3.0', '5.0', '', '', ''],           # a borrowed interval
            ['v2', 'ensemble_median', 'bootstrap', '9.0', '', '', '', '', ''],                 # not the median
            ['v1', 'event_multiplier', 'unopposed', '2.4', '2.0', '2.8', '', '', ''],          # a single qualified model: its own figures
            ['v3', '', 'no eligible forecast', '', '', '', '', '', ''],                        # no candidate: nothing reported
            ['v3', '', 'no eligible forecast', '7.0', '6.0', '8.0', '', '', ''],               # ... and never another forecast in its place
        ]
        path = os.path.join(folder, 'selection.csv')
        with open(path, 'w', newline='') as fh:
            writer = csv.writer(fh)
            writer.writerow(columns)
            writer.writerows(rows)
        tally, chosen, modes = A.check_selection(path, pool)
        self.assertEqual(tally, {
            'tournament: own forecast and own interval': 2,
            'tournament: own forecast; the candidate has no interval and none is reported': 1,
            'tournament: an interval is reported although the chosen candidate has none': 1,
            "tournament: forecast differs from the chosen candidate's own": 1,
            "tournament: interval differs from the chosen candidate's own": 1,
            'tournament: the chosen model is not in the pool': 1,
            'bootstrap: the median of the pool, no interval reported': 2,
            'bootstrap: an interval is reported although a median has none of its own': 1,
            'bootstrap: forecast is not the median of the pool': 1,
            'unopposed: own forecast and own interval': 1,
            'no eligible forecast: nothing reported': 1,
            'no eligible forecast: a forecast or an interval is reported all the same': 1})
        self.assertEqual(modes, {'tournament': 7, 'bootstrap': 4, 'unopposed': 1, 'no eligible forecast': 2})
        self.assertEqual(chosen[('ensemble_median', 'bootstrap')], 4)


class SizingReportTests(unittest.TestCase):

    def test_the_sizing_is_compared_lpar_by_lpar_on_the_planning_profile(self):
        def member(planning, ent, ds, role=None):
            out = {'planningProfile': planning, 'profiles': {planning: {'entitlement': ent, 'currentCycle': {'entitlement': ent}, 'demandSource': ds}}}
            if role:
                out['role'] = {'entitlement': role}
            return out
        sel = lambda mode, demand, source: {'source': 'selection', 'mode': mode, 'demand': demand, 'sourceModelDemand': source}
        adaptive = {'context': {'selection': {'status': 'used', 'storedIn': '2026-09', 'candidatesStoredIn': '2026-09',
                                              'target': {'occurrenceStart': '2026-10-28', 'occurrenceEnd': '2026-10-31'},
                                              'withheld': {'no eligible forecast': 1}, 'modes': {'tournament': 9, 'bootstrap': 3}},
                                'notAssessed': ['PowerHA role sizing (standard runs only)', 'frame capacity']},
                    'members': {'a': member('G', 2.5, sel('tournament', 2.5, 2.0)), 'b': member('G', 1.0, sel('bootstrap', 0.8, 1.0)),
                                'c': member('P', 3.0, {'source': 'source model', 'demand': 3.0, 'reason': 'no eligible forecast'}, role=3.2)}}
        predictive = {'members': {'a': member('G', 2.0, {}), 'b': member('G', 1.0, {}), 'c': member('G', 3.0, {}, role=3.2)}}
        text = '\n'.join(A.sizing_report(adaptive, predictive))
        self.assertIn('selection used: stored in 2026-09, candidates of 2026-09, for the occurrence 2026-10-28 to 2026-10-31', text)
        self.assertIn('proposal profiles by demand source: 1 tournament, 1 bootstrap median, 1 source model', text)
        self.assertRegex(text, r'tournament\s+1\s+2\.00\s+2\.50\s+1\.250\s+2\.50\s+2\.00\s+1\s+0')
        self.assertRegex(text, r'bootstrap median\s+1\s+1\.00\s+0\.80\s+0\.800\s+1\.00\s+1\.00\s+0\s+0')
        self.assertRegex(text, r'source model\s+1\s+3\.00\s+3\.00\s+1\.000\s+3\.20\s+3\.20\s+0\s+0')    # a VIOS role publishes its own figure
        self.assertIn('LPARs on another planning profile than in the source event run: 1', text)
        self.assertIn('VIOS roles: 1 in this run, 1 in the source event run', text)
        self.assertIn('sized pairs from the selection: 3 bootstrap median, 9 tournament', text)
        # With two or more LPARs in a group: the spread of selected against source demand, and how many are equal.
        two = dict(adaptive, members=dict(adaptive['members'], d=member('G', 2.2, sel('tournament', 2.0, 2.0))))
        self.assertIn('tournament         selected / source model: quartiles 1.062, 1.125, 1.188; equal within 0.05%: 1 of 2',
                      '\n'.join(A.sizing_report(two, predictive)))
        self.assertIn('1  sized pairs from the source model: no eligible forecast', text)
        withheld = dict(adaptive, context={'selection': {'status': 'withheld', 'reason': 'its target occurrence is already observed'}})
        self.assertIn('selection NOT used: its target occurrence is already observed', '\n'.join(A.sizing_report(withheld, None)))
        self.assertEqual(A.sizing_report(None, predictive), ['  the adaptive run wrote no proposal'])


class CapacityPlanReportTests(unittest.TestCase):

    def test_the_plan_is_summarised_and_its_rules_checked(self):
        def cp(action, ient, ivp, pent, pvp, **extra):
            return {'capacityPlan': dict({'action': action, 'unresolved': [], 'installed': {'entitlement': ient, 'VPs': ivp},
                                          'plan': {'entitlement': pent, 'VPs': pvp},
                                          'additional': {'entitlement': round(pent - ient, 2), 'VPs': pvp - ivp, 'workloadEntitlement': round(pent - ient, 2),
                                                         'workloadVPs': pvp - ivp, 'platformMinimumEntitlement': 0, 'widthVPs': 0},
                                          'combinedEvents': [], 'overlaps': []}, **extra)}
        context = {'capacityPlan': {'produced': True, 'occurrence': {'start': '2026-11-24', 'end': '2026-11-30'}, 'requiredBy': '2026-11-24 00:00',
                                    'timeBasis': 'UTC', 'alternatives': ['month-end-predictive'],
                                    'overlaps': [{'event': 'bf', 'overlapStart': '2026-11-24', 'overlapEnd': '2026-11-27'}],
                                    'pools': [{'pool': 7, 'verdict': 'raise', 'planned': 7.4, 'reliedOn': 0, 'maximum': 7, 'requiredMaximum': 8}],
                                    'normalPlan': {'model': 'hybrid-state-decay', 'status': 'matching'}}}
        members = {'a': cp('increase', 2.0, 4, 2.6, 4, unresolved=['overlapping event', 'confirm before the event'], overlaps=[{'event': 'bf', 'combined': False}],
                           confirmBeforeEvent='the run queue is implausibly high (XRQ); find its cause',
                           step={'entitlement': 2.4, 'VPs': 4, 'limit': "limited by pool 7's maximum (7); it adds VPs only to carry the entitlement"}),
                   'b': cp('retain', 1.5, 3, 1.5, 3, normalPlanConflict={'entitlement': 1.2}),
                   'c': dict(cp('increase', 1.6, 2, 1.6, 3, overlaps=[{'event': 'bf', 'combined': False}]),           # its overlap left out
                             planningProfile='G', profiles={'G': {'currentCycle': {'VPs': 2, 'reviewVPs': 3}}})}
        members['c']['capacityPlan']['eventRequirement'] = {'entitlement': 1.5}
        text = '\n'.join(A.capacity_plan_report({'context': context, 'members': members}))
        self.assertIn('event days 2026-11-24 to 2026-11-30; capacity needed by 2026-11-24 00:00 (time zone: UTC)', text)
        self.assertIn('LPARs: 3 (2 to increase, 1 already sufficient)', text)
        self.assertIn('to add: +0.60 processing units (of which +0.00 only to reach the PowerVM minimum per VP), +1 VPs (of which +0 only to carry the entitlement)', text)
        self.assertIn('checks: planned below installed 0; entitlement and VPs PowerVM cannot configure together 0; increases missing a dependency in their row 1', text)
        self.assertIn("this cycle's recommendation (NFIT - ENT / NFIT - VCPU) below the event figure: 1 LPAR (it adds VPs only to carry the entitlement: 1; limited by pool 7's maximum: 1)", text)
        self.assertIn('increases resting on run-queue readings flagged as doubtful (confirm before the event): 1 LPAR, +0.60 processing units, +0 VPs', text)
        self.assertIn('totals: installed 5.10 processing units / 9 VPs; event requirement 1.50 processing units; planned 5.70 / 10 VPs (installed kept where it is higher)', text)
        self.assertIn('pool 7: raise the maximum from 7 to at least 8 (planned entitlements 7.40)', text)
        self.assertIn('overlapping events: bf 2026-11-24 to 2026-11-27; LPARs whose forecast included one: 0', text)
        self.assertIn('normal recommendation (hybrid-state-decay run): saved on the same data; 1 LPAR below the event requirement', text)
        self.assertEqual(A.capacity_plan_report({'context': {'capacityPlan': {'produced': False, 'reason': 'x'}}}), ['  not produced: x'])
        self.assertEqual(A.capacity_plan_report(None), ['  the adaptive run wrote no capacity plan'])


class MatchedTests(unittest.TestCase):

    def test_models_are_compared_on_the_cases_both_were_scored_on(self):
        def scores(losses, label='observed'):
            return {'monthly_scores': {month: {'pinball_loss': loss, 'target_class': label} for month, loss in losses.items()}}
        months = ['2026-0%d' % n for n in range(1, 8)]
        ledger = {'scores': {'v1': {'P': {'ad': {'models': {
            'event_multiplier': scores({m: 0.20 for m in months}),
            'holt_event': scores({m: 0.10 for m in months}),
            'predictive_peak': scores({'2026-06': 0.30, '2026-07': 0.50})}}, 'other': {'models': {'x': scores({'2026-01': 9.0})}}}},
            'v2': {'P': {'ad': {'models': {'event_multiplier': scores({'2026-07': 0.40}, 'estimated (hidden demand added)')}}}}}}
        cases = A.ledger_cases(ledger, 'ad')
        self.assertEqual(sorted(cases), ['event_multiplier', 'holt_event', 'predictive_peak'])        # the other event's scores are not read
        text = '\n'.join(A.matched_report(cases, 5))
        # Outcomes are counted once, however many candidates were scored against them.
        self.assertIn('outcomes scored against: 8 (LPAR, profile, occurrence) - 1 estimated (hidden demand added), 7 observed; 17 candidate evaluations of them', text)
        self.assertRegex(text, r'holt_event\s+all\s+7\s+7\s+0\.1000\s+0\.2000\s+-0\.1000\s+100%')
        self.assertRegex(text, r'predictive_peak\s+all\s+2\s+2\s+0\.4000\s+0\.2000\s+\+0\.2000\s+0%')
        # At the sixth and seventh origins five earlier scores are known: holt_event has the lowest and is chosen.
        self.assertIn('2 cases: mean loss of the model chosen 0.1000; of event_multiplier on the same cases 0.2000; difference -0.1000', text)
        self.assertIn('chosen: 2 x holt_event', text)
        self.assertEqual(A.matched_report({}, 5), [])


@unittest.skipUnless(TT.T and M.perl_ready() and TT.RUNTIME, "nfit-forecast, perl with JSON or nfit-forecast's Python modules unavailable")
class EndToEndTests(unittest.TestCase):

    def test_the_acceptance_run_on_a_copy_of_a_kept_tree(self):
        import profile_demand_test as P
        M.MemberExposureTests.roots = []
        self.addCleanup(M.MemberExposureTests.tearDownClass)
        case = P.Case('occurrence', peak={P.PROFILE: 1.2}, models=('predictive_peak', 'multiplicative_seasonal'), later_peak=1.3,
                      target_event={'eventName': 'ev', 'periodEndDate': '2026-09-12', 'generatedOn': '2026-09-13T00:00:00'})
        # A kept work tree: the application in app/, staged data and history in case/.
        work = Path(tempfile.mkdtemp(prefix='kept-')) / 'history-check'
        self.addCleanup(lambda: shutil.rmtree(work.parent, ignore_errors=True))
        (work / 'case').mkdir(parents=True)
        shutil.copytree(os.path.join(case.root, 'stage'), work / 'case' / 'stage')
        shutil.copytree(os.path.join(case.root, 'history'), work / 'case' / 'history')
        shutil.copytree(case.root, work / 'app', ignore=shutil.ignore_patterns('stage', 'history'))
        seasonality = work.parent / 'nfit.seasonality.cfg'
        with open(os.path.join(case.root, 'etc', 'nfit.seasonality.cfg')) as fh:
            text = fh.read()
        # (The source event is a predictive peak event, as an adaptive event's must be for the sizing of section 5.)
        seasonality.write_text(text.replace(P.NEXT_DATES, P.NEXT_DATES + ', 2026-11-01:2026-11-01').replace('2026-09-01:2026-09-01, ', '')
                               .replace('model = multiplicative_seasonal', 'model = predictive_peak')
                               + '\n[ad]\nmodel = adaptive_peak\nsource_event = ev\npriority = 10\n')
        before = A.fingerprint(work)

        run = subprocess.run([sys.executable, TOOL, '--work', str(work), '--event', 'ad', '--seasonality', str(seasonality), '--no-seasonal-run'],
                             capture_output=True, text=True, timeout=160)
        text = run.stdout
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-2500:])
        self.assertEqual(A.fingerprint(work), before)                               # the kept tree: nothing written, nothing removed
        self.assertEqual(sorted(p.name for p in work.parent.iterdir()), ['history-check', 'nfit.seasonality.cfg'])   # the copy is gone
        self.assertIn('the kept tree is not written to', text)
        self.assertIn("Event ad: observed occurrences of 'ev'; nfit-profile records of ev.", text)
        self.assertRegex(text, r'2026-09 \(data ends 2026-09-12\), scored against occurrence 2026-10-01 to 2026-10-01')
        self.assertRegex(text, r'eligible now\s+predictive_peak\s+ev\s+\d+ pairs  states profile-demand; frozen 2026-09-12')
        self.assertRegex(text, r'eligible now\s+event_multiplier\s+ad\s+\d+ pairs')
        self.assertRegex(text, r'another quantity or no later target\s+holt\s+ad\s+0 pairs  output is another quantity')
        self.assertIn('1. Eligible forecasts (admitted to scoring)', text)
        self.assertRegex(text, r'2\. Scored candidates \(the ledger of this run\)\n  ledger nfit\.scores\.profile-requirement-1\.json: profile-requirement-1, quantity profile-demand')
        self.assertRegex(text, r'predictive_peak\s+\d+\s+\d+\s+\d+\.\d{4}')
        self.assertIn('none: every selection falls back to the median of the pool (bootstrap)', text)     # one scored occurrence is not five
        self.assertIn('Ledger rebuilt from nothing: identical', text)
        self.assertIn('4. Matched comparison (the same LPAR, profile and occurrence)', text)
        self.assertRegex(text, r'holt_event\s+all\s+1\s+\d+\s+\d\.\d{4}')                   # the derived candidates are scored and compared
        self.assertIn('ad, candidates for selection: those stored in 2026-10', text)
        self.assertRegex(text, r'\d+ x bootstrap: the median of the pool, no interval reported')
        for wrong in ('differs', 'although', 'is not the median', 'not in the pool\n', 'DIFFERENT', 'HISTORY CHANGED'):
            self.assertNotIn(wrong, text.split('Selection for the occurrence ahead')[1] if wrong == 'although' else text, wrong)
        # 5. The adaptive event sized through its source event. The selection is for November; the source model on this
        # data forecasts an earlier occurrence, so every pair keeps the source model's demand, and the two runs agree.
        self.assertIn("5. Sizing from the selection ('ad' through 'ev', latest bucket)", text)
        self.assertRegex(text, r'selection NOT used: it is for the occurrence 2026-11-01 to 2026-11-01; this run sizes ')
        self.assertRegex(text, r'proposal profiles by demand source: \d+ source model\n')
        self.assertRegex(text, r'source model\s+\d+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+([\d.]+)\s+\1\s+0\s+0\n')
        self.assertIn('LPARs on another planning profile than in the source event run: 0', text)
        self.assertIn('not assessed by either run: PowerHA role sizing (standard runs only), frame capacity', text)
        self.assertIn('6. Seasonal Capacity Plan of the adaptive run (totals only; no LPAR names)', text)
        self.assertIn('checks: planned below installed 0; entitlement and VPs PowerVM cannot configure together 0; increases missing a dependency in their row 0', text)
        self.assertIn('history after the adaptive run: unchanged', text)
        self.assertIn('Kept tree: history as it was found', text)
        for name in P.LPARS + (M.SERIAL,):
            self.assertNotIn(name, text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
