#!/usr/bin/env python3
# ==============================================================================
# Unit tests for dev/protection-replay: the research's original-grid choices,
# the reconciliation of the exact bound with the grid, the per-profile totals
# (never summed across a member's alternative profiles) and the report.
# Synthetic records only.
# ==============================================================================
import importlib.machinery
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, '..', 'dev', 'protection-replay'))


def load():
    loader = importlib.machinery.SourceFileLoader('protection_replay', TOOL)
    spec = importlib.util.spec_from_loader('protection_replay', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def level(value, busy_min, busy_core, consumed_min=None, consumed_core=None):
    c_min = busy_min if consumed_min is None else consumed_min
    c_core = busy_core if consumed_core is None else consumed_core
    return {'level': value, 'busy': {'minutes': busy_min, 'coreMinutes': busy_core, 'days': 1 if busy_min else 0,
                                     'episodes': 1 if busy_min else 0, 'longest': busy_min},
            'consumed': {'minutes': c_min, 'coreMinutes': c_core, 'days': 1 if c_min else 0, 'episodes': 1 if c_min else 0,
                         'longest': c_min}}


def record(member, profile, target, installed, vps, busy, ladder, consumed, later, planning=True, cut=True):
    return {'member': member, 'system': member.split('-member-')[0], 'profile': profile, 'tier': profile[1] if profile[0] != 'P' else 'P',
            'pool': 7, 'installed': installed, 'installedVPs': vps, 'target': target, 'cut': cut, 'status': 'bound',
            'bound': {'busy': busy, 'ladder': ladder, 'consumed': consumed},
            'adjusted': {'busy': max(target, busy), 'ladder': max(target, ladder), 'consumed': max(target, consumed)},
            'planning': planning, 'scored': True, 'later': later}


class GridChoices(unittest.TestCase):
    def test_original_grid_levels(self):
        m = load()
        # installed 2.00 on 4 VPs, proposal 1.00: feasible 1.00, midpoint 1.50, installed 2.00.
        self.assertEqual(m.candidate_choices(2.0, 4, 1.0), [100, 150, 200])
        # A proposal below the width floor (0.05 x 10 VPs = 0.50): the floor replaces it.
        self.assertEqual(m.candidate_choices(1.0, 10, 0.3), [50, 75, 100])
        self.assertEqual(m.candidate_choices(1.0, 0, 0.3), [])


class Reconciliation(unittest.TestCase):
    def test_projection_onto_the_grid(self):
        m = load()
        later = {p: level(1.0, 0, 0) for p in m.PLANS + ('installed',)}
        recs = [record('P10-01-member-001', 'G3-95W15', 1.0, 2.0, 4, 1.20, 1.30, 1.60, later)]
        rows = [
            {'member': 'P10-01-member-001', 'profile': 'G3-95W15', 'grid': 'original', 'signal': 'busy',
             'baseline': 1.0, 'installed': 2.0, 'selected': 1.5},          # 1.20 projects to 1.50
            {'member': 'P10-01-member-001', 'profile': 'G3-95W15', 'grid': 'original', 'signal': 'physc',
             'baseline': 1.0, 'installed': 2.0, 'selected': 2.0},          # 1.60 projects to 2.00
            {'member': 'P10-01-member-001', 'profile': 'G2-98W10', 'grid': 'original', 'signal': 'busy',
             'baseline': 2.0, 'installed': 2.0, 'selected': 2.0},          # no cut: skipped
            {'member': 'P10-01-member-002', 'profile': 'G3-95W15', 'grid': 'original', 'signal': 'busy',
             'baseline': 1.0, 'installed': 2.0, 'selected': 1.0},          # no record
            {'member': 'P10-01-member-001', 'profile': 'G3-95W15', 'grid': 'family', 'signal': 'busy',
             'baseline': 1.0, 'installed': 2.0, 'selected': 1.1},          # other grids are not reconciled
        ]
        result, mismatches = m.reconcile(recs, rows)
        self.assertEqual(result, {'compared': 2, 'equal': 2, 'noCut': 1, 'researchRowWithoutRecord': 1})
        self.assertEqual([x['why'] for x in mismatches], ['no record'])
        rows[0]['selected'] = 1.0
        result, mismatches = m.reconcile(recs, rows)
        self.assertEqual(result['different'], 1)
        self.assertEqual(mismatches[0]['ours'], 1.5)


class Totals(unittest.TestCase):
    def test_per_profile_and_planning_only(self):
        m = load()
        later_a = {'target': level(1.0, 10, 3.0), 'busy': level(1.2, 2, 0.4), 'ladder': level(1.3, 1, 0.1),
                   'consumed': level(1.6, 0, 0.0), 'installed': level(2.0, 0, 0.0)}
        later_b = {p: level(1.5, 4, 1.0) for p in m.PLANS + ('installed',)}
        recs = [record('P10-01-member-001', 'G3-95W15', 1.0, 2.0, 4, 1.2, 1.3, 1.6, later_a),
                record('P10-01-member-001', 'G2-98W10', 1.5, 2.0, 4, 1.2, 1.3, 1.4, later_b, planning=False)]
        planning = m.aggregate([r for r in recs if r['planning']], lambda r: 'all')['all']
        self.assertEqual(planning['cuts'], 1)
        self.assertEqual(planning['busyCoreMinutes'], {'target': 3.0, 'busy': 0.4, 'ladder': 0.1, 'consumed': 0.0})
        self.assertEqual(planning['retained'], {'busy': 0.2, 'ladder': 0.3, 'consumed': 0.6})
        per_profile = m.aggregate(recs, lambda r: r['profile'])
        self.assertEqual(sorted(per_profile), ['G2-98W10', 'G3-95W15'])
        self.assertEqual(per_profile['G2-98W10']['retained']['busy'], 0.0)

    def test_report_is_written(self):
        m = load()
        later = {p: level(1.0, 1, 0.5) for p in m.PLANS + ('installed',)}
        recs = [record('P10-01-member-001', 'G3-95W15', 1.0, 2.0, 4, 1.2, 1.3, 1.6, later)]
        agg = m.aggregate(recs, lambda r: 'P10')
        result = {'generated': 'now', 'commit': 'abc', 'rule': 'cut-protection-1', 'shares': {}, 'reconciliation': {},
                  'byArchitecture': agg, 'byProfile': agg, 'byTier': agg,
                  'criterion': {'planning': {p: 1.0 for p in m.PLANS}, 'matched': {p: 1.0 for p in m.PLANS},
                                'planningExactShare': 1.0, 'matchedExactShare': 1.0, 'matchedGridShare': 0.5,
                                'matchedResearchGrid': 0.5},
                  'exceptions': [], 'poolExceptions': [],
                  'systems': [{'system': 'P10-01', 'dates': {'trainingStart': 'a', 'trainingEnd': 'b', 'laterStart': 'c',
                                                             'laterEnd': 'd'}, 'members': 1, 'recordCount': 1, 'notes': 0}]}
        with tempfile.TemporaryDirectory() as tmp:
            m.write_report(Path(tmp), result)
            text = (Path(tmp) / 'protection-replay.md').read_text()
        self.assertIn('## 1. Reconciliation', text)
        self.assertIn('## 5. Criterion of plan 5.7', text)
        self.assertNotIn('vm_', text)


if __name__ == '__main__':
    unittest.main()
