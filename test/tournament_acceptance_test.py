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
        seasonality.write_text(text.replace(P.NEXT_DATES, P.NEXT_DATES + ', 2026-11-01:2026-11-01').replace('2026-09-01:2026-09-01, ', '')
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
        self.assertIn('Kept tree: history as it was found', text)
        for name in P.LPARS + (M.SERIAL,):
            self.assertNotIn(name, text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
