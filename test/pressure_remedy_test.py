#!/usr/bin/env python3
# ==============================================================================
# Pressure and remedy (nfit-profile, v10 milestone 2.4). On the member exposure
# fixture, each minute of busy work above an LPAR's installed entitlement is
# classified by what limited it; the remedy is decided on the minutes at or
# above the LPAR's planning level (its busy at the planning profile's
# percentile). Every expected figure is recomputed here from the fixture's own
# CPU and pool definitions and the saved proposal snapshot.
#   - As staged, no LPAR's planning level is above its guarantee: none listed,
#     the two that borrowed against a limit above it are counted.
#   - vmA at 0.40: contested at its planning level; the pool has room.
#   - vmA at 0.40 and vmB at 3.50: no room: review the pool.
#   - vmA at 0.40 and vmB at 0.30: two contested LPARs share the pool's room.
#   - vmS at 0.20 fills pool 9 (2 cores) at its 2 VPs: VP width and pool.
#   - the same with pool 9 at 4 cores: VP width 20, contested 15 (frame
#     scarce): still both limits, each a quarter or more.
# ==============================================================================
import csv
import json
import shutil
import unittest
from pathlib import Path

from one_entry_point_test import mx, run

EPS = 1e-9
POOL_OF = {'vmA': 7, 'vmB': 7, 'vmS': 9}
BUSY_OVERRIDE = {}      # (vm, minute) -> PhysB text written over the fixture's


def busy_of(vm, t):
    return BUSY_OVERRIDE.get((vm, t), mx.cpu(vm, t)[1])


def admitted(vm, t):
    c, b = mx.cpu(vm, t)[0], busy_of(vm, t)
    return None if (b == '' or float(b) > c + 1e-6) else (c, float(b))


def kind(vm, t, ceiling, vp):
    c = mx.cpu(vm, t)[0]
    own = c >= 0.95 * vp - EPS
    frame = mx.PHYS_SIZE - mx.physical_busy(t) <= max(0.50, 0.01 * mx.PHYS_SIZE) + EPS
    if POOL_OF[vm] == 7:
        observed = not mx.between(t, mx.W5, 10)
        busy = round(mx.cpu('vmA', t)[0] + mx.cpu('vmB', t)[0], 4)
    else:
        observed, busy = True, mx.cpu('vmS', t)[0]
    local = observed and ceiling - busy <= max(0.10, 0.02 * ceiling) + EPS
    competition = frame or (local and busy - c >= max(0.10, 0.02 * ceiling) - EPS)
    scarce = local or frame
    classified = scarce or observed
    if scarce:
        return 'both' if own else 'contested' if competition else 'alone'
    if own:
        return 'vpWidth' if classified else 'unknown'
    return 'open' if classified else 'unknown'


def expected(vm, ent, vp, ceiling, percentile):
    minutes = [(t,) + p for t in range(mx.DAY1, mx.DAY1 + mx.DAYS * 86400, 60) if (p := admitted(vm, t))]
    bins = sorted(int(b * 100 + 1e-6) for _, _, b in minutes)
    level_bin = next(x for i, x in enumerate(bins) if i + 1 >= percentile / 100 * len(bins) - 1e-9)
    counts = dict.fromkeys(('vpWidth', 'contested', 'alone', 'both', 'open', 'unknown'), 0)
    limited_any = 0
    for t, c, b in minutes:
        if b <= ent + EPS:
            continue
        k = kind(vm, t, ceiling, vp)
        limited_any += k in ('vpWidth', 'contested', 'alone', 'both')
        if int(b * 100 + 1e-6) >= level_bin:
            counts[k] += 1
    return level_bin / 100, counts, limited_any


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

    def section(self, ent=None, size=None, size_of=None):
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
        with self.cfg.open('w') as fh:
            w = csv.writer(fh)
            w.writerow('hostname serial systemtype procpool_name procpool_id entitledcpu maxcpu'.split())
            for vm in ('vmA', 'vmB', 'vmS'):
                e, vp, pool, _ = self.config[vm]
                w.writerow([vm, mx.SERIAL, 'AIX', 'pool', pool, e, vp])
        run(str(self.root), str(self.cache), '--config', str(self.cfg), '--candidate-test', '--runq-perc-behavior=match')
        log, = [p for p in (self.root / 'output').glob('*.log') if 'state-based' in p.name]
        text = log.read_text()
        start = text.index('Pressure and remedy')
        snap, = self.cache.glob('.nfit.store/proposals/v1/state-based/*.json')
        self.members = json.loads(snap.read_bytes())['members']
        return ' '.join(text[start:text.index('\n=====', start)].split())

    def figures(self, vm):
        ent, vp, _, ceiling = self.config[vm]
        m = self.members[vm]
        pct = float(m['planningProfile'].split('-')[1].split('W')[0])
        level, counts, limited_any = expected(vm, ent, vp, ceiling, pct)
        proposal = m['profiles'][m['planningProfile']]['entitlement']
        return level, counts, limited_any, max(0, proposal - ent, level - ent), m['planningProfile'], proposal

    def assert_lpar(self, text, vm, remedy):
        level, n, _, need, planning, proposal = self.figures(vm)
        ent, vp, pool, ceiling = self.config[vm]
        self.assertGreater(level, ent)
        installed = sum(self.config[v][0] for v in POOL_OF if POOL_OF[v] == pool)
        vps = '1 VP' if vp == 1 else f'{vp} VPs'
        units = f'{round(ceiling)}' if abs(ceiling - round(ceiling)) < 1e-9 else f'{ceiling:.2f}'
        self.assertIn(f'{vm}: pool {pool} (maximum {units}; installed guarantees {installed:.2f}), {vps}, entitlement {ent:.2f}', text)
        pct = planning.split('-')[1].split('W')[0]
        self.assertIn(f'Planning level ({planning}, P{pct} of minute busy): {level:.2f} cores; proposal {proposal:.2f}.', text)
        self.assertIn(f"Minutes at or above it: VP width {n['vpWidth']}, pool contested {n['contested']}, pool alone {n['alone']}, "
                      f"both {n['both']}, open {n['open']}, unknown {n['unknown']}.", text)
        start = text.index(f'{vm}: pool')
        end = text.find(': pool ', start + len(vm) + 2)       # the next LPAR's block
        self.assertIn(f'Remedy: {remedy}', text[start:end if end > 0 else None])
        return need, ceiling - installed

    def test_as_staged_nothing_is_listed_at_the_planning_level(self):
        text = self.section()
        self.assertIn('No LPAR with limited borrowing at its planning level.', text)
        below = sum(1 for vm in ('vmA', 'vmB', 'vmS') if self.figures(vm)[2] >= 15)
        self.assertEqual(below, 2)
        self.assertIn('2 more LPARs borrowed against a limit only above their planning level', text)

    def test_contested_with_room_in_the_pool(self):
        text = self.section(ent={'vmA': 0.4})
        need, room = self.assert_lpar(text, 'vmA', 'review the guarantee')
        self.assertLessEqual(need, room)
        self.assertIn(f'leave {room:.2f} cores, enough for the {need:.2f} cores this LPAR needs', text)

    def test_contested_without_room_in_the_pool(self):
        text = self.section(ent={'vmA': 0.4, 'vmB': 3.5})
        need, room = self.assert_lpar(text, 'vmA', 'review the pool: competing guarantees or the pool maximum')
        self.assertIn(f'leave {room:.2f} cores, less than the {need:.2f} cores this LPAR needs', text)

    def test_two_contested_lpars_share_the_room(self):
        # Pool 7 at 2.2 cores: each LPAR's need fits the room on its own, not together.
        text = self.section(ent={'vmA': 0.4, 'vmB': 0.3}, size={'vmA': 2.2, 'vmB': 2.2})
        needs = {vm: self.figures(vm)[3] for vm in ('vmA', 'vmB')}
        room = 2.2 - 0.7
        together = sum(needs.values())
        self.assertTrue(max(needs.values()) <= room < together - EPS, (needs, room))
        remedy = 'review the pool: competing guarantees or the pool maximum'
        for vm in ('vmA', 'vmB'):
            self.assert_lpar(text, vm, remedy)
            self.assertIn(f"the {needs[vm]:.2f} cores this LPAR needs ({together:.2f} cores for the pool's contested LPARs together)", text)

    def test_vp_width_and_pool_together(self):
        text = self.section(ent={'vmS': 0.2})
        self.assert_lpar(text, 'vmS', 'review the VP width and the pool together')

    def test_vp_width_when_the_pool_minutes_are_below_the_planning_level(self):
        # vmS's busy in the frame-scarce minutes (W4) set to 0.25: above its 0.20
        # guarantee, below its 0.30 planning level. Those minutes no longer count.
        for t in range(mx.W4, mx.W4 + 15 * 60, 60):
            BUSY_OVERRIDE[('vmS', t)] = '0.25'
        text = self.section(ent={'vmS': 0.2}, size={'vmS': 4})
        _, n, _, _, _, _ = self.figures('vmS')
        self.assertEqual((n['vpWidth'], n['contested'], n['both']), (20, 0, 0))
        self.assert_lpar(text, 'vmS', 'review the VP width - at its planning level its busy work reached its VPs while the pool had room')

    def test_pool_open_at_the_vp_width(self):
        # With pool 9 at 4 cores, vmS's 20 minutes at its VPs are VP width, not
        # both; the 15 frame-scarce minutes (W4) keep the pool at a quarter or
        # more of the limited minutes, so the remedy stays both limits.
        text = self.section(ent={'vmS': 0.2}, size={'vmS': 4})
        _, n, _, _, _, _ = self.figures('vmS')
        self.assertEqual((n['vpWidth'], n['contested'], n['both']), (20, 15, 0))
        self.assert_lpar(text, 'vmS', 'review the VP width and the pool together')


if __name__ == '__main__':
    unittest.main()
