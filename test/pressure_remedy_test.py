#!/usr/bin/env python3
# ==============================================================================
# Pressure and remedy (nfit-profile, v10 milestone 2.4). On the member exposure
# fixture, each minute of busy work above an LPAR's installed entitlement is
# classified by what limited it. The remedy is decided at the planning level:
# the planning profile applied to PhysB (its hours, EMA smoothing restarted
# after a gap or invalid pair, --filter-above-perc rule and percentile).
#   - The planning level equals the engine's own PhysB profile value
#     (PhysBMetrics.ProfileValuesB in monthly history), for an all-hours and an
#     online, weekdays-only planning profile.
#   - The section's figures are recomputed here from the fixture's CPU and pool
#     definitions, and the remedy from the documented rule, for configurations
#     that exercise each branch: room for the proposals, no room, room shared
#     by two LPARs, VP width with the pool full, VP width with the pool open.
# ==============================================================================
import csv
import json
import re
import shutil
import unittest
from pathlib import Path

from one_entry_point_test import mx, run

EPS = 1e-9
POOL_OF = {'vmA': 7, 'vmB': 7, 'vmS': 9}
ALPHA = {'low': 0.03, 'medium': 0.08, 'high': 0.15, 'very-high': 0.30, 'extreme': 0.40}
PROFILE = {   # planning profile: (percentile, window, decay, filter-above-perc, hours, no weekends)
    'G3-95W15': (95, 15, 'medium', 20, 'none', False),
    'O3-95W15': (95, 15, 'medium', 20, 'online', True),
}
BUSY_OVERRIDE = {}      # (vm, minute) -> PhysB text written over the fixture's


def admits(hours, no_weekends, t):
    g = mx.time.gmtime(t)
    if no_weekends and g.tm_wday >= 5:
        return False
    m = g.tm_hour * 60 + g.tm_min
    return hours == 'none' or (hours == 'online' and 480 <= m < 1020) or (hours == 'batch' and (m >= 1080 or m < 360))


def busy(vm, t):
    c, b = mx.cpu(vm, t)[0], BUSY_OVERRIDE.get((vm, t), mx.cpu(vm, t)[1])
    return None if (b == '' or float(b) > c + 1e-6) else (c, float(b))


def quantile(sorted_values, p):
    n = len(sorted_values)
    rank = (n - 1) * p
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (rank - lo)


def f32(x):
    import struct
    return struct.unpack('<f', struct.pack('<f', x))[0]


def kind(vm, t, ceiling, vp, ent):
    c, b = busy(vm, t)
    if b <= ent + EPS:
        return 'none'
    own = c >= 0.95 * vp - EPS
    frame = mx.PHYS_SIZE - mx.physical_busy(t) <= max(0.50, 0.01 * mx.PHYS_SIZE) + EPS
    if POOL_OF[vm] == 7:
        observed = not mx.between(t, mx.W5, 10)
        pool_busy = round(mx.cpu('vmA', t)[0] + mx.cpu('vmB', t)[0], 4)
    else:
        observed, pool_busy = True, mx.cpu('vmS', t)[0]
    local = observed and ceiling - pool_busy <= max(0.10, 0.02 * ceiling) + EPS
    competition = frame or (local and pool_busy - c >= max(0.10, 0.02 * ceiling) - EPS)
    if local or frame:
        return 'both' if own else 'contested' if competition else 'alone'
    if own:
        return 'vpWidth' if observed else 'unknown'
    return 'open' if observed else 'unknown'


def model(vm, profile, ent, vp, ceiling):
    """Planning level, qualifying-minute counts by kind, and all limited minutes."""
    pct, window, decay, filt, hours, no_weekends = PROFILE[profile]
    smoothed, last, queue, prev = [], None, [], None
    for t in range(mx.DAY1, mx.DAY1 + mx.DAYS * 86400, 60):
        if not admits(hours, no_weekends, t) or busy(vm, t) is None:
            continue
        b = busy(vm, t)[1]
        if last is None or t != last + 60:
            queue, prev = [], None
        last = t
        s = None
        if prev is None:
            queue = (queue + [b])[-window:]
            if len(queue) == window:
                s = prev = sum(queue) / window
        else:
            s = prev = b * ALPHA[decay] + prev * (1 - ALPHA[decay])
        if s is not None:
            smoothed.append((t, f32(s)))
    values = sorted(s for _, s in smoothed)
    threshold = quantile(values, filt / 100)
    kept = [v for v in values if v >= threshold - EPS]
    level = quantile(kept, pct / 100)
    counts = dict.fromkeys(('vpWidth', 'contested', 'alone', 'both', 'open', 'unknown'), 0)
    for t, s in smoothed:
        k = kind(vm, t, ceiling, vp, ent)
        if s >= level - EPS and k != 'none':
            counts[k] += 1
    return level, counts


def remedy_of(n):
    limited = n['vpWidth'] + n['contested'] + n['alone'] + n['both']
    pool = n['contested'] + n['alone']
    if n['both'] >= 0.25 * limited or (n['vpWidth'] >= 0.25 * limited and pool >= 0.25 * limited):
        return 'both'
    if n['vpWidth'] >= pool + n['both']:
        return 'vpWidth'
    return 'alone' if n['alone'] > n['contested'] else 'contested'


def count(n):
    return f'{n:,}'


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class PressureRemedyTest(unittest.TestCase):

    def setUp(self):
        BUSY_OVERRIDE.clear()
        mx.MemberExposureTests.roots = []
        self.root = Path(mx.MemberExposureTests.make_root())
        self.addCleanup(shutil.rmtree, self.root)
        self.cache = self.root / 'stage' / mx.SERIAL
        self.cfg = self.root / 'vms.csv'
        self.config = {vm: list(mx.CONFIG[vm]) for vm in mx.CONFIG}        # ent, VPs, pool, size

    def section(self, ent=None, size=None, tiers=None, history=False):
        if BUSY_OVERRIDE:
            data = self.cache / '.nfit.cache.data'
            lines = data.read_text().splitlines()
            for i, line in enumerate(lines[1:], 1):
                stamp, vm = line.split(',')[:2]
                t = mx.calendar.timegm(mx.time.strptime(stamp, '%Y-%m-%d %H:%M:%S'))
                if (vm, t) in BUSY_OVERRIDE:
                    lines[i] = ','.join(line.split(',')[:4] + [BUSY_OVERRIDE[(vm, t)]])
            data.write_text('\n'.join(lines) + '\n')
        f = self.cache / '.nfit.cache.states'
        states = json.loads(f.read_bytes())
        for vm, value in (ent or {}).items():
            states[vm][0]['metadata']['entitlement'] = value
            self.config[vm][0] = value
        for vm, value in (size or {}).items():
            states[vm][0]['metadata']['pool_cpu'] = value
            self.config[vm][3] = value
        f.write_text(json.dumps(states))
        if tiers:
            (self.root / 'etc' / 'nfit.vms.cfg').write_text(''.join(f'[{vm}]\ntier = {t}\n' for vm, t in tiers.items()))
        with self.cfg.open('w') as fh:
            w = csv.writer(fh)
            w.writerow('hostname serial systemtype procpool_name procpool_id entitledcpu maxcpu'.split())
            for vm in ('vmA', 'vmB', 'vmS'):
                e, vp, pool, _ = self.config[vm]
                w.writerow([vm, mx.SERIAL, 'AIX', 'pool', pool, e, vp])
        run(str(self.root), str(self.cache), '--config', str(self.cfg), '--candidate-test', '--runq-perc-behavior=match',
            '--no-contested-level')      # the cases are built on the proposals as sized without the contested-level floor
        log, = [p for p in (self.root / 'output').glob('*.log') if 'state-based' in p.name and '.decisions.' not in p.name]
        text = log.read_text()
        start = text.index('Pressure and remedy')
        snap, = self.cache.glob('.nfit.store/proposals/v1/state-based/*.json')
        self.members = json.loads(snap.read_bytes())['members']
        decisions, = (self.root / 'output').glob('*.decisions.*.csv')
        with decisions.open(newline='') as fh:
            self.decisions = {r['vm']: r for r in csv.DictReader(fh)}
        if history:
            run(str(self.root), str(self.cache), '--update-history', '--min-history-days', '1', '--force')
        return ' '.join(text[start:text.index('\n=====', start)].split())

    def figures(self, vm):
        ent, vp, _, ceiling = self.config[vm]
        m = self.members[vm]
        level, n = model(vm, m['planningProfile'], ent, vp, ceiling)
        proposal = m['profiles'][m['planningProfile']]['entitlement']
        return level, n, max(0, proposal - ent, level - ent), m['planningProfile'], proposal

    def block(self, text, vm):
        start = text.index(f' {vm}: pool ')
        nxt = [text.find(f' {other}: pool ', start + 1) for other in ('vmA', 'vmB', 'vmS') if other != vm]
        ends = [x for x in nxt if x > 0] + [text.find(' more LPAR', start + 1) if ' more LPAR' in text[start:] else len(text)]
        return text[start:min(ends)]

    def assert_lpar(self, text, vm):
        level, n, add, planning, proposal = self.figures(vm)
        ent, vp, pool, ceiling = self.config[vm]
        self.assertGreater(level, ent)
        b = self.block(text, vm)
        units = f'{round(ceiling)}' if abs(ceiling - round(ceiling)) < 1e-9 else f'{ceiling:.2f}'
        installed = sum(self.config[v][0] for v in POOL_OF if POOL_OF[v] == pool)
        vps = '1 VP' if vp == 1 else f'{vp} VPs'
        self.assertIn(f'{vm}: pool {pool}, {vps}, entitlement {ent:.2f}, proposal {proposal:.2f}.', b)
        self.assertIn(f'Pool {pool}: reported size {units} cores; observed member entitlements total {installed:.2f}.', b)
        hours = 'all hours' if PROFILE[planning][4] == 'none' else 'online hours (08:00-17:00 UTC), weekdays only'
        self.assertIn(f'Planning level: {level:.2f} busy cores ({planning} applied to PhysB: P95, 15-minute EMA, {hours}).', b)
        qualifying = sum(n.values())
        full = n['contested'] + n['alone'] + n['both']
        if full:
            self.assertIn(f'Pool full in {count(full)} of {count(qualifying)} qualifying minutes ({100 * full / qualifying:.1f}%)', b)
        else:
            self.assertIn(f'Pool had room throughout the {count(qualifying)} qualifying minutes.', b)
        width = n['vpWidth'] + n['both']
        if width:
            self.assertIn(f"PhysC reached the {'1-VP' if vp == 1 else f'{vp}-VP'} limit in {count(width)} of them ({100 * width / qualifying:.1f}%)", b)
        self.assertEqual(self.decisions[vm]['planningLevel'], f'{level:.2f}')
        return b, remedy_of(n), add, ceiling - installed

    def test_planning_level_is_the_engine_profile_value_of_physb(self):
        self.section(ent={'vmA': 0.4}, tiers={'vmB': 'O3'}, history=True)
        month = json.loads((self.root / 'history' / mx.SERIAL / f'nfit.hist.{mx.MONTH}.json').read_text())[mx.MONTH]
        for vm, planning in (('vmA', 'G3-95W15'), ('vmB', 'O3-95W15'), ('vmS', 'G3-95W15')):
            self.assertEqual(self.members[vm]['planningProfile'], planning)
            engine = month['MonthlyWorkloadAnalysis'][vm]['PhysBMetrics']['ProfileValuesB'][planning]
            self.assertEqual(self.decisions[vm]['planningLevel'], f'{engine:.2f}', vm)
            self.assertAlmostEqual(model(vm, planning, *self.config[vm][:2], self.config[vm][3])[0], engine, places=3)

    def test_contested_with_room_in_the_pool(self):
        text = self.section(ent={'vmA': 0.4})
        b, kind_, add, room = self.assert_lpar(text, 'vmA')
        self.assertEqual(kind_, 'contested')
        self.assertLessEqual(add, room)
        self.assertIn(f'Remedy: review the guarantee - the pool was full while other LPARs were busy;', b)
        self.assertIn(f'the observed member entitlements leave {room:.2f} cores of room.', b)

    def test_contested_without_room_in_the_pool(self):
        text = self.section(ent={'vmA': 0.4, 'vmB': 3.5})
        b, kind_, add, room = self.assert_lpar(text, 'vmA')
        self.assertEqual(kind_, 'contested')
        _, _, _, _, proposal = self.figures('vmA')
        self.assertIn('Remedy: review the pool: competing guarantees or the pool maximum - the pool was full while other LPARs were busy;', b)
        self.assertIn(f'applying the {proposal:.2f} proposal adds {proposal - 0.4:.2f} cores', b)
        self.assertIn(f'more than the {room:.2f} cores of room the observed member entitlements leave. '
                      f'Review the {proposal:.2f} proposal alongside the other guarantees in pool 7 before applying it.', b)

    def test_two_contested_lpars_share_the_room(self):
        text = self.section(ent={'vmA': 0.4, 'vmB': 0.3}, size={'vmA': 2.2, 'vmB': 2.2})
        adds, kinds = {}, {}
        for vm in ('vmA', 'vmB'):
            _, kinds[vm], adds[vm], room = self.assert_lpar(text, vm)
        self.assertEqual(kinds, {'vmA': 'contested', 'vmB': 'contested'})
        together = sum(adds.values())
        self.assertTrue(max(adds.values()) <= room < together - EPS, (adds, room))
        for vm in ('vmA', 'vmB'):
            b = self.block(text, vm)
            self.assertIn('Remedy: review the pool: competing guarantees or the pool maximum', b)
            self.assertIn(f'with the other LPARs that met the same limit in pool 7, {together:.2f} cores together', b)

    def test_vp_width_and_pool_together(self):
        text = self.section(ent={'vmS': 0.2})
        b, kind_, _, _ = self.assert_lpar(text, 'vmS')
        self.assertEqual(kind_, 'both')
        self.assertIn('Remedy: review the VP width and the pool together', b)

    def test_vp_width_with_the_pool_open(self):
        # vmS's busy in the frame-scarce minutes (W4) set to 0.25, below its
        # planning level: those minutes do not qualify, only its VP width does.
        for t in range(mx.W4, mx.W4 + 15 * 60, 60):
            BUSY_OVERRIDE[('vmS', t)] = '0.25'
        text = self.section(ent={'vmS': 0.2}, size={'vmS': 4})
        b, kind_, _, _ = self.assert_lpar(text, 'vmS')
        self.assertEqual(kind_, 'vpWidth')
        self.assertIn('Remedy: review the VP width - at its planning level the LPAR was at its VPs while pool 9 had room.', b)


if __name__ == '__main__':
    unittest.main()
