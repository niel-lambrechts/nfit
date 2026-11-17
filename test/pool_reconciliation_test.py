#!/usr/bin/env python3
# ==============================================================================
# Pool reconciliation (nfit-profile, v10 milestone 2.3): the planning-profile
# cuts of one pool replayed together. On the member exposure fixture (pool 7:
# vmA and vmB; pool 9: vmS alone) the section is recomputed independently from
# the fixture's own CPU and pool definitions and the saved proposal snapshot:
#   - the full-pool minutes with every member paired;
#   - at the proposals, how many members were exposed together, the longest run
#     with all exposed, per-LPAR counts, and the candidate test's within-installed
#     counts beside them;
#   - for each budget, the combination of candidate entitlements releasing the
#     most guarantee, by exhaustive search.
# The saved-snapshot replay must print the same section.
# ==============================================================================
import csv
import itertools
import json
import os
import shutil
import unittest
from pathlib import Path

from one_entry_point_test import mx, run

EPS = 1e-9
BUDGETS = [('A', 0, 0, 0), ('B', 15, 3, 2), ('C', 60, 10, 10)]
MIN_PER_VP = 0.05


def levels(ent, vp, proposal):
    """Port of _candidate_levels: label -> hundredths of a core."""
    installed, base, floor = (int(x * 100 + 0.5) for x in (ent, proposal, MIN_PER_VP * vp))
    if base > installed or installed < floor or installed > 100 * vp:
        return {}
    feasible = max(base, floor)
    out = {'installed': installed, ('widthFloor' if base < floor else 'proposal'): feasible,
           'midpoint': (installed + feasible + 1) // 2}
    if feasible > floor:
        out['lower'] = max(floor, (9 * feasible + 5) // 10)
    return out


def tolerance(abs_cores, rel, size):
    return max(abs_cores, rel * size)


def full_minutes():
    """Minutes pool 7 was full (local or frame) with both members paired, and
    whether other LPARs were busy for each member."""
    out = {}
    for t in range(mx.DAY1, mx.DAY1 + mx.DAYS * 86400, 60):
        if mx.between(t, mx.W5, 10):
            continue                                         # pool 7 unobserved
        c = {vm: mx.cpu(vm, t)[0] for vm in ('vmA', 'vmB')}
        busy = round(c['vmA'] + c['vmB'], 4)
        local = 4.0 - busy <= tolerance(0.10, 0.02, 4.0) + EPS
        frame = (mx.PHYS_SIZE - mx.physical_busy(t)) <= tolerance(0.50, 0.01, mx.PHYS_SIZE) + EPS
        if not (local or frame):
            continue
        pairs = {vm: mx.cpu(vm, t) for vm in ('vmA', 'vmB')}
        if any(b == '' or float(b) > cc + 1e-6 for cc, b in pairs.values()):
            continue                                         # a member without a valid pair
        out[t] = {vm: (float(pairs[vm][1]),
                       frame or (busy - pairs[vm][0]) >= tolerance(0.10, 0.02, 4.0) - EPS) for vm in pairs}
    return out


def longest(minutes):
    best = run_ = 0
    prior = None
    for t in sorted(minutes):
        run_ = run_ + 1 if prior == t - 60 else 1
        best = max(best, run_)
        prior = t
    return best


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class PoolReconciliationTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        mx.MemberExposureTests.roots = []
        cls.root = Path(mx.MemberExposureTests.make_root())
        cls.cache = cls.root / 'stage' / mx.SERIAL
        cls.cfg = cls.root / 'vms.csv'
        with cls.cfg.open('w') as f:
            w = csv.writer(f)
            w.writerow('hostname serial systemtype procpool_name procpool_id entitledcpu maxcpu'.split())
            for vm in ('vmA', 'vmB', 'vmS'):
                ent, vp, pool, _ = mx.CONFIG[vm]
                w.writerow([vm, mx.SERIAL, 'AIX', 'pool', pool, ent, vp])
        run(str(cls.root), str(cls.cache), '--config', str(cls.cfg), '--candidate-test', '--runq-perc-behavior=match')
        log, = [p for p in (cls.root / 'output').glob('*.log') if 'state-based' in p.name]
        text = log.read_text()
        cls.section = text[text.index('Pool reconciliation'):text.index('\n=====', text.index('Pool reconciliation'))]
        snap, = cls.cache.glob('.nfit.store/proposals/v1/state-based/*.json')
        cls.snapshot = snap
        cls.members = json.loads(snap.read_bytes())['members']

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def pool7(self):
        start = self.section.index('  Pool 7 ')
        return self.section[start:self.section.index('  Pool 9 ', start)]

    def candidates(self):
        out = {}
        for vm in ('vmA', 'vmB'):
            m = self.members[vm]
            ent, vp, _, _ = mx.CONFIG[vm]
            out[vm] = (ent, levels(ent, vp, m['profiles'][m['planningProfile']]['entitlement']))
        return out

    def exposed(self, minutes, vm, ent, cents, within=False):
        level = cents / 100
        hits = {}
        for t, row in minutes.items():
            b, competition = row[vm]
            if competition and b > level + EPS and ent > level + EPS and (not within or b <= ent + EPS):
                hits[t] = min(b, ent) - level
        return hits

    def test_full_minutes_and_coverage_note(self):
        minutes = full_minutes()
        self.assertEqual(len(minutes), 65)
        self.assertIn(f'Full-pool minutes with every member paired: {len(minutes)}.', self.pool7())
        self.assertIn('20 paired member-minutes could not be classified', self.pool7())

    def test_members_exposed_together_at_the_proposals(self):
        minutes, cand = full_minutes(), self.candidates()
        at = {vm: self.exposed(minutes, vm, ent, lv.get('proposal', lv.get('widthFloor'))) for vm, (ent, lv) in cand.items()}
        within = {vm: self.exposed(minutes, vm, ent, lv.get('proposal', lv.get('widthFloor')), True) for vm, (ent, lv) in cand.items()}
        together = {k: sum(1 for t in minutes if sum(t in at[vm] for vm in at) == k) for k in (0, 1, 2)}
        both = set(at['vmA']) & set(at['vmB'])
        self.assertGreater(together[2], 0, 'the fixture must expose both members together')
        text = self.pool7()
        self.assertIn(f'Exposed together: all 2: {together[2]}; exactly 1: {together[1]}; none: {together[0]} (of 65 full-pool minutes).', text)
        self.assertIn(f'Longest run with all exposed: {longest(both)} minutes.', text)
        self.assertIn(f"Per LPAR: vmA {len(at['vmA'])}, vmB {len(at['vmB'])}.", text)
        self.assertIn(f"all exposed in {len(set(within['vmA']) & set(within['vmB']))}; per LPAR vmA {len(within['vmA'])}, vmB {len(within['vmB'])}.", text)

    def test_best_combination_within_each_budget(self):
        minutes, cand = full_minutes(), self.candidates()
        names = sorted(cand)
        options = []
        for vm in names:
            ent, lv = cand[vm]
            by_cents = {}
            for label, cents in lv.items():
                by_cents.setdefault(cents, []).append(label)
            options.append([(cents, sorted(labels), self.exposed(minutes, vm, ent, cents), ent) for cents, labels in sorted(by_cents.items())])
        for name, max_minutes, max_run, max_excess in BUDGETS:
            best = None
            for combo in itertools.product(*options):
                union = set().union(*(set(o[2]) for o in combo))
                excess = sum(sum(o[2].values()) for o in combo)
                released = sum(max(0, o[3] - o[0] / 100) for o in combo)
                if len(union) > max_minutes or longest(union) > max_run or excess > max_excess + EPS:
                    continue
                key = (round(released, 9), -len(union), -round(excess, 9))
                if best is None or key > best[0]:
                    best = (key, combo, union, excess, released)
            self.assertIsNotNone(best)
            _, combo, union, excess, released = best
            each = ', '.join(f"{vm} {o[0] / 100:.2f} ({','.join(o[1])})" for vm, o in zip(names, combo))
            self.assertIn(f'      {name}: {each} - releases {released:.2f}; {len(union)}/{longest(union)}/{excess:.2f}', self.pool7())

    def test_single_member_pool(self):
        text = self.section[self.section.index('  Pool 9 '):]
        self.assertIn('Pool 9 (maximum 2): one planning-profile cut.', text)
        self.assertIn('Per LPAR: vmS 0.', text)        # vmS fills pool 9 alone: nobody else was busy

    def test_saved_snapshot_replay_prints_the_same_section(self):
        p = run(str(self.root), str(self.cache), '--config', str(self.cfg), '--candidate-proposals', str(self.snapshot))
        out = p.stdout
        self.assertIn(self.section.strip(), out)


if __name__ == '__main__':
    unittest.main()
