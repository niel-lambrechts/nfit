#!/usr/bin/env python3
# ==============================================================================
# Pressure and remedy (nfit-profile, v10 milestone 2.4). On the member exposure
# fixture, each minute of busy work above an LPAR's installed entitlement is
# classified by what limited it, and each pressured LPAR gets one remedy. The
# fixture's own definitions give the expected counts independently:
#   vmA, pool 7 (4 cores, guarantees 3.00): above 2.00 in W1/W3 (pool full,
#       vmB busy) and W4 (frame scarce) = contested 65; W2 open 10; W5 pool
#       unobserved 10. Median busy above the guarantee 0.50 <= room 1.00:
#       review the guarantee.
#   vmS, pool 9 (2 cores = its 2 VPs): fills the pool at its VP width in W6:
#       both 20, review the VP width and the pool together.
# Variants of the configuration states:
#   vmB at 1.80 leaves pool 7 0.20 of room < 0.50: review the pool;
#   pool 9 at 4 cores leaves vmS at its VPs with the pool open: VP width;
#   vmA with 3 VPs reaches its VP width in W1/W3 while pool 7 is full and vmB
#   is busy: both 50, contested 15 (W4): the VP width and the pool together.
# ==============================================================================
import csv
import json
import shutil
import unittest
from pathlib import Path

from one_entry_point_test import mx, run

EPS = 1e-9


def vmA_minutes():
    """(kind, busy above 2.00) of vmA's borrowing minutes."""
    out = []
    for t in range(mx.DAY1, mx.DAY1 + mx.DAYS * 86400, 60):
        c, b = mx.cpu('vmA', t)
        if b == '' or float(b) > c + 1e-6 or float(b) <= 2.0 + EPS:
            continue
        if mx.between(t, mx.W5, 10):
            kind = 'unknown'
        else:
            busy = round(c + mx.cpu('vmB', t)[0], 4)
            local = 4.0 - busy <= 0.10 + EPS
            frame = mx.PHYS_SIZE - mx.physical_busy(t) <= max(0.50, 0.01 * mx.PHYS_SIZE) + EPS
            kind = 'contested' if (local and busy - c >= 0.10 - EPS) or frame else 'alone' if local else 'open'
        out.append((kind, float(b) - 2.0))
    return out


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class PressureRemedyTest(unittest.TestCase):

    def setUp(self):
        mx.MemberExposureTests.roots = []
        self.root = Path(mx.MemberExposureTests.make_root())
        self.addCleanup(shutil.rmtree, self.root)
        self.cache = self.root / 'stage' / mx.SERIAL
        self.cfg = self.root / 'vms.csv'

    def section(self, change=None):
        if change:
            f = self.cache / '.nfit.cache.states'
            states = json.loads(f.read_bytes())
            change(states)
            f.write_text(json.dumps(states))
        with self.cfg.open('w') as fh:
            w = csv.writer(fh)
            w.writerow('hostname serial systemtype procpool_name procpool_id entitledcpu maxcpu'.split())
            for vm in ('vmA', 'vmB', 'vmS'):
                ent, vp, pool, _ = mx.CONFIG[vm]
                w.writerow([vm, mx.SERIAL, 'AIX', 'pool', pool, ent, vp])
        run(str(self.root), str(self.cache), '--config', str(self.cfg), '--candidate-test', '--runq-perc-behavior=match')
        log, = [p for p in (self.root / 'output').glob('*.log') if 'state-based' in p.name]
        text = log.read_text()
        start = text.index('Pressure and remedy')
        return ' '.join(text[start:text.index('\n=====', start)].split())

    def test_counts_and_remedies(self):
        text = self.section()
        minutes = vmA_minutes()
        n = {k: sum(1 for kind, _ in minutes if kind == k) for k in ('contested', 'alone', 'open', 'unknown')}
        self.assertEqual(n, {'contested': 65, 'alone': 0, 'open': 10, 'unknown': 10})
        self.assertIn(f"vmA: pool 7 (maximum 4; installed guarantees 3.00), 4 VPs, entitlement 2.00 Borrowing minutes: "
                      f"VP width 0, pool contested {n['contested']}, pool alone 0, both 0, open {n['open']}, unknown {n['unknown']}.", text)
        limited = sum(x for kind, x in minutes if kind in ('contested', 'alone'))
        self.assertIn(f'Busy core-minutes above the guarantee while limited: {limited:.2f}; longest limited run 30 minutes.', text)
        self.assertIn('Remedy: review the guarantee - the pool was full while other LPARs were busy; its installed guarantees '
                      'leave 1.00 cores, enough for the 0.50 cores', text)
        self.assertIn('vmS: pool 9 (maximum 2; installed guarantees 1.50), 2 VPs, entitlement 1.50 Borrowing minutes: '
                      'VP width 0, pool contested 0, pool alone 0, both 20, open 0, unknown 0.', text)
        self.assertIn('Remedy: review the VP width and the pool together', text)
        self.assertNotIn('vmB: pool', text)                      # never above its guarantee

    def test_pool_without_room_for_a_higher_guarantee(self):
        def raise_vmB(states):
            states['vmB'][0]['metadata']['entitlement'] = 1.8
        text = self.section(raise_vmB)
        self.assertIn('installed guarantees 3.80', text)
        self.assertIn('Remedy: review the pool: competing guarantees or the pool maximum - the pool was full while other '
                      'LPARs were busy; its installed guarantees leave 0.20 cores, less than the 0.50 cores', text)

    def test_vp_width_with_the_pool_open(self):
        def widen_pool_9(states):
            states['vmS'][0]['metadata']['pool_cpu'] = 4
        text = self.section(widen_pool_9)
        self.assertIn('vmS: pool 9 (maximum 4; installed guarantees 1.50), 2 VPs, entitlement 1.50 Borrowing minutes: '
                      'VP width 20, pool contested 0, pool alone 0, both 0, open 0, unknown 0.', text)
        self.assertIn('Remedy: review the VP width - its busy work reached its VPs while the pool had room.', text)

    def test_vp_width_and_pool_with_competition(self):
        def narrow_vmA(states):
            states['vmA'][0]['metadata']['virtual_cpus'] = 3
            states['vmA'][0]['metadata']['max_cpu'] = 3
        text = self.section(narrow_vmA)
        self.assertIn('vmA: pool 7 (maximum 4; installed guarantees 3.00), 3 VPs, entitlement 2.00 Borrowing minutes: '
                      'VP width 0, pool contested 15, pool alone 0, both 50, open 10, unknown 10.', text)
        start = text.index('vmA: pool 7')
        self.assertIn('Remedy: review the VP width and the pool together', text[start:text.index('vmS: pool 9')])


if __name__ == '__main__':
    unittest.main()
