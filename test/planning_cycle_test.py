#!/usr/bin/env python3
# ==============================================================================
# NAME     : test/planning_cycle_test.py
# PURPOSE  : Joining and registering in a planning cycle (v10 C7 with B11), on
#            the two-frame PowerHA fixture (standby hanodeA on SYSHA1, primary
#            hanodeB on SYSHA2).
#            - the first run creates the cycle; each completed analysis is
#              registered with its capture, its provisional plan (stored once,
#              by content), the hashes of its outputs and its pending standbys;
#            - a standby whose primary is on another frame is provisional and
#              the same whichever frame was analysed first;
#            - a second model joins without changing the cycle ID or the first
#              model's registrations; an option given twice counts by the value
#              in effect;
#            - another option, a changed configuration file, a run for selected
#              LPARs or a seasonal run does not join; a changed capture closes
#              the cycle.
#            Finalisation is tested with the finaliser.
# USAGE    : python3 test/planning_cycle_test.py
# ==============================================================================
import csv
import glob
import gzip
import hashlib
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

SHARE = '[GLOBAL]\nha_sizing = standby_share\n'


def analyse(root, serial, last, *flags, cycle='cycle'):
    cmd = ['perl', os.path.join(root, 'nfit-profile'), '--runq-perc-behavior=match', '--enddate', last,
           '--nmondir', os.path.join(root, 'stage', serial), '--cycle', os.path.join(root, cycle)] + list(flags)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=root)


def load(path):
    with open(path) as fh:
        return json.load(fh)


def text(path, mode='r'):
    with open(path, mode) as fh:
        return fh.read()


def registration(root, serial, model='state-based'):
    return load(os.path.join(root, 'cycle', 'runs', '%s.%s.json' % (serial, model)))


def sha(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def standby_row(root):
    reg = registration(root, 'SYSHA1')
    path, = [o['path'] for o in reg['outputs'] if o['kind'] == 'csv']
    with open(path) as fh:
        return next(r for r in csv.DictReader(fh) if r['VM'] == 'hanodeA')


@unittest.skipUnless(H.perl_ready(), 'perl with JSON and List::MoreUtils needed')
class PlanningCycleTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.roots = {}
        cls.runs = {}
        for name, order in (('primary_first', ('SYSHA2', 'SYSHA1')), ('standby_first', ('SYSHA1', 'SYSHA2'))):
            root = tempfile.mkdtemp(prefix='cycle-')
            _, cls.last = H.build(root, True, SHARE)
            with open(os.path.join(root, 'expected.txt'), 'w') as fh:
                fh.write('SYSHA1\nSYSHA2\nSYSHA3\n')
            cls.roots[name] = root
            cls.runs[name] = [analyse(root, serial, cls.last, '--cycle-systems', os.path.join(root, 'expected.txt')) for serial in order]

    @classmethod
    def tearDownClass(cls):
        for root in cls.roots.values():
            shutil.rmtree(root, ignore_errors=True)

    def test_the_cycle_and_its_registrations(self):
        root = self.roots['primary_first']
        for run in self.runs['primary_first']:
            self.assertEqual(run.returncode, 0, run.stderr[-800:])
        cycle = load(os.path.join(root, 'cycle', 'cycle.json'))
        self.assertRegex(cycle['id'], r'^\d{8}T\d{6}-[0-9a-f]{6}$')
        self.assertRegex(cycle['contextFingerprint'], r'^[0-9a-f]{64}$')
        self.assertNotIn(cycle['contextFingerprint'][:6], cycle['id'])           # the ID is not derived from the settings
        self.assertEqual(cycle['context']['analysisEnd'], self.last)
        self.assertEqual(set(cycle['context']['configuration']), {'profiles', 'tiers', 'vmConfig', 'seasonality'})
        self.assertNotIn('closed', cycle)
        self.assertIn('Planning cycle %s: this analysis joins it (state-based)' % cycle['id'], self.runs['primary_first'][0].stderr)
        self.assertEqual(text(os.path.join(root, 'cycle', 'expected-systems')).split(), ['SYSHA1', 'SYSHA2', 'SYSHA3'])
        self.assertTrue(glob.glob(os.path.join(root, 'cycle', 'inputs', '*.nfit.vms.cfg')))
        self.assertTrue(glob.glob(os.path.join(root, 'cycle', 'inputs', '*.nfit.profiles.cfg')))
        for serial in ('SYSHA1', 'SYSHA2'):
            reg = registration(root, serial)
            marker = load(os.path.join(root, 'cycle', 'runs', serial + '.state-based.started'))
            self.assertEqual((reg['cycle'], reg['token'], reg['model']), (cycle['id'], marker['token'], 'state-based'))
            self.assertEqual(reg['capture']['kind'], 'size and time (weaker)')    # the fixture's manifest states no revisions
            self.assertEqual(reg['coverage']['end'], self.last)
            self.assertEqual(sorted(o['kind'] for o in reg['outputs']), ['csv', 'plan', 'rationale'])
            for output in reg['outputs']:
                self.assertEqual(sha(output['path']), output['sha256'], output['kind'])
            copy, = [o['path'] for o in reg['outputs'] if o['kind'] == 'plan']
            stored = os.path.join(root, 'cycle', 'plans', reg['plan'] + '.json.gz')
            with gzip.open(stored, 'rb') as fh, open(copy, 'rb') as plain:
                self.assertEqual(fh.read(), plain.read())                             # the provisional plan, by content
            self.assertEqual(reg['plan'], sha(copy))
            self.assertGreater(reg['totals']['All']['Installed'], 0)
            other = 'SYSHA2' if serial == 'SYSHA1' else 'SYSHA1'
            self.assertEqual(list(reg['peers']), [other])                             # the capture its role evidence read
            self.assertEqual(reg['peers'][other], registration(root, other)['capture'])

    def test_a_cross_frame_standby_is_pending_and_provisional(self):
        root = self.roots['primary_first']
        reg = registration(root, 'SYSHA1')
        self.assertEqual([(p['lpar'], p['primary'], p['primaryFrames']) for p in reg['pending']], [('hanodeA', 'hanodeB', ['SYSHA2'])])
        self.assertEqual(registration(root, 'SYSHA2')['pending'], [])
        with gzip.open(os.path.join(root, 'cycle', 'plans', reg['plan'] + '.json.gz'), 'rt') as fh:
            plan = json.load(fh)
        self.assertEqual(plan['cycle']['state'], 'provisional')
        self.assertEqual(plan['settings']['options']['enddate'], self.last)
        member = plan['members']['hanodeA']
        row = standby_row(root)
        self.assertTrue(row['NFIT - Action'].startswith('Provisional: the PowerHA floor from hanodeB is set when the planning cycle is '
                                                        'finalised. Until then 25% of its installed 1.50 limits a cut to 0.38 and adds no increase.'),
                        row['NFIT - Action'])
        self.assertEqual((member['published']['action'], member['published']['entitlement'], str(member['published']['VPs'])),
                         (row['NFIT - Action'], row['NFIT - ENT'], row['NFIT - VCPU']))
        self.assertIn('poolSize', member)
        self.assertEqual(member['roleEvidence']['configuration']['hanodeB'], {'entitlement': 1.5, 'VPs': 4})
        self.assertEqual(member['pendingRole']['share'], 0.25)
        self.assertLessEqual(float(row['NFIT - ENT']), float(row['Current - ENT']) + 1e-9)

    def test_the_provisional_result_does_not_depend_on_the_order(self):
        first, second = standby_row(self.roots['primary_first']), standby_row(self.roots['standby_first'])
        self.assertEqual(first, second)

    def test_models_join_and_options_count_as_resolved(self):
        root = self.roots['primary_first']
        cycle_before = load(os.path.join(root, 'cycle', 'cycle.json'))
        first = text(os.path.join(root, 'cycle', 'runs', 'SYSHA1.state-based.json'), 'rb')
        run = analyse(root, 'SYSHA1', self.last, '--decay-over-states')
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        self.assertEqual(load(os.path.join(root, 'cycle', 'cycle.json')), cycle_before)        # ID and context as they were
        self.assertEqual(text(os.path.join(root, 'cycle', 'runs', 'SYSHA1.state-based.json'), 'rb'), first)
        self.assertEqual(sorted(os.listdir(os.path.join(root, 'cycle', 'models'))), ['hybrid-state-decay.json', 'state-based.json'])
        self.assertEqual(registration(root, 'SYSHA1', 'hybrid-state-decay')['model'], 'hybrid-state-decay')
        # An option given twice counts by the value in effect (here the default): the rerun joins and replaces its registration.
        run = analyse(root, 'SYSHA2', self.last, '--guarantee-policy', 'strict', '--guarantee-policy', 'normal')
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        marker = load(os.path.join(root, 'cycle', 'runs', 'SYSHA2.state-based.started'))
        self.assertEqual(registration(root, 'SYSHA2')['token'], marker['token'])

    def test_what_does_not_join_and_what_closes_the_cycle(self):
        root = self.roots['standby_first']
        marker = text(os.path.join(root, 'cycle', 'runs', 'SYSHA2.state-based.started'))
        # Another option.
        run = analyse(root, 'SYSHA2', self.last, '--guarantee-policy', 'strict')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("its options differ from the cycle's state-based runs: --guarantee-policy ('strict' here, 'normal' in the cycle)", run.stderr)
        self.assertEqual(text(os.path.join(root, 'cycle', 'runs', 'SYSHA2.state-based.started')), marker)    # no new attempt
        # Selected LPARs, and a seasonal run.
        for flags in (('--vm', 'hanodeB'), ('--update-history',)):
            run = analyse(root, 'SYSHA2', self.last, *flags)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn('--cycle takes whole-frame runs of the standard models', run.stderr)
        # A changed configuration file.
        vms = os.path.join(root, 'etc', 'nfit.vms.cfg')
        original = text(vms)
        with open(vms, 'a') as fh:
            fh.write('# edited\n')
        run = analyse(root, 'SYSHA2', self.last)
        self.assertNotEqual(run.returncode, 0)
        self.assertRegex(run.stderr, r'does not belong to planning cycle \S+\. It differs in: nfit\.vms\.cfg \(changed\)\.')
        with open(vms, 'w') as fh:
            fh.write(original)
        self.assertNotIn('closed', load(os.path.join(root, 'cycle', 'cycle.json')))
        # A changed capture of a registered frame closes the cycle, for every frame.
        data = os.path.join(root, 'stage', 'SYSHA1', '.nfit.cache.data')
        os.utime(data, (1, 1))
        run = analyse(root, 'SYSHA1', self.last)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('is not the data analysed earlier in planning cycle', run.stderr)
        closed = load(os.path.join(root, 'cycle', 'cycle.json'))['closed']
        self.assertIn('the staged data of SYSHA1 changed', closed['reason'])
        run = analyse(root, 'SYSHA2', self.last)
        self.assertNotEqual(run.returncode, 0)
        self.assertRegex(run.stderr, r'Planning cycle \S+ is closed \(the staged data of SYSHA1 changed')


if __name__ == '__main__':
    unittest.main(verbosity=1)
