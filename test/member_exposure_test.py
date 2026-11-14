#!/usr/bin/env python3
# ==============================================================================
# Tests for member busy exposure (member-busy-exposure-1):
#   nfit-profile --update-history --pool-history   MemberExposure blocks
#   nfit-profile --pool-exposure                   Entitlement Cut Exposure
# on a small synthetic schema-2 cache (PhysB column) with one-minute samples.
#
# Covered: thresholds (installed entitlement and the month's lower profile
# values), populations (all hours, online weekdays, batch), paired minutes
# (invalid and missing PhysB excluded), excess core-minutes, local scarcity
# with competition, local scarcity of an LPAR alone in its pool (own width,
# not competition), physical scarcity, unclassified minutes where the pool is
# unobserved, a joint run across midnight credited to the day it ends, a
# dedicated LPAR left out, every daily array against an independent
# reference, an unchanged second run, the rationale section of a standard run
# at the exact proposed entitlement, and the CSV unchanged by the option.
# ==============================================================================
import calendar
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
TRACKED_ETC = ('nfit.profiles.cfg', 'nfit.profiles.cfg.default', 'nfit.mgsys.cfg.default',
               'nfit.scenarios.cfg.default')
SERIAL = 'SYNEX'
MONTH = '2026-09'
DAY1 = calendar.timegm((2026, 9, 1, 0, 0, 0))          # a Tuesday
DAYS = 2
NS = 1_000_000_000
HEADER = ['time_ns', 'host', 'series_id', 'pool_id', 'phys_cpus_pool', 'idle_cores', 'busy_cores',
          'scaled_busy_cores', 'max_cores', 'global_pool_busy_cores', 'global_pool_tot_cores',
          'max_capacity_cores', 'entitled_capacity_cores', 'max_capacity_raw', 'entitled_capacity_raw',
          'max_capacity', 'entitled_capacity']
COLUMNS = ['aboveMinutes', 'excessCoreMinutes', 'aboveScarceMinutes', 'aboveCompetitionMinutes',
           'aboveOwnWidthMinutes', 'aboveUnclassifiedMinutes', 'longestJointRunMinutes']
PHYS_SIZE = 64.0
CONFIG = {   # vm: (entitlement, VPs, pool, pool size)
    'vmA': (2.0, 4, 7, 4.0),
    'vmB': (1.0, 2, 7, 4.0),
    'vmS': (1.5, 2, 9, 2.0),
}


def perl_ready():
    return (shutil.which('perl') is not None and
            subprocess.run(['perl', '-MJSON', '-MList::MoreUtils', '-e1'], capture_output=True).returncode == 0)


def at(day, hh, mm):
    return DAY1 + day * 86400 + hh * 3600 + mm * 60


def between(t, start, minutes):
    return start <= t < start + minutes * 60


W1 = at(0, 10, 0)       # 30 min, online: vmA above its entitlement, pool 7 full with vmB consuming
W2 = at(0, 20, 0)       # 10 min, batch: vmA above, pool 7 open
W3 = at(0, 23, 50)      # 20 min across midnight: vmA above, pool 7 full
W4 = at(1, 12, 0)       # 15 min: vmA above, shared pool scarce
W5 = at(1, 15, 0)       # 10 min: vmA above, pool 7 unobserved
W6 = at(0, 14, 0)       # 20 min: vmS fills pool 9 alone, at its own width
BAD_PAIR = at(1, 5, 0)  # 5 min: B > C (invalid pair)
NO_B = at(1, 5, 10)     # 1 min: PhysB missing


def cpu(vm, t):
    """(PhysC, PhysB as text) of one LPAR in the minute starting at t."""
    if vm == 'vmA':
        if between(t, W1, 30) or between(t, W3, 20):
            return 3.0, '2.5'
        if between(t, W2, 10):
            return 2.5, '2.2'
        if between(t, W4, 15):
            return 2.5, '2.3'
        if between(t, W5, 10):
            return 2.2, '2.1'
        if between(t, BAD_PAIR, 5):
            return 1.0, '1.5'
        if between(t, NO_B, 1):
            return 1.0, ''
        return 1.0, '0.5'
    if vm == 'vmB':
        if between(t, W1, 30) or between(t, W3, 20):
            return 1.0, '0.95'      # within its installed 1.0, above a cut, while vmA fills the pool
        return 0.9, '0.6'
    if vm == 'vmS':
        if between(t, W6, 20):
            return 2.0, '1.9'
        return 0.5, '0.3'
    return 3.0, '1.0'       # vmX, dedicated


def physical_busy(t):
    return 63.8 if between(t, W4, 15) else 20.0


def obs(epoch, host, pool, size, busy, gbusy):
    row = dict.fromkeys(HEADER, '')
    row.update({'time_ns': str(int(round(epoch * NS))), 'host': host, 'series_id': 's' + host, 'pool_id': pool,
                'phys_cpus_pool': size, 'busy_cores': busy, 'global_pool_busy_cores': gbusy,
                'global_pool_tot_cores': PHYS_SIZE})
    return ','.join(str(row[h]) for h in HEADER)


def pool_rows():
    rows = []
    for m in range(DAYS * 1440):
        t = DAY1 + 60 * m
        g = physical_busy(t)
        if not between(t, W5, 10):
            busy7 = round(cpu('vmA', t)[0] + cpu('vmB', t)[0], 4)
            rows += [obs(t + 10, 'vmA', 7, 4, busy7, g), obs(t + 20, 'vmB', 7, 4, busy7, g)]
        rows.append(obs(t + 15, 'vmS', 9, 2, cpu('vmS', t)[0], g))
    return rows


def population_of(flags):
    f = 'online' if re.search(r' -online\b', flags) else 'batch' if re.search(r' -batch\b', flags) else 'none'
    return f + ('+no-weekends' if re.search(r' -no-weekends\b', flags) else '')


def admits(pop, t):
    g = time.gmtime(t)
    filt, _, rule = pop.partition('+')
    if rule == 'no-weekends' and g.tm_wday >= 5:
        return False
    mod = g.tm_hour * 60 + g.tm_min
    return filt == 'none' or (filt == 'online' and 480 <= mod < 1020) or (filt == 'batch' and (mod >= 1080 or mod < 360))


def reference(vm, pop, thresholds):
    """{date: (expected, paired, classified, scarce, [per threshold columns])}, independently of nfit-profile."""
    ent, vp, pool, size = CONFIG[vm]
    out = {}
    runs = [[None, 0] for _ in thresholds]
    for m in range(DAYS * 1440):
        t = DAY1 + 60 * m
        date = time.strftime('%Y-%m-%d', time.gmtime(t))
        acc = out.setdefault(date, [0, 0, 0, 0, [[0, 0.0, 0, 0, 0, 0, 0] for _ in thresholds]])
        if not admits(pop, t):
            continue
        acc[0] += 1
        c, b = cpu(vm, t)
        if b == '' or float(b) > c + 1e-6:
            continue
        b = float(b)
        acc[1] += 1
        if pool == 7:
            pb = None if between(t, W5, 10) else cpu('vmA', t)[0] + cpu('vmB', t)[0]
        else:
            pb = cpu('vmS', t)[0]
        local = 'unknown' if pb is None else ('scarce' if size - pb <= max(0.10, 0.02 * size) + 1e-9 else 'open')
        idle = PHYS_SIZE - physical_busy(t)
        phys = 'scarce' if idle <= max(0.5, 0.01 * PHYS_SIZE) + 1e-9 else 'open'
        scarce = local == 'scarce' or phys == 'scarce'
        classified = scarce or (local == 'open' and phys == 'open')
        competition = phys == 'scarce' or (local == 'scarce' and pb - c >= max(0.10, 0.02 * size) - 1e-9)
        own = c >= 0.95 * vp - 1e-9
        acc[2] += classified
        acc[3] += scarce
        for k, thr in enumerate(thresholds):
            if b <= thr + 1e-9:
                continue
            e = acc[4][k]
            e[0] += 1
            e[1] += b - thr
            e[4] += own
            e[5] += not classified
            if scarce:
                e[2] += 1
                e[3] += competition
                runs[k][1] = runs[k][1] + 1 if runs[k][0] == t - 60 else 1
                runs[k][0] = t
                e[6] = max(e[6], runs[k][1])
    return out


@unittest.skipUnless(perl_ready(), 'perl with JSON and List::MoreUtils needed')
class MemberExposureTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.roots = []
        cls.root = cls.make_root()
        cls.first = cls.run_profile(cls.root, '--update-history', '--min-history-days', '1', '--pool-history', '--force')
        cls.bytes_first = cls.raw(cls.root)
        cls.second = cls.run_profile(cls.root, '--update-history', '--min-history-days', '1', '--pool-history')
        cls.bytes_second = cls.raw(cls.root)
        cls.populations, cls.hidden = {}, {'Peak_P-99W1'}
        with open(os.path.join(REPO, 'etc', 'nfit.profiles.cfg')) as fh:
            name = None
            for line in fh:
                hit = re.match(r'\s*\[(.+)\]\s*$', line)
                if hit:
                    name = hit.group(1)
                hit = re.match(r'\s*nfit_flags\s*=\s*(.*)$', line)
                if hit and name:
                    cls.populations[name] = population_of(' ' + hit.group(1))
                if name and re.match(r'\s*csv_output\s*=\s*false', line):
                    cls.hidden.add(name)

    @classmethod
    def tearDownClass(cls):
        for r in cls.roots:
            shutil.rmtree(r, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------------
    @classmethod
    def make_root(cls):
        r = tempfile.mkdtemp(prefix='member-exp-')
        cls.roots.append(r)
        os.makedirs(os.path.join(r, 'etc'))
        os.makedirs(os.path.join(r, 'output'))
        for prog in ('nfit', 'nfit-profile'):
            shutil.copy(os.path.join(REPO, prog), r)
        for name in TRACKED_ETC:
            src = os.path.join(REPO, 'etc', name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(r, 'etc', name))
        open(os.path.join(r, 'etc', 'nfit.seasonality.cfg'), 'w').close()
        d = os.path.join(r, 'stage', SERIAL)
        os.makedirs(d)
        end = DAY1 + DAYS * 86400
        header = 'Timestamp,VMName,PhysC,RunQ,PhysB'
        lines = [header]
        for t in range(DAY1, end, 60):
            stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t))
            for vm in ('vmA', 'vmB', 'vmS', 'vmX'):
                c, b = cpu(vm, t)
                lines.append(f'{stamp},{vm},{c:.4f},{c * 2:.4f},{b}')
        with open(os.path.join(d, '.nfit.cache.data'), 'w') as fh:
            fh.write('\n'.join(lines) + '\n')
        with open(os.path.join(d, '.nfit.cache.manifest'), 'w') as fh:
            fh.write('cache_build_status: success\ndata_start: 2026-09-01\ndata_end: 2026-09-02 23:59:00\n'
                     f'data_schema_version: 2\ndata_columns: {header}\ndata_revision: synthetic-1\n')
        last = end - 60

        def state(vm):
            ent, vp, pool, size = CONFIG.get(vm, (3.0, 3, None, None))
            md = {'proc_clock': '3800MHz', 'serial_number': SERIAL, 'proc_type': 'PowerPC_POWER10',
                  'proc_version': '2.0', 'max_cpu': vp, 'smt': 8, 'entitlement': ent, 'capped': 0,
                  'virtual_cpus': vp, 'pool_id': pool, 'pool_cpu': size}
            if pool is None:
                md = {k: v for k, v in md.items() if k not in ('pool_id', 'pool_cpu')}
            return [{'start_epoch': DAY1, 'end_epoch': last, 'duration_days': (last - DAY1) / 86400, 'metadata': md}]

        with open(os.path.join(d, '.nfit.cache.states'), 'w') as fh:
            json.dump({vm: state(vm) for vm in ('vmA', 'vmB', 'vmS', 'vmX')}, fh)
        with open(os.path.join(d, '.nfit_stage_id'), 'w') as fh:
            fh.write('synthetic\n')
        with gzip.open(os.path.join(d, '.nfit.cache.pool.observations.csv.gz'), 'wt') as fh:
            fh.write(','.join(HEADER) + '\n' + '\n'.join(pool_rows()) + '\n')
        return r

    @staticmethod
    def run_profile(r, *flags):
        cmd = ['perl', os.path.join(r, 'nfit-profile'), '--nmondir', os.path.join(r, 'stage', SERIAL)] + list(flags)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=280, cwd=r)
        assert p.returncode == 0, p.stderr[-1500:]
        return p

    @staticmethod
    def raw(r):
        with open(os.path.join(r, 'history', SERIAL, f'nfit.hist.{MONTH}.json'), 'rb') as fh:
            return fh.read()

    def month(self):
        return json.loads(self.bytes_first)[MONTH]

    def entry(self, vm):
        states = self.month()['MemberExposure'][vm]
        self.assertEqual(len(states), 1)
        return states[0]

    def columns(self, vm, pop, date, k=0):
        day = next(d for d in self.entry(vm)['populations'][pop]['daily'] if d['date'] == date)
        row = day['exposure'][k]
        return dict(zip(COLUMNS, row if row != 0 else [0] * len(COLUMNS))), day

    # -- block and marker --------------------------------------------------------------
    def test_block_and_marker(self):
        m = self.month()
        self.assertEqual(sorted(m['MemberExposure']), ['vmA', 'vmB', 'vmS'])        # vmX is dedicated
        ev = m['Metadata']['MemberExposureEvidence']
        self.assertEqual((ev['projection'], ev['status'], ev['columns'], ev['layers']),
                         ('member-busy-exposure-1', 'complete', COLUMNS,
                          {'local': 'poolBusyAgainstCeiling', 'physical': 'sharedPoolIdle'}))
        self.assertEqual((ev['windowStart'], ev['windowEndExclusive'], ev['dataRevision']),
                         ('2026-09-01T00:00:00Z', '2026-09-03T00:00:00Z', 'synthetic-1'))
        self.assertEqual(ev['rules']['classifier'], 'pool-scarcity-1')
        self.assertIn('MemberExposure-1', m['Metadata']['Features'])
        a = self.entry('vmA')
        self.assertEqual((a['pool'], a['scope'], a['entitlement'], a['virtualProcessors'], a['poolCeiling']),
                         (f'{SERIAL}:7', 'user', 2.0, 4, 4.0))
        self.assertIn('[INFO] Member exposure for 2026-09', self.first.stderr)

    def test_thresholds_are_installed_entitlement_and_lower_profile_values(self):
        values = self.month()['MonthlyWorkloadAnalysis']['vmA']['ProfileValues']
        for pop, block in self.entry('vmA')['populations'].items():
            th = block['thresholds']
            self.assertEqual((th[0]['value'], th[0]['basis']), (2.0, 'installedEntitlement'))
            expected = defaultdict(list)
            for p in block['profiles']:
                self.assertEqual(self.populations.get(p, pop), pop)
                self.assertNotIn(p, self.hidden)                    # actionable profiles only
                v = values.get(p)
                if isinstance(v, (int, float)) and 0 < round(v, 4) < 2.0 - 0.005:
                    expected[round(v, 4)].append(p)
            self.assertEqual([(t['value'], t['basis'], t['profiles']) for t in th[1:]],
                             [(v, 'monthlyProfileValue', sorted(expected[v])) for v in sorted(expected, reverse=True)])

    def test_every_daily_array_matches_the_reference(self):
        compared = 0
        for vm in ('vmA', 'vmB', 'vmS'):
            for pop, block in self.entry(vm)['populations'].items():
                ref = reference(vm, pop, [t['value'] for t in block['thresholds']])
                got = {d['date']: d for d in block['daily']}
                self.assertEqual(sorted(got), sorted(k for k, v in ref.items() if v[0]))
                for date, d in got.items():
                    e, p, c, s, cols = ref[date]
                    self.assertEqual((d['expectedMinutes'], d['pairedMinutes'], d['classifiedMinutes'], d['scarceMinutes']),
                                     (e, p, c, s), (vm, pop, date))
                    for k, col in enumerate(cols):
                        row = d['exposure'][k]
                        if col[0] == 0:
                            self.assertEqual(row, 0, (vm, pop, date, k))      # no minute above: a single 0
                            continue
                        self.assertEqual(row[0], col[0])
                        self.assertAlmostEqual(row[1], round(col[1], 4), places=4)
                        self.assertEqual(row[2:], col[2:], (vm, pop, date, k))
                        compared += 1
        self.assertGreater(compared, 20)

    # -- analytic cases at the installed entitlement ---------------------------------
    def test_competition_physical_scarcity_and_unclassified_minutes(self):
        day1, _ = self.columns('vmA', 'none', '2026-09-01')
        # W1 (30, pool full, vmB consuming) + W2 (10, open) + W3 before midnight (10, full)
        self.assertEqual((day1['aboveMinutes'], day1['aboveScarceMinutes'], day1['aboveCompetitionMinutes'],
                          day1['aboveOwnWidthMinutes']), (50, 40, 40, 0))
        self.assertAlmostEqual(day1['excessCoreMinutes'], 30 * 0.5 + 10 * 0.2 + 10 * 0.5, places=4)
        day2, raw = self.columns('vmA', 'none', '2026-09-02')
        # W3 after midnight (10, full) + W4 (15, shared pool scarce) + W5 (10, pool unobserved)
        self.assertEqual((day2['aboveMinutes'], day2['aboveScarceMinutes'], day2['aboveCompetitionMinutes'],
                          day2['aboveUnclassifiedMinutes']), (35, 25, 25, 10))
        self.assertEqual((raw['expectedMinutes'], raw['pairedMinutes']), (1440, 1440 - 6))   # 5 B > C, 1 missing B

    def test_a_joint_run_across_midnight_counts_in_full_on_the_day_it_ends(self):
        day1, _ = self.columns('vmA', 'none', '2026-09-01')
        day2, _ = self.columns('vmA', 'none', '2026-09-02')
        self.assertEqual((day1['longestJointRunMinutes'], day2['longestJointRunMinutes']), (30, 20))

    def test_an_lpar_alone_in_a_full_pool_is_at_its_own_width_not_competing(self):
        s, _ = self.columns('vmS', 'none', '2026-09-01')
        self.assertEqual((s['aboveMinutes'], s['aboveScarceMinutes'], s['aboveCompetitionMinutes'],
                          s['aboveOwnWidthMinutes'], s['longestJointRunMinutes']), (20, 20, 0, 20, 20))

    def test_populations_follow_the_profile_windows(self):
        online, raw = self.columns('vmA', 'online+no-weekends', '2026-09-01')
        self.assertEqual((raw['expectedMinutes'], online['aboveMinutes']), (540, 30))       # W1 only
        batch, raw = self.columns('vmA', 'batch', '2026-09-01')
        self.assertEqual((raw['expectedMinutes'], batch['aboveMinutes']), (720, 20))        # W2 and W3 before midnight

    # -- re-runs and the CSV ------------------------------------------------------------
    def test_an_identical_second_run_leaves_the_partition_unchanged(self):
        self.assertEqual(self.bytes_first, self.bytes_second)
        self.assertNotIn('Member exposure blocks absent', self.second.stderr)

    def test_rationale_reports_the_exact_cut_and_leaves_the_csv_unchanged(self):
        plain = self.run_profile(self.root)
        csv_plain = self.latest(self.root, '.csv')
        with_option = self.run_profile(self.root, '--pool-exposure')
        csv_option = self.latest(self.root, '.csv')
        with open(csv_plain) as fh:
            plain_rows = fh.read().splitlines()
        with open(csv_option) as fh:
            option_rows = fh.read().splitlines()
        self.assertEqual(plain_rows, option_rows)
        self.assertIn('[INFO] Entitlement cut exposure:', with_option.stderr)
        self.assertRegex(with_option.stderr, r'\[INFO\] Recommended actions: \d+')
        with open(self.latest(self.root, '.log'), encoding='utf-8') as fh:
            log = fh.read()
        section = log[log.index('Entitlement Cut Exposure\n'):]
        hit = re.search(r'\n  vmA: reduce entitlement from 2\.00 to ([0-9.]+) cores \((\S+), [^)]*\)\n', section)
        self.assertIsNotNone(hit, section[:1500])
        proposed = float(hit.group(1))
        self.assertLess(proposed, 2.0)
        ref = reference('vmA', self.populations[hit.group(2)], [proposed, 2.0])
        tot = [sum(v[4][0][i] for v in ref.values()) for i in range(6)]
        inst = [sum(v[4][1][i] for v in ref.values()) for i in range(6)]
        paired = sum(v[1] for v in ref.values())
        block = re.sub(r'\s+', ' ', section[hit.end():].split('\n  vm')[0])
        self.assertIn(f'busy above {proposed:.2f} cores in {tot[0]:,} of {paired:,} measured minutes', block)
        if inst[0] == tot[0]:
            # Every minute above the proposal was already above 2.00: no guaranteed minute is lost,
            # but the cut adds the difference in excess core-minutes (here all of vmA's minutes).
            self.assertIn("today's 2.00 covered none of them", block)
            self.assertIn(f"the cut adds {tot[1] - inst[1]:.2f} core-minutes of busy work above the guarantee", block)
            self.assertGreater(tot[1] - inst[1], 0)
        self.assertIn('Recommendation:', block)
        self.assertNotIn('removes no guarantee', section)
        for jargon in ('credible scarcity', 'paired', 'Classifier'):
            self.assertNotIn(jargon, section.split('Recommended Actions')[0].split('Key Terminology')[0])

    def test_advisory_needs_newly_exposed_busy_scarcity_and_competition(self):
        self.run_profile(self.root, '--pool-exposure')
        with open(self.latest(self.root, '.log'), encoding='utf-8') as fh:
            log = fh.read()
        section = log[log.index('Entitlement Cut Exposure\n'):]
        advised = []
        for vm in ('vmA', 'vmB', 'vmS'):
            hit = re.search(r'\n  ' + vm + r': reduce entitlement from ([0-9.]+) to ([0-9.]+) cores \((\S+), [^)]*\)\n', section)
            self.assertIsNotNone(hit, vm)
            installed, proposed = float(hit.group(1)), float(hit.group(2))
            ref = reference(vm, self.populations[hit.group(3)], [proposed, installed])
            col = lambda k, i: sum(v[4][k][i] for v in ref.values())
            scarce_total = sum(v[3] for v in ref.values())
            newly_comp = col(0, 3) - col(1, 3)
            block = re.sub(r'\s+', ' ', section[hit.end():].split('\n  vm')[0])
            if newly_comp > 0:
                advised.append(vm)
                self.assertIn(f'>> Recommendation: review before applying. Reducing {installed:.2f} to {proposed:.2f} removes '
                              f'the guarantee in {newly_comp} of the {scarce_total} minutes the pool was full while other LPARs were busy.', block)
            else:
                self.assertNotIn('>> Recommendation', block, vm)
                self.assertIn('Recommendation: no objection from the pool evidence', block, vm)
        # vmB is the positive case; vmA's exposed busy was already above its guarantee and
        # vmS fills its own pool, so neither is advised.
        self.assertEqual(advised, ['vmB'])
        self.assertIn('Review before applying: 1 cut on 1 LPAR (vmB)', section)
        actions = re.sub(r'\s+', ' ', log[log.index('Recommended Actions'):])
        self.assertIn('vmB (pool 7): review the cut from 1.00 to 0.90 before applying it', actions)
        self.assertIn('No additional action identified by the checks run.', actions)

    @staticmethod
    def latest(r, suffix):
        out = os.path.join(r, 'output')
        files = [os.path.join(out, f) for f in os.listdir(out) if f.endswith(suffix)]
        return max(files, key=os.path.getmtime)


if __name__ == '__main__':
    unittest.main()
