#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/cycle_finalise_test.py
# PURPOSE  : Finalising a planning cycle (v10 C7 with B11), on the two-frame
#            PowerHA fixture (standby hanodeA on SYSHA1, primary hanodeB on
#            SYSHA2).
#            - the standby's final row is the same whichever frame was analysed
#              first, and is the row of one analysis that had the primary's plan
#              at hand (only the named source of the plan differs);
#            - CSV, rationale, saved plan and cycle summary agree; the rationale
#              marks its earlier figures provisional;
#            - a second finalisation writes the same bytes, and recognises the
#              outputs of the first as its own;
#            - a cycle is reusable: finalised with the primary's frame missing
#              (standby unresolved, cycle INCOMPLETE, exit 0), then again once
#              that frame has joined, with the complete result;
#            - the allocation footprint (B11) is written with the summary: frame
#              figures equal the sums of the final CSV, the shared capacity and
#              its check are stated, hybrid-state-decay leads, no fit wording;
#            - each model is finalised on its own;
#            - an analysis begun and not completed is not used; an expected
#              frame that is missing is listed;
#            - an output edited since the analysis is left alone (exit 5);
#              a changed capture or configuration file closes the cycle (exit 4)
#              and nothing is rewritten.
# USAGE    : python3 test/cycle_finalise_test.py
# ==============================================================================
import csv
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import powerha_roles_test as H
import planning_cycle_test as P

RESOLVED = ('PowerHA standby for hanodeB (inferred): 25% of its 1.50 target (its plan in this planning cycle) = 0.38 entitlement; '
            'its own workload needs more.')


def finalise(root, cycle='cycle'):
    return subprocess.run(['perl', os.path.join(root, 'nfit-profile'), '--finalise-cycle', os.path.join(root, cycle)],
                          capture_output=True, text=True, timeout=120, cwd=root)


def new_root(order, *flags):
    root = tempfile.mkdtemp(prefix='finalise-')
    _, last = H.build(root, True, P.SHARE)
    runs = [P.analyse(root, serial, last, *flags) for serial in order]
    return root, last, runs


def outputs(root, serial, model='state-based'):
    return {o['kind']: o['path'] for o in P.registration(root, serial, model)['outputs']}


def hashes(root, serial, model='state-based'):
    return {kind: P.sha(path) for kind, path in outputs(root, serial, model).items()}


def summary(root):
    with open(os.path.join(root, 'cycle', 'cycle-summary.csv')) as fh:
        return list(csv.DictReader(fh))


def summary_row(root, frame):
    return next(r for r in summary(root) if r['Frame'] == frame)


def status(root):
    return P.text(os.path.join(root, 'cycle', 'cycle-status.txt'))


@unittest.skipUnless(H.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class CycleFinaliseTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.roots = {}
        cls.done = {}
        for name, order in (('primary_first', ('SYSHA2', 'SYSHA1')), ('standby_first', ('SYSHA1', 'SYSHA2'))):
            root, cls.last, runs = new_root(order)
            assert all(r.returncode == 0 for r in runs), runs[-1].stderr[-600:]
            cls.roots[name] = root
            cls.done[name] = finalise(root)

    @classmethod
    def tearDownClass(cls):
        for root in cls.roots.values():
            shutil.rmtree(root, ignore_errors=True)

    def test_the_final_row_does_not_depend_on_the_order_of_analysis(self):
        for run in self.done.values():
            self.assertEqual(run.returncode, 0, run.stderr[-800:])
        first, second = P.standby_row(self.roots['primary_first']), P.standby_row(self.roots['standby_first'])
        self.assertEqual(first, second)
        self.assertTrue(first['NFIT - Action'].startswith(RESOLVED), first['NFIT - Action'])
        self.assertNotIn('Provisional', first['NFIT - Action'])
        # One analysis outside a cycle, with the primary's plan at hand, gives the same row.
        root = tempfile.mkdtemp(prefix='finalise-')
        try:
            H.build(root, True, P.SHARE)
            for serial in ('SYSHA2', 'SYSHA1'):
                run = subprocess.run(['perl', os.path.join(root, 'nfit-profile'), '--runq-perc-behavior=match', '--enddate', self.last,
                                      '--nmondir', os.path.join(root, 'stage', serial)], capture_output=True, text=True, timeout=300, cwd=root)
                self.assertEqual(run.returncode, 0, run.stderr[-600:])
            path, = [os.path.join(root, 'output', f) for f in os.listdir(os.path.join(root, 'output'))
                     if 'SYSHA1' in f and f.endswith('.csv') and '.decisions.' not in f]
            with open(path) as fh:
                single = next(r for r in csv.DictReader(fh) if r['VM'] == 'hanodeA')
        finally:
            shutil.rmtree(root, ignore_errors=True)
        single['NFIT - Action'] = single['NFIT - Action'].replace('its proposal for data ending ' + self.last, 'its plan in this planning cycle')
        self.assertEqual(first, single)

    def test_the_outputs_agree(self):
        root = self.roots['standby_first']
        row = P.standby_row(root)
        files = outputs(root, 'SYSHA1')
        plan = P.load(files['plan'])
        member = plan['members']['hanodeA']
        self.assertEqual(plan['cycle']['state'], 'final')
        self.assertEqual(plan['cycle']['provisionalPlan'], P.registration(root, 'SYSHA1')['plan'])
        self.assertNotIn('pendingRole', member)
        self.assertNotIn('unresolved', member['role'])
        self.assertEqual(member['role']['primaryTarget']['source'], 'cycle')
        self.assertEqual(('%.2f' % member['role']['entitlement'], str(member['role']['VPs'])), (row['NFIT - ENT'], row['NFIT - VCPU']))
        self.assertEqual((member['published']['entitlement'], str(member['published']['VPs']), member['published']['action']),
                         (row['NFIT - ENT'], row['NFIT - VCPU'], row['NFIT - Action']))
        # The final plan is kept in the cycle by content, and in the frame's own store for later analyses.
        done = P.load(os.path.join(root, 'cycle', 'finalised.json'))
        final = done['written']['SYSHA1.state-based']['final']
        self.assertEqual(final, P.sha(files['plan']))
        with gzip.open(os.path.join(root, 'cycle', 'final', final + '.json.gz'), 'rb') as fh:
            self.assertEqual(fh.read(), P.text(files['plan'], 'rb'))
        self.assertTrue(os.path.exists(os.path.join(root, 'stage', 'SYSHA1', '.nfit.store', 'proposals', 'v1', 'state-based', final + '.json')))
        # The provisional plan stays in the cycle as it was registered.
        with gzip.open(os.path.join(root, 'cycle', 'plans', plan['cycle']['provisionalPlan'] + '.json.gz'), 'rt') as fh:
            self.assertEqual(json.load(fh)['cycle']['state'], 'provisional')
        # The rationale: one block, after the analysis, naming the same pair.
        rationale = P.text(files['rationale'])
        self.assertEqual(rationale.count('Cycle Finalisation (planning cycle %s)' % done['cycle']), 1)
        block = rationale[rationale.index('Cycle Finalisation (planning cycle'):]
        self.assertIn('they replace the provisional figures above', block)
        self.assertRegex(block, r'hanodeA: \S+ / \d+ provisional, now %s / %s\.' % (row['NFIT - ENT'], row['NFIT - VCPU']))
        self.assertTrue(block.rstrip().endswith('[End of Cycle Finalisation]'))
        # The summary: each frame on its own, with what the registration and the block state.
        rows = {r['Frame']: r for r in summary(root)}
        self.assertEqual(set(rows), {'SYSHA1', 'SYSHA2'})
        self.assertEqual([rows[f]['State'] for f in ('SYSHA1', 'SYSHA2')], ['final', 'final'])
        self.assertEqual((rows['SYSHA1']['Standbys_Resolved'], rows['SYSHA1']['Standbys_Unresolved']), ('1', '0'))
        self.assertEqual(rows['SYSHA1']['Capture_Check'], 'size and time (weaker)')
        self.assertIn('current cycle %s, target %s.' % (rows['SYSHA1']['CurrentCycle_ENT'], rows['SYSHA1']['Target_ENT']), block)
        with open(files['csv']) as fh:
            total = sum(float(r['NFIT - ENT']) for r in csv.DictReader(fh))
        self.assertAlmostEqual(total, float(rows['SYSHA1']['CurrentCycle_ENT']), places=2)
        text = status(root)
        self.assertRegex(text, r'^Planning cycle \S+: finalised\n')
        self.assertIn('No list of expected frames was given: this is not stated to be the whole estate.', text)
        self.assertIn('state-based: 2 frames final; standbys across frames: 1 set, 0 unresolved', text)
        self.assertIn(text.splitlines()[0], self.done['standby_first'].stderr)

    def test_the_allocation_footprint(self):
        root = self.roots['standby_first']
        cycle = os.path.join(root, 'cycle')
        text = P.text(os.path.join(cycle, 'cycle-footprint.txt'))
        self.assertRegex(text, r'^Planning cycle \S+: finalised\n')
        self.assertIn('No list of expected frames was given: this is not stated to be the whole estate.', text)
        self.assertRegex(text, r'Model: hybrid-state-decay \(primary view\)\n-+\n  Not analysed in this cycle\.')
        self.assertLess(text.index('Model: hybrid-state-decay'), text.index('Model: state-based'))
        self.assertIn('Shared capacity 32 (agreed by 2 LPARs)', text)
        self.assertIn('Assessment: the entitlements of the LPARs analysed are within the shared capacity reported.', text)
        self.assertNotRegex(text, r'(?i)\bfits\b|procure|consolidat|spare')
        with open(os.path.join(cycle, 'cycle-footprint.csv')) as fh:
            frames = {r['Frame']: r for r in csv.DictReader(fh)}
        self.assertEqual(set(frames), {'SYSHA1', 'SYSHA2'})
        row = frames['SYSHA1']
        with open(outputs(root, 'SYSHA1')['csv']) as fh:
            published = list(csv.DictReader(fh))
        # The frame figures are the sums of what the final CSV shows; the standby is counted once, at its final figure.
        self.assertAlmostEqual(float(row['Installed_ENT']), sum(float(r['Current - ENT']) for r in published), places=2)
        self.assertAlmostEqual(float(row['CurrentCycle_ENT']), sum(float(r['NFIT - ENT']) for r in published), places=2)
        self.assertEqual((row['Installed_VP'], row['CurrentCycle_VP']),
                         (str(sum(int(r['Current - VCPU']) for r in published)), str(sum(int(r['NFIT - VCPU']) for r in published))))
        self.assertEqual(row['Standby_CurrentCycle_ENT'], P.standby_row(root)['NFIT - ENT'])
        self.assertEqual((row['Shared_Capacity'], row['Shared_Capacity_Check'], row['State']), ('32', 'agreed by 2 LPARs', 'final'))
        self.assertAlmostEqual(float(row['CurrentCycle_ENT_Uncommitted']), 32 - float(row['CurrentCycle_ENT']), places=2)
        self.assertEqual(row['Full_ENT'], summary_row(root, 'SYSHA1')['Target_ENT'])                 # one figure in both cycle files
        self.assertEqual((row['LPARs'], row['LPARs_Left'], row['Left_Installed_ENT']), ('2', '0', '0.00'))
        members = P.load(outputs(root, 'SYSHA1')['plan'])['members']
        self.assertEqual({m.get('lastSampled') for m in members.values()}, {self.last})          # from the engine's sample store
        self.assertEqual(members['hanodeA'].get('estateRole'), 'standby')
        with open(os.path.join(cycle, 'cycle-pools.csv')) as fh:
            pools = list(csv.DictReader(fh))
        self.assertEqual([(p['Frame'], p['Pool'], p['Maximum']) for p in pools], [('SYSHA1', '0 (default)', '32'), ('SYSHA2', '0 (default)', '32')])
        self.assertEqual(pools[0]['Full_ENT'], row['Full_ENT'])                                      # D-024: totals and pool sums agree
        plan = P.load(outputs(root, 'SYSHA1')['plan'])
        self.assertEqual(plan['frame']['sharedProcessors']['status'], 'agreed')
        # Order of analysis does not change it, and a repeat writes the same bytes.
        other = os.path.join(self.roots['primary_first'], 'cycle')
        strip = lambda t: re.sub(r'\d{8}T\d{6}-[0-9a-f]{6}', 'CYCLE', t)
        for name in ('cycle-footprint.csv', 'cycle-pools.csv'):
            self.assertEqual(strip(P.text(os.path.join(cycle, name))), strip(P.text(os.path.join(other, name))), name)
        before = {name: P.text(os.path.join(cycle, name), 'rb') for name in ('cycle-footprint.txt', 'cycle-footprint.csv', 'cycle-pools.csv')}
        self.assertEqual(finalise(root).returncode, 0)
        self.assertEqual({name: P.text(os.path.join(cycle, name), 'rb') for name in before}, before)

    def test_a_second_finalisation_changes_nothing(self):
        root = self.roots['primary_first']
        before = {serial: hashes(root, serial) for serial in ('SYSHA1', 'SYSHA2')}
        recorded = P.load(os.path.join(root, 'cycle', 'finalised.json'))['written']
        self.assertEqual(set(recorded), {'SYSHA1.state-based'})                       # only the frame with a standby is rewritten
        self.assertEqual(sorted(recorded['SYSHA1.state-based']['outputs'].values()), sorted(before['SYSHA1'].values()))
        registered = {o['kind']: o['sha256'] for o in P.registration(root, 'SYSHA1')['outputs']}
        self.assertNotEqual(registered['csv'], before['SYSHA1']['csv'])               # the row was replaced
        self.assertEqual({o['kind']: o['sha256'] for o in P.registration(root, 'SYSHA2')['outputs']}, before['SYSHA2'])
        summary_before = P.text(os.path.join(root, 'cycle', 'cycle-summary.csv'), 'rb')
        again = finalise(root)
        self.assertEqual(again.returncode, 0, again.stderr[-800:])
        self.assertEqual({serial: hashes(root, serial) for serial in ('SYSHA1', 'SYSHA2')}, before)
        self.assertEqual(P.text(os.path.join(root, 'cycle', 'cycle-summary.csv'), 'rb'), summary_before)
        self.assertEqual(P.load(os.path.join(root, 'cycle', 'finalised.json'))['written'], recorded)
        self.assertEqual(len(os.listdir(os.path.join(root, 'cycle', 'final'))), 1)


@unittest.skipUnless(H.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class CycleReuseTests(unittest.TestCase):

    def setUp(self):
        self.roots = []

    def tearDown(self):
        for root in self.roots:
            shutil.rmtree(root, ignore_errors=True)

    def root(self, order, *flags):
        root, last, runs = new_root(order, *flags)
        self.roots.append(root)
        for run in runs:
            self.assertEqual(run.returncode, 0, run.stderr[-600:])
        return root, last

    def test_a_cycle_finalised_early_and_again_when_complete(self):
        root, last = self.root(('SYSHA1',))
        with open(os.path.join(root, 'expected.txt'), 'w') as fh:
            fh.write('SYSHA1\nSYSHA2\n')
        shutil.copy(os.path.join(root, 'expected.txt'), os.path.join(root, 'cycle', 'expected-systems'))
        early = finalise(root)
        self.assertEqual(early.returncode, 0, early.stderr[-800:])                     # processed, though incomplete
        self.assertRegex(early.stderr, r'Planning cycle \S+: INCOMPLETE')
        self.assertIn('Frames analysed: 1. Frames expected: 2.', early.stderr)
        self.assertIn('expected and not analysed: SYSHA2', early.stderr)
        row = P.standby_row(root)
        self.assertTrue(row['NFIT - Action'].startswith("PowerHA standby for hanodeB (inferred): unresolved, hanodeB's frame is not analysed in "
                                                        "this cycle. 25% of its installed 1.50 limits a cut to 0.38 and adds no increase."), row['NFIT - Action'])
        self.assertLessEqual(float(row['NFIT - ENT']), float(row['Current - ENT']) + 1e-9)
        rows = {r['Frame']: r for r in summary(root)}
        self.assertEqual((rows['SYSHA1']['State'], rows['SYSHA1']['Standbys_Unresolved']), ('standbys unresolved', '1'))
        self.assertEqual(rows['SYSHA2']['State'], 'not analysed')
        plan = P.load(outputs(root, 'SYSHA1')['plan'])
        self.assertEqual(plan['members']['hanodeA']['role']['unresolved'], "hanodeB's frame is not analysed in this cycle")
        # The primary's frame joins the same cycle: the earlier finalisation is marked out of date.
        run = P.analyse(root, 'SYSHA2', last)
        self.assertEqual(run.returncode, 0, run.stderr[-600:])
        self.assertIn('The cycle was finalised before this analysis', run.stderr)
        self.assertIn('OUT OF DATE: an analysis joined the cycle after this finalisation.', status(root))
        done = finalise(root)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        self.assertRegex(done.stderr, r'Planning cycle \S+: finalised')
        self.assertNotIn('OUT OF DATE', status(root))
        row = P.standby_row(root)
        self.assertTrue(row['NFIT - Action'].startswith(RESOLVED), row['NFIT - Action'])
        rationale = P.text(outputs(root, 'SYSHA1')['rationale'])
        self.assertEqual(rationale.count('Cycle Finalisation (planning cycle'), 1)     # the earlier block is replaced
        self.assertNotIn('unresolved, hanodeB', rationale[rationale.index('Cycle Finalisation (planning cycle'):])
        # ... and it is the result of a cycle that was complete from the start.
        whole, _ = self.root(('SYSHA2', 'SYSHA1'))
        self.assertEqual(finalise(whole).returncode, 0)
        self.assertEqual(P.standby_row(whole), row)

    def test_each_model_is_finalised_on_its_own(self):
        root, last = self.root(('SYSHA1', 'SYSHA2'))
        run = P.analyse(root, 'SYSHA1', last, '--decay-over-states')                   # the second model: the standby's frame only
        self.assertEqual(run.returncode, 0, run.stderr[-600:])
        done = finalise(root)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        self.assertRegex(done.stderr, r'Planning cycle \S+: INCOMPLETE')
        self.assertIn('state-based: 2 frames final; standbys across frames: 1 set, 0 unresolved', done.stderr)
        self.assertIn('hybrid-state-decay: 1 frame standbys unresolved; standbys across frames: 0 set, 1 unresolved', done.stderr)
        rows = {(r['Frame'], r['Model']): r for r in summary(root)}
        self.assertEqual(len(rows), 3)                                                # nothing is added across models
        self.assertEqual(rows[('SYSHA1', 'hybrid-state-decay')]['State'], 'standbys unresolved')
        with open(outputs(root, 'SYSHA1', 'hybrid-state-decay')['csv']) as fh:
            other = next(r for r in csv.DictReader(fh) if r['VM'] == 'hanodeA')
        self.assertIn("unresolved, hanodeB's frame is not analysed in this cycle", other['NFIT - Action'])    # another model's plan is not borrowed
        self.assertTrue(P.standby_row(root)['NFIT - Action'].startswith(RESOLVED))

    def test_an_analysis_begun_and_not_completed_is_not_used(self):
        root, last = self.root(('SYSHA1', 'SYSHA2'))
        marker = os.path.join(root, 'cycle', 'runs', 'SYSHA2.state-based.started')
        with open(marker, 'w') as fh:
            json.dump({'token': 'a-later-attempt', 'started': '2026-11-25T00:00:00Z'}, fh)
        done = finalise(root)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        self.assertRegex(done.stderr, r'Planning cycle \S+: INCOMPLETE')
        self.assertIn("unresolved, the analysis of hanodeB's frame was not completed in this cycle", P.standby_row(root)['NFIT - Action'])
        rows = {r['Frame']: r for r in summary(root)}
        self.assertEqual(rows['SYSHA2']['State'], 'analysis not completed')
        self.assertEqual(P.load(os.path.join(root, 'cycle', 'finalised.json'))['incomplete'], ['SYSHA2.state-based'])

    def test_an_edited_output_is_left_alone(self):
        root, last = self.root(('SYSHA1', 'SYSHA2'))
        path = outputs(root, 'SYSHA1')['csv']
        with open(path, 'a') as fh:
            fh.write('"edited by hand"\n')
        edited = hashes(root, 'SYSHA1')
        done = finalise(root)
        self.assertEqual(done.returncode, 5, done.stderr[-800:])
        self.assertEqual(hashes(root, 'SYSHA1'), edited)                               # CSV, rationale and plan as they were
        self.assertIn('outputs changed since the analysis; not rewritten', done.stderr)
        self.assertRegex(done.stderr, r'Planning cycle \S+: INCOMPLETE')
        self.assertNotIn('closed', P.load(os.path.join(root, 'cycle', 'cycle.json')))

    def test_a_changed_input_closes_the_cycle(self):
        for change in ('capture', 'peer capture', 'configuration'):
            root, last = self.root(('SYSHA1', 'SYSHA2'))
            before = hashes(root, 'SYSHA1')
            if change == 'configuration':
                with open(os.path.join(root, 'etc', 'nfit.vms.cfg'), 'a') as fh:
                    fh.write('# edited\n')
                expect = r'cannot be finalised: changed since its analyses: nfit\.vms\.cfg \(changed\)\.'
            else:
                serial = 'SYSHA1' if change == 'capture' else 'SYSHA2'
                os.utime(os.path.join(root, 'stage', serial, '.nfit.cache.data'), (1, 1))
                expect = r'cannot be finalised: changed since its analyses: the staged data of frame %s\.' % serial
            done = finalise(root)
            self.assertEqual(done.returncode, 4, (change, done.stderr[-600:]))
            self.assertRegex(done.stderr, expect)
            self.assertIn('The cycle is closed and nothing was rewritten. Start a new cycle.', done.stderr)
            self.assertEqual(hashes(root, 'SYSHA1'), before)
            self.assertFalse(os.path.exists(os.path.join(root, 'cycle', 'finalised.json')))
            self.assertIn('closed', P.load(os.path.join(root, 'cycle', 'cycle.json')))
            again = finalise(root)
            self.assertEqual(again.returncode, 4)
            self.assertRegex(again.stderr, r'Planning cycle \S+ is closed')

    def test_what_is_not_a_cycle(self):
        root = tempfile.mkdtemp(prefix='finalise-')
        self.roots.append(root)
        H.build(root, True, P.SHARE)
        done = finalise(root, 'nothing-here')
        self.assertEqual(done.returncode, 4)
        self.assertIn('is not a planning cycle of this nFit', done.stderr)
        run = subprocess.run(['perl', os.path.join(root, 'nfit-profile'), '--finalise-cycle', os.path.join(root, 'cycle'),
                              '--nmondir', os.path.join(root, 'stage', 'SYSHA1')], capture_output=True, text=True, cwd=root)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('--finalise-cycle takes the cycle directory only: it runs no analysis', run.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=1)
