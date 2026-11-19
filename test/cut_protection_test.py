"""Cut protection in shadow (v10 stage 3.3) on the member-exposure fixture.

The standard run records, for each profile of an LPAR with a cut, the exact
protection bound from its contested minutes, writes the "Cut Protection
(shadow)" section and leaves the CSV unchanged. A replay of the saved proposal
on the held-out day scores the target, the three adjusted values and the
installed entitlement.

Fixture facts used: pool 7 (size 4) is full while vmA bursts (W1 10:00 and W3
23:50, PhysB 2.5 above vmA's installed 2.0), with vmB busy, so those minutes are
contested for vmA; on the second day W3's tail and W4 (shared pool scarce) are
contested again.
"""
import csv
import glob
import json
from pathlib import Path
import subprocess
import shutil
import unittest
from one_entry_point_test import mx, run


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class CutProtectionTest(unittest.TestCase):
    def setUp(self):
        mx.MemberExposureTests.roots = []
        self.root = Path(mx.MemberExposureTests.make_root())
        self.addCleanup(shutil.rmtree, self.root)
        self.cache = self.root / 'stage' / mx.SERIAL
        self.cfg = self.root / 'vms.csv'
        with self.cfg.open('w') as f:
            w = csv.writer(f)
            w.writerow('hostname serial systemtype procpool_name procpool_id entitledcpu maxcpu'.split())
            for vm, role in [('vmA', 'AIX'), ('vmB', 'VIO Server'), ('vmS', 'PowerHA Standby')]:
                ent, vp, pool, _ = mx.CONFIG[vm]
                w.writerow([vm, mx.SERIAL, role, 'pool', pool, ent, vp])

    def outputs(self, suffix):
        return [Path(p) for p in glob.glob(str(self.root / 'output' / ('*' + suffix)))]

    def clear_outputs(self):
        for path in self.outputs(''):
            if path.is_file():
                path.unlink()

    def standard(self, *flags):
        self.clear_outputs()
        run(str(self.root), str(self.cache), '--config', str(self.cfg), '--enddate', '2026-09-01', *flags)
        log, = self.outputs('.log')
        csvs = [p for p in self.outputs('.csv') if '.decisions.' not in p.name]
        return log.read_text(encoding='utf-8'), csvs[0].read_text(encoding='utf-8')

    def snapshot(self):
        snaps = glob.glob(str(self.cache / '.nfit.store' / 'proposals' / 'v1' / '*' / '*.json'))
        self.assertEqual(len(snaps), 1)
        return Path(snaps[0])

    def test_shadow_record_bound_and_unchanged_csv(self):
        log, csv_on = self.standard()
        self.assertIn('\nCut Protection (shadow)\n', log)
        self.assertIn('The CSV is unchanged.', log)
        snap = self.snapshot()
        data = json.loads(snap.read_text())
        records = {p: v['cutProtection'] for p, v in data['members']['vmA']['profiles'].items() if 'cutProtection' in v}
        self.assertTrue(records, 'vmA has a cut, so its profiles carry a record')
        bound = [r for r in records.values() if r['status'] == 'bound']
        self.assertTrue(bound, 'vmA has contested minutes on the sizing day')
        for r in bound:
            self.assertEqual(r['rule'], 'cut-protection-1')
            self.assertEqual(r['installed'], 2.0)
            # PhysB 2.5 in a contested minute is above installed: every bound is capped at 2.0.
            self.assertEqual(r['bound'], {'busy': 2.0, 'ladder': 2.0, 'consumed': 2.0})
            if r['cut']:
                self.assertEqual(r['adjusted']['ladder'], 2.0)
            self.assertGreater(r['support']['contestedMinutes'], 0)
            self.assertLessEqual(r['support']['contestedMinutes'], r['support']['classifiedMinutes'])
            self.assertTrue(r['frontier'] and r['top'])
        # The CSV does not depend on the shadow record.
        self.clear_outputs()
        shutil.rmtree(self.cache / '.nfit.store' / 'proposals', ignore_errors=True)
        _, csv_off = self.standard('--no-pool-exposure')
        self.assertEqual(csv_on, csv_off)

    def test_replay_scores_the_recorded_levels(self):
        self.standard()
        snap = self.snapshot()
        self.clear_outputs()
        proc = subprocess.run(['perl', str(self.root / 'nfit-profile'), '--nmondir', str(self.cache), '--config', str(self.cfg),
                               '--candidate-proposals', str(snap), '--candidate-window', '2026-09-02/2026-09-02'],
                              cwd=str(self.root), capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        replay, = self.outputs('.json')
        self.assertIn('.protection.replay-2026-09-02_2026-09-02.json', replay.name)
        data = json.loads(replay.read_text())
        self.assertEqual(data['replay'], {'start': '2026-09-02', 'end': '2026-09-02'})
        trackers = [t for t in data['trackers'] if t['vm'] == 'vmA']
        self.assertTrue(trackers, 'vmA is the eligible member (vmB is a VIOS, vmS a standby)')
        self.assertFalse([t for t in data['trackers'] if t['vm'] != 'vmA'])
        for t in trackers:
            by_label = {}
            for level in t['levels']:
                for label in level['labels']:
                    by_label[label] = level
            for profile in t['profiles']:
                if profile + '/target' not in by_label:
                    continue
                target, installed = by_label[profile + '/target'], by_label[profile + '/installed']
                # Losses fall as the threshold rises; at installed nothing is lost.
                self.assertEqual(installed['busy']['minutes'], 0)
                self.assertEqual(by_label[profile + '/ladder']['busy']['minutes'], 0)
                self.assertGreater(target['busy']['minutes'], 0, 'the uncapped cut loses guarantee on the held-out day')
                self.assertGreaterEqual(target['consumed']['coreMinutes'], target['busy']['coreMinutes'])


if __name__ == '__main__':
    unittest.main()
