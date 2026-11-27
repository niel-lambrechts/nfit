"""Checks on the resolved plan (v10 B7): the joint evidence and its replay, through the real
analysis on a synthetic cache of 30 days. One pool of 6 processing units, four LPARs.
On three nights the pool is at its maximum for half an hour while all are busy.

  online  3.00 on 4 VPs, sized on its online hours (an O profile): its night-time
          busy work of 1.20 is above its reduced entitlement. Exposed outside its
          profile hours only: named, its profile unchanged, no hold.
  night   4.00 on 6 VPs and pair 2.00 on 3 VPs, sized on all hours: cut protection
          puts their contested level into the target, so the replay finds nothing
          in profile hours. With cut protection switched off the same minutes show
          both exposed together.
  idle    0.50 on 1 VP, nearly idle.

The replay is of observed demand and competition. It changes no figure.
"""
import calendar
import glob
import gzip
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import member_exposure_test as mx

FIRST = calendar.timegm((2026, 9, 1, 0, 0, 0))
DAYS = 30
POOL = 6
LPARS = {'online': (3.0, 4, 0), 'night': (4.0, 6, 0), 'pair': (2.0, 3, 0), 'idle': (0.5, 1, 0)}


def minute_of(vm, day, minute):
    busy_day = day in (5, 12, 19)
    night = 120 <= minute < 150            # 02:00 to 02:30 UTC
    if vm == 'online':
        if busy_day and night:
            return 1.30, 1.20, 2.0
        return (1.00, 0.80, 2.0) if 480 <= minute < 1020 else (0.30, 0.20, 1.0)
    if vm == 'night':
        return (3.60, 3.40, 6.0) if (busy_day and night) else (0.60, 0.40, 1.0)
    if vm == 'pair':
        return (1.00, 0.90, 2.0) if (busy_day and night) else (0.40, 0.30, 1.0)
    return 0.05, 0.03, 0.2


def build(root, serial):
    cache = os.path.join(root, 'stage', serial)
    os.makedirs(cache)
    last = FIRST + DAYS * 86400 - 60
    header = 'Timestamp,VMName,PhysC,RunQ,PhysB'
    with open(os.path.join(cache, '.nfit.cache.data'), 'w') as fh, \
         gzip.open(os.path.join(cache, '.nfit.cache.pool.observations.csv.gz'), 'wt') as pool:
        fh.write(header + '\n')
        pool.write(','.join(mx.HEADER) + '\n')
        for day in range(DAYS):
            lines, rows = [], []
            for minute in range(1440):
                t = FIRST + day * 86400 + minute * 60
                stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t))
                total = 0.0
                for vm in LPARS:
                    c, b, q = minute_of(vm, day, minute)
                    total += c
                    lines.append(f'{stamp},{vm},{c:.4f},{q:.4f},{b:.4f}')
                rows.append(mx.obs(t + 15, 'online', 20, POOL, round(total, 4), 20.0))
            fh.write('\n'.join(lines) + '\n')
            pool.write('\n'.join(rows) + '\n')
    with open(os.path.join(cache, '.nfit.cache.manifest'), 'w') as fh:
        fh.write(f'cache_build_status: success\ndata_start: 2026-09-01\ndata_end: 2026-09-30 23:59:00\n'
                 f'data_schema_version: 2\ndata_columns: {header}\ndata_revision: synthetic-1\n')
    states = {}
    for vm, (ent, vp, capped) in LPARS.items():
        states[vm] = [{'start_epoch': FIRST, 'end_epoch': last, 'duration_days': DAYS, 'metadata': {
            'proc_clock': '3800MHz', 'serial_number': serial, 'proc_type': 'PowerPC_POWER10', 'proc_version': '2.0', 'max_cpu': 8,
            'smt': 8, 'entitlement': ent, 'capped': capped, 'virtual_cpus': vp, 'pool_id': 20, 'pool_cpu': POOL}}]
    with open(os.path.join(cache, '.nfit.cache.states'), 'w') as fh:
        json.dump(states, fh)
    with open(os.path.join(cache, '.nfit_stage_id'), 'w') as fh:
        fh.write('synthetic\n')
    return cache


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class JointCheckTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='joint-check-')
        os.makedirs(os.path.join(cls.root, 'etc'))
        os.makedirs(os.path.join(cls.root, 'output'))
        for prog in ('nfit', 'nfit-profile'):
            shutil.copy(os.path.join(mx.REPO, prog), cls.root)
        for name in mx.TRACKED_ETC:
            src = os.path.join(mx.REPO, 'etc', name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(cls.root, 'etc', name))
        open(os.path.join(cls.root, 'etc', 'nfit.seasonality.cfg'), 'w').close()
        cache = build(cls.root, 'SYNJC')
        cls.docs = {}
        for case, extra in (('protected', []), ('unprotected', ['--no-cut-protection'])):
            store = os.path.join(cache, '.nfit.store', 'proposals')
            shutil.rmtree(store, ignore_errors=True)
            proc = subprocess.run(['perl', os.path.join(cls.root, 'nfit-profile'), '--nmondir', cache, '--runq-perc-behavior=match'] + extra,
                                  cwd=cls.root, capture_output=True, text=True, timeout=280)
            assert proc.returncode == 0, proc.stderr[-1500:]
            plan, = glob.glob(os.path.join(store, 'v1', 'state-based', '*.json'))
            with open(plan) as fh:
                cls.docs[case] = json.load(fh)
            cls.docs[case]['_bytes'] = os.path.getsize(plan)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def result(self, case):
        return self.docs[case]['jointCheck']['results']['20']

    def test_the_evidence_is_saved_whole_and_small(self):
        doc = self.docs['protected']
        ev = doc['jointCheck']['evidence']['20']
        self.assertEqual((ev['members'], ev['truncated'], ev['readingCount'], len(ev['readings'])), (['idle', 'night', 'online', 'pair'], 0, 90, 90))
        self.assertEqual(ev['support']['online'], {'paired': 43200, 'classified': 43200, 'contested': 90})   # 3 nights of 30 minutes
        self.assertEqual(ev['notAssessed'], {})
        # Only readings above the lowest entitlement the LPAR can end at are kept: here those of one LPAR.
        self.assertEqual({r[1] for r in ev['readings']}, {ev['members'].index('online')})
        self.assertEqual({(r[2], r[3]) for r in ev['readings']}, {(1.2, 0)})                              # min(PhysB, installed); outside its hours
        self.assertLess(len(json.dumps(doc['jointCheck'])), 0.10 * doc['_bytes'])
        self.assertEqual(doc['jointCheck']['rule'], 'joint-check-1')
        with open(glob.glob(os.path.join(self.root, 'output', '*state-based*.log'))[0], encoding='utf-8') as fh:
            log = fh.read()
        self.assertIn('Contested minutes observed for them: 360 LPAR-minutes, all hours.', log)            # 4 LPARs x 90

    def test_this_cycle_and_the_full_target_are_assessed_apart(self):
        r = self.result('protected')
        self.assertEqual(r['state'], 'replayed')
        cycle, target = r['levels']['online']
        self.assertGreater(cycle, target)                                                                   # the staged figure of this cycle
        self.assertAlmostEqual(r['cycle']['allHours']['shortfall'], 90 * (1.2 - cycle), places=2)
        self.assertAlmostEqual(r['target']['allHours']['shortfall'], 90 * (1.2 - target), places=2)

    def test_exposure_outside_profile_hours_is_reported_apart_and_named(self):
        r = self.result('protected')
        for level in ('cycle', 'target'):
            self.assertEqual(r[level]['profileHours']['poolMinutes'], 0)                                    # cut protection holds in profile hours
            other = r[level]['otherHours']
            self.assertEqual((other['poolMinutes'], other['lparMinutes'], other['mostTogether'], other['longestEpisode'], other['days']), (90, 90, 1, 30, 3))
            self.assertEqual(list(other['members']), ['online'])
        # No figure is changed for it: the LPAR keeps its online-hours profile and its reduction.
        m = self.docs['protected']['members']['online']
        self.assertTrue(m['planningProfile'].startswith('O'))
        self.assertLess(m['profiles'][m['planningProfile']]['entitlement'], 3.0)

    def test_the_protected_level_is_met_on_the_final_figures(self):
        levels = self.docs['protected']['jointCheck']['protectedLevel']
        self.assertEqual({vm: (o['cycle']['outcome'], o['target']['outcome']) for vm, o in levels.items()},
                         {vm: ('met', 'met') for vm in LPARS})
        self.assertEqual(levels['night']['target']['required'], 3.48)                                       # 3.40 busy + 0.40 of the 0.20 above it (tier 3)
        self.assertEqual(levels['online']['target']['required'], 0)                                         # no competition in its profile hours

    def test_complete_evidence_is_said_to_be_complete(self):
        self.assertEqual(self.result('protected')['completeness'], {'state': 'complete', 'members': 4, 'assessed': 4, 'notAssessed': {}})

    def test_without_cut_protection_members_are_exposed_together_in_profile_hours(self):
        r = self.result('unprotected')
        p = r['target']['profileHours']
        self.assertEqual((p['poolMinutes'], p['mostTogether'], p['longestEpisode'], p['days']), (90, 2, 30, 3))
        self.assertEqual(p['lparMinutes'], 180)                                                             # two LPARs in each of the 90 pool-minutes
        self.assertEqual(sorted(p['members']), ['night', 'pair'])
        self.assertEqual(p['together'], {'2': 90})
        self.assertEqual(r['target']['allHours']['mostTogether'], 3)                                        # with the LPAR exposed outside its hours
        # No protected level was recorded in this run: not known, and not reported as met.
        levels = self.docs['unprotected']['jointCheck']['protectedLevel']
        self.assertEqual({o['target']['outcome'] for o in levels.values()}, {'notKnown'})

    def test_no_figure_is_changed_by_the_check(self):
        for vm in LPARS:
            m = self.docs['protected']['members'][vm]
            p = m['profiles'][m['planningProfile']]
            self.assertEqual(str(m['published']['entitlement']), '%.2f' % p['currentCycle']['entitlement'])


if __name__ == '__main__':
    unittest.main()
