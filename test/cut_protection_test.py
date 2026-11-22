"""Cut protection (v10 stage 3.3) on the member-exposure fixture.

The standard run records, for each profile of an LPAR with a cut, the exact
protection bound from its contested minutes, raises each cut to the bound in
the CSV and writes the "Cut Protection" section. The profile figure recorded is
the one --no-cut-protection publishes. A replay of the saved proposal on the
held-out day scores the target, the three adjusted values and the installed
entitlement.

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

    def test_record_bound_and_protected_csv(self):
        log, csv_on = self.standard()
        self.assertIn('\nCut Protection\n', log)
        self.assertIn('The CSV carries the protected figures.', log)
        self.assertIn('Cut protection                 : profile ', log)
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
        # Switched off, the CSV carries the profile figures the record holds; switched
        # on, each cut stops at the bound (here installed, 2.00).
        self.clear_outputs()
        shutil.rmtree(self.cache / '.nfit.store' / 'proposals', ignore_errors=True)
        log_off, csv_off = self.standard('--no-cut-protection')
        self.assertIn('Not evaluated: switched off with --no-cut-protection.', log_off)
        self.assertIn('No figure in the CSV is protected.', log_off)
        row = lambda text: next(r for r in csv.DictReader(text.splitlines()) if r['VM'] == 'vmA')
        on, off = row(csv_on), row(csv_off)
        for profile, r in records.items():
            self.assertAlmostEqual(r['target'], float(off[profile]), places=2, msg=profile)
            if r['cut'] and r['status'] == 'bound':
                self.assertEqual(on[profile], '2.00', profile)
        self.assertEqual(on['NFIT - ENT'], '2.00')
        self.assertLess(float(off['NFIT - ENT']), 2.0)
        self.assertTrue(on['NFIT - Action'].startswith('Cut limited to 2.00 (profile %s): busy work reached this level while pool 7 was contested'
                                                       % off['NFIT - ENT']), on['NFIT - Action'])
        self.assertFalse(off['NFIT - Action'].startswith('Cut limited'))
        # The prefix appears exactly where the published planning figure moved: vmB's bound
        # raises some alternatives but leaves its planning entitlement as it was.
        rows_on = {r['VM']: r for r in csv.DictReader(csv_on.splitlines())}
        for r_off in csv.DictReader(csv_off.splitlines()):
            r_on = rows_on[r_off['VM']]
            moved = float(r_on['NFIT - ENT']) > float(r_off['NFIT - ENT']) + 0.005
            self.assertEqual(r_on['NFIT - Action'].startswith('Cut limited'), moved, r_off['VM'])

    def gates(self, *flags):
        shutil.rmtree(self.cache / '.nfit.store' / 'proposals', ignore_errors=True)
        log, text = self.standard(*flags)
        data = json.loads(self.snapshot().read_text())
        gates = {}
        for vm, member in data['members'].items():
            for v in member['profiles'].values():
                if 'tierFloor' in v:
                    gates.setdefault(vm, set()).add(v['tierFloor']['gate'])
        return gates, {r['VM']: r for r in csv.DictReader(text.splitlines())}, log

    def test_uplift_exemption_follows_the_pool_evidence(self):
        # vmA's pool was contested: the rule applies. The standby vmS sits in a pool that was
        # never contested, fully classified: its own-workload sizing keeps the profile figure.
        gates, rows, log = self.gates()
        self.assertEqual(gates['vmA'], {'applied (contested)'})
        self.assertEqual(gates['vmS'], {'withheld (no contested minute)'})
        self.assertIn('not applied: no pool competition observed (100.0% of the profile hours classified); the profile figure stands', log)
        self.assertIn('applies: the pool was contested in', log)
        # Switched off, or without pool evidence, the rule applies everywhere as before.
        off_gates, off_rows, _ = self.gates('--no-uplift-exemption')
        self.assertEqual(off_gates['vmS'], {'applied (exemption off)'})
        none_gates, none_rows, none_log = self.gates('--no-cut-protection')
        self.assertEqual(none_gates['vmS'], {'applied (legacy: no pool evidence for the current state)'})
        self.assertIn('applies as legacy policy: no pool evidence for the current state', none_log)
        profiles = [k for k in rows['vmS'] if k + '_rENT_Tactical' in rows['vmS']]
        lower = 0
        for name in profiles:
            self.assertLessEqual(float(rows['vmS'][name]), float(off_rows['vmS'][name]) + 1e-9, name)
            self.assertEqual(off_rows['vmS'][name], none_rows['vmS'][name], name)
            lower += float(rows['vmS'][name]) < float(off_rows['vmS'][name]) - 0.005
            self.assertEqual(rows['vmA'][name], off_rows['vmA'][name], name)      # contested: unchanged
        self.assertGreater(lower, 0)
        for vm in rows:                                                          # every pair stays valid
            ent, vps = float(rows[vm]['NFIT - ENT']), int(rows[vm]['NFIT - VCPU'])
            self.assertTrue(0.05 * vps - 1e-9 <= ent <= vps + 1e-9, vm)

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

    def test_pool_use_and_the_trial_increase(self):
        # One day: pool 7 was at its maximum on that day only, which is too seldom to act on.
        log, text = self.standard()
        self.assertIn('\nPool Use\n', log)
        self.assertRegex(log, r'Pool 7 was at its maximum of 4 in \d+ minutes on 1 day: too seldom to act on\.')
        self.assertNotIn('trial increase of the pool', log + text)
        # Two days: it recurs, and vmA had busy work above its entitlement while it was full.
        self.clear_outputs()
        shutil.rmtree(self.cache / '.nfit.store' / 'proposals', ignore_errors=True)
        run(str(self.root), str(self.cache), '--config', str(self.cfg), '--enddate', '2026-09-02')
        log = self.outputs('.log')[0].read_text(encoding='utf-8')
        rows = {r['VM']: r for r in csv.DictReader(
            [p for p in self.outputs('.csv') if '.decisions.' not in p.name][0].read_text(encoding='utf-8').splitlines())}
        flat = ' '.join(log.split())
        self.assertRegex(flat, r'Pool 7: trial increase of the pool maximum from 4 to 5 processing units, then re-measure\. The pool was at '
                               r'its maximum of 4 in \d+ minutes on 2 days, with busy work above entitlement on 1 LPAR in its profile hours \(vmA\)\. A trial, '
                               r'not a measured requirement\. Check licensing\.')
        self.assertRegex(rows['vmA']['NFIT - Action'], r"Pool 7 was at its maximum with this LPAR's busy work above its entitlement on \d+ "
                                                       r'minutes? over 2 days: a trial increase of the pool to 5 is recommended\.$')
        for vm in ('vmB', 'vmS', 'vmX'):
            self.assertNotIn('trial increase of the pool', rows[vm]['NFIT - Action'], vm)
        self.assertRegex(flat, r'Pool 9 was at its maximum of 2 in \d+ minutes on 1 day: too seldom to act on\.')
        self.assertNotIn('Pool 7 was at its maximum in', flat)        # the older observation line gives way

    def test_contested_level_floor_and_its_research_record(self):
        # The research switch adds its record; with the fixture's contested minutes on one day only, no floor
        # applies and the CSV figures stay.
        _, plain = self.standard()
        shutil.rmtree(self.cache / '.nfit.store' / 'proposals', ignore_errors=True)
        _, text = self.standard('--research-contested-level', '--research-busy-share')
        cells = lambda t: {r['VM']: {k: v for k, v in r.items() if k and k[0] in 'PGOB' and '-' in k} for r in csv.DictReader(t.splitlines())}
        self.assertEqual(cells(plain), cells(text))
        snap = self.snapshot()
        profiles = json.loads(snap.read_text())['members']['vmA']['profiles']
        levels = {p: v['contestedLevel'] for p, v in profiles.items() if 'contestedLevel' in v}
        self.assertTrue(levels, 'vmA has contested minutes, so its profiles carry a contested level')
        # Busy share (research): the fixture has no relaxed minute at the contested consumption,
        # so the comparison is recorded as not comparable, with its minutes counted.
        shares = [row for v in profiles.values() for row in v.get('busyShare', [])]
        self.assertTrue(shares and all(r['condition'] == 'contested' and r['regime'] == 'above' for r in shares))
        self.assertTrue(all(not r['sufficient'] and r['pressuredMinutesAll'] > 0 and 'busySharePressured' not in r for r in shares))
        for name, c in levels.items():
            self.assertGreater(c['minutes'], 0, name)
            self.assertEqual(c['sufficient'], c['minutes'] >= 15 and c['days'] >= 2, name)
            self.assertGreaterEqual(c['candidate'], c['noRule'] - 1e-9, name)
            if c['sufficient'] and c['level'] > c['noRule'] + 0.005:
                self.assertGreaterEqual(c['candidate'], c['level'] - 1e-9, name)     # a floor, not limited by installed
            else:
                self.assertEqual(c['candidate'], c['noRule'], name)                  # thin evidence or no gain: unchanged
        # The fixture's contested minutes fall on one day. With the day requirement of the
        # copied program lowered to 1, the evidence is sufficient: the floor raises the published
        # target above the installed 2.0 (a requirement is not limited by today's configuration),
        # the record says what it rests on, and --no-contested-level gives the earlier figures.
        prog = self.root / 'nfit-profile'
        source = prog.read_text(encoding='utf-8')
        self.assertEqual(source.count('my $CONTESTED_LEVEL_MIN_DAYS    = 2;'), 1)
        prog.write_text(source.replace('my $CONTESTED_LEVEL_MIN_DAYS    = 2;', 'my $CONTESTED_LEVEL_MIN_DAYS    = 1;'), encoding='utf-8')
        shutil.rmtree(self.cache / '.nfit.store' / 'proposals', ignore_errors=True)
        _, off = self.standard('--no-contested-level')
        self.assertEqual(cells(plain), cells(off))
        self.assertEqual(json.loads(self.snapshot().read_text())['settings']['contestedLevel'], 'off')
        shutil.rmtree(self.cache / '.nfit.store' / 'proposals', ignore_errors=True)
        _, text = self.standard('--research-contested-level')
        self.assertNotEqual(cells(plain), cells(text))
        snap = self.snapshot()
        self.assertEqual(json.loads(snap.read_text())['settings']['contestedLevel'], 'contested-level-2')
        profiles = json.loads(snap.read_text())['members']['vmA']['profiles']
        published = 0
        for name, v in profiles.items():
            floor = v.get('contestedFloor')
            if not floor or not floor['applied']:
                continue
            self.assertGreaterEqual(v['entitlement'], floor['level'] - 1e-9, name)       # the published target carries the floor
            self.assertGreater(v['entitlement'], 2.0, name)                              # above the installed entitlement
            self.assertGreater(floor['level'], floor['originalTarget'], name)
            self.assertEqual((floor['finalTarget'], floor['rule'], floor['cadenceMinutes'], floor['pool']), (v['entitlement'], 'contested-level-2', 1, 7), name)
            self.assertGreaterEqual(floor['observations'], 15, name)
            self.assertTrue(floor['stateStartUTC'], name)
            # the current cycle is resolved apart from the requirement: a valid pair that does not exceed the target
            cycle = v['currentCycle']
            self.assertLessEqual(cycle['entitlement'], v['entitlement'] + 1e-9, name)
            self.assertLessEqual(cycle['entitlement'], cycle['VPs'] + 1e-9, name)
            published += 1
        self.assertGreater(published, 0)
        row = next(r for r in csv.DictReader(text.splitlines()) if r['VM'] == 'vmA')
        if (profiles[row['Planning Profile']].get('contestedFloor') or {}).get('applied'):
            self.assertRegex(row['NFIT - Action'], r'Target raised to \d+\.\d\d: profile-level protection of observed work during pool competition; '
                                                   r'\d+ observations over 1 day\.')
        raised = 0
        for name, c in ((p, v['contestedLevel']) for p, v in profiles.items() if 'contestedLevel' in v):
            self.assertEqual(c['sufficient'], c['minutes'] >= 15, name)
            if c['sufficient'] and c['level'] > c['noRule'] + 0.005:
                self.assertGreaterEqual(c['candidate'], c['level'] - 1e-9, name)
                self.assertGreater(c['candidate'], 2.0, name)
                self.assertGreaterEqual(c['candidateVPs'], c['candidate'] - 1e-9, name)
                raised += 1
        self.assertGreater(raised, 0)
        self.clear_outputs()
        proc = subprocess.run(['perl', str(self.root / 'nfit-profile'), '--nmondir', str(self.cache), '--config', str(self.cfg),
                               '--candidate-proposals', str(snap), '--candidate-window', '2026-09-02/2026-09-02'],
                              cwd=str(self.root), capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        replay, = self.outputs('.json')
        labels = {l for t in json.loads(replay.read_text())['trackers'] for level in t['levels'] for l in level['labels']}
        for name in levels:
            for plan in ('clNoRule', 'clCandidate', 'clPublished'):
                self.assertIn(name + '/' + plan, labels)


if __name__ == '__main__':
    unittest.main()
