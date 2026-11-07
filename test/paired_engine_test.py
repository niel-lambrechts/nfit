#!/usr/bin/env python3
# ==============================================================================
# Analytic fixture tests for nfit's paired PhysC/PhysB transforms (Increment
# 6, design v3.2 §3): exposure, coverage, gaps, invalid pairs, same-second
# samples, state boundaries, tail, weekday filter, aggregated runs, and the
# invariant that the legacy outputs do not depend on the optional columns
# (I2). Runs the real nfit on small hand-built caches with known answers.
# ==============================================================================
import calendar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
T0 = calendar.timegm((2026, 10, 6, 8, 0, 0))          # Tuesday 08:00 UTC
META = {'proc_clock': '3800MHz', 'serial_number': 'SYNP', 'proc_type': 'PowerPC_POWER9', 'proc_version': '2.2',
        'max_cpu': 8, 'pool_id': 0, 'smt': 8, 'pool_cpu': 64, 'entitlement': 2, 'capped': 0, 'virtual_cpus': 8}


def ts(epoch):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(epoch))


def perl_ready():
    return (shutil.which('perl') is not None and
            subprocess.run(['perl', '-MJSON', '-MList::MoreUtils', '-e1'], capture_output=True).returncode == 0)


def strip_paired(obj):
    obj = json.loads(json.dumps(obj))
    obj.pop('physbProvenance', None)
    obj.pop('physbByState', None)
    obj.get('metrics', {}).pop('physb', None)
    return obj


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class PairedEngineTests(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='paired-')
        shutil.copy(os.path.join(REPO, 'nfit'), self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------
    def cache(self, rows, states=None, schema=2, name='SYNP'):
        """rows: (epoch, c, b[, runq[, freqpct]]); b None = blank PhysB cell."""
        d = os.path.join(self.root, 'stage', name)
        os.makedirs(d)
        header = 'Timestamp,VMName,PhysC,RunQ' + (',PhysB,FreqPct' if schema == 2 else '')
        lines = [header]
        for r in sorted(rows, key=lambda r: r[0]):
            e, c, b = r[0], r[1], r[2]
            q = r[3] if len(r) > 3 else 1.0
            f = r[4] if len(r) > 4 else 100.0
            line = f'{ts(e)},vmP,{c},{q}'
            if schema == 2:
                line += f",{'' if b is None else b},{'' if f is None else f}"
            lines.append(line)
        with open(os.path.join(d, '.nfit.cache.data'), 'w') as fh:
            fh.write('\n'.join(lines) + '\n')
        first, last = min(r[0] for r in rows), max(r[0] for r in rows)
        manifest = ['cache_build_status: success', f'data_start: {ts(first)[:10]}', f'data_end: {ts(last)}']
        if schema == 2:
            manifest += ['data_schema_version: 2', f'data_columns: {header}',
                         'column_sources: PhysC=cpu_util.physical_consumed; RunQ=kernel.run_queue; '
                         'PhysB=cpu_util.physical_busy; FreqPct=cpu_util.freq_pct']
        with open(os.path.join(d, '.nfit.cache.manifest'), 'w') as fh:
            fh.write('\n'.join(manifest) + '\n')
        states = states or [(first, last)]
        with open(os.path.join(d, '.nfit.cache.states'), 'w') as fh:
            json.dump({'vmP': [{'start_epoch': s, 'end_epoch': e, 'duration_days': (e - s) / 86400,
                                'metadata': getattr(self, 'meta', META)} for s, e in states]}, fh)
        with open(os.path.join(d, '.nfit_stage_id'), 'w') as fh:
            fh.write('synthetic\n')
        return d

    def nfit(self, d, *flags, start=None, end=None):
        days = sorted({ts(e)[:10] for e in (self._first, self._last)})
        cmd = ['perl', os.path.join(self.root, 'nfit'), '--nmondir', d, '--profile-label', 'T', '-q',
               '--startdate', start or days[0], '--enddate', end or days[-1]] + list(flags)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]

    def run_paired(self, rows, *flags, states=None):
        self._first, self._last = min(r[0] for r in rows), max(r[0] for r in rows)
        out = self.nfit(self.cache(rows, states), '-p', '95', '-w', '5', '--avg-method', 'sma', '--physb-paired', *flags)
        return out

    @staticmethod
    def minutes(n, c=2.0, b=1.0, start=T0, step=60):
        return [(start + i * step, c, b) for i in range(n)]


    # -- exposure and coverage ------------------------------------------------------
    def test_constant_series(self):
        o = self.run_paired(self.minutes(180))[0]
        p = o['metrics']['physb']['T']
        self.assertEqual(p['status'], 'available')
        self.assertEqual(p['pairedExposure'], {'sumB': 180.0, 'sumC': 360.0, 'pairedMinutes': 180.0})
        self.assertEqual(p['busyShare'], 0.5)
        cov = p['coverage']
        self.assertEqual((cov['expectedMinutes'], cov['physcMinutes'], cov['pairedMinutes']), (180.0, 180.0, 180.0))
        self.assertEqual((cov['pairedCoverage'], cov['physcCoverage']), (1.0, 1.0))
        self.assertEqual(p['smoothedWindows'], 176)                          # SMA of 5 samples
        tail = p['tail']['P95']
        self.assertEqual((tail['threshold'], tail['windows'], tail['tiesIncluded'], tail['distinctDays']),
                         (2.0, 176, 176, 1))
        self.assertEqual((tail['busyShare'], tail['meanB'], tail['meanC'], tail['meanNonBusy']), (0.5, 1.0, 2.0, 1.0))
        self.assertEqual(p['profileValueB'], {'P95': 1.0})
        self.assertEqual(o['physbProvenance']['source'], 'cpu_util.physical_busy')
        self.assertEqual(o['physbProvenance']['derivationVersion'], 'paired-2')
        self.assertEqual(o['physbProvenance']['calendarRules'], 'weekday-v2')
        self.assertEqual(p['daily'], [{'date': '2026-10-06', 'expectedMinutes': 180.0, 'physcMinutes': 180.0,
                                       'pairedMinutes': 180.0, 'sumB': 180.0, 'sumC': 360.0, 'freqPctMean': 100.0,
                                       'freqPctMinutes': 180.0,
                                       'tail': {'P95': {'windows': 176, 'sumB': 176.0, 'sumC': 352.0}}}])
        self.assertEqual((cov['firstPair'], cov['lastPair'], cov['pairedDays']),
                         ('2026-10-06T08:00:00', '2026-10-06T10:59:00', 1))

    def test_gap_resets_and_reduces_coverage(self):
        rows = [r for i, r in enumerate(self.minutes(180)) if not 60 <= i < 70]
        p = self.run_paired(rows)[0]['metrics']['physb']['T']
        self.assertEqual(p['pairedExposure']['pairedMinutes'], 170.0)        # never the gap's 11 minutes
        self.assertEqual(p['coverage']['expectedMinutes'], 180.0)
        self.assertEqual(p['coverage']['pairedCoverage'], round(170 / 180, 4))
        self.assertEqual(p['quality']['runResets'], 1)
        self.assertEqual(p['smoothedWindows'], (60 - 4) + (110 - 4))         # no window spans the gap

    def test_missing_and_implausible_physb(self):
        rows = self.minutes(180)
        rows[30:33] = [(e, c, None) for e, c, _ in rows[30:33]]
        rows[100] = (rows[100][0], 2.0, 2.5)                                   # PhysB above PhysC
        p = self.run_paired(rows)[0]['metrics']['physb']['T']
        q = p['quality']
        self.assertEqual((q['validPairs'], q['missingB'], q['bAboveC'], q['runResets']), (176, 3, 1, 2))
        self.assertEqual(p['coverage']['physcMinutes'], 180.0)
        self.assertEqual(p['coverage']['pairedMinutes'], 176.0)
        self.assertEqual(p['pairedExposure']['sumC'], 352.0)                  # never imputed, never zero

    def test_same_second_samples_carry_no_weight(self):
        rows = self.minutes(180)
        rows.append((rows[50][0], 1.8, 0.9))                                  # a second, different sample
        p = self.run_paired(rows)[0]['metrics']['physb']['T']
        self.assertEqual(p['quality']['sameSecondSamples'], 1)
        self.assertEqual(p['pairedExposure'], {'sumB': 180.0, 'sumC': 360.0, 'pairedMinutes': 180.0})
        self.assertEqual(p['quality']['runResets'], 0)
        self.assertEqual(p['smoothedWindows'], 176)

    def test_state_boundary_separates_bins(self):
        rows = self.minutes(180)
        states = [(T0, T0 + 89 * 60), (T0 + 90 * 60, T0 + 179 * 60)]
        out = {o['state']['id']: o['metrics']['physb']['T'] for o in self.run_paired(rows, states=states)}
        self.assertEqual(sorted(out), [1, 2])
        for p in out.values():
            self.assertEqual(p['coverage']['pairedMinutes'], 90.0)
            self.assertEqual(p['coverage']['expectedMinutes'], 90.0)
            self.assertEqual(p['smoothedWindows'], 86)                       # no window crosses states

    def test_tail_is_measured_on_the_paired_series(self):
        rows = self.minutes(120, c=1.0, b=0.5) + self.minutes(60, c=3.0, b=1.2, start=T0 + 120 * 60)
        p = self.run_paired(rows)[0]['metrics']['physb']['T']
        tail = p['tail']['P95']
        self.assertEqual(tail['threshold'], 3.0)
        self.assertEqual((tail['meanC'], tail['meanB'], tail['busyShare']), (3.0, 1.2, 0.4))
        self.assertEqual(tail['meanNonBusy'], 1.8)
        self.assertEqual(p['busyShare'], round((120 * 0.5 + 60 * 1.2) / (120 * 1.0 + 60 * 3.0), 4))

    def test_weekday_filter_retains_monday_to_friday(self):
        """-no-weekends keeps Monday to Friday and rejects Saturday and Sunday
        in both paths. Each day has its own level and the weekend levels are
        the highest, so admitting any weekend day changes every assertion."""
        monday = calendar.timegm((2026, 10, 5, 0, 0, 0))
        level = {0: 1.0, 1: 1.1, 2: 1.2, 3: 1.3, 4: 1.4, 5: 5.0, 6: 6.0}      # Mon=0 .. Sun=6
        rows = [(monday + i * 900, level[i // 96], level[i // 96] / 2) for i in range(7 * 96)]
        self._first, self._last = rows[0][0], rows[-1][0]
        d = self.cache(rows)
        o = self.nfit(d, '-p', '95', '-w', '15', '--avg-method', 'sma', '-online', '-no-weekends',
                      '--physb-paired')[0]
        p = o['metrics']['physb']['T']
        self.assertEqual(p['coverage']['expectedMinutes'], 5 * 9 * 60.0)       # 08:00-17:00, Monday-Friday
        self.assertEqual(p['coverage']['pairedMinutes'], 5 * 9 * 60.0)
        self.assertEqual([r['date'] for r in p['daily']],
                         ['2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09'])
        self.assertEqual([r['sumC'] for r in p['daily']], [round(v * 9 * 60, 4) for v in (1.0, 1.1, 1.2, 1.3, 1.4)])
        self.assertEqual(p['tail']['P95']['threshold'], 1.4)                   # Friday, never a weekend level
        self.assertEqual(p['tail']['P95']['distinctDays'], 1)
        self.assertAlmostEqual(o['metrics']['physc']['T']['P95'], 1.4, places=6)  # the legacy path agrees (float32)
        self.assertEqual(sorted(o['metrics']['physc']['T']['DailySeries']),
                         ['2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09'])

    # -- quality ------------------------------------------------------------------------
    def test_implausible_values_are_rejected_counted_and_break_support(self):
        rows = self.minutes(180)
        rows[40] = (rows[40][0], 50.0, 10.0)                                   # B <= C, both above the ceiling
        rows[80] = (rows[80][0], 2.0, 40.0)                                    # B above the ceiling
        rows[120] = (rows[120][0], 2.0, 2.5)                                   # B modestly above C
        p = self.run_paired(rows)[0]['metrics']['physb']['T']
        q = p['quality']
        self.assertEqual((q['cAboveCeiling'], q['bAboveCeiling'], q['bAboveC'], q['nonFiniteC']), (1, 1, 1, 0))
        self.assertEqual((q['ceilingCores'], q['ceilingBasis']), (8.45, 'virtual_cpus'))
        self.assertEqual((q['validPairs'], q['runResets']), (177, 3))
        self.assertEqual(p['pairedExposure'], {'sumB': 177.0, 'sumC': 354.0, 'pairedMinutes': 177.0})
        self.assertEqual(p['coverage']['physcMinutes'], 180.0)                 # legacy support is unchanged
        self.assertEqual(p['tail']['P95']['threshold'], 2.0)                   # no rejected value reaches the tail
        self.assertEqual(p['smoothedWindows'], 176 - 3 * 5)                    # each rejection costs five windows

    def test_non_finite_values_are_rejected(self):
        rows = self.minutes(180)
        rows[40] = (rows[40][0], 'nan', 1.0)
        rows[80] = (rows[80][0], 2.0, 'inf')
        rows[120] = (rows[120][0], 2.0, 'nan')
        p = self.run_paired(rows)[0]['metrics']['physb']['T']
        q = p['quality']
        self.assertEqual((q['nonFiniteC'], q['nonNumericB'], q['validPairs'], q['runResets']), (1, 2, 177, 3))
        self.assertEqual(p['pairedExposure']['sumC'], 354.0)

    def test_ceiling_falls_back_to_the_pool_then_none(self):
        self.meta = dict(META, virtual_cpus=None)
        q = self.run_paired(self.minutes(60))[0]['metrics']['physb']['T']['quality']
        self.assertEqual((q['ceilingCores'], q['ceilingBasis']), (67.25, 'pool_cpu'))
        shutil.rmtree(os.path.join(self.root, 'stage'))
        self.meta = dict(META, virtual_cpus=None, pool_cpu=None)
        rows = self.minutes(60)
        rows[10] = (rows[10][0], 200.0, 100.0)
        q = self.run_paired(rows)[0]['metrics']['physb']['T']['quality']
        self.assertEqual((q['ceilingCores'], q['ceilingBasis'], q['cAboveCeiling'], q['validPairs']),
                         (None, 'none', 0, 60))

    # -- daily statistics and context ---------------------------------------------------
    def test_daily_statistics_are_additive_and_carry_freqpct(self):
        rows = ([(T0 + i * 60, 2.0, 1.0, 1.0, 100.0) for i in range(60)] +
                [(T0 + 86400 + i * 60, 4.0, 1.0, 1.0, 120.0 if i < 30 else None) for i in range(60)])
        self._first, self._last = rows[0][0], rows[-1][0]
        p = self.nfit(self.cache(rows, states=[(T0, T0 + 86400 + 59 * 60)]), '-p', '95', '-w', '5',
                      '--avg-method', 'sma', '--physb-paired')[0]['metrics']['physb']['T']
        days = {r['date']: r for r in p['daily']}
        self.assertEqual(sorted(days), ['2026-10-06', '2026-10-07'])
        for field in ('sumB', 'sumC', 'pairedMinutes'):
            self.assertEqual(sum(r[field] for r in p['daily']), p['pairedExposure'][field])
        self.assertEqual((days['2026-10-06']['freqPctMean'], days['2026-10-07']['freqPctMean']), (100.0, 120.0))
        self.assertEqual(days['2026-10-07']['freqPctMinutes'], 30.0)
        self.assertEqual(p['quality']['freqPctUnavailable'], 30)
        tail = p['tail']['P95']
        self.assertEqual(sum(r['tail']['P95']['windows'] for r in p['daily']), tail['windows'])
        self.assertEqual(sum(r['tail']['P95']['sumC'] for r in p['daily']), tail['sumC'])
        self.assertEqual(days['2026-10-06']['tail']['P95']['windows'], 0)       # the tail is all day two

    def test_partial_presence_keeps_extent_and_support_apart(self):
        """A VM present on the first and third days of one state: the period
        runs across the state, support only where pairs exist."""
        rows = self.minutes(180) + self.minutes(180, start=T0 + 2 * 86400)
        p = self.run_paired(rows, states=[(T0, T0 + 2 * 86400 + 179 * 60)])[0]['metrics']['physb']['T']
        cov = p['coverage']
        self.assertEqual((cov['periodStart'], cov['periodEnd']), ('2026-10-06T08:00:00', '2026-10-08T10:59:00'))
        self.assertEqual((cov['firstPair'], cov['lastPair'], cov['pairedDays']),
                         ('2026-10-06T08:00:00', '2026-10-08T10:59:00', 2))
        self.assertEqual(cov['expectedMinutes'], 2 * 1440.0 + 180)
        self.assertEqual(cov['pairedMinutes'], 360.0)
        self.assertEqual(cov['pairedCoverage'], round(360 / (2 * 1440 + 180), 4))
        self.assertEqual([(r['date'], r['expectedMinutes'], r['pairedMinutes']) for r in p['daily']],
                         [('2026-10-06', 960.0, 180.0), ('2026-10-07', 1440.0, 0.0), ('2026-10-08', 660.0, 180.0)])

    # -- invariants -------------------------------------------------------------------
    def test_legacy_outputs_do_not_depend_on_optional_columns(self):
        rows = self.minutes(120, c=1.0, b=0.5) + self.minutes(60, c=3.0, b=None, start=T0 + 120 * 60)
        self._first, self._last = rows[0][0], rows[-1][0]
        d2 = self.cache(rows, schema=2, name='TWO')
        two = self.nfit(d2, '-p', '95', '-w', '5', '--physb-paired')
        one = self.nfit(self.cache(rows, schema=1, name='ONE'), '-p', '95', '-w', '5', '--physb-paired')
        self.assertTrue(all('physb' not in o['metrics'] and 'physbProvenance' not in o for o in one))
        self.assertEqual([strip_paired(o) for o in two], one)
        os.remove(os.path.join(d2, '.nfit.cache.results'))
        plain = self.nfit(d2, '-p', '95', '-w', '5')                           # not requested: nothing added
        self.assertEqual(plain, one)

    def test_aggregated_run_reports_paired_results_by_state(self):
        rows = self.minutes(180)
        states = [(T0, T0 + 89 * 60), (T0 + 90 * 60, T0 + 179 * 60)]
        out = self.run_paired(rows, '--decay-over-states', states=states)
        by_state = out[0]['physbByState']
        self.assertEqual(sorted(by_state), ['1', '2'])
        self.assertEqual(by_state['2']['T']['pairedExposure']['pairedMinutes'], 90.0)

    def test_no_valid_pairs_is_unavailable_not_zero(self):
        rows = [(e, c, None) for e, c, _ in self.minutes(60)]
        p = self.run_paired(rows)[0]['metrics']['physb']['T']
        self.assertEqual(p['status'], 'unavailable')
        self.assertIsNone(p['busyShare'])
        self.assertEqual(p['quality']['missingB'], 60)


if __name__ == '__main__':
    unittest.main(verbosity=1)
