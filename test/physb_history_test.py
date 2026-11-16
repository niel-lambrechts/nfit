#!/usr/bin/env python3
# ==============================================================================
# Fixture tests for PhysB monthly history (Increment 6b, design v3.2 §4):
# nfit-profile --update-history --physb-history on small synthetic caches.
# Covers absent PhysB, unchanged PhysC analysis (I2/I6), partial presence
# (window, period and support kept apart), a partial month and its extension,
# idempotent re-runs, back-fill of stored months, a narrower cache never
# replacing a wider stored block, recomputation on a semantics change, and
# month scheduling that does not depend on the local time zone.
# Runs copies of nfit and nfit-profile in scratch roots with tracked
# configuration only and an empty seasonality configuration.
# ==============================================================================
import calendar
import copy
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
TRACKED_ETC = ('nfit.profiles.cfg', 'nfit.profiles.cfg.default', 'nfit.mgsys.cfg.default',
               'nfit.scenarios.cfg.default')
SERIAL = 'SYNH'
MONTH = '2026-09'
STEP = 900
META = {'proc_clock': '3800MHz', 'serial_number': SERIAL, 'proc_type': 'PowerPC_POWER9', 'proc_version': '2.2',
        'max_cpu': 8, 'pool_id': 0, 'smt': 8, 'pool_cpu': 64, 'entitlement': 2, 'capped': 0, 'virtual_cpus': 8}


def day(s):
    return calendar.timegm(time.strptime(s, '%Y-%m-%d'))


def ts(epoch):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(epoch))


def level(epoch):
    hour = time.gmtime(epoch).tm_hour
    return 0.5 + (1.5 if 9 <= hour < 17 else 0.0) + (epoch // 86400 % 7) * 0.05


def perl_ready():
    return (shutil.which('perl') is not None and
            subprocess.run(['perl', '-MJSON', '-MList::MoreUtils', '-e1'], capture_output=True).returncode == 0)


def without_physb(partition):
    """The partition as it would be without any PhysB addition."""
    p = copy.deepcopy(partition)
    for month in p.values():
        month.get('Metadata', {}).pop('LastUpdated', None)
        month.get('_metadata', {}).pop('last_updated', None)
        month.get('Metadata', {}).pop('PhysB', None)
        month.get('Metadata', {}).pop('DailyPhysBUnavailable', None)
        month.get('Metadata', {}).pop('Features', None)
        for vm in month.get('MonthlyWorkloadAnalysis', {}).values():
            vm.pop('PhysBMetrics', None)
    return p


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class PhysBHistoryTests(unittest.TestCase):

    def setUp(self):
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            shutil.rmtree(r, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------------
    def root(self):
        r = tempfile.mkdtemp(prefix='physb-hist-')
        self.roots.append(r)
        os.makedirs(os.path.join(r, 'etc'))
        os.makedirs(os.path.join(r, 'output'))
        for prog in ('nfit', 'nfit-profile'):
            shutil.copy(os.path.join(REPO, prog), r)
        for name in TRACKED_ETC:
            src = os.path.join(REPO, 'etc', name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(r, 'etc', name))
        open(os.path.join(r, 'etc', 'nfit.seasonality.cfg'), 'w').close()
        return r

    def cache(self, r, first, last, schema=2, vms=None, revision=None):
        """vms: {name: (data_first, data_last, state_first, state_last)} as dates."""
        vms = vms or {'vmA': (first, last, first, last)}
        d = os.path.join(r, 'stage', SERIAL)
        if os.path.isdir(d):                       # a new generation of the cache
            shutil.move(d, os.path.join(r, 'old-' + str(len(os.listdir(r)))))
        os.makedirs(d)
        header = 'Timestamp,VMName,PhysC,RunQ' + (',PhysB,FreqPct' if schema == 2 else '')
        lines = [header]
        for e in range(day(first), day(last) + 86400, STEP):
            for vm, (a, z, _, _) in sorted(vms.items()):
                if not day(a) <= e < day(z) + 86400:
                    continue
                c = level(e)
                line = f'{ts(e)},{vm},{c:.4f},{c * 2:.4f}'
                if schema == 2:
                    line += f',{c * 0.6:.4f},{100 + time.gmtime(e).tm_hour % 3 * 5:.1f}'
                lines.append(line)
        with open(os.path.join(d, '.nfit.cache.data'), 'w') as fh:
            fh.write('\n'.join(lines) + '\n')
        manifest = ['cache_build_status: success', f'data_start: {first}', f'data_end: {last} 23:45:00']
        if schema == 2:
            manifest += ['data_schema_version: 2', f'data_columns: {header}',
                         f'data_revision: {revision or "rev-" + first + "-" + last}',
                         'column_sources: PhysC=cpu_util.physical_consumed; RunQ=kernel.run_queue; '
                         'PhysB=cpu_util.physical_busy; FreqPct=cpu_util.freq_pct']
        with open(os.path.join(d, '.nfit.cache.manifest'), 'w') as fh:
            fh.write('\n'.join(manifest) + '\n')
        states = {vm: [{'start_epoch': day(s0), 'end_epoch': day(s1) + 86400 - STEP,
                        'duration_days': (day(s1) + 86400 - day(s0)) / 86400, 'metadata': META}]
                  for vm, (_, _, s0, s1) in vms.items()}
        with open(os.path.join(d, '.nfit.cache.states'), 'w') as fh:
            json.dump(states, fh)
        with open(os.path.join(d, '.nfit_stage_id'), 'w') as fh:
            fh.write('synthetic\n')

    def update(self, r, *flags, force=True, tz=None):
        """force: pass --force, needed where a month is (re)analysed because the
        fixtures carry no tactical results; it also re-analyses every month."""
        cmd = ['perl', os.path.join(r, 'nfit-profile'), '--update-history', '--min-history-days', '1',
               '--nmondir', os.path.join(r, 'stage', SERIAL)] + (['--force'] if force else []) + list(flags)
        env = dict(os.environ, TZ=tz) if tz else None
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=150, cwd=r, env=env)
        self.assertEqual(p.returncode, 0, p.stderr[-1200:])
        return p

    def months(self, r):
        return sorted(os.listdir(os.path.join(r, 'history', SERIAL)))

    def partition(self, r, month=MONTH):
        with open(os.path.join(r, 'history', SERIAL, f'nfit.hist.{month}.json')) as fh:
            return json.load(fh)

    def raw(self, r, month=MONTH):
        with open(os.path.join(r, 'history', SERIAL, f'nfit.hist.{month}.json'), 'rb') as fh:
            return fh.read()

    def block(self, r, vm='vmA'):
        return self.partition(r)[MONTH]['MonthlyWorkloadAnalysis'][vm].get('PhysBMetrics')

    # -- absence and invariants ------------------------------------------------------------
    def test_absent_physb_writes_no_block_and_changes_nothing(self):
        a, b = self.root(), self.root()
        for r in (a, b):
            self.cache(r, '2026-09-01', '2026-09-30', schema=1)
        self.update(a, '--physb-history')
        self.update(b)
        self.assertIsNone(self.block(a))
        self.assertNotIn('PhysB', self.partition(a)[MONTH]['Metadata'])
        self.assertEqual(without_physb(self.partition(a)), without_physb(self.partition(b)))

        self.assertEqual(self.partition(a)[MONTH]['Metadata']['DailyPhysBUnavailable'], 'no_declared_physb_column')
        self.assertNotIn('DailyPhysBUnavailable', self.partition(b)[MONTH]['Metadata'])
        self.update(b, '--physb-history', force=False)
        self.assertEqual(self.partition(b)[MONTH]['Metadata']['DailyPhysBUnavailable'], 'no_declared_physb_column')
        before = self.raw(b)
        self.update(b, '--physb-history', force=False)
        self.assertEqual(self.raw(b), before)
        self.cache(b, '2026-09-01', '2026-09-30', schema=2)
        self.update(b, '--physb-history', force=False)
        self.assertNotIn('DailyPhysBUnavailable', self.partition(b)[MONTH]['Metadata'])
        self.assertTrue(self.block(b)['DailyProfileSeries'])

    def test_block_is_additive_and_physc_analysis_unchanged(self):
        a, b = self.root(), self.root()
        for r in (a, b):
            self.cache(r, '2026-09-01', '2026-09-30')
        self.update(a, '--physb-history')
        self.update(b)
        self.assertEqual(without_physb(self.partition(a)), without_physb(self.partition(b)))   # I2 / I6
        blk = self.block(a)
        self.assertEqual(blk['schemaVersion'], 1)
        self.assertEqual(blk['Coverage'], {'monthStart': '2026-09-01', 'monthEnd': '2026-09-30',
                                           'windowStart': '2026-09-01', 'windowEnd': '2026-09-30', 'complete': True})
        self.assertEqual((blk['Source']['derivationVersion'], blk['Source']['calendarRules'],
                          blk['Source']['physbSource']), ('paired-2', 'weekday-v2', 'cpu_util.physical_busy'))
        pop = blk['Populations']['none']
        n = 30 * 96
        expected_c = sum(round(level(day('2026-09-01') + i * STEP), 4) for i in range(n)) * STEP / 60
        self.assertEqual(pop['Coverage']['pairedMinutes'], n * 15.0)
        self.assertEqual((pop['Coverage']['pairedCoverage'], pop['Coverage']['pairedDays']), (1.0, 30))
        self.assertAlmostEqual(pop['PairedExposure']['sumC'], expected_c, places=2)
        self.assertEqual(pop['BusyShare'], 0.6)
        self.assertEqual(pop['Quality']['validPairs'], n)
        self.assertEqual(pop['Quality']['invalidPairs'], 0)
        for field in ('sumB', 'sumC'):
            self.assertAlmostEqual(sum(d[field] for d in pop['DailyByState']), pop['PairedExposure'][field], places=2)
        self.assertEqual(len(pop['Context']), 30)
        # Profile values: one per PhysC profile, each naming its population.
        pv = self.partition(a)[MONTH]['MonthlyWorkloadAnalysis']['vmA']['ProfileValues']
        self.assertEqual(set(blk['ProfileValuesB']) - set(pv), set())
        for name, prof in blk['Profiles'].items():
            self.assertIn(prof['population'], blk['Populations'])
            if name in blk['TailByProfile']:
                self.assertEqual(sum(d['windows'] for d in prof['tailDaily']), blk['TailByProfile'][name]['windows'])
        meta = self.partition(a)[MONTH]['Metadata']
        self.assertEqual(meta['Features'], ['PhysB-v1'])
        self.assertEqual((meta['PhysB']['windowStart'], meta['PhysB']['windowEnd']), ('2026-09-01', '2026-09-30'))

    def test_daily_paired_profiles_match_a_one_day_engine_pass(self):
        r = self.root()
        self.cache(r, '2026-09-01', '2026-09-30')
        self.update(r, '--physb-history')
        blk = self.block(r)
        series = blk['DailyProfileSeries']
        self.assertEqual(blk['Source']['dailyProfileVersion'], 1)
        self.assertIn('G3-95W15', series)
        self.assertNotIn('Peak_P-99W1', series)
        cache = os.path.join(r, 'stage', SERIAL)
        date = '2026-09-14'
        # Use the month manifest without the new daily directive. This is the
        # original native paired calculation, with an independent daily origin.
        manifest = os.path.join(cache, '.nfit.engine.last_manifest')
        with open(manifest) as fh:
            transforms = json.load(fh)
        for entry in transforms.values():
            if isinstance(entry, dict):
                entry.pop('daily_paired_profiles', None)
        oracle = os.path.join(r, 'single-day.json')
        with open(oracle, 'w') as fh:
            json.dump(transforms, fh)
        cmd = ['perl', os.path.join(r, 'nfit'), '--nmondir', cache, '--manifest', oracle,
               '--physb-paired', '--startdate', date, '--enddate', date, '-q']
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        obj = json.loads(result.stdout.strip())
        for profile, rows in series.items():
            row, = [d for d in rows if d['date'] == date]
            ref = obj['metrics']['physb'][profile]
            self.assertEqual(row['windows'], ref['smoothedWindows'])
            self.assertEqual(row['pairedMinutes'], ref['coverage']['pairedMinutes'])
            self.assertEqual(row['expectedMinutes'], ref['coverage']['expectedMinutes'])
            if row['windows']:
                self.assertIn(row['physB'], ref['profileValueB'].values())
                self.assertIn(row['pairedPhysC'], [t['threshold'] for t in ref['tail'].values()])
            else:
                self.assertIsNone(row['physB'])
                self.assertIsNone(row['pairedPhysC'])

    def test_daily_series_keeps_both_states_and_missing_busy(self):
        r = self.root()
        self.cache(r, '2026-09-01', '2026-09-03')
        cache = os.path.join(r, 'stage', SERIAL)
        path = os.path.join(cache, '.nfit.cache.states')
        with open(path) as fh:
            states = json.load(fh)
        first, = states['vmA']
        second = copy.deepcopy(first)
        boundary = day('2026-09-02') + 12 * 3600
        first['end_epoch'] = boundary - 1
        second['end_epoch'] = day('2026-09-04') - 1
        second['start_epoch'] = boundary
        second['metadata']['entitlement'] = 3
        states['vmA'].append(second)
        with open(path, 'w') as fh:
            json.dump(states, fh)
        path = os.path.join(cache, '.nfit.cache.data')
        with open(path) as fh:
            rows = fh.readlines()
        with open(path, 'w') as fh:
            for line in rows:
                fields = line.rstrip('\n').split(',')
                if fields[0].startswith('2026-09-03'):
                    fields[4] = ''
                fh.write(','.join(fields) + '\n')
            fh.write('2026-09-03 23:59:59,vmA,1,1,,100\n')
        self.update(r, '--physb-history')
        series = self.block(r)['DailyProfileSeries']['G3-95W15']
        self.assertEqual(max(d['date'] for d in series), '2026-09-03')
        split = [d for d in series if d['date'] == '2026-09-02']
        self.assertEqual(len({d['stateId'] for d in split}), 2)
        self.assertEqual(sum(d['pairedMinutes'] for d in split), 1440)
        last, = [d for d in series if d['date'] == '2026-09-03']
        self.assertEqual((last['windows'], last['pairedMinutes']), (0, 0))
        self.assertIsNone(last['physB'])
        self.assertIsNone(last['pairedPhysC'])

    def test_partial_presence_keeps_window_period_and_support_apart(self):
        r = self.root()
        self.cache(r, '2026-09-01', '2026-09-30', vms={'vmA': ('2026-09-01', '2026-09-30', '2026-09-01', '2026-09-30'),
                                                       'vmB': ('2026-09-01', '2026-09-10', '2026-09-01', '2026-09-30')})
        self.update(r, '--physb-history')
        blk = self.block(r, 'vmB')
        self.assertTrue(blk['Coverage']['complete'])                       # the window is the whole month
        cov = blk['Populations']['none']['Coverage']
        self.assertEqual((cov['periodStart'], cov['periodEnd']), ('2026-09-01T00:00:00', '2026-09-30T23:45:00'))
        self.assertEqual((cov['firstPair'], cov['lastPair'], cov['pairedDays']),
                         ('2026-09-01T00:00:00', '2026-09-10T23:45:00', 10))
        self.assertEqual((cov['expectedMinutes'], cov['pairedMinutes']), (30 * 1440.0, 10 * 1440.0))
        self.assertEqual(cov['pairedCoverage'], round(1 / 3, 4))
        self.assertEqual(blk['Populations']['none']['status'], 'available')

    # -- partial months, extension and re-runs ---------------------------------------------
    def test_partial_month_is_flagged_then_extended_without_double_counting(self):
        r, fresh = self.root(), self.root()
        self.cache(r, '2026-09-01', '2026-09-15')
        self.update(r, '--physb-history')
        blk = self.block(r)
        self.assertFalse(blk['Coverage']['complete'])
        self.assertEqual(blk['Coverage']['windowEnd'], '2026-09-15')
        self.assertEqual(blk['Populations']['none']['Coverage']['pairedMinutes'], 15 * 1440.0)

        self.cache(r, '2026-09-01', '2026-09-30')                          # more data arrives
        self.update(r, '--physb-history')
        self.cache(fresh, '2026-09-01', '2026-09-30')
        self.update(fresh, '--physb-history')
        extended, single = self.block(r), self.block(fresh)
        self.assertTrue(extended['Coverage']['complete'])
        self.assertEqual(extended['Populations']['none']['Coverage']['pairedMinutes'], 30 * 1440.0)
        self.assertEqual(extended, single)                                 # recomputed, never merged

    def test_rerun_is_idempotent(self):
        r = self.root()
        self.cache(r, '2026-09-01', '2026-09-30')
        self.update(r, '--physb-history')
        before = self.raw(r)
        p = self.update(r, '--physb-history', force=False)
        self.assertEqual(self.raw(r), before)
        self.assertIn('No new months to process', p.stderr)

    def test_backfill_adds_blocks_and_keeps_stored_physc(self):
        r = self.root()
        self.cache(r, '2026-09-01', '2026-09-30')
        self.update(r)                                                     # history without PhysB
        stored = self.partition(r)
        p = self.update(r, '--physb-history', force=False)
        self.assertIn('stored PhysC analysis is kept', p.stderr)
        after = self.partition(r)
        self.assertIsNotNone(self.block(r))
        self.assertEqual(after[MONTH]['Metadata']['LastUpdated'], stored[MONTH]['Metadata']['LastUpdated'])
        self.assertEqual(without_physb(after), without_physb(stored))     # I6
        once = self.raw(r)
        self.update(r, '--physb-history', force=False)
        self.assertEqual(self.raw(r), once)

    def test_month_reanalysed_without_the_flag_keeps_its_blocks(self):
        r = self.root()
        self.cache(r, '2026-09-01', '2026-09-15')
        self.update(r, '--physb-history')
        blk, marker = self.block(r), self.partition(r)[MONTH]['Metadata']['PhysB']
        self.cache(r, '2026-09-01', '2026-09-30')
        self.update(r)                                                     # reanalysed, flag absent
        self.assertEqual(self.block(r), blk)                               # carried, its window says so
        self.assertEqual(self.partition(r)[MONTH]['Metadata']['PhysB'], marker)
        p = self.update(r, '--physb-history', force=False)                 # now brought up to date
        self.assertIn('stored PhysC analysis is kept', p.stderr)
        self.assertTrue(self.block(r)['Coverage']['complete'])

    def test_narrower_cache_never_replaces_a_wider_block(self):
        r = self.root()
        self.cache(r, '2026-09-01', '2026-09-30')
        self.update(r, '--physb-history')
        blk = self.block(r)
        self.cache(r, '2026-09-11', '2026-09-30')                          # rolling retention
        before = self.raw(r)
        self.update(r, '--physb-history', force=False)
        self.assertEqual(self.raw(r), before)                              # nothing to do
        p = self.update(r, '--physb-history')                              # forced re-analysis
        self.assertIn('1 kept', p.stderr)
        self.assertEqual(self.block(r), blk)
        marker = self.partition(r)[MONTH]['Metadata']['PhysB']
        self.assertEqual((marker['vmsKept'], marker['vmsWritten'], marker['windowStart']), (1, 0, '2026-09-11'))

    def test_older_engine_cannot_overwrite_daily_history(self):
        r = self.root()
        self.cache(r, '2026-09-01', '2026-09-04')
        self.update(r, '--physb-history')
        before = self.raw(r)
        engine = os.path.join(r, 'nfit')
        with open(engine) as fh:
            code = fh.read()
        with open(engine, 'w') as fh:
            fh.write(code.replace('daily_paired_profile_version => 1', 'daily_paired_profile_version => 0'))
        result = subprocess.run(['perl', os.path.join(r, 'nfit-profile'), '--update-history', '--physb-history',
                                 '--nmondir', os.path.join(r, 'stage', SERIAL)], capture_output=True, text=True, cwd=r)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('supports daily paired profiles', result.stderr)
        self.assertEqual(self.raw(r), before)

    def test_changed_semantics_recompute_the_block(self):
        r = self.root()
        self.cache(r, '2026-09-01', '2026-09-30')
        self.update(r, '--physb-history')
        path = os.path.join(r, 'history', SERIAL, f'nfit.hist.{MONTH}.json')
        part = self.partition(r)
        part[MONTH]['Metadata']['PhysB']['derivationVersion'] = 'paired-1'
        part[MONTH]['MonthlyWorkloadAnalysis']['vmA']['PhysBMetrics']['Source']['derivationVersion'] = 'paired-1'
        with open(path, 'w') as fh:
            json.dump(part, fh)
        p = self.update(r, '--physb-history', force=False)
        self.assertIn('out of date', p.stderr)
        self.assertEqual(self.block(r)['Source']['derivationVersion'], 'paired-2')
        self.assertEqual(self.partition(r)[MONTH]['Metadata']['PhysB']['derivationVersion'], 'paired-2')

    # -- time zones ---------------------------------------------------------------------
    def test_month_scheduling_does_not_depend_on_the_local_timezone(self):
        """Cache timestamps are naive and parsed on the UTC scale; months must
        be the cache's own months in every local zone, either side of UTC."""
        results = {}
        for tz in ('UTC', 'Africa/Johannesburg', 'America/New_York', 'Pacific/Kiritimati'):
            r = self.root()
            self.cache(r, '2026-08-15', '2026-09-30')
            self.update(r, '--physb-history', tz=tz)
            self.assertEqual([m for m in self.months(r) if m.endswith('.json')],
                             ['nfit.hist.2026-08.json', 'nfit.hist.2026-09.json'], tz)
            aug, sep = self.partition(r, '2026-08'), self.partition(r)
            self.assertEqual((aug['2026-08']['Metadata']['ProcessedStartDate'], aug['2026-08']['Metadata']['ProcessedEndDate'],
                              sep[MONTH]['Metadata']['ProcessedStartDate'], sep[MONTH]['Metadata']['ProcessedEndDate']),
                             ('2026-08-15', '2026-08-31', '2026-09-01', '2026-09-30'), tz)
            self.assertEqual(sep[MONTH]['MonthlyWorkloadAnalysis']['vmA']['PhysBMetrics']['Coverage']['monthEnd'],
                             '2026-09-30', tz)
            blocks = {m: p[m]['MonthlyWorkloadAnalysis']['vmA']['PhysBMetrics'] for m, p in (('2026-08', aug), (MONTH, sep))}
            results[tz] = (without_physb(aug), without_physb(sep), blocks)
        for tz, res in results.items():
            self.assertEqual(res, results['UTC'], tz)


if __name__ == '__main__':
    unittest.main(verbosity=1)
