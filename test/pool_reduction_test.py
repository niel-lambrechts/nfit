"""Pool reduction candidates (v10 B6) in a standard run, on synthetic caches of
one-minute data.

Frame SYNPR, 30 days (September 2026):
  pool 20  maximum 8: a quiet pool with one eight-minute peak. A conditional
           candidate, listed for review and never among the actions.
  pool 21  maximum 4: at its maximum on two days. No reduction.
  pool 23  maximum 8: one day without pool observations. Not offered.
  pool 24  maximum 6: its member gains a VP mid-period. Not offered.
Configured events: one inside the period, one that runs past its end, one that
does not occur in it.
Frame SYNPF, 28 days: pool 30 peaks on day 5; the frame is without spare cores
for ten quiet minutes of day 20. Not offered, for the frame.
"""
import calendar
import glob
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import member_exposure_test as mx

FIRST = calendar.timegm((2026, 9, 1, 0, 0, 0))
FRAMES = {
    'SYNPR': {'days': 30, 'members': {   # LPAR: (entitlement, VPs, pool, pool maximum)
        'a1': (2.0, 4, 20, 8), 'a2': (1.5, 2, 20, 8), 'b1': (2.0, 4, 21, 4), 'd1': (1.0, 2, 23, 8), 'e1': (1.0, 2, 24, 6)}},
    'SYNPF': {'days': 28, 'members': {'f1': (1.0, 2, 30, 6)}},
}
EVENTS = '''[Global]
min_history_days = 21
[month-close]
model = recency_decay
period = monthly
day_of_period = -1
duration_days = 3
[audit]
model = multiplicative_seasonal
dates = 2026-09-29:2026-10-02
[year-end]
model = multiplicative_seasonal
dates = 2025-12-28:2025-12-31
[month-close-adaptive]
model = adaptive_peak
source_event = month-close
'''


def cpu(vm, day, minute):
    if vm == 'a1':
        return 1.90 if (day == 12 and 540 <= minute < 548) else 0.60
    if vm == 'a2':
        return 0.40
    if vm == 'b1':
        return 4.00 if (day in (5, 6) and 600 <= minute < 630) else 1.00
    if vm == 'f1':
        return 2.40 if (day == 5 and 600 <= minute < 610) else 0.50
    return 0.50


def frame_busy(serial, day, minute):
    return 63.9 if (serial == 'SYNPF' and day == 20 and 120 <= minute < 130) else 20.0      # of 64: no spare cores, at a quiet time


def build(root, serial):
    spec = FRAMES[serial]
    days, members = spec['days'], spec['members']
    cache = os.path.join(root, 'stage', serial)
    os.makedirs(cache)
    last = FIRST + days * 86400 - 60
    header = 'Timestamp,VMName,PhysC,RunQ,PhysB'
    with open(os.path.join(cache, '.nfit.cache.data'), 'w') as fh:
        fh.write(header + '\n')
        for day in range(days):
            lines = []
            for minute in range(1440):
                stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(FIRST + day * 86400 + minute * 60))
                for vm in members:
                    c = cpu(vm, day, minute)
                    lines.append(f'{stamp},{vm},{c:.4f},{c:.4f},{c * 0.8:.4f}')
            fh.write('\n'.join(lines) + '\n')
    end = time.strftime('%Y-%m-%d', time.gmtime(last))
    with open(os.path.join(cache, '.nfit.cache.manifest'), 'w') as fh:
        fh.write(f'cache_build_status: success\ndata_start: 2026-09-01\ndata_end: {end} 23:59:00\n'
                 f'data_schema_version: 2\ndata_columns: {header}\ndata_revision: synthetic-1\n')
    states = {}
    for vm, (ent, vp, pool, size) in members.items():
        def md(vps):
            return {'proc_clock': '3800MHz', 'serial_number': serial, 'proc_type': 'PowerPC_POWER10', 'proc_version': '2.0', 'max_cpu': 8,
                    'smt': 8, 'entitlement': ent, 'capped': 0, 'virtual_cpus': vps, 'pool_id': pool, 'pool_cpu': size}
        if vm == 'e1':                                            # a VP added on day 15
            cut = FIRST + 15 * 86400
            states[vm] = [{'start_epoch': FIRST, 'end_epoch': cut - 60, 'duration_days': 15, 'metadata': md(vp)},
                          {'start_epoch': cut, 'end_epoch': last, 'duration_days': days - 15, 'metadata': md(vp + 1)}]
        else:
            states[vm] = [{'start_epoch': FIRST, 'end_epoch': last, 'duration_days': days, 'metadata': md(vp)}]
    with open(os.path.join(cache, '.nfit.cache.states'), 'w') as fh:
        json.dump(states, fh)
    with open(os.path.join(cache, '.nfit_stage_id'), 'w') as fh:
        fh.write('synthetic\n')
    pools = {}
    for vm, (ent, vp, pool, size) in members.items():
        pools.setdefault(pool, (size, []))[1].append(vm)
    with gzip.open(os.path.join(cache, '.nfit.cache.pool.observations.csv.gz'), 'wt') as fh:
        fh.write(','.join(mx.HEADER) + '\n')
        for day in range(days):
            rows = []
            for minute in range(1440):
                t = FIRST + day * 86400 + minute * 60
                for pool, (size, vms) in pools.items():
                    if pool == 23 and day == 28:                   # one day without pool observations, inside the month close
                        continue
                    busy = round(sum(cpu(vm, day, minute) for vm in vms), 4)
                    rows.append(mx.obs(t + 15, vms[0], pool, size, busy, frame_busy(serial, day, minute)))
            fh.write('\n'.join(rows) + '\n')
    return cache


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class PoolReductionTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='pool-reduction-')
        os.makedirs(os.path.join(cls.root, 'etc'))
        os.makedirs(os.path.join(cls.root, 'output'))
        for prog in ('nfit', 'nfit-profile'):
            shutil.copy(os.path.join(mx.REPO, prog), cls.root)
        for name in mx.TRACKED_ETC:
            src = os.path.join(mx.REPO, 'etc', name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(cls.root, 'etc', name))
        with open(os.path.join(cls.root, 'etc', 'nfit.seasonality.cfg'), 'w') as fh:
            fh.write(EVENTS)
        cls.log, cls.plan, cls.csv = {}, {}, {}
        for serial in FRAMES:
            cache = build(cls.root, serial)
            proc = subprocess.run(['perl', os.path.join(cls.root, 'nfit-profile'), '--nmondir', cache, '--runq-perc-behavior=match'],
                                  cwd=cls.root, capture_output=True, text=True, timeout=280)
            assert proc.returncode == 0, proc.stderr[-1500:]
            log, = [f for f in glob.glob(os.path.join(cls.root, 'output', '*.log')) if serial in f and 'state-based' in f]
            with open(log, encoding='utf-8') as fh:
                cls.log[serial] = fh.read()
            plan, = glob.glob(os.path.join(cache, '.nfit.store', 'proposals', 'v1', 'state-based', '*.json'))
            with open(plan) as fh:
                cls.plan[serial] = json.load(fh)
            cls.csv[serial] = ''.join(open(f).read() for f in sorted(glob.glob(os.path.join(cls.root, 'output', '*.csv'))) if serial in f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def candidates(self, serial='SYNPR'):
        return {int(k): v for k, v in self.plan[serial]['poolReduction']['candidates'].items()}

    def evidence(self, serial='SYNPR'):
        return {int(k): v for k, v in self.plan[serial]['poolReduction']['evidence'].items()}

    def flat(self, serial='SYNPR'):
        return re.sub(r'\s+', ' ', self.log[serial])

    def test_a_quiet_pool_is_a_conditional_candidate(self):
        c, e = self.candidates()[20], self.evidence()[20]
        self.assertEqual((c['verdict'], c['eligible'], c['reasons']), ('candidate', True, []))
        self.assertEqual((e['peak'], e['peakDay'][:10], e['days'], e['coverage'], e['worstDayCoverage'], e['cadenceMinutes']), (2.3, '2026-09-13', 30, 1, 1, 1))
        self.assertEqual(c['workload']['units'], 3)                                   # 2.30 outside the tolerance of 3
        self.assertEqual(c['candidate'], max(c['structural']['full'], c['structural']['cycle'], 3))
        self.assertEqual(c['installedEntitlement'], 3.5)
        self.assertEqual(c['takeover']['state'], 'none')
        codes = [p['code'] for p in c['prerequisites']]
        self.assertIn('reserve', codes)                                               # the reserve is not collected: never taken as zero
        self.assertEqual(('memberReductions' in codes), c['candidate'] < 4)           # installed 3.50 holds the maximum at 4 until reduced
        self.assertIn(f"Pool 20: reduction candidate, maximum 8 to {c['candidate']} processing units. For planner review, not an instruction", self.flat())
        self.assertIn('no exceedance observed at the recorded resolution in 30 days', self.flat())

    def test_the_candidate_is_an_observation_never_an_action(self):
        log = self.log['SYNPR']
        self.assertIn('\nPool Reduction Candidates\n', log)
        self.assertLess(log.index('\nPool Use\n'), log.index('\nPool Reduction Candidates\n'))
        actions = log[log.index('Recommended Actions'):]
        numbered = [line for line in actions.splitlines() if re.match(r'\s*\d+\.\s', line)]
        self.assertFalse([line for line in numbered if 'reduction' in line.lower()], 'no reduction among the numbered actions')
        self.assertNotRegex(self.flat(), r'(?i)no clipping')

    def test_lpar_sizing_is_not_changed_by_the_candidate(self):
        self.assertNotIn('reduction candidate', self.csv['SYNPR'])
        for vm in ('a1', 'a2'):
            self.assertNotIn('poolReduction', self.plan['SYNPR']['members'][vm])

    def test_a_pool_that_reached_its_maximum_is_no_reduction(self):
        c = self.candidates()[21]
        self.assertEqual((c['verdict'], c['candidate'], c['needed'] > c['maximum']), ('none', None, True))     # its use at the maximum needs more, not less
        self.assertNotIn('Pool 21: reduction', self.flat())

    def test_a_day_without_observations_is_not_offered(self):
        c, e = self.candidates()[23], self.evidence()[23]
        self.assertEqual((e['daysObserved'], e['worstDayCoverage']), (29, 0))
        self.assertEqual((c['verdict'], [r['code'] for r in c['reasons']]), ('withheld', ['coverage', 'dayCoverage']))
        self.assertIn('that figure is not sufficient for a reduction decision', self.flat())

    def test_a_member_vp_change_in_the_period_is_not_offered(self):
        c, e = self.candidates()[24], self.evidence()[24]
        self.assertEqual([d[:10] for d in e['changes']], ['2026-09-16'])
        self.assertEqual([r['code'] for r in c['reasons']], ['changed'])
        self.assertEqual(e['changeKinds'], [['VPs']])
        self.assertIn('the pool or its members changed in the period (2026-09-16: VPs): re-measure after the change', self.flat())

    def test_events_missing_or_incompletely_observed_are_prerequisites(self):
        e = {x['event']: x['state'] for x in self.evidence()[20]['events']}
        # No entry for [Global], nor for an event that follows another and has no calendar of its own.
        self.assertEqual(e, {'month-close': 'observed', 'audit': 'incomplete', 'year-end': 'notInPeriod'})
        # Each pool on its own observations: the day pool 23 lacks falls in the month close, and only pool 23 says so.
        self.assertEqual({x['event']: x['state'] for x in self.evidence()[23]['events']}['month-close'], 'incomplete')
        need = {p['code']: p['text'] for p in self.candidates()[20]['prerequisites']}
        self.assertEqual(need['eventsMissing'], 'the period does not contain year-end')
        self.assertEqual(need['eventsIncomplete'], 'incompletely observed in the period: audit')
        self.assertEqual(need['futureEvents'], 'future event capacity has not been checked')                     # no saved seasonal plan here

    def test_the_frame_without_spare_cores_away_from_the_pool_peak(self):
        c, e = self.candidates('SYNPF')[30], self.evidence('SYNPF')[30]
        self.assertEqual((e['peakDay'][:10], e['frame']['assessed'], e['frame']['withoutSpareCores']), ('2026-09-06', True, 10))
        self.assertEqual((c['verdict'], [r['code'] for r in c['reasons']]), ('withheld', ['frame']))
        self.assertIn('the frame was without spare cores in 10 minutes of the period: use may have been held back', self.flat('SYNPF'))


if __name__ == '__main__':
    unittest.main()
