#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/history_overlap_compare_test.py
# PURPOSE  : dev/history-overlap-compare sets two histories of the same months
#            side by side: matched values agree or their ratio is reported, a
#            different relation of the event value to the daily values shows a
#            different definition, a model record from before the correction
#            beside results is named, and no LPAR is named.
# USAGE    : python3 test/history_overlap_compare_test.py
# ==============================================================================
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, '..', 'dev', 'history-overlap-compare'))
P = 'G3-95W15'


def month(scale=1.0, event=1.2, basis=None, earlier_record=False, days=30, peaks=True):
    """A month of two LPARs: daily profile values 1.0 and 2.0 (times scale), an occurrence on days 24 to 30."""
    analysis = {}
    for vm, level in (('lparA', 1.0), ('lparB', 2.0)):
        block = {'DailyProfileSeries': {P: {'2026-08-%02d' % d: round(level * scale, 4) for d in range(1, days + 1)}}}
        if peaks:
            # as stored: a list of {date, raw_peak_value}
            block['DailyPeakSeries'] = {P: [{'date': '2026-08-%02d' % d, 'raw_peak_value': round(level * 2, 4)} for d in range(1, days + 1)]}
        analysis[vm] = block
    values = {'lparA': {P: round(1.0 * event, 4)}, 'lparB': {P: round(2.0 * event, 4)}}
    snapshot = {'eventName': 'ev', 'periodEndDate': '2026-08-30', 'generatedOn': '2026-08-31T00:00:00',
                'baselinePeriod': {'startDate': '2026-08-08', 'endDate': '2026-08-23'},
                'results': {'HistoricPeak': values, 'HistoricBaseline': {'lparA': {P: round(1.0 * scale, 4)}, 'lparB': {P: round(2.0 * scale, 4)}},
                            'ClippingInfo': {'lparA': {P: {'isClipped': 1, 'unmetDemandEstimate': 0.2}}, 'lparB': {P: {'isClipped': 0}}},
                            'OccurrencePeaks': [{'vm': vm, 'profile': P, 'occurrence_start': '2026-08-24', 'occurrence_end': '2026-08-30',
                                                 'raw_peak_value': level * 2} for vm, level in (('lparA', 1.0), ('lparB', 2.0))]}}
    if earlier_record:
        snapshot['ModelForecasts'] = {'recency_decay': {'_meta': {'model_version': 1}, 'lparA': {P: 9.0}}}
    meta = {'ProcessedStartDate': '2026-08-01', 'ProcessedEndDate': '2026-08-%02d' % days, 'ProcessedDays': days}
    if basis:
        meta['SmoothingTimeBasis'] = basis
    return {'2026-08': {'Metadata': meta, 'MonthlyWorkloadAnalysis': analysis, 'SeasonalEventSnapshots': {'ev': snapshot}}}


class OverlapTests(unittest.TestCase):

    def run_tool(self, old, new, *extra):
        root = tempfile.mkdtemp(prefix='overlap-')
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        for name, content in (('old', old), ('new', new)):
            os.mkdir(os.path.join(root, name))
            with open(os.path.join(root, name, 'nfit.hist.2026-08.json'), 'w') as fh:
                json.dump(content, fh)
        before = {}
        for name in ('old', 'new'):
            with open(os.path.join(root, name, 'nfit.hist.2026-08.json'), 'rb') as fh:
                before[name] = fh.read()
        run = subprocess.run([sys.executable, TOOL, '--old', os.path.join(root, 'old'), '--new', os.path.join(root, 'new')] + list(extra),
                             capture_output=True, text=True, timeout=60)
        for name in before:
            with open(os.path.join(root, name, 'nfit.hist.2026-08.json'), 'rb') as fh:
                self.assertEqual(fh.read(), before[name])                           # it reads; it writes nothing
        for lpar in ('lparA', 'lparB'):
            self.assertNotIn(lpar, run.stdout)
        return run

    def test_the_same_month_twice_agrees_throughout(self):
        run = self.run_tool(month(), month())
        self.assertEqual(run.returncode, 0, run.stderr)
        text = run.stdout
        self.assertIn('months in both: 2026-08', text)
        self.assertRegex(text, r'all profiles\s+60 matched; identical 100%; within 2% 100%; within 5% 100%; ratio median 1\.000')
        self.assertRegex(text, r'HistoricPeak\s+2 matched; identical 100%')
        self.assertRegex(text, r'Daily peaks \(DailyPeakSeries\)\n(.*\n){3}\s+all profiles\s+60 matched; identical 100%')
        self.assertNotIn('largest differences', text)                                # only when asked for
        self.assertIn('occurrence and baseline dates    same', text)
        self.assertRegex(text, r'clipped pairs\s+both 1, only old 0, only new 0')
        self.assertRegex(text, r'event value / highest daily profile value of the occurrence\s+old 1\.200 \(2 pairs\), new 1\.200 \(2 pairs\)')
        self.assertRegex(text, r'stored baseline / mean daily profile value of its days\s+old 1\.000 \(2 pairs\), new 1\.000 \(2 pairs\)')
        self.assertNotIn('DIFFERENT', text)
        self.assertNotIn('before the correction', text)

    def test_differences_of_value_definition_coverage_and_provenance_are_reported(self):
        # The new history: daily values 10% higher, the event value equal to the raw peak rather than a profile value,
        # two days fewer, no daily peaks, another smoothing basis; the old one holds an earlier model record.
        new = month(scale=1.1, event=2.0, basis='elapsed-time', days=28, peaks=False)
        run = self.run_tool(month(basis='per-sample', earlier_record=True), new)
        text = run.stdout
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertRegex(text, r'SmoothingTimeBasis\s+DIFFERENT \(old per-sample, new elapsed-time\)')
        self.assertRegex(text, r'ProcessedDays\s+DIFFERENT \(old 30, new 28\)')
        self.assertRegex(text, r'fields stored per LPAR\s+only old: DailyPeakSeries; only new: -')
        self.assertRegex(text, r'days\s+both 28, only old 2, only new 0')
        self.assertRegex(text, r'all profiles\s+56 matched; identical\s+0%; within 2%\s+0%; within 5%\s+0%; ratio median 1\.100')
        self.assertIn('stored only in the old history', text)
        self.assertRegex(text, r'HistoricPeak\s+2 matched; identical\s+0%.*ratio median 1\.667')
        self.assertIn('old: model record(s) from before the correction beside the results: recency_decay', text)
        # The relation shows the definition: 1.2 times the daily values in the old history, the raw peak in the new.
        self.assertRegex(text, r'raw occurrence peak / event value\s+old 1\.667 \(2 pairs\), new 1\.000 \(2 pairs\)')
        # The largest differences, pair by pair, with numbered LPARs.
        listed = self.run_tool(month(basis='per-sample', earlier_record=True), new, '--top', '1').stdout
        self.assertIn('largest differences of HistoricPeak (the same pair in both histories)', listed)
        self.assertRegex(listed, r'LPAR-01\s+G3-95W15\s+\d\.\d{3}\s+\d\.\d{3}\s+1\.667\s+(yes|no) / (yes|no)\s+\d\.\d{3} / \d\.\d{3}\s+5 days in both, 0 within 2%')
        self.assertEqual(listed.count('LPAR-'), 1)

    def test_a_month_not_in_both_is_said_so(self):
        run = self.run_tool(month(), month(), '--months', '2026-07')
        self.assertEqual(run.returncode, 1)
        self.assertIn('2026-07\n  not in both histories', run.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
