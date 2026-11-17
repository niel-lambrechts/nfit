"""Shadow candidate integration, with a known busy burst crossing midnight."""
import csv
import json
from pathlib import Path
import subprocess
import shutil
import unittest
from one_entry_point_test import mx, run


@unittest.skipUnless(mx.perl_ready(), 'Perl modules required')
class CandidateTest(unittest.TestCase):
    def setUp(self):
        mx.MemberExposureTests.roots = []
        self.root = Path(mx.MemberExposureTests.make_root())
        self.addCleanup(shutil.rmtree, self.root)
        self.cache = self.root / 'stage' / mx.SERIAL
        self.cfg = self.root / 'vms.csv'
        with self.cfg.open('w') as f:
            w = csv.writer(f)
            w.writerow('hostname serial systemtype procpool_name procpool_id entitledcpu maxcpu'.split())
            for vm, role in [('vmA','AIX'), ('vmB','VIO Server'), ('vmS','PowerHA Standby')]:
                ent, vp, pool, _ = mx.CONFIG[vm]
                w.writerow([vm, mx.SERIAL, role, 'pool', pool, ent, vp])

    def replay(self, *flags):
        for path in (self.root/'output').glob('*.log'):
            path.unlink()
        run(str(self.root), str(self.cache), '--config', str(self.cfg), '--candidate-test', *flags)
        log, = (self.root/'output').glob('*.log')
        return log.read_text().split('Shadow entitlement candidates')[-1]

    def test_exact_counts_and_roles(self):
        text = self.replay()
        self.assertIn('vmB: excluded from automatic change: VIOS', text)
        self.assertIn('vmS: excluded from automatic change: standby LPAR', text)
        self.assertIn('vmX: role not known in the VM configuration', text)
        line, = [l for l in text.splitlines() if 'G3-95W15/lower' in l]
        threshold = float(line.split()[0])
        def stats(full):
            minutes = []
            for t in range(mx.DAY1, mx.DAY1+86400*mx.DAYS, 60):
                b = mx.cpu('vmA',t)[1]
                if not b or t == mx.BAD_PAIR or float(b) <= threshold:
                    continue
                if full and not any(mx.between(t,start,n) for start,n in
                                    [(mx.W1,30),(mx.W3,20),(mx.W4,15)]):
                    continue
                minutes.append((t,float(b)-threshold))
            longest = run_length = 0
            prior = None
            for t,_ in minutes:
                run_length = run_length+1 if prior == t-60 else 1
                longest = max(longest,run_length)
                prior = t
            return f'{len(minutes)}/{sum(x for _,x in minutes):.2f}/{longest}/{len({t//86400 for t,_ in minutes})}'
        self.assertIn('Above '+stats(False), line)
        self.assertIn('Full '+stats(True), line)
        self.assertIn('A/B/C', line)

    def test_saved_snapshot_matches_integrated_and_refuses_stale_source(self):
        expected = self.replay()
        snap, = self.cache.glob('.nfit.store/proposals/v1/state-based/*.json')
        args = ['--config', str(self.cfg), '--candidate-proposals', str(snap)]
        result = run(str(self.root), str(self.cache), *args)
        line = next(l for l in expected.splitlines() if 'G3-95W15/lower' in l)
        self.assertIn(line, result.stdout)
        manifest = self.cache/'.nfit.cache.manifest'
        manifest.write_text(manifest.read_text().replace('synthetic-1', 'synthetic-2'))
        bad = subprocess.run(['perl',str(self.root/'nfit-profile'),'--nmondir',str(self.cache),*args],
                             capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('generation mismatch',bad.stderr)

    def test_heldout_replay_stops_at_configuration_change(self):
        f = self.cache/'.nfit.cache.states'
        states = json.loads(f.read_bytes())
        first, = states['vmA']
        second = json.loads(json.dumps(first))
        second['start_epoch'] = mx.DAY1 + 86400
        first['end_epoch'] = second['start_epoch'] - 60
        second['metadata']['smt'] = 4
        states['vmA'].append(second)
        f.write_text(json.dumps(states))
        text = self.replay('--enddate','2026-09-01','--candidate-window','2026-09-02/2026-09-02')
        self.assertIn('vmA: its configuration changed before the replay period',text)
        self.assertNotIn('G3-95W15/lower',text)

    def test_mixed_transition_minute_is_not_replayed(self):
        f = self.cache/'.nfit.cache.states'
        states = json.loads(f.read_bytes())
        first, = states['vmA']
        second = json.loads(json.dumps(first))
        second['start_epoch'] = mx.DAY1 + 86400 + 90
        first['end_epoch'] = second['start_epoch'] - 1
        second['metadata']['smt'] = 4
        states['vmA'].append(second)
        f.write_text(json.dumps(states))
        text = self.replay('--enddate','2026-09-01','--candidate-window','2026-09-02/2026-09-02')
        line = next(l for l in text.splitlines() if 'vmA pool' in l and 'all hours' in l)
        self.assertIn('expected minutes 1/1/1', line)

    def test_heldout_and_capped(self):
        f = self.cache/'.nfit.cache.states'
        states = json.loads(f.read_bytes())
        states['vmA'][0]['metadata']['capped'] = 1
        f.write_text(json.dumps(states))
        text = self.replay('--enddate','2026-09-01','--candidate-window','2026-09-02/2026-09-02')
        self.assertIn('2026-09-02 through 2026-09-02', text)
        self.assertIn('vmA: not confirmed as an uncapped shared-processor LPAR', text)
        self.assertIn('No eligible candidates', text)


if __name__ == '__main__':
    unittest.main()
